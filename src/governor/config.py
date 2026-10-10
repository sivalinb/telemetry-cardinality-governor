import json
import os
from dataclasses import dataclass
from pathlib import Path

from governor.models import Settings


@dataclass(frozen=True)
class Config:
    runtime_dir: Path
    prometheus_url: str
    temporal_address: str
    temporal_namespace: str
    task_queue: str
    mode: str
    api_token: str
    monitor_enabled: bool
    exporter_target: str
    settings: Settings
    prometheus_token: str = ""
    production_job: str = "governor-production"

    @classmethod
    def from_env(cls) -> "Config":
        mode = os.getenv("GOVERNOR_MODE", "lab")
        if mode not in {"lab", "observe"}:
            raise ValueError("GOVERNOR_MODE must be lab or observe")
        return cls(
            runtime_dir=Path(os.getenv("GOVERNOR_RUNTIME_DIR", ".runtime")).resolve(),
            prometheus_url=os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9090").rstrip("/"),
            temporal_address=os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
            temporal_namespace=os.getenv("TEMPORAL_NAMESPACE", "default"),
            task_queue=os.getenv("TEMPORAL_TASK_QUEUE", "cardinality-governor"),
            mode=mode,
            api_token=os.getenv("GOVERNOR_API_TOKEN", ""),
            monitor_enabled=os.getenv("GOVERNOR_MONITOR", "false").lower() == "true",
            exporter_target=os.getenv("GOVERNOR_EXPORTER_TARGET", "127.0.0.1:8000"),
            settings=Settings(
                series_budget=int(os.getenv("GOVERNOR_SERIES_BUDGET", "400")),
                approval_timeout_seconds=float(os.getenv("GOVERNOR_APPROVAL_TIMEOUT", "300")),
                observation_seconds=float(os.getenv("GOVERNOR_OBSERVATION_SECONDS", "8")),
                allowed_drop_metrics=json.loads(
                    os.getenv("GOVERNOR_OPTIONAL_METRICS", '["checkout_debug_events_total"]')
                ),
            ),
            prometheus_token=os.getenv("PROMETHEUS_BEARER_TOKEN", ""),
            production_job=os.getenv("PROMETHEUS_PRODUCTION_JOB", "governor-production"),
        )

    @property
    def database(self) -> Path:
        return self.runtime_dir / "governor.sqlite"

    @property
    def prometheus_config(self) -> Path:
        return self.runtime_dir / "prometheus.yml"
