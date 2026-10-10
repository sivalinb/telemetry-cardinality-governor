import asyncio
import json
import math
import time

import httpx

from governor.models import Snapshot

POOLS = {"control", "canary", "production"}


class Prometheus:
    def __init__(self, url: str, token: str = "", production_job: str = "governor-production"):
        self.url = url.rstrip("/")
        self.token = token
        self.production_job = production_job

    async def query(self, expression: str, *, at: float | None = None) -> list:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            response = await client.get(
                self.url + "/api/v1/query",
                params={
                    "query": expression,
                    "time": at or time.time(),
                    "timeout": "5s",
                },
            )
            response.raise_for_status()
            body = response.json()
            if body.get("status") != "success" or body.get("warnings"):
                raise RuntimeError("Prometheus returned incomplete query evidence")
            return body["data"]["result"]

    async def snapshot(self, pool: str, *, sample_metric: str = "") -> Snapshot:
        if pool not in POOLS:
            raise ValueError("Unknown pool")
        job = self.production_job if pool == "production" else "governor-" + pool
        job_literal = json.dumps(job)
        selector = "{job=" + job_literal + ',__name__!~"up|scrape_.*"}'
        # All queries in a snapshot use the same evaluation time. Freshness checks
        # reject stale data retained by Prometheus's normal lookback semantics.
        at = time.time()
        count_rows, health_rows, error_rows, freshness = await asyncio.gather(
            self.query("count by (__name__) (" + selector + ")", at=at),
            self.query("up{job=" + job_literal + "}", at=at),
            self.query("max(checkout_error_ratio{job=" + job_literal + "})", at=at),
            self.query("min(timestamp(up{job=" + job_literal + "}))", at=at),
        )
        metrics = {row["metric"]["__name__"]: int(float(row["value"][1])) for row in count_rows}
        error_ratio = float(error_rows[0]["value"][1]) if error_rows else 1
        valid_error_ratio = math.isfinite(error_ratio) and 0 <= error_ratio <= 1
        healthy = (
            bool(health_rows)
            and all(float(row["value"][1]) == 1 for row in health_rows)
            and bool(freshness)
            and at - float(freshness[0]["value"][1]) < 15
            and bool(error_rows)
            and valid_error_ratio
        )
        labels: dict[str, set[str]] = {}
        sampled = []
        if sample_metric:
            # The caller chooses only a validated metric from count_rows.
            if sample_metric not in metrics:
                raise ValueError("Sample metric absent from snapshot")
            sampled = await self.query(
                "topk(2000, " + sample_metric + "{job=" + job_literal + "})", at=at
            )
            for row in sampled:
                for name, value in row["metric"].items():
                    labels.setdefault(name, set()).add(value)
        return Snapshot(
            pool,
            at,
            sum(metrics.values()),
            metrics,
            {name: len(values) for name, values in labels.items()},
            len(sampled),
            2000,
            error_ratio if valid_error_ratio else 1,
            healthy,
        )
