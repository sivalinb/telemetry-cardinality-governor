"""Durable UI projection and operation ledger. Temporal owns execution state."""

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

from governor.models import TERMINAL, Report


class Store:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript("""
              CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, phase TEXT NOT NULL, body TEXT NOT NULL, updated REAL NOT NULL
              );
              CREATE TABLE IF NOT EXISTS lease (scope TEXT PRIMARY KEY, workflow_id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL, action TEXT NOT NULL,
                status TEXT NOT NULL, before_yaml TEXT NOT NULL, after_yaml TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0
              );
              CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            conn.execute("INSERT OR IGNORE INTO settings VALUES ('scenario', '\"healthy\"')")
            conn.execute("INSERT OR IGNORE INTO settings VALUES ('last_finished', '0')")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def reserve(self, workflow_id: str) -> str:
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT workflow_id FROM lease WHERE scope='lab'").fetchone()
            if existing:
                return existing["workflow_id"]
            # Retried request IDs return the same report even after completion.
            if conn.execute("SELECT 1 FROM reports WHERE id=?", (workflow_id,)).fetchone():
                return workflow_id
            conn.execute("INSERT INTO lease VALUES ('lab', ?)", (workflow_id,))
            report = Report(workflow_id)
            conn.execute(
                "INSERT INTO reports VALUES (?, ?, ?, ?)",
                (
                    workflow_id,
                    report.phase,
                    json.dumps(asdict(report)),
                    time.time(),
                ),
            )
            return workflow_id

    def release_unstarted(self, workflow_id: str):
        with self.connection() as conn:
            conn.execute("DELETE FROM lease WHERE workflow_id=?", (workflow_id,))
            conn.execute("DELETE FROM reports WHERE id=? AND phase='queued'", (workflow_id,))

    def save(self, report: Report):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO reports VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "phase=excluded.phase, body=excluded.body, updated=excluded.updated",
                (
                    report.workflow_id,
                    report.phase,
                    json.dumps(asdict(report)),
                    time.time(),
                ),
            )
            if report.phase in TERMINAL and report.phase != "rollback_failed":
                conn.execute("DELETE FROM lease WHERE workflow_id=?", (report.workflow_id,))
                conn.execute(
                    "UPDATE settings SET value=? WHERE key='last_finished'", (str(time.time()),)
                )

    def active(self) -> str | None:
        with self.connection() as conn:
            row = conn.execute("SELECT workflow_id FROM lease WHERE scope='lab'").fetchone()
            return row[0] if row else None

    def get_report(self, workflow_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT body FROM reports WHERE id=?", (workflow_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def reports(self) -> list[dict]:
        with self.connection() as conn:
            return [
                json.loads(row[0])
                for row in conn.execute("SELECT body FROM reports ORDER BY updated DESC LIMIT 100")
            ]

    def get_setting(self, key: str, default=None):
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        with self.connection() as conn:
            conn.execute(
                "INSERT INTO settings VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value",
                (key, json.dumps(value)),
            )

    def operations(self, workflow_id: str) -> list[dict]:
        with self.connection() as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT id,action,status,attempts FROM operations "
                    "WHERE workflow_id=? ORDER BY rowid",
                    (workflow_id,),
                )
            ]
