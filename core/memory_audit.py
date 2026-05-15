"""Audited memory adapter — framework §11.6 / ADR-008 §1.

Both Vertex AI **Memory Bank** and Bedrock **AgentCore Memory** are
first-class managed primitives now (both GA as of 2026-Q1). They store
data *about users* across sessions, which inherits the full §11 data-
handling obligations *plus* four memory-specific controls (framework
§11.6):

1. **Retention** — declared per memory store, defaulting to the
   minimum required for the agent's purpose. Expired entries are
   purged, not archived.
2. **User-scoped deletion** — a right-to-be-forgotten request deletes
   every memory entry associated with the user across every session.
3. **Cross-session isolation** — keyed by user / tenant; cross-tenant
   retrieval is a security incident (§15).
4. **Memory-injection threat model** — retrieved memory is treated as
   untrusted input. It passes through the same input filters
   (:mod:`core.filters`) as user prompts.

This module supplies a provider-agnostic adapter that wraps any
backend implementing :class:`MemoryBackend` with:

* write-side filter pass (PII / secret / prompt-injection)
* read-side filter pass (memory-injection defence)
* mandatory ``user_id`` on every write
* :meth:`MemoryAdapter.delete_for_user` — RtbF
* expiry sweep
* :class:`~core.agent_trace.MemoryOperationStep` emission for every op
* :class:`~core.audit.AuditLogger` entry per op

Backends shipped here:

* :class:`InMemoryMemoryBackend` — in-process dict + lock. For tests
  and dev. Honors TTL.

Other backends (``MemoryBankBackend``, ``AgentCoreMemoryBackend``)
live in their respective ``providers/*/memory.py`` files and are
constructed at runtime by the cloud setup script.
"""

from __future__ import annotations

import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from .agent_trace import AgentTrace, MemoryOperationStep
from .filters import (
    PIIRedactor,
    PromptInjectionHeuristic,
    SecretScanner,
)

if TYPE_CHECKING:
    from .audit import AuditLogger

SCHEMA_VERSION = "1"

__all__ = [
    "SCHEMA_VERSION",
    "InMemoryMemoryBackend",
    "MemoryAdapter",
    "MemoryBackend",
    "MemoryInjectionError",
    "MemoryRecord",
]


_PREVIEW_CHARS = 512


class MemoryInjectionError(RuntimeError):
    """Raised when a memory read returns content that fails the filter stack."""


class MemoryRecord(BaseModel):
    """Durable record of a single memory entry, keyed by user."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str = Field(
        description="Owning principal. Mandatory — drives cross-tenant "
        "isolation + the right-to-be-forgotten flow."
    )
    session_id: str = ""
    content: str
    provenance: str = Field(
        default="agent",
        description="Where this memory came from: 'agent', 'user', 'ingest_event', 'tool_output'.",
    )
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = None

    def is_expired(self, *, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or datetime.now(UTC)) > self.expires_at


class MemoryBackend(ABC):
    """Abstract interface every memory store must implement."""

    name: str = "abstract"

    @abstractmethod
    def put(self, record: MemoryRecord) -> MemoryRecord:
        """Persist a record. Returns the stored form (server may rewrite IDs)."""

    @abstractmethod
    def get(self, user_id: str, memory_id: str) -> MemoryRecord | None:
        """Fetch a single record. None if not found."""

    @abstractmethod
    def query(
        self,
        user_id: str,
        *,
        tag: str | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        """Fetch records for ``user_id`` — never crosses user boundaries."""

    @abstractmethod
    def delete(self, user_id: str, memory_id: str) -> bool:
        """Delete a single record. Returns True if deleted."""

    @abstractmethod
    def delete_all_for_user(self, user_id: str) -> int:
        """RtbF — remove every record for ``user_id``. Returns count."""

    @abstractmethod
    def sweep_expired(self, *, now: datetime | None = None) -> int:
        """Hard-delete entries past their expiry. Returns count purged."""


class InMemoryMemoryBackend(MemoryBackend):
    """Thread-safe in-process backend. For tests and dev runs.

    Same semantics as the managed backends: user-keyed, RtbF-supporting,
    TTL-respecting. Not persistent; restart loses state.
    """

    name = "in_memory"

    def __init__(self) -> None:
        # records[user_id][memory_id] = MemoryRecord
        self._records: dict[str, dict[str, MemoryRecord]] = {}
        self._lock = threading.Lock()

    def put(self, record: MemoryRecord) -> MemoryRecord:
        if not record.user_id:
            raise ValueError("MemoryRecord.user_id is mandatory (cross-tenant isolation)")
        with self._lock:
            self._records.setdefault(record.user_id, {})[record.memory_id] = record
        return record

    def get(self, user_id: str, memory_id: str) -> MemoryRecord | None:
        with self._lock:
            return self._records.get(user_id, {}).get(memory_id)

    def query(
        self,
        user_id: str,
        *,
        tag: str | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        now = now or datetime.now(UTC)
        with self._lock:
            entries = list(self._records.get(user_id, {}).values())
        out: list[MemoryRecord] = []
        for r in entries:
            if r.is_expired(now=now):
                continue
            if tag and tag not in r.tags:
                continue
            out.append(r)
        # Newest first; cap at limit.
        out.sort(key=lambda r: r.created_at, reverse=True)
        return out[:limit]

    def delete(self, user_id: str, memory_id: str) -> bool:
        with self._lock:
            user_records = self._records.get(user_id, {})
            if memory_id not in user_records:
                return False
            del user_records[memory_id]
            return True

    def delete_all_for_user(self, user_id: str) -> int:
        with self._lock:
            user_records = self._records.pop(user_id, {})
            return len(user_records)

    def sweep_expired(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        purged = 0
        with self._lock:
            for user_id, user_records in list(self._records.items()):
                for memory_id, record in list(user_records.items()):
                    if record.is_expired(now=now):
                        del user_records[memory_id]
                        purged += 1
                if not user_records:
                    self._records.pop(user_id, None)
        return purged


# ---------------------------------------------------------------------------
# Adapter — the only path callers should take to a backend.
# ---------------------------------------------------------------------------


class MemoryAdapter:
    """Governance front-end for any :class:`MemoryBackend`.

    Wires every operation through:

    1. **Filter stack on write** — :class:`SecretScanner` blocks
       credentials; :class:`PIIRedactor` redacts personal data;
       :class:`PromptInjectionHeuristic` blocks instruction-overrides
       so an attacker cannot plant a steering payload.
    2. **Filter stack on read** — same scanners run again on retrieved
       content. A *blocked* verdict raises
       :class:`MemoryInjectionError` rather than returning poisoned
       content; framework §11.6 #4.
    3. **Audit + trace emission** — every operation produces a
       :class:`~core.agent_trace.MemoryOperationStep` appended to the
       supplied :class:`AgentTrace`, then ingested through
       :class:`~core.audit.AuditLogger` if one is wired.

    Callers (MCP server, ADK callbacks, Bedrock action group glue) only
    ever talk to this class, never to a backend directly.
    """

    def __init__(
        self,
        backend: MemoryBackend,
        *,
        trace: AgentTrace | None = None,
        audit_logger: AuditLogger | None = None,
        default_ttl: timedelta | None = None,
        secret_scanner: SecretScanner | None = None,
        pii_redactor: PIIRedactor | None = None,
        injection_heuristic: PromptInjectionHeuristic | None = None,
    ) -> None:
        self._backend = backend
        self._trace = trace
        self._audit = audit_logger
        self._default_ttl = default_ttl
        self._secret_scanner = secret_scanner or SecretScanner()
        self._pii = pii_redactor or PIIRedactor()
        self._injection = injection_heuristic or PromptInjectionHeuristic()

    @property
    def backend(self) -> MemoryBackend:
        return self._backend

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def write(
        self,
        *,
        user_id: str,
        content: str,
        session_id: str = "",
        provenance: str = "agent",
        tags: Iterable[str] | None = None,
        ttl: timedelta | None = None,
    ) -> MemoryRecord:
        """Filter the content then write a :class:`MemoryRecord`.

        Returns the stored record. Raises ``ValueError`` if the secret
        scanner trips (we refuse to persist credentials even at rest).
        """
        if not user_id:
            raise ValueError("user_id is mandatory")

        secret_verdict = self._secret_scanner.apply(content)
        if secret_verdict.verdict == "block":
            self._emit(
                operation="write",
                user_id=user_id,
                memory_key="",
                content=content,
                filter_verdict="block",
                succeeded=False,
                error="secret detected — refusing to persist",
                session_id=session_id,
            )
            raise ValueError("memory write blocked: secret detected in content")

        injection_verdict = self._injection.apply(content)
        if injection_verdict.verdict == "block":
            self._emit(
                operation="write",
                user_id=user_id,
                memory_key="",
                content=content,
                filter_verdict="block",
                succeeded=False,
                error="prompt-injection pattern in content",
                session_id=session_id,
            )
            raise ValueError("memory write blocked: prompt-injection pattern")

        pii_verdict = self._pii.apply(content)
        clean_content = pii_verdict.redacted_text if pii_verdict.verdict == "redact" else content
        filter_verdict = "redact" if pii_verdict.verdict == "redact" else "allow"

        effective_ttl = ttl or self._default_ttl
        expires_at = datetime.now(UTC) + effective_ttl if effective_ttl else None

        record = MemoryRecord(
            user_id=user_id,
            session_id=session_id,
            content=clean_content,
            provenance=provenance,
            tags=list(tags or []),
            expires_at=expires_at,
        )
        stored = self._backend.put(record)
        self._emit(
            operation="write",
            user_id=user_id,
            memory_key=stored.memory_id,
            content=clean_content,
            filter_verdict=filter_verdict,
            succeeded=True,
            session_id=session_id,
        )
        return stored

    def read(
        self,
        *,
        user_id: str,
        tag: str | None = None,
        limit: int = 50,
        session_id: str = "",
        now: datetime | None = None,
    ) -> list[MemoryRecord]:
        """Fetch records for ``user_id`` and rescan against injection.

        A *block*-verdict record raises :class:`MemoryInjectionError`
        so the caller never sees poisoned content. Non-poisoned records
        with ``redact`` verdicts have their ``content`` replaced with
        the redacted form (PII strip on retrieval).
        """
        records = self._backend.query(user_id, tag=tag, limit=limit, now=now)
        clean: list[MemoryRecord] = []
        poisoned: list[str] = []
        for record in records:
            injection = self._injection.apply(record.content)
            if injection.verdict == "block":
                poisoned.append(record.memory_id)
                self._emit(
                    operation="read",
                    user_id=user_id,
                    memory_key=record.memory_id,
                    content=record.content,
                    filter_verdict="block",
                    succeeded=False,
                    error="memory-injection pattern detected",
                    session_id=session_id,
                )
                continue
            pii = self._pii.apply(record.content)
            if pii.verdict == "redact":
                clean.append(record.model_copy(update={"content": pii.redacted_text}))
                filter_verdict: str = "redact"
            else:
                clean.append(record)
                filter_verdict = "allow"
            self._emit(
                operation="read",
                user_id=user_id,
                memory_key=record.memory_id,
                content=record.content,
                filter_verdict=filter_verdict,
                succeeded=True,
                session_id=session_id,
            )
        if poisoned:
            # Surface the security signal but don't leak poisoned content.
            raise MemoryInjectionError(
                f"{len(poisoned)} memory record(s) failed the read-side filter "
                f"for user_id={user_id}; records quarantined."
            )
        return clean

    def delete(
        self,
        *,
        user_id: str,
        memory_id: str,
        session_id: str = "",
    ) -> bool:
        deleted = self._backend.delete(user_id, memory_id)
        self._emit(
            operation="delete",
            user_id=user_id,
            memory_key=memory_id,
            content="",
            filter_verdict="allow",
            succeeded=deleted,
            error="" if deleted else "memory_id not found",
            session_id=session_id,
        )
        return deleted

    def delete_for_user(
        self,
        *,
        user_id: str,
        actor: str = "system",
        session_id: str = "",
    ) -> int:
        """Right-to-be-forgotten — remove every record for ``user_id``.

        Audited as a single ``delete`` step with the count in the
        ``rationale`` field. The framework §11.6 #2 mandates a tested
        procedure; this is that procedure.
        """
        count = self._backend.delete_all_for_user(user_id)
        self._emit(
            operation="delete",
            user_id=user_id,
            memory_key="*",
            content="",
            filter_verdict="allow",
            succeeded=True,
            session_id=session_id,
            rationale=f"right-to-be-forgotten by {actor} ({count} records)",
        )
        return count

    def sweep_expired(self, *, session_id: str = "", now: datetime | None = None) -> int:
        """Purge expired entries. Returns count purged. Audited."""
        count = self._backend.sweep_expired(now=now)
        if count:
            self._emit(
                operation="expire",
                user_id="*",
                memory_key="*",
                content="",
                filter_verdict="allow",
                succeeded=True,
                session_id=session_id,
                rationale=f"retention sweep purged {count} record(s)",
            )
        return count

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _emit(
        self,
        *,
        operation: str,
        user_id: str,
        memory_key: str,
        content: str,
        filter_verdict: str,
        succeeded: bool,
        error: str = "",
        session_id: str = "",
        rationale: str = "",
    ) -> None:
        if self._trace is None and self._audit is None:
            return
        preview = (content or "")[:_PREVIEW_CHARS]
        # Strict typing on the variants
        from typing import cast

        step = MemoryOperationStep(
            session_id=session_id or (self._trace.session_id if self._trace else ""),
            correlation_id=self._trace.correlation_id if self._trace else "",
            rationale=rationale,
            operation=cast(Any, operation),
            user_id=user_id,
            memory_key=memory_key,
            backend=self._backend.name,
            content_preview=preview,
            filter_verdict=cast(Any, filter_verdict),
            succeeded=succeeded,
            error=error,
        )
        if self._trace is not None:
            self._trace.add_step(step)
        if self._audit is not None:
            # Single-step ingest keeps the chained audit invariants.
            wrapped = AgentTrace(
                agent_name="memory_adapter",
                provider=self._trace.provider if self._trace else _default_provider(),
                session_id=step.session_id,
                correlation_id=step.correlation_id,
            )
            wrapped.add_step(step)
            self._audit.ingest_agent_trace(wrapped)


def _default_provider() -> Any:
    """Lazy default to AWS to avoid circular imports."""
    from .models import CloudProvider

    return CloudProvider.AWS
