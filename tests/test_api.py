from dataclasses import replace

from fastapi.testclient import TestClient

from governor.api import create_app
from governor.config import Config
from governor.models import Report
from governor.store import Store


def test_dashboard_health_security_headers_and_request_validation(tmp_path):
    config = replace(Config.from_env(), runtime_dir=tmp_path)
    with TestClient(create_app(config)) as client:
        assert client.get("/healthz").json()["status"] == "ok"
        page = client.get("/")
        assert page.status_code == 200
        assert "Fewer runaway series" in page.text
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert client.post("/api/scans", json={"request_id": "../../bad"}).status_code == 422
        assert (
            client.post(
                "/api/scans", json={}, headers={"Origin": "https://other.example"}
            ).status_code
            == 403
        )


def test_token_authentication(tmp_path):
    config = replace(Config.from_env(), runtime_dir=tmp_path, api_token="test-only-value")
    with TestClient(create_app(config)) as client:
        assert client.get("/api/reports/absent").status_code == 401
        assert (
            client.get(
                "/api/reports/absent", headers={"Authorization": "Bearer test-only-value"}
            ).status_code
            == 404
        )


def test_observe_mode_disables_mutation_and_synthetic_exporter(tmp_path):
    config = replace(Config.from_env(), runtime_dir=tmp_path, mode="observe")
    with TestClient(create_app(config)) as client:
        assert client.get("/lab/metrics/production").status_code == 404
        assert client.post("/api/lab/scenario", json={"scenario": "explosion"}).status_code == 403


def test_active_workflow_prevents_scenario_reset(tmp_path):
    config = replace(Config.from_env(), runtime_dir=tmp_path)
    Store(config.database).reserve("existing-owner")
    with TestClient(create_app(config)) as client:
        assert client.post("/api/lab/scenario", json={"scenario": "healthy"}).status_code == 409


def test_missing_decision_target_is_not_created(tmp_path):
    config = replace(Config.from_env(), runtime_dir=tmp_path)
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/api/reports/missing/decision", json={"approved": True, "reviewer": "tester"}
        )
        assert response.status_code == 404
    assert Store(config.database).reports() == []


def test_projection_evidence_round_trip(tmp_path):
    config = replace(Config.from_env(), runtime_dir=tmp_path)
    store = Store(config.database)
    store.save(Report("test-report", phase="rejected", reason="Explicit rejection"))
    with TestClient(create_app(config)) as client:
        body = client.get("/api/reports/test-report").json()
        assert body["phase"] == "rejected" and body["operations"] == []
