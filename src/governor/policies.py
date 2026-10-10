"""A managed lab config deployer with a write-ahead journal and compare-and-swap.

This backend only changes the two explicitly owned lab scrape jobs. It does not
implement writes to an arbitrary production Prometheus installation.
"""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from governor.analysis import drop_rule
from governor.config import Config
from governor.models import PolicyCommand
from governor.store import Store


def initial_config(target: str) -> str:
    return yaml.safe_dump(
        {
            "global": {"scrape_interval": "2s", "scrape_timeout": "1s"},
            "scrape_configs": [
                {
                    "job_name": "governor-" + pool,
                    "metrics_path": "/lab/metrics/" + pool,
                    "static_configs": [{"targets": [target]}],
                }
                for pool in ["control", "canary", "production"]
            ],
        },
        sort_keys=False,
    )


def atomic_write(path: Path, body: str):
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)
    # Persist the rename as well as the file contents across a process crash.
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class LabPolicies:
    def __init__(self, config: Config, store: Store):
        self.config, self.store = config, store
        config.runtime_dir.mkdir(parents=True, exist_ok=True)

    def check_managed(self):
        if self.config.mode != "lab":
            raise ValueError("Observe mode cannot deploy policies")
        if urlparse(self.config.prometheus_url).hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
            "prometheus",
        }:
            raise ValueError("Lab writes require a local or Compose Prometheus target")

    @contextmanager
    def lock(self):
        with (self.config.runtime_dir / "policy.lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def initialize(self):
        self.check_managed()
        with self.lock():
            if not self.config.prometheus_config.exists():
                atomic_write(
                    self.config.prometheus_config, initial_config(self.config.exporter_target)
                )

    def reload(self):
        response = httpx.post(self.config.prometheus_url + "/-/reload", timeout=8)
        response.raise_for_status()

    def reset(self):
        self.check_managed()
        with self.lock():
            if self.store.active():
                raise ValueError("Finish the active workflow before resetting the lab")
            atomic_write(self.config.prometheus_config, initial_config(self.config.exporter_target))
            self.reload()

    def apply(self, command: PolicyCommand) -> str:
        self.check_managed()
        if command.action not in {"canary", "promote", "restore"}:
            raise ValueError("Unknown policy operation")
        if command.metric not in self.config.settings.allowed_drop_metrics:
            raise ValueError("Only configured optional metrics can be dropped")
        if command.metric in self.config.settings.protected_metrics:
            raise ValueError("Protected metric cannot be dropped")
        operation_id = command.workflow_id + ":" + command.action
        with self.lock():
            current = self.config.prometheus_config.read_text()
            with self.store.connection() as conn:
                operation = conn.execute(
                    "SELECT * FROM operations WHERE id=?", (operation_id,)
                ).fetchone()
                if operation and operation["status"] == "applied":
                    return "already_applied"
                if operation:
                    before, after = operation["before_yaml"], operation["after_yaml"]
                elif command.action == "restore":
                    own = conn.execute(
                        "SELECT before_yaml,after_yaml FROM operations WHERE workflow_id=? "
                        "ORDER BY rowid",
                        (command.workflow_id,),
                    ).fetchall()
                    if not own:
                        return "nothing_to_restore"
                    owned_versions = {version for row in own for version in row}
                    if current not in owned_versions:
                        raise ValueError(
                            "Config changed outside this workflow; refusing to overwrite it"
                        )
                    before, after = current, own[0]["before_yaml"]
                else:
                    before = current
                    data = yaml.safe_load(current)
                    jobs = {job["job_name"]: job for job in data.get("scrape_configs", [])}
                    if set(jobs) != {"governor-control", "governor-canary", "governor-production"}:
                        raise ValueError("Config is not the managed three-pool lab")
                    pool = "canary" if command.action == "canary" else "production"
                    job = jobs["governor-" + pool]
                    rules = job.setdefault("metric_relabel_configs", [])
                    rule = drop_rule(command.metric)
                    if rule not in rules:
                        rules.append(rule)
                    after = yaml.safe_dump(data, sort_keys=False)
                if not operation:
                    # The before-image commits before any file or HTTP mutation.
                    conn.execute(
                        "INSERT INTO operations "
                        "(id,workflow_id,action,status,before_yaml,after_yaml) "
                        "VALUES (?,?,?,'prepared',?,?)",
                        (
                            operation_id,
                            command.workflow_id,
                            command.action,
                            before,
                            after,
                        ),
                    )
                conn.execute(
                    "UPDATE operations SET attempts=attempts+1 WHERE id=?", (operation_id,)
                )
            if current not in {before, after}:
                raise ValueError("Config changed outside the prepared operation")
            if current != after:
                atomic_write(self.config.prometheus_config, after)
            # Retrying a prepared operation repeats a reload of the same bytes.
            self.reload()
            with self.store.connection() as conn:
                conn.execute("UPDATE operations SET status='applied' WHERE id=?", (operation_id,))
            return "applied"
