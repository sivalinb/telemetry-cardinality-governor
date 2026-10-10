import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
import pytest_asyncio
from temporalio import activity
from temporalio.client import WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from governor.analysis import propose, verify
from governor.models import (
    Decision,
    GovernanceRequest,
    PolicyCommand,
    Proposal,
    Report,
    Settings,
    Snapshot,
    Verification,
    VerifyCommand,
)
from governor.workflow import CardinalityWorkflow
from tests.test_analysis import sample

pytestmark = pytest.mark.integration


class StubActivities:
    """Deterministic evidence fixtures on a real Temporal service; not a fake workflow engine."""

    def __init__(self):
        self.optional = 1600
        self.fail_canary = False
        self.fail_production = False
        self.fail_promote = False
        self.commands: list[str] = []

    @activity.defn
    async def collect(self, pool: str) -> Snapshot:
        return sample(pool, self.optional)

    @activity.defn
    def plan(self, snapshot: Snapshot, settings: Settings) -> Proposal:
        return propose(snapshot, settings)

    @activity.defn
    def save_report(self, report: Report) -> None:
        pass

    @activity.defn
    def deploy_policy(self, command: PolicyCommand) -> str:
        self.commands.append(command.action)
        if command.action == "promote" and self.fail_promote:
            raise ApplicationError("Injected config conflict", non_retryable=True)
        return "applied"

    @activity.defn
    async def verify_policy(self, command: VerifyCommand) -> Verification:
        bad = (command.pool == "canary" and self.fail_canary) or (
            command.pool == "production" and self.fail_production
        )
        return verify(
            sample(),
            sample(command.pool, optional=0, error=0.16 if bad else 0.001),
            command.metric,
            command.settings,
        )


@pytest_asyncio.fixture
async def temporal_lab():
    binary = Path(__file__).parents[1] / ".runtime" / "bin" / "temporal"
    if not binary.exists():
        pytest.skip("Run governor fetch-binaries before Temporal integration tests")
    environment = await WorkflowEnvironment.start_local(dev_server_existing_path=str(binary))
    stub = StubActivities()
    queue = "test-" + uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            environment.client,
            task_queue=queue,
            workflows=[CardinalityWorkflow],
            activities=[
                stub.collect,
                stub.plan,
                stub.save_report,
                stub.deploy_policy,
                stub.verify_policy,
            ],
            activity_executor=executor,
        ):
            yield environment.client, queue, stub
    await environment.shutdown()


async def start(lab, settings=None):
    client, queue, _ = lab
    config = settings or replace(
        Settings(), observation_seconds=0.01, verification_samples=1, approval_timeout_seconds=5
    )
    return await client.start_workflow(
        CardinalityWorkflow.run,
        GovernanceRequest(uuid.uuid4().hex, config),
        id="test-" + uuid.uuid4().hex,
        task_queue=queue,
    )


async def wait_phase(handle, phase):
    for _ in range(200):
        state = await handle.query(CardinalityWorkflow.state)
        if state.phase == phase:
            return state
        await asyncio.sleep(0.025)
    raise AssertionError("Workflow did not reach " + phase)


async def test_approval_promotes_and_history_replays(temporal_lab):
    handle = await start(temporal_lab)
    await wait_phase(handle, "awaiting_approval")
    assert temporal_lab[2].commands == []
    assert (
        await handle.execute_update(CardinalityWorkflow.decide, Decision(True, "test-reviewer"))
        == "accepted"
    )
    result = await handle.result()
    assert result.phase == "succeeded"
    assert temporal_lab[2].commands == ["canary", "promote"]
    history = await handle.fetch_history()
    await Replayer(workflows=[CardinalityWorkflow]).replay_workflow(history)


async def test_rejection_never_deploys(temporal_lab):
    handle = await start(temporal_lab)
    await wait_phase(handle, "awaiting_approval")
    await handle.execute_update(CardinalityWorkflow.decide, Decision(False, "test-reviewer"))
    assert (await handle.result()).phase == "rejected"
    assert temporal_lab[2].commands == []


async def test_approval_expiry_never_deploys(temporal_lab):
    settings = replace(Settings(), approval_timeout_seconds=0.15)
    result = await (await start(temporal_lab, settings)).result()
    assert result.phase == "expired"
    assert temporal_lab[2].commands == []


async def test_canary_failure_restores_without_promotion(temporal_lab):
    temporal_lab[2].fail_canary = True
    handle = await start(temporal_lab)
    await wait_phase(handle, "awaiting_approval")
    await handle.execute_update(CardinalityWorkflow.decide, Decision(True, "test-reviewer"))
    result = await handle.result()
    assert result.phase == "rolled_back"
    assert temporal_lab[2].commands == ["canary", "restore"]


async def test_production_failure_restores_both_pools(temporal_lab):
    temporal_lab[2].fail_production = True
    handle = await start(temporal_lab)
    await wait_phase(handle, "awaiting_approval")
    await handle.execute_update(CardinalityWorkflow.decide, Decision(True, "test-reviewer"))
    assert (await handle.result()).phase == "rolled_back"
    assert temporal_lab[2].commands == ["canary", "promote", "restore"]


async def test_deploy_exception_uses_compensation(temporal_lab):
    temporal_lab[2].fail_promote = True
    handle = await start(temporal_lab)
    await wait_phase(handle, "awaiting_approval")
    await handle.execute_update(CardinalityWorkflow.decide, Decision(True, "test-reviewer"))
    assert (await handle.result()).phase == "failed"
    assert temporal_lab[2].commands == ["canary", "promote", "restore"]


async def test_healthy_workload_finishes_without_a_decision(temporal_lab):
    temporal_lab[2].optional = 12
    assert (await (await start(temporal_lab)).result()).phase == "no_action"
    assert temporal_lab[2].commands == []


async def test_empty_reviewer_is_rejected(temporal_lab):
    handle = await start(temporal_lab)
    await wait_phase(handle, "awaiting_approval")
    with pytest.raises(WorkflowUpdateFailedError):
        await handle.execute_update(CardinalityWorkflow.decide, Decision(True, ""))
    await handle.execute_update(CardinalityWorkflow.decide, Decision(False, "reviewer"))
    assert (await handle.result()).phase == "rejected"
