"""Immutable audit trail for all FinOps hub actions.

Every decision, alert, policy change, and user action is recorded
with tamper-detection checksums. Aligned with UK NCSC logging guidance
for security-relevant events.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import AuditEntry, CloudProvider

__all__ = ["AuditLogger"]


class AuditLogger:
    """Append-only audit logger with integrity verification."""

    def __init__(self, audit_dir: str | Path, *, base_dir: Path | None = None) -> None:
        audit_path = Path(audit_dir).resolve()
        # Path containment: if a base_dir is given, ensure audit_dir is within it
        if base_dir is not None:
            base = base_dir.resolve()
            try:
                audit_path.relative_to(base)
            except ValueError as exc:
                raise ValueError(f"audit_dir must be within {base}, got {audit_path}") from exc
        if ".." in str(audit_dir):
            raise ValueError("audit_dir: path traversal ('..') is not allowed")
        self._audit_dir = audit_path
        self._audit_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[AuditEntry] = []
        self._previous_checksum: str = ""

    def log(
        self,
        action: str,
        actor: str,
        target: str,
        provider: CloudProvider | None = None,
        details: dict[str, Any] | None = None,
        outcome: str = "success",
        related_alert_id: str = "",
        related_policy_id: str = "",
        correlation_id: str = "",
        causation_id: str = "",
    ) -> AuditEntry:
        """Record an audit entry with chained integrity checksum."""
        entry = AuditEntry(
            action=action,
            actor=actor,
            target=target,
            provider=provider,
            correlation_id=correlation_id,
            causation_id=causation_id,
            details=details or {},
            outcome=outcome,
            related_alert_id=related_alert_id,
            related_policy_id=related_policy_id,
        )
        entry.checksum = self._compute_checksum(entry)
        self._entries.append(entry)
        self._previous_checksum = entry.checksum
        self._persist_entry(entry)
        return entry

    def get_entries(
        self,
        action: str | None = None,
        actor: str | None = None,
        provider: CloudProvider | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters.

        The limit is capped at MAX_AUDIT_QUERY (10 000) to prevent
        unbounded memory consumption on long-running systems.
        """
        from .validation import MAX_AUDIT_QUERY

        limit = min(limit, MAX_AUDIT_QUERY)
        results = self._entries
        if action:
            results = [e for e in results if e.action == action]
        if actor:
            results = [e for e in results if e.actor == actor]
        if provider:
            results = [e for e in results if e.provider == provider]
        if since:
            results = [e for e in results if e.timestamp >= since]
        return results[-limit:]

    def verify_integrity(self) -> list[dict[str, str]]:
        """Verify the integrity chain of all audit entries.

        Returns a list of any entries where the checksum does not match,
        indicating potential tampering.
        """
        violations: list[dict[str, str]] = []
        prev_checksum = ""
        for entry in self._entries:
            expected = self._compute_checksum(entry, override_previous=prev_checksum)
            if entry.checksum != expected:
                violations.append(
                    {
                        "audit_id": entry.audit_id,
                        "timestamp": entry.timestamp.isoformat(),
                        "action": entry.action,
                        "expected_checksum": expected,
                        "actual_checksum": entry.checksum,
                    }
                )
            prev_checksum = entry.checksum
        return violations

    def _compute_checksum(self, entry: AuditEntry, override_previous: str | None = None) -> str:
        """Compute SHA-256 checksum chaining to the previous entry."""
        prev = override_previous if override_previous is not None else self._previous_checksum
        payload = (
            f"{prev}|{entry.audit_id}|{entry.timestamp.isoformat()}|"
            f"{entry.action}|{entry.actor}|{entry.target}|{entry.outcome}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def _persist_entry(self, entry: AuditEntry) -> None:
        """Persist audit entry to the append-only log file.

        Handles disk-full gracefully by logging to stderr as fallback.
        """
        date_str = entry.timestamp.strftime("%Y-%m-%d")
        log_file = self._audit_dir / f"audit-{date_str}.jsonl"
        try:
            with log_file.open("a") as f:
                f.write(json.dumps(entry.model_dump(), default=str) + "\n")
        except OSError as e:
            # Disk full or permission error — don't crash, log to stderr
            import sys

            print(
                f"AUDIT WRITE FAILED ({e}): {entry.action} by {entry.actor} on {entry.target}",
                file=sys.stderr,
            )

    def load_from_disk(self) -> int:
        """Load existing audit entries from disk. Returns count loaded."""
        count = 0
        for log_file in sorted(self._audit_dir.glob("audit-*.jsonl")):
            with log_file.open() as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data = json.loads(line)
                        entry = AuditEntry(**data)
                        self._entries.append(entry)
                        self._previous_checksum = entry.checksum
                        count += 1
        return count

    def export_for_compliance(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Export audit entries in a compliance-friendly format."""
        entries = self._entries
        if since:
            entries = [e for e in entries if e.timestamp >= since]
        if until:
            entries = [e for e in entries if e.timestamp <= until]
        return [
            {
                "audit_id": e.audit_id,
                "timestamp": e.timestamp.isoformat(),
                "action": e.action,
                "actor": e.actor,
                "target": e.target,
                "provider": e.provider.value if e.provider else None,
                "outcome": e.outcome,
                "details": e.details,
                "integrity_checksum": e.checksum,
            }
            for e in entries
        ]
