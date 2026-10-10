from concurrent.futures import ThreadPoolExecutor

from governor.models import Report
from governor.store import Store


def test_concurrent_requests_have_one_config_owner(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    with ThreadPoolExecutor(max_workers=8) as executor:
        selected = list(executor.map(store.reserve, ["wf-" + str(i) for i in range(20)]))
    assert len(set(selected)) == 1
    assert len(store.reports()) == 1


def test_request_deduplication_survives_completion(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    store.reserve("wf-a")
    store.save(Report("wf-a", phase="succeeded"))
    assert store.active() is None
    assert store.reserve("wf-a") == "wf-a"
    assert store.active() is None
    assert store.get_report("wf-a")["phase"] == "succeeded"


def test_projection_repeated_write_is_idempotent(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    report = Report("wf-a", phase="awaiting_approval")
    store.save(report)
    store.save(report)
    assert len(store.reports()) == 1


def test_failed_rollback_keeps_exclusive_owner_for_manual_recovery(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    store.reserve("wf-a")
    store.save(Report("wf-a", phase="rollback_failed"))
    assert store.active() == "wf-a"
    assert store.reserve("wf-b") == "wf-a"
