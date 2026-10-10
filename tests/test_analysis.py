from dataclasses import replace

import pytest

from governor.analysis import drop_rule, propose, verify
from governor.exporter import exposition
from governor.models import Settings, Snapshot


def sample(pool="control", optional=1600, healthy=True, error=0.001):
    metrics = {
        "checkout_requests_total": 4,
        "checkout_request_duration_seconds_bucket": 8,
        "checkout_request_duration_seconds_count": 2,
        "checkout_request_duration_seconds_sum": 2,
        "checkout_error_ratio": 1,
    }
    if optional:
        metrics["checkout_debug_events_total"] = optional
    return Snapshot(
        pool,
        100,
        sum(metrics.values()),
        metrics,
        {"request_id": optional, "route": 2},
        optional,
        2000,
        error,
        healthy,
    )


def test_exact_rule_drops_whole_metric_not_label():
    assert drop_rule("checkout_debug_events_total") == {
        "source_labels": ["__name__"],
        "regex": "^checkout_debug_events_total$",
        "action": "drop",
    }
    with pytest.raises(ValueError):
        drop_rule(".*")


def test_healthy_budget_no_action():
    result = propose(sample(optional=12), Settings())
    assert not result.breached and not result.actionable


def test_explosion_exposes_bounded_label_evidence():
    result = propose(sample(), Settings())
    assert result.actionable
    assert result.offending_labels == ["request_id"]
    assert result.estimated_removed_series == 1600


def test_allowlist_and_protection_take_precedence():
    assert not propose(sample(), Settings(allowed_drop_metrics=[])).actionable
    protected = Settings(protected_metrics=["checkout_debug_events_total"])
    assert not propose(sample(), protected).actionable


def test_unhealthy_and_missing_label_evidence_block_policy():
    assert not propose(sample(healthy=False), Settings()).actionable
    assert not propose(replace(sample(), labels={}), Settings()).actionable


def test_verification_uses_contemporaneous_control():
    result = verify(
        sample(), sample("canary", optional=0), "checkout_debug_events_total", Settings()
    )
    assert result.passed
    assert result.reduction == pytest.approx(1600 / 1617, abs=0.000001)


@pytest.mark.parametrize(
    "candidate",
    [
        sample("canary", optional=0, healthy=False),
        sample("canary", optional=0, error=0.16),
        sample("canary"),
    ],
)
def test_failed_guardrails_prevent_promotion(candidate):
    result = verify(sample(), candidate, "checkout_debug_events_total", Settings())
    assert not result.passed and result.reasons


def test_missing_latency_buckets_prevents_promotion():
    candidate = sample("canary", optional=0)
    candidate.metrics.pop("checkout_request_duration_seconds_bucket")
    result = verify(sample(), candidate, "checkout_debug_events_total", Settings())
    assert not result.passed
    assert any("coverage" in reason for reason in result.reasons)


def test_nonfinite_error_evidence_cannot_pass():
    result = verify(
        sample(),
        sample("canary", optional=0, error=float("nan")),
        "checkout_debug_events_total",
        Settings(),
    )
    assert not result.passed


def test_synthetic_exporter_cardinality_and_real_exposition():
    from prometheus_client.parser import text_string_to_metric_families

    healthy = list(text_string_to_metric_families(exposition("production", "healthy")))
    spike = list(text_string_to_metric_families(exposition("production", "explosion")))
    assert sum(len(metric.samples) for metric in healthy) == 29
    assert sum(len(metric.samples) for metric in spike) == 1617
    assert "checkout_error_ratio 0.16" in exposition("canary", "rollback")
    assert "checkout_error_ratio 0.001" in exposition("production", "rollback")
