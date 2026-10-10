"""Serializable types shared by the API, activities and deterministic workflow."""

from dataclasses import dataclass, field

TERMINAL = {
    "succeeded",
    "no_action",
    "blocked",
    "rejected",
    "expired",
    "rolled_back",
    "failed",
    "rollback_failed",
}


@dataclass
class Settings:
    series_budget: int = 400
    metric_budget: int = 128
    label_budget: int = 32
    minimum_reduction: float = 0.25
    maximum_error_ratio: float = 0.03
    approval_timeout_seconds: float = 300
    observation_seconds: float = 8
    verification_samples: int = 3
    allowed_drop_metrics: list[str] = field(default_factory=lambda: ["checkout_debug_events_total"])
    protected_metrics: list[str] = field(
        default_factory=lambda: [
            "checkout_requests_total",
            "checkout_request_duration_seconds_bucket",
            "checkout_request_duration_seconds_count",
            "checkout_request_duration_seconds_sum",
            "checkout_error_ratio",
        ]
    )


@dataclass
class Snapshot:
    pool: str
    timestamp: float
    total_series: int
    metrics: dict[str, int]
    labels: dict[str, int] = field(default_factory=dict)
    sampled_series: int = 0
    sample_limit: int = 2000
    error_ratio: float = 0
    healthy: bool = True


@dataclass
class Proposal:
    breached: bool
    actionable: bool
    reason: str
    metric: str = ""
    offending_labels: list[str] = field(default_factory=list)
    estimated_removed_series: int = 0
    rule_yaml: str = ""


@dataclass
class Decision:
    approved: bool
    reviewer: str
    comment: str = ""


@dataclass
class GovernanceRequest:
    request_id: str
    settings: Settings = field(default_factory=Settings)


@dataclass
class Verification:
    passed: bool
    reduction: float
    reasons: list[str]
    control_series: int
    candidate_series: int
    candidate_error_ratio: float


@dataclass
class Report:
    workflow_id: str
    phase: str = "queued"
    reason: str = "Waiting for a worker"
    updated_at: str = ""
    baseline: Snapshot | None = None
    proposal: Proposal | None = None
    decision: Decision | None = None
    verification: Verification | None = None
    production_verification: Verification | None = None
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class PolicyCommand:
    workflow_id: str
    action: str
    metric: str


@dataclass
class VerifyCommand:
    metric: str
    pool: str
    settings: Settings
