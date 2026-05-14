"""Kill-switch + session-halt registry — framework §14.1.

Operators must be able to halt an agent mid-session in under a minute,
without killing the process or rotating IAM credentials. This module
implements the *backend* the governor consults; the *frontend* is the
``finops_halt_session`` MCP tool that writes a halt entry here.

Design:

* In-process registry keyed by ``session_id``. Thread-safe.
* A halted session causes every subsequent ``governed_call`` to
  short-circuit to ``Decision.DENY`` with ``denial_reason`` carrying the
  halt reason and the operator's identity.
* ``resume`` is supported but explicitly audited; halted-then-resumed is
  not the same as never-halted.
* The registry can be observed via :meth:`status` for the
  ``finops_session_stats`` MCP tool.

The supervisor does not own the audit logger or the agent trace; the
caller threads those in so this file stays pure.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

__all__ = [
    "SCHEMA_VERSION",
    "AgentSupervisor",
    "HaltEntry",
    "global_supervisor",
]


class HaltEntry(BaseModel):
    """Durable record of a halt action."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    halt_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    correlation_id: str = ""
    operator: str
    reason: str = ""
    halted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resumed_at: datetime | None = None
    resumed_by: str = ""
    resume_reason: str = ""

    @property
    def is_active(self) -> bool:
        return self.resumed_at is None


class AgentSupervisor:
    """Thread-safe halt registry the governor consults.

    Typical wiring:

        supervisor = AgentSupervisor()
        # ...inside governed_call, before policy check:
        if (active := supervisor.is_halted(session_id)):
            return Decision.DENY, f"session halted: {active.reason}"

    Halt + resume are themselves audited by the caller (the MCP server
    layer); this class is purely the registry and consultation surface.
    """

    def __init__(self) -> None:
        self._records: dict[str, HaltEntry] = {}
        self._lock = threading.Lock()

    def halt(
        self,
        *,
        session_id: str,
        operator: str,
        reason: str = "",
        correlation_id: str = "",
    ) -> HaltEntry:
        """Mark a session as halted. Idempotent for already-halted sessions."""
        with self._lock:
            existing = self._records.get(session_id)
            if existing is not None and existing.is_active:
                return existing
            entry = HaltEntry(
                session_id=session_id,
                operator=operator,
                reason=reason,
                correlation_id=correlation_id,
            )
            self._records[session_id] = entry
            return entry

    def resume(
        self,
        *,
        session_id: str,
        operator: str,
        reason: str = "",
    ) -> HaltEntry | None:
        """Lift a halt. Returns the resumed entry, or None if no halt exists.

        Resuming is itself observable: the entry stays in the registry
        with ``resumed_at`` / ``resumed_by`` populated, so the audit trail
        records both the halt and the lift. Framework §14.2 requires
        every override to be an ``AgentStep``.
        """
        with self._lock:
            entry = self._records.get(session_id)
            if entry is None or not entry.is_active:
                return None
            entry.resumed_at = datetime.now(UTC)
            entry.resumed_by = operator
            entry.resume_reason = reason
            return entry

    def is_halted(self, session_id: str) -> HaltEntry | None:
        """Return the active halt entry if the session is halted, else None.

        Callers should treat a non-None return as a deny signal. The
        returned entry's ``reason`` is suitable for inclusion in a
        ``Decision.DENY`` ``denial_reason``.
        """
        with self._lock:
            entry = self._records.get(session_id)
        if entry is None or not entry.is_active:
            return None
        return entry

    def status(self, session_id: str) -> dict[str, Any] | None:
        """Operator-facing status payload for ``finops_session_stats``."""
        with self._lock:
            entry = self._records.get(session_id)
        if entry is None:
            return None
        return entry.model_dump(mode="json")

    def all_active(self) -> list[HaltEntry]:
        """List every currently-halted session. For dashboards / health."""
        with self._lock:
            records = list(self._records.values())
        return [r for r in records if r.is_active]

    def all_records(self) -> list[HaltEntry]:
        with self._lock:
            return list(self._records.values())

    def clear(self, session_ids: Iterable[str] | None = None) -> int:
        """Remove records — test affordance, not for production use.

        Without arguments clears everything; with arguments clears the
        named sessions only. Returns the count removed.
        """
        with self._lock:
            if session_ids is None:
                count = len(self._records)
                self._records.clear()
                return count
            count = 0
            for sid in session_ids:
                if sid in self._records:
                    del self._records[sid]
                    count += 1
            return count


# ---------------------------------------------------------------------------
# Module-level singleton — the MCP server uses this; tests can inject a fresh
# AgentSupervisor() per case.
# ---------------------------------------------------------------------------


_GLOBAL: AgentSupervisor | None = None
_GLOBAL_LOCK = threading.Lock()


def global_supervisor() -> AgentSupervisor:
    """Lazy singleton for the process-wide supervisor."""
    global _GLOBAL
    with _GLOBAL_LOCK:
        if _GLOBAL is None:
            _GLOBAL = AgentSupervisor()
        return _GLOBAL
