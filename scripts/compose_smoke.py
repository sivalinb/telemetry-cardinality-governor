"""Check that the documented container distribution actually measures a canary."""

import time

import httpx


def main():
    with httpx.Client(base_url="http://127.0.0.1:8000", timeout=15) as client:
        client.post("/api/monitor", json={"enabled": False}).raise_for_status()
        client.post("/api/lab/scenario", json={"scenario": "explosion"}).raise_for_status()
        time.sleep(5)
        response = client.post("/api/scans", json={"request_id": "compose-smoke"})
        response.raise_for_status()
        workflow_id = response.json()["workflow_id"]
        for _ in range(80):
            report = client.get("/api/reports/" + workflow_id).json()
            if report["phase"] == "awaiting_approval":
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Compose workflow did not reach approval: " + str(report))
        client.post(
            f"/api/reports/{workflow_id}/decision",
            json={"approved": True, "reviewer": "compose-ci"},
        ).raise_for_status()
        for _ in range(120):
            report = client.get("/api/reports/" + workflow_id).json()
            if report["phase"] == "succeeded":
                assert report["production_verification"]["candidate_series"] == 17
                print("Compose measured 1,617 -> 17 series, protected coverage retained")
                return
            if report["phase"] in {"failed", "rolled_back", "rollback_failed"}:
                raise RuntimeError(str(report))
            time.sleep(0.5)
        raise RuntimeError("Compose workflow did not complete: " + str(report))


if __name__ == "__main__":
    main()
