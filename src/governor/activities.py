import asyncio

from temporalio import activity
from temporalio.exceptions import ApplicationError

from governor.analysis import propose, verify
from governor.config import Config
from governor.models import (
    PolicyCommand,
    Proposal,
    Report,
    Settings,
    Snapshot,
    Verification,
    VerifyCommand,
)
from governor.policies import LabPolicies
from governor.prometheus import Prometheus
from governor.store import Store


class Activities:
    def __init__(self, config: Config):
        self.config = config
        self.store = Store(config.database)
        self.prometheus = Prometheus(
            config.prometheus_url, config.prometheus_token, config.production_job
        )
        self.policies = LabPolicies(config, self.store)

    @activity.defn
    async def collect(self, pool: str) -> Snapshot:
        baseline = await self.prometheus.snapshot(pool)
        candidates = [
            (name, count)
            for name, count in baseline.metrics.items()
            if name in self.config.settings.allowed_drop_metrics
        ]
        if candidates:
            metric = max(candidates, key=lambda item: item[1])[0]
            return await self.prometheus.snapshot(pool, sample_metric=metric)
        return baseline

    @activity.defn
    def plan(self, snapshot: Snapshot, settings: Settings) -> Proposal:
        result = propose(snapshot, settings)
        if self.config.mode == "observe" and result.actionable:
            result.actionable = False
            result.reason += "; observe mode produces evidence only"
        return result

    @activity.defn
    def save_report(self, report: Report) -> None:
        self.store.save(report)

    @activity.defn
    def deploy_policy(self, command: PolicyCommand) -> str:
        try:
            return self.policies.apply(command)
        except ValueError as exc:
            raise ApplicationError(str(exc), type="PolicyConflict", non_retryable=True) from exc

    @activity.defn
    async def verify_policy(self, command: VerifyCommand) -> Verification:
        control, candidate = await asyncio.gather(
            self.prometheus.snapshot("control"),
            self.prometheus.snapshot(command.pool),
        )
        return verify(control, candidate, command.metric, command.settings)

    def registered(self) -> list:
        return [self.collect, self.plan, self.save_report, self.deploy_policy, self.verify_policy]
