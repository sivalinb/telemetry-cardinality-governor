"""Measured data-plane tests against real Temporal and Prometheus processes."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.e2e


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if os.getenv("GOVERNOR_RUN_E2E") != "1":
        pytest.skip("Set GOVERNOR_RUN_E2E=1 to launch the real stack")
    root = Path(__file__).parents[1]
    runtime = tmp_path_factory.mktemp("real-stack")
    ports = {key: free_port() for key in ["api", "prometheus", "temporal", "ui"]}
    url = "http://127.0.0.1:" + str(ports["api"])
    log = (runtime / "launcher.log").open("w")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "governor",
            "local",
            "--runtime-dir",
            str(runtime),
            "--binary-dir",
            str(root / ".runtime" / "bin"),
            "--no-monitor",
            "--port",
            str(ports["api"]),
            "--prometheus-port",
            str(ports["prometheus"]),
            "--temporal-port",
            str(ports["temporal"]),
            "--ui-port",
            str(ports["ui"]),
        ],
        cwd=root,
        stdout=log,
        stderr=log,
        env={**os.environ, "GOVERNOR_API_TOKEN": ""},
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise AssertionError((runtime / "launcher.log").read_text())
            try:
                state = httpx.get(url + "/api/state", timeout=2).json()
                if state["pools"]["production"].get("total_series") == 29:
                    break
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            time.sleep(0.25)
        else:
            raise AssertionError("Real stack did not become ready: " + str(runtime))
        with httpx.Client(base_url=url, timeout=20) as client:
            yield client, runtime, ports
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def poll(client, predicate, timeout=45):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get("/api/state").json()
        if predicate(last):
            return last
        time.sleep(0.25)
    raise AssertionError("State deadline exceeded: " + json.dumps(last))


def scenario(client, name, series):
    response = client.post("/api/lab/scenario", json={"scenario": name})
    assert response.status_code == 200, response.text
    return poll(
        client,
        lambda state: all(pool.get("total_series") == series for pool in state["pools"].values()),
    )


def scan(client, request_id=None):
    response = client.post("/api/scans", json={"request_id": request_id or uuid.uuid4().hex})
    assert response.status_code == 202, response.text
    return response.json()["workflow_id"]


def phase(client, workflow_id, wanted):
    state = poll(
        client,
        lambda state: any(
            report["workflow_id"] == workflow_id and report["phase"] == wanted
            for report in state["reports"]
        ),
    )
    return next(report for report in state["reports"] if report["workflow_id"] == workflow_id)


def decide(client, workflow_id, approved):
    response = client.post(
        f"/api/reports/{workflow_id}/decision",
        json={"approved": approved, "reviewer": "e2e-reviewer"},
    )
    assert response.status_code == 200, response.text


def test_real_ingestion_approval_promotion_deduplication_and_history(stack):
    client, runtime, _ = stack
    scenario(client, "explosion", 1617)
    request_id = "measured-success"
    workflow_id = scan(client, request_id)
    report = phase(client, workflow_id, "awaiting_approval")
    assert report["baseline"]["labels"]["request_id"] == 1600
    before = (runtime / "prometheus.yml").read_text()
    assert client.get(f"/api/reports/{workflow_id}").json()["operations"] == []
    assert scan(client, request_id) == workflow_id
    decide(client, workflow_id, True)
    report = phase(client, workflow_id, "succeeded")
    assert report["production_verification"]["candidate_series"] == 17
    assert report["production_verification"]["control_series"] == 1617
    assert report["production_verification"]["reduction"] == pytest.approx(0.989487, abs=1e-6)
    assert (runtime / "prometheus.yml").read_text() != before
    operations = client.get(f"/api/reports/{workflow_id}").json()["operations"]
    assert [op["action"] for op in operations] == ["canary", "promote"]
    assert all(op["attempts"] == 1 for op in operations)
    assert client.get(f"/api/reports/{workflow_id}/history").json()["events"]


def test_real_rejection_leaves_config_identical(stack):
    client, runtime, _ = stack
    scenario(client, "explosion", 1617)
    workflow_id = scan(client)
    phase(client, workflow_id, "awaiting_approval")
    before = (runtime / "prometheus.yml").read_text()
    decide(client, workflow_id, False)
    phase(client, workflow_id, "rejected")
    assert (runtime / "prometheus.yml").read_text() == before
    assert client.get(f"/api/reports/{workflow_id}").json()["operations"] == []


def test_real_error_guardrail_rolls_back_and_restores_series(stack):
    client, runtime, _ = stack
    scenario(client, "rollback", 1617)
    workflow_id = scan(client)
    phase(client, workflow_id, "awaiting_approval")
    before = (runtime / "prometheus.yml").read_text()
    decide(client, workflow_id, True)
    report = phase(client, workflow_id, "rolled_back")
    assert report["verification"]["candidate_error_ratio"] == 0.16
    assert (runtime / "prometheus.yml").read_text() == before
    poll(client, lambda state: state["pools"]["canary"]["total_series"] == 1617)
    assert [
        op["action"] for op in client.get(f"/api/reports/{workflow_id}").json()["operations"]
    ] == ["canary", "restore"]


def test_worker_sigkill_during_canary_recovers_without_duplicate_policy(stack):
    client, runtime, _ = stack
    scenario(client, "explosion", 1617)
    workflow_id = scan(client)
    phase(client, workflow_id, "awaiting_approval")
    decide(client, workflow_id, True)
    phase(client, workflow_id, "observing_canary")
    old_pid = json.loads((runtime / "processes.json").read_text())["worker"]
    os.kill(old_pid, signal.SIGKILL)
    report = phase(client, workflow_id, "succeeded")
    new_pid = json.loads((runtime / "processes.json").read_text())["worker"]
    assert old_pid != new_pid
    assert report["production_verification"]["candidate_series"] == 17
    operations = client.get(f"/api/reports/{workflow_id}").json()["operations"]
    assert len(operations) == 2 and all(op["attempts"] == 1 for op in operations)


def test_automatic_monitor_starts_governance(stack):
    client, _, _ = stack
    scenario(client, "explosion", 1617)
    assert client.post("/api/monitor", json={"enabled": True}).status_code == 200
    state = poll(client, lambda state: bool(state["active_workflow"]))
    workflow_id = state["active_workflow"]
    assert workflow_id.startswith("governor-auto-")
    phase(client, workflow_id, "awaiting_approval")
    decide(client, workflow_id, False)
    phase(client, workflow_id, "rejected")
    client.post("/api/monitor", json={"enabled": False})
