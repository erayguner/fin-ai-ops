"""Event persistence abstraction.

Pluggable backends for storing and querying ResourceCreationEvent objects.
Ships with in-memory and SQLite backends. Additional backends (DynamoDB,
PostgreSQL, BigQuery) can be added by implementing BaseEventStore.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import CloudProvider, ResourceCreationEvent

__all__ = [
    "BaseEventStore",
    "InMemoryEventStore",
    "SQLiteEventStore",
]


class BaseEventStore(ABC):
    """Abstract interface for event persistence."""

    @abstractmethod
    def store(self, event: ResourceCreationEvent) -> None:
        """Persist a single event."""

    @abstractmethod
    def store_batch(self, events: list[ResourceCreationEvent]) -> int:
        """Persist multiple events. Returns count stored."""

    @abstractmethod
    def query(
        self,
        *,
        provider: CloudProvider | None = None,
        resource_type: str | None = None,
        creator: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[ResourceCreationEvent]:
        """Query events with optional filters."""

    @abstractmethod
    def count(self) -> int:
        """Return total event count."""

    @abstractmethod
    def exists(self, event_id: str) -> bool:
        """Check whether an event has already been stored."""

    @abstractmethod
    def get_cost_summary(
        self,
        *,
        provider: CloudProvider | None = None,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        """Aggregate cost data grouped by resource type and team."""


class InMemoryEventStore(BaseEventStore):
    """In-memory event store for development and testing."""

    def __init__(self) -> None:
        self._events: list[ResourceCreationEvent] = []
        self._index: set[str] = set()

    def store(self, event: ResourceCreationEvent) -> None:
        if event.event_id not in self._index:
            self._events.append(event)
            self._index.add(event.event_id)

    def store_batch(self, events: list[ResourceCreationEvent]) -> int:
        count = 0
        for event in events:
            if event.event_id not in self._index:
                self._events.append(event)
                self._index.add(event.event_id)
                count += 1
        return count

    def query(
        self,
        *,
        provider: CloudProvider | None = None,
        resource_type: str | None = None,
        creator: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[ResourceCreationEvent]:
        results = self._events
        if provider:
            results = [e for e in results if e.provider == provider]
        if resource_type:
            results = [e for e in results if e.resource_type == resource_type]
        if creator:
            results = [e for e in results if creator in (e.creator_email, e.creator_identity)]
        if since:
            results = [e for e in results if e.timestamp >= since]
        if until:
            results = [e for e in results if e.timestamp <= until]
        return results[-limit:]

    def count(self) -> int:
        return len(self._events)

    def exists(self, event_id: str) -> bool:
        return event_id in self._index

    def get_cost_summary(
        self,
        *,
        provider: CloudProvider | None = None,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        events = self._events
        if provider:
            events = [e for e in events if e.provider == provider]
        if since:
            events = [e for e in events if e.timestamp >= since]

        by_type: dict[str, dict[str, Any]] = {}
        by_team: dict[str, float] = {}
        for e in events:
            rt = e.resource_type
            if rt not in by_type:
                by_type[rt] = {"count": 0, "total_cost": 0.0}
            by_type[rt]["count"] += 1
            by_type[rt]["total_cost"] += e.estimated_monthly_cost_usd

            team = e.tags.get("team", "Untagged")
            by_team[team] = by_team.get(team, 0.0) + e.estimated_monthly_cost_usd

        return {
            "total_events": len(events),
            "total_cost": sum(e.estimated_monthly_cost_usd for e in events),
            "by_resource_type": by_type,
            "by_team": by_team,
        }


class SQLiteEventStore(BaseEventStore):
    """SQLite-backed event store for single-node production use."""

    def __init__(self, db_path: str | Path = "events.db") -> None:
        self._db_path = str(db_path)
        # For :memory: databases, keep a single connection alive
        self._persistent_conn: sqlite3.Connection | None = None
        if self._db_path == ":memory:":
            self._persistent_conn = sqlite3.connect(":memory:")
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            # Enable WAL mode for concurrent reads and crash resilience
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    correlation_id TEXT DEFAULT '',
                    provider TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    region TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    resource_name TEXT DEFAULT '',
                    creator_identity TEXT NOT NULL,
                    creator_email TEXT DEFAULT '',
                    estimated_monthly_cost_usd REAL DEFAULT 0.0,
                    tags TEXT DEFAULT '{}',
                    raw_event TEXT DEFAULT '{}'
                )
            """)
            # Migrate existing databases: add correlation_id if missing
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute("ALTER TABLE events ADD COLUMN correlation_id TEXT DEFAULT ''")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_provider ON events(provider)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_resource_type ON events(resource_type)"
            )

    def _connect(self) -> sqlite3.Connection:
        if self._persistent_conn is not None:
            return self._persistent_conn
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        return conn

    def health_check(self) -> dict[str, Any]:
        """Check SQLite store health — connection, table, integrity."""
        try:
            with self._connect() as conn:
                count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                return {
                    "status": "healthy" if integrity == "ok" else "degraded",
                    "events": count,
                    "integrity": integrity,
                    "backend": "sqlite",
                    "path": self._db_path,
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "backend": "sqlite",
                "path": self._db_path,
            }

    def store(self, event: ResourceCreationEvent) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, correlation_id, provider, timestamp, account_id,
                    region, resource_type, resource_id, resource_name,
                    creator_identity, creator_email,
                    estimated_monthly_cost_usd, tags, raw_event)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.event_id,
                    event.correlation_id,
                    event.provider.value,
                    event.timestamp.isoformat(),
                    event.account_id,
                    event.region,
                    event.resource_type,
                    event.resource_id,
                    event.resource_name,
                    event.creator_identity,
                    event.creator_email,
                    event.estimated_monthly_cost_usd,
                    json.dumps(event.tags),
                    json.dumps(event.raw_event, default=str),
                ),
            )

    def store_batch(self, events: list[ResourceCreationEvent]) -> int:
        count = 0
        with self._connect() as conn:
            for event in events:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, correlation_id, provider, timestamp,
                        account_id, region, resource_type, resource_id,
                        resource_name, creator_identity, creator_email,
                        estimated_monthly_cost_usd, tags, raw_event)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        event.event_id,
                        event.correlation_id,
                        event.provider.value,
                        event.timestamp.isoformat(),
                        event.account_id,
                        event.region,
                        event.resource_type,
                        event.resource_id,
                        event.resource_name,
                        event.creator_identity,
                        event.creator_email,
                        event.estimated_monthly_cost_usd,
                        json.dumps(event.tags),
                        json.dumps(event.raw_event, default=str),
                    ),
                )
                count += cursor.rowcount
        return count

    def query(
        self,
        *,
        provider: CloudProvider | None = None,
        resource_type: str | None = None,
        creator: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 100,
    ) -> list[ResourceCreationEvent]:
        clauses: list[str] = []
        params: list[Any] = []
        if provider:
            clauses.append("provider = ?")
            params.append(provider.value)
        if resource_type:
            clauses.append("resource_type = ?")
            params.append(resource_type)
        if creator:
            clauses.append("(creator_email = ? OR creator_identity = ?)")
            params.extend([creator, creator])
        if since:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        if until:
            clauses.append("timestamp <= ?")
            params.append(until.isoformat())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ?"  # noqa: S608  # nosec B608
        params.append(limit)

        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        return [self._row_to_event(row) for row in reversed(rows)]

    def count(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def exists(self, event_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM events WHERE event_id = ?", (event_id,)).fetchone()
            return row is not None

    def get_cost_summary(
        self,
        *,
        provider: CloudProvider | None = None,
        since: datetime | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        if provider:
            clauses.append("provider = ?")
            params.append(provider.value)
        if since:
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(estimated_monthly_cost_usd), 0) FROM events {where}",  # noqa: S608  # nosec B608
                params,
            ).fetchone()

            by_type_rows = conn.execute(
                f"SELECT resource_type, COUNT(*), SUM(estimated_monthly_cost_usd)"  # noqa: S608  # nosec B608
                f" FROM events {where} GROUP BY resource_type",
                params,
            ).fetchall()

            by_team_rows: list[Any] = []
            try:
                by_team_rows = conn.execute(
                    f"SELECT COALESCE(json_extract(tags, '$.team'), 'Untagged'),"  # noqa: S608  # nosec B608
                    f" SUM(estimated_monthly_cost_usd)"
                    f" FROM events {where}"
                    f" GROUP BY COALESCE(json_extract(tags, '$.team'), 'Untagged')",
                    params,
                ).fetchall()
            except Exception:
                by_team_rows = []

        by_type = {
            row[0]: {"count": row[1], "total_cost": round(row[2], 2)} for row in by_type_rows
        }

        by_team: dict[str, float] = {}
        if by_team_rows:
            by_team = {row[0]: round(row[1], 2) for row in by_team_rows}

        return {
            "total_events": total[0],
            "total_cost": round(total[1], 2),
            "by_resource_type": by_type,
            "by_team": by_team,
        }

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ResourceCreationEvent:
        # correlation_id may be absent in databases created before the migration
        correlation_id = ""
        with contextlib.suppress(IndexError, KeyError):
            correlation_id = row["correlation_id"] or ""
        return ResourceCreationEvent(
            event_id=row["event_id"],
            correlation_id=correlation_id,
            provider=CloudProvider(row["provider"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
            account_id=row["account_id"],
            region=row["region"],
            resource_type=row["resource_type"],
            resource_id=row["resource_id"],
            resource_name=row["resource_name"],
            creator_identity=row["creator_identity"],
            creator_email=row["creator_email"],
            estimated_monthly_cost_usd=row["estimated_monthly_cost_usd"],
            tags=json.loads(row["tags"]),
            raw_event=json.loads(row["raw_event"]),
        )
