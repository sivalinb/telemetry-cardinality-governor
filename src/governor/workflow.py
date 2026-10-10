"""Durable orchestration. All network, file and database I/O lives in activities."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from governor.models import (
        Decision,
        GovernanceRequest,
        PolicyCommand,
        Proposal,
        Report,
        Snapshot,
        Verification,
        VerifyCommand,
    )

RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=3)


@workflow.defn
class CardinalityWorkflow:
    def __init__(self):
        self.report = Report("")
        self.decision: Decision | None = None

    @workflow.query
    def state(self) -> Report:
        return self.report

    @workflow.update
    def decide(self, decision: Decision) -> str:
        self.decision = decision
        return "accepted"

    @decide.validator
    def validate_decision(self, decision: Decision):
        if self.report.phase != "awaiting_approval" or self.decision is not None:
            raise ValueError("This workflow is not accepting a decision")
        if not decision.reviewer.strip():
            raise ValueError("A reviewer name is required")

    async def activity(self, name: str, *args, result_type=None):
        return await workflow.execute_activity(
            name,
            args=args,
            result_type=result_type,
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=RETRY,
        )

    async def phase(self, phase: str, reason: str):
        self.report.phase, self.report.reason = phase, reason
        self.report.updated_at = workflow.now().isoformat()
        self.report.history.append({"phase": phase, "reason": reason, "at": self.report.updated_at})
        await self.activity("save_report", self.report)

    async def observe(self, request: GovernanceRequest, pool: str) -> Verification:
        assert self.report.proposal is not None
        await workflow.sleep(request.settings.observation_seconds)
        result = None
        for index in range(request.settings.verification_samples):
            result = await self.activity(
                "verify_policy",
                VerifyCommand(
                    self.report.proposal.metric,
                    pool,
                    request.settings,
                ),
                result_type=Verification,
            )
            if not result.passed:
                return result
            if index < request.settings.verification_samples - 1:
                await workflow.sleep(2)
        assert result is not None
        return result

    @workflow.run
    async def run(self, request: GovernanceRequest) -> Report:
        self.report.workflow_id = workflow.info().workflow_id
        policy_started = False
        try:
            await self.phase(
                "analyzing", "Collecting current production series and bounded label evidence"
            )
            self.report.baseline = await self.activity(
                "collect", "production", result_type=Snapshot
            )
            self.report.proposal = await self.activity(
                "plan", self.report.baseline, request.settings, result_type=Proposal
            )
            proposal = self.report.proposal
            if not proposal.breached or not proposal.actionable:
                await self.phase("blocked" if proposal.breached else "no_action", proposal.reason)
                return self.report
            await self.phase(
                "awaiting_approval", "Review the exact metric drop before any configuration write"
            )
            try:
                await workflow.wait_condition(
                    lambda: self.decision is not None,
                    timeout=timedelta(seconds=request.settings.approval_timeout_seconds),
                )
            except TimeoutError:
                await self.phase("expired", "Approval window expired; no policy was deployed")
                return self.report
            self.report.decision = self.decision
            assert self.decision is not None
            if not self.decision.approved:
                await self.phase(
                    "rejected", "Reviewer rejected the proposal; no policy was deployed"
                )
                return self.report
            await self.phase(
                "applying_canary", "Applying the approved rule to the canary scrape job"
            )
            policy_started = True  # Set before the first activity; its write may outlive a crash.
            await self.activity(
                "deploy_policy", PolicyCommand(self.report.workflow_id, "canary", proposal.metric)
            )
            await self.phase(
                "observing_canary", "Waiting for fresh scrapes and three verification samples"
            )
            self.report.verification = await self.observe(request, "canary")
            if not self.report.verification.passed:
                await self.activity(
                    "deploy_policy",
                    PolicyCommand(self.report.workflow_id, "restore", proposal.metric),
                )
                await self.phase("rolled_back", "; ".join(self.report.verification.reasons))
                return self.report
            await self.phase(
                "promoting", "Canary passed; applying the rule to the lab production scrape job"
            )
            await self.activity(
                "deploy_policy", PolicyCommand(self.report.workflow_id, "promote", proposal.metric)
            )
            await self.phase(
                "verifying_production",
                "Verifying production with the same coverage and error checks",
            )
            self.report.production_verification = await self.observe(request, "production")
            if not self.report.production_verification.passed:
                await self.activity(
                    "deploy_policy",
                    PolicyCommand(self.report.workflow_id, "restore", proposal.metric),
                )
                await self.phase(
                    "rolled_back", "; ".join(self.report.production_verification.reasons)
                )
                return self.report
            await self.phase("succeeded", "Measured reduction passed; protected metrics retained")
        except (Exception, asyncio.CancelledError) as exc:
            if policy_started and self.report.proposal:
                try:
                    await asyncio.shield(
                        self.activity(
                            "deploy_policy",
                            PolicyCommand(
                                self.report.workflow_id,
                                "restore",
                                self.report.proposal.metric,
                            ),
                        )
                    )
                except Exception as rollback_error:
                    await asyncio.shield(
                        self.phase(
                            "rollback_failed", "Manual recovery required: " + str(rollback_error)
                        )
                    )
                    return self.report
            await asyncio.shield(
                self.phase("failed", str(exc) or "Workflow cancelled; config restored")
            )
        return self.report
