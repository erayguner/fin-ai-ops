"""Alert persistence abstraction.

Pluggable backends for storing and querying CostAlert objects.
Ships with in-memory and SQLite backends.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .models import ActionStatus, CloudProvider, CostAlert, Severity

__all__ = [
    "BaseAlertStore",
    "InMemoryAlertStore",
    "SQLiteAlertStore",
]


class BaseAlertStore(ABC):
    """Abstract interface for alert persistence."""

    @abstractmethod
    def store(self, alert: CostAlert) -> None:
        """Persist a single alert."""

    @abstractmethod
    def get(self, alert_id: str) -> CostAlert | None:
        """Retrieve a single alert by ID."""

    @abstractmethod
    def query(
        self,
        *,
        severity: Severity | None = None,
        status: ActionStatus | None = None,
        provider: CloudProvider | None = None,
        limit: int = 100,
    ) -> list[CostAlert]:
        """Query alerts with optional filters."""

    @abstractmethod
    def update_status(
        self,
        alert_id: str,
        status: ActionStatus,
        *,
        acknowledged_by: str = "",
        resolved_by: str = "",
    ) -> bool:
        """Update alert status. Returns True if alert was found."""

    @abstractmethod
    def count(self) -> int:
        """Return total alert count."""


class InMemoryAlertStore(BaseAlertStore):
    """In-memory alert store for development and testing."""

    def __init__(self) -> None:
        self._alerts: dict[str, CostAlert] = {}

    def store(self, alert: CostAlert) -> None:
        self._alerts[alert.alert_id] = alert

    def get(self, alert_id: str) -> CostAlert | None:
        return self._alerts.get(alert_id)

    def query(
        self,
        *,
        severity: Severity | None = None,
        status: ActionStatus | None = None,
        provider: CloudProvider | None = None,
        limit: int = 100,
    ) -> list[CostAlert]:
        results = list(self._alerts.values())
        if severity:
            results = [a for a in results if a.severity == severity]
        if status:
            results = [a for a in results if a.status == status]
        if provider:
            results = [a for a in results if a.provider == provider]
        return results[-limit:]

    def update_status(
        self,
        alert_id: str,
        status: ActionStatus,
        *,
        acknowledged_by: str = "",
        resolved_by: str = "",
    ) -> bool:
        alert = self._alerts.get(alert_id)
        if alert is None:
            return False
        alert.status = status
        if acknowledged_by:
            alert.acknowledged_by = acknowledged_by
        if resolved_by:
            alert.resolved_by = resolved_by
        return True

    def count(self) -> int:
        return len(self._alerts)


class SQLiteAlertStore(BaseAlertStore):
    """SQLite-backed alert store for single-node production use."""

    def __init__(self, db_path: str | Path = "alerts.db") -> None:
        self._db_path = str(db_path)
        self._persistent_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:")
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    provider TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    resource_creator TEXT NOT NULL,
                    creator_email TEXT DEFAULT '',
                    team TEXT DEFAULT '',
                    cost_centre TEXT DEFAULT '',
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    resource_name TEXT DEFAULT '',
                    estimated_monthly_cost_usd REAL DEFAULT 0.0,
                    threshold_exceeded_usd REAL DEFAULT 0.0,
                    baseline_monthly_usd REAL DEFAULT 0.0,
                    cost_increase_percentage REAL DEFAULT 0.0,
                    recommended_actions TEXT DEFAULT '[]',
                    accountability_note TEXT DEFAULT '',
                    escalation_path TEXT DEFAULT '',
                    acknowledged_by TEXT DEFAULT '',
                    resolved_by TEXT DEFAULT '',
                    source_event_id TEXT NOT NULL,
                    correlation_id TEXT DEFAULT '',
                    policy_id TEXT DEFAULT '',
                    data TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_severity ON alerts(severity)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_provider ON alerts(provider)")

    def _connect(self) -> sqlite3.Connection:
        if self._persistent_conn is not None:
            return self._persistent_conn
        return sqlite3.connect(self._db_path, timeout=10.0)

    def store(self, alert: CostAlert) -> None:
        data = json.dumps(alert.model_dump(), default=str)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO alerts
                   (alert_id, created_at, severity, status, provider, account_id,
                    region, title, summary, resource_creator, creator_email,
                    team, cost_centre, resource_type, resource_id, resource_name,
                    estimated_monthly_cost_usd, threshold_exceeded_usd,
                    baseline_monthly_usd, cost_increase_percentage,
                    recommended_actions, accountability_note, escalation_path,
                    acknowledged_by, resolved_by, source_event_id,
                    correlation_id, policy_id, data)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    alert.alert_id,
                    alert.created_at.isoformat(),
                    alert.severity.value,
                    alert.status.value,
                    alert.provider.value,
                    alert.account_id,
                    alert.region,
                    alert.title,
                    alert.summary,
                    alert.resource_creator,
                    alert.creator_email,
                    alert.team,
                    alert.cost_centre,
                    alert.resource_type,
                    alert.resource_id,
                    alert.resource_name,
                    alert.estimated_monthly_cost_usd,
                    alert.threshold_exceeded_usd,
                    alert.baseline_monthly_usd,
                    alert.cost_increase_percentage,
                    json.dumps(alert.recommended_actions),
                    alert.accountability_note,
                    alert.escalation_path,
                    alert.acknowledged_by,
                    alert.resolved_by,
                    alert.source_event_id,
                    alert.correlation_id,
                    alert.policy_id,
                    data,
                ),
            )

    def get(self, alert_id: str) -> CostAlert | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        if row is None:
            return None
        return CostAlert(**json.loads(row[0]))

    def query(
        self,
        *,
        severity: Severity | None = None,
        status: ActionStatus | None = None,
        provider: CloudProvider | None = None,
        limit: int = 100,
    ) -> list[CostAlert]:
        clauses: list[str] = []
        params: list[Any] = []
        if severity:
            clauses.append("severity = ?")
            params.append(severity.value)
        if status:
            clauses.append("status = ?")
            params.append(status.value)
        if provider:
            clauses.append("provider = ?")
            params.append(provider.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT data FROM alerts {where} ORDER BY created_at DESC LIMIT ?"  # noqa: S608  # nosec B608
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [CostAlert(**json.loads(row[0])) for row in reversed(rows)]

    def update_status(
        self,
        alert_id: str,
        status: ActionStatus,
        *,
        acknowledged_by: str = "",
        resolved_by: str = "",
    ) -> bool:
        alert = self.get(alert_id)
        if alert is None:
            return False
        alert.status = status
        if acknowledged_by:
            alert.acknowledged_by = acknowledged_by
        if resolved_by:
            alert.resolved_by = resolved_by
        self.store(alert)
        return True

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
