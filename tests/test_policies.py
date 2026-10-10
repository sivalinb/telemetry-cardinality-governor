from dataclasses import replace

import httpx
import pytest
import yaml

from governor.config import Config
from governor.models import PolicyCommand
from governor.policies import LabPolicies
from governor.store import Store


@pytest.fixture
def lab(tmp_path, monkeypatch):
    config = replace(Config.from_env(), runtime_dir=tmp_path, mode="lab")
    store = Store(config.database)
    policies = LabPolicies(config, store)
    policies.initialize()
    monkeypatch.setattr(policies, "reload", lambda: None)
    return policies, store


def command(action="canary", metric="checkout_debug_events_total"):
    return PolicyCommand("wf-example", action, metric)


def test_only_canary_changes_and_repeated_activity_is_idempotent(lab):
    policies, store = lab
    original = yaml.safe_load(policies.config.prometheus_config.read_text())
    assert policies.apply(command()) == "applied"
    assert policies.apply(command()) == "already_applied"
    new = yaml.safe_load(policies.config.prometheus_config.read_text())
    assert new["scrape_configs"][0] == original["scrape_configs"][0]
    assert new["scrape_configs"][2] == original["scrape_configs"][2]
    assert "metric_relabel_configs" in new["scrape_configs"][1]
    assert store.operations("wf-example")[0]["attempts"] == 1


def test_crash_after_file_write_recovers_from_prepared_journal(lab, monkeypatch):
    policies, store = lab
    calls = 0

    def flaky_reload():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("worker died before recording reload result")

    monkeypatch.setattr(policies, "reload", flaky_reload)
    with pytest.raises(httpx.ReadTimeout):
        policies.apply(command())
    after_write = policies.config.prometheus_config.read_text()
    assert store.operations("wf-example")[0]["status"] == "prepared"
    assert policies.apply(command()) == "applied"
    assert policies.config.prometheus_config.read_text() == after_write
    assert store.operations("wf-example")[0]["attempts"] == 2


def test_rollback_restores_before_image_after_promotion(lab):
    policies, store = lab
    before = policies.config.prometheus_config.read_text()
    policies.apply(command())
    policies.apply(command("promote"))
    policies.apply(command("restore"))
    assert policies.config.prometheus_config.read_text() == before
    assert [op["action"] for op in store.operations("wf-example")] == [
        "canary",
        "promote",
        "restore",
    ]


def test_compare_and_swap_preserves_external_change(lab):
    policies, _ = lab
    policies.apply(command())
    path = policies.config.prometheus_config
    external = path.read_text() + "# an operator changed this file\n"
    path.write_text(external)
    with pytest.raises(ValueError, match="outside"):
        policies.apply(command("restore"))
    assert path.read_text() == external


@pytest.mark.parametrize("metric", ["checkout_requests_total", ".*", "unknown_metric"])
def test_unapproved_metrics_never_change_config(lab, metric):
    policies, store = lab
    before = policies.config.prometheus_config.read_text()
    with pytest.raises(ValueError):
        policies.apply(command(metric=metric))
    assert policies.config.prometheus_config.read_text() == before
    assert store.operations("wf-example") == []


def test_observe_mode_cannot_deploy(lab):
    policies, _ = lab
    readonly = LabPolicies(replace(policies.config, mode="observe"), policies.store)
    with pytest.raises(ValueError, match="Observe"):
        readonly.apply(command())


def test_external_host_cannot_be_managed(lab):
    policies, _ = lab
    external = LabPolicies(
        replace(policies.config, prometheus_url="https://example.com"), policies.store
    )
    with pytest.raises(ValueError, match="local"):
        external.apply(command())
