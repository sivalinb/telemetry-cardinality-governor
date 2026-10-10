import asyncio
import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from temporalio.client import Client
from temporalio.exceptions import WorkflowAlreadyStartedError

from governor.config import Config
from governor.exporter import exposition
from governor.models import Decision, GovernanceRequest
from governor.policies import LabPolicies
from governor.prometheus import POOLS, Prometheus
from governor.store import Store
from governor.workflow import CardinalityWorkflow

logger = logging.getLogger(__name__)


class ScanBody(BaseModel):
    request_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex, pattern=r"^[a-zA-Z0-9_-]{1,80}$"
    )


class DecisionBody(BaseModel):
    approved: bool
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str = Field(default="", max_length=1000)


class ScenarioBody(BaseModel):
    scenario: str = Field(pattern=r"^(healthy|explosion|rollback)$")


class MonitorBody(BaseModel):
    enabled: bool


def create_app(config: Config | None = None) -> FastAPI:
    config = config or Config.from_env()
    store = Store(config.database)
    prom = Prometheus(config.prometheus_url, config.prometheus_token, config.production_job)
    policies = LabPolicies(config, store)
    static = Path(__file__).parent / "static"

    async def connect() -> Client:
        if not getattr(app.state, "temporal", None):
            app.state.temporal = await Client.connect(
                config.temporal_address, namespace=config.temporal_namespace
            )
        return app.state.temporal

    async def start_scan(request_id: str) -> str:
        workflow_id = "governor-" + request_id
        selected = store.reserve(workflow_id)
        if selected != workflow_id or store.get_report(selected)["phase"] != "queued":
            return selected
        try:
            client = await connect()
            await client.start_workflow(
                CardinalityWorkflow.run,
                GovernanceRequest(request_id, config.settings),
                id=workflow_id,
                task_queue=config.task_queue,
            )
        except WorkflowAlreadyStartedError:
            pass
        except Exception:
            # Keep a queued reservation when a RPC outcome is ambiguous. A retry
            # uses the same workflow ID, avoiding a second config owner.
            raise
        return workflow_id

    async def monitor():
        while True:
            await asyncio.sleep(3)
            active = store.active()
            if active:
                # Recover a reservation left by an API crash or ambiguous RPC.
                if store.get_report(active)["phase"] == "queued":
                    try:
                        await start_scan(active.removeprefix("governor-"))
                    except Exception as exc:
                        logger.warning("Queued dispatch will retry: %s", exc)
                continue
            if not store.get_setting("monitor", config.monitor_enabled):
                continue
            try:
                import time

                if time.time() - store.get_setting("last_finished", 0) < 60:
                    continue
                snapshot = await prom.snapshot("production")
                if snapshot.healthy and snapshot.total_series > config.settings.series_budget:
                    await start_scan("auto-" + uuid.uuid4().hex[:16])
            except Exception as exc:
                logger.warning("Monitor will retry after telemetry/Temporal failure: %s", exc)

    @asynccontextmanager
    async def lifespan(_app):
        task = asyncio.create_task(monitor())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    app = FastAPI(title="Telemetry Cardinality Governor", version="0.1.0", lifespan=lifespan)

    async def access(request: Request):
        if config.api_token:
            supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(supplied, config.api_token):
                raise HTTPException(401, "A valid API bearer token is required")
        origin = request.headers.get("Origin")
        if request.method != "GET" and origin:
            # A local dashboard cannot be used as a cross-origin write target.
            expected = str(request.base_url).rstrip("/")
            if origin != expected:
                raise HTTPException(403, "Cross-origin writes are not accepted")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/healthz")
    def health():
        return {"status": "ok", "mode": config.mode}

    @app.get("/lab/metrics/{pool}", response_class=PlainTextResponse)
    def lab_metrics(pool: str):
        if config.mode != "lab" or pool not in POOLS:
            raise HTTPException(404, "Lab exporter unavailable")
        return PlainTextResponse(
            exposition(pool, store.get_setting("scenario", "healthy")),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/api/state", dependencies=[Depends(access)])
    async def state():
        results = await asyncio.gather(
            *(prom.snapshot(pool) for pool in sorted(POOLS)), return_exceptions=True
        )
        pools = {
            pool: (
                asdict(result)
                if not isinstance(result, BaseException)
                else {"error": "Telemetry unavailable"}
            )
            for pool, result in zip(sorted(POOLS), results, strict=True)
        }
        return {
            "mode": config.mode,
            "scenario": store.get_setting("scenario", "healthy"),
            "pools": pools,
            "settings": asdict(config.settings),
            "monitor": store.get_setting("monitor", config.monitor_enabled),
            "reports": store.reports(),
            "active_workflow": store.active(),
        }

    @app.post("/api/scans", status_code=202, dependencies=[Depends(access)])
    async def scan(body: ScanBody):
        try:
            workflow_id = await start_scan(body.request_id)
        except Exception as exc:
            logger.exception("Unable to dispatch scan")
            raise HTTPException(
                503, "Temporal unavailable; retry with the same request_id"
            ) from exc
        return {"workflow_id": workflow_id}

    @app.get("/api/reports/{workflow_id}", dependencies=[Depends(access)])
    def report(workflow_id: str):
        body = store.get_report(workflow_id)
        if not body:
            raise HTTPException(404, "Workflow not found")
        return {**body, "operations": store.operations(workflow_id)}

    @app.get("/api/reports/{workflow_id}/history", dependencies=[Depends(access)])
    async def history(workflow_id: str):
        if not store.get_report(workflow_id):
            raise HTTPException(404, "Workflow not found")
        client = await connect()
        result = await client.get_workflow_handle(workflow_id).fetch_history()
        return PlainTextResponse(
            result.to_json(),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{workflow_id}.json"'},
        )

    @app.post("/api/reports/{workflow_id}/decision", dependencies=[Depends(access)])
    async def decision(workflow_id: str, body: DecisionBody):
        if not store.get_report(workflow_id):
            raise HTTPException(404, "Workflow not found")
        try:
            client = await connect()
            handle = client.get_workflow_handle(workflow_id)
            result = await handle.execute_update(
                CardinalityWorkflow.decide, Decision(**body.model_dump())
            )
        except Exception as exc:
            raise HTTPException(409, "Decision not accepted; refresh workflow state") from exc
        return {"status": result}

    @app.post("/api/lab/scenario", dependencies=[Depends(access)])
    async def scenario(body: ScenarioBody):
        if config.mode != "lab":
            raise HTTPException(403, "Observe mode has no lab scenario controls")
        if store.active():
            raise HTTPException(409, "Finish the current workflow before changing the scenario")
        try:
            await asyncio.to_thread(policies.reset)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "Prometheus reload unavailable") from exc
        store.set_setting("scenario", body.scenario)
        store.set_setting("last_finished", 0)
        return {"scenario": body.scenario, "scrape_settle_seconds": 5}

    @app.post("/api/monitor", dependencies=[Depends(access)])
    def set_monitor(body: MonitorBody):
        store.set_setting("monitor", body.enabled)
        return {"enabled": body.enabled}

    @app.get("/metrics", response_class=PlainTextResponse)
    def governor_metrics():
        reports = store.reports()
        phases: dict[str, int] = {}
        for item in reports:
            phases[item["phase"]] = phases.get(item["phase"], 0) + 1
        lines = [
            "# HELP governor_workflows Workflows in the retained UI projection (last 100)",
            "# TYPE governor_workflows gauge",
        ]
        lines.extend(
            f'governor_workflows{{phase="{phase}"}} {count}'
            for phase, count in sorted(phases.items())
        )
        lines.append(f"governor_active_workflow {int(store.active() is not None)}")
        return "\n".join(lines) + "\n"

    return app
