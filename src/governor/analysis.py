"""Transparent policy decisions; no model, credential or opaque confidence score."""

import math
import re

import yaml

from governor.models import Proposal, Settings, Snapshot, Verification

METRIC_NAME = re.compile(r"^[a-zA-Z_:][a-zA-Z0-9_:]*$")


def drop_rule(metric: str) -> dict:
    if not METRIC_NAME.fullmatch(metric):
        raise ValueError("Invalid metric name")
    return {"source_labels": ["__name__"], "regex": "^" + re.escape(metric) + "$", "action": "drop"}


def propose(snapshot: Snapshot, settings: Settings) -> Proposal:
    if not snapshot.healthy:
        return Proposal(False, False, "The scrape is unhealthy; collect fresh evidence first")
    if snapshot.total_series <= settings.series_budget:
        return Proposal(False, False, f"Within the {settings.series_budget:,}-series budget")
    candidates = [
        (name, count)
        for name, count in snapshot.metrics.items()
        if name in settings.allowed_drop_metrics
        and name not in settings.protected_metrics
        and count > settings.metric_budget
    ]
    if not candidates:
        return Proposal(
            True, False, "Budget breached, but no optional metric is eligible for dropping"
        )
    metric, count = max(candidates, key=lambda item: (item[1], item[0]))
    labels = [
        label
        for label, distinct in sorted(snapshot.labels.items())
        if distinct > settings.label_budget and label not in {"job", "instance", "__name__"}
    ]
    if not labels:
        return Proposal(
            True, False, "Budget breached; sampled labels do not establish an unbounded dimension"
        )
    return Proposal(
        True,
        True,
        f"{metric} contributes {count:,} series with high-cardinality labels",
        metric,
        labels,
        count,
        yaml.safe_dump({"metric_relabel_configs": [drop_rule(metric)]}, sort_keys=False),
    )


def verify(control: Snapshot, candidate: Snapshot, metric: str, settings: Settings) -> Verification:
    reduction = 1 - candidate.total_series / control.total_series if control.total_series else 0
    reasons = []
    if not control.healthy or not candidate.healthy or control.total_series == 0:
        reasons.append("Missing or unhealthy scrape evidence")
    if candidate.metrics.get(metric, 0):
        reasons.append("The offending metric is still being ingested")
    if reduction < settings.minimum_reduction:
        reasons.append(
            f"Series reduction {reduction:.1%} is below {settings.minimum_reduction:.0%}"
        )
    for name in settings.protected_metrics:
        if not control.metrics.get(name) or candidate.metrics.get(name) != control.metrics.get(
            name
        ):
            reasons.append(f"Protected metric coverage changed: {name}")
    if (
        not math.isfinite(candidate.error_ratio)
        or not 0 <= candidate.error_ratio <= settings.maximum_error_ratio
    ):
        reasons.append(f"Error ratio {candidate.error_ratio:.1%} exceeds the guardrail")
    return Verification(
        not reasons,
        round(reduction, 6),
        reasons,
        control.total_series,
        candidate.total_series,
        candidate.error_ratio,
    )
