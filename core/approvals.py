"""Out-of-band approval gateway — ADR-008 §4.

Implements the approval primitive required by AGENT_GOVERNANCE_FRAMEWORK §5.
The in-process ``ApprovalHandler`` callable that ``core.tool_governor``
accepts is fine for unit tests but cannot meet the framework's
out-of-band requirement (same process must not be able to approve its
own request). This module supplies the production primitive.

Shape:

* :class:`ApprovalRequest` — durable record of *what* is awaiting a
  decision, by *whom*, until when, and from *which* approver pool.
* :class:`ApprovalDecision` — durable record of the verdict, who gave
  it, and a signed decision token bound to the request.
* :class:`ApprovalGateway` — abstract base. Concrete gateways below.
* :class:`ApprovalStore` — thread-safe in-memory store with TTL, quorum
  tallying, and revocation. Replaceable with a durable backend (Redis,
  Postgres) without changing call sites.
* :class:`LocalCLIApprovalGateway` — dev only.
* :class:`WebhookApprovalGateway` — POST to a signed URL the operator
  clicks; response carries the signed token.
* :class:`SlackApprovalGateway` — reuses ``core.notifications.SlackDispatcher``.

Decision tokens are HMAC-signed using the same key material as audit
exports (:data:`core.audit.AUDIT_SIGNING_KEY_ENV`) so an audited
approval is independently verifiable after the fact.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import threading
import uuid
from abc import ABC, abstractmethod
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from .audit import AUDIT_SIGNING_KEY_ENV

SCHEMA_VERSION = "1"

__all__ = [
    "DEFAULT_BATCH_TTL_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "SCHEMA_VERSION",
    "ApprovalDecision",
    "ApprovalExpired",
    "ApprovalGateway",
    "ApprovalNotFound",
    "ApprovalRequest",
    "ApprovalStore",
    "ApprovalVerdict",
    "InvalidApprovalToken",
    "LocalCLIApprovalGateway",
    "SlackApprovalGateway",
    "WebhookApprovalGateway",
    "issue_decision_token",
    "verify_decision_token",
]


# Framework §5.4 — defaults
DEFAULT_TTL_SECONDS = 15 * 60  # 15 min per-action
DEFAULT_BATCH_TTL_SECONDS = 24 * 3600  # 24 hours batch


class ApprovalVerdict(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ApprovalNotFoundError(KeyError):
    """Raised when a request_id has no matching record."""


# Back-compat aliases — old import sites used the un-suffixed form.
ApprovalNotFound = ApprovalNotFoundError


class ApprovalExpiredError(RuntimeError):
    """Raised when an approval is consumed after its expiry."""


ApprovalExpired = ApprovalExpiredError


class InvalidApprovalTokenError(RuntimeError):
    """Raised when a decision token fails signature verification."""


InvalidApprovalToken = InvalidApprovalTokenError


class ApprovalRequest(BaseModel):
    """Durable record of a pending approval.

    The same fields appear in :class:`~core.agent_trace.ApprovalRequestStep`
    so a trace replay sees the approval lifecycle without joining tables.
    """

    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    correlation_id: str = ""
    tool_name: str = ""
    action_summary: str = Field(
        description="Plain-English description of what the agent wants "
        "to do — surfaced verbatim to the approver."
    )
    arguments_preview: dict[str, Any] = Field(default_factory=dict)
    requested_by: str = "agent"
    approver_pool: list[str] = Field(
        default_factory=list,
        description="Allow-listed approver identifiers. Empty means "
        "any authenticated approver on the configured channel.",
    )
    quorum: int = Field(
        default=1,
        description="Minimum number of distinct approvers required. "
        "Framework §5.3: destructive actions default to 2-of-N.",
    )
    blast_radius: str = ""
    rollback_procedure: str = ""
    rationale: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC) + timedelta(seconds=DEFAULT_TTL_SECONDS)
    )
    verdict: ApprovalVerdict = ApprovalVerdict.PENDING
    decisions: list[ApprovalDecision] = Field(default_factory=list)

    def is_expired(self, *, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return now > self.expires_at

    def is_satisfied(self) -> bool:
        """True when the request has at least :attr:`quorum` approvals."""
        approvals = [d for d in self.decisions if d.verdict is ApprovalVerdict.APPROVED]
        return len(approvals) >= self.quorum


class ApprovalDecision(BaseModel):
    """A single verdict against an :class:`ApprovalRequest`."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    decision_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    request_id: str
    approver: str
    verdict: ApprovalVerdict
    notes: str = ""
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    token: str = Field(
        default="",
        description="HMAC-signed token bound to (request_id, approver, "
        "verdict). Verifies the decision came through the gateway, not "
        "a forged payload.",
    )


# ApprovalDecision is referenced in ApprovalRequest before its definition
# under PEP 563 future annotations, but Pydantic v2 resolves forward refs
# automatically. No model_rebuild required.


# ---------------------------------------------------------------------------
# Decision token signing — bound to AUDIT_SIGNING_KEY for chain-of-custody.
# ---------------------------------------------------------------------------


def _signing_key() -> bytes:
    """Return key material for HMAC. Falls back to zero key in dev."""
    material = os.environ.get(AUDIT_SIGNING_KEY_ENV, "")
    if len(material) >= 32:
        return material.encode()
    # Zero-key fallback — same posture as audit signed manifests when
    # the key isn't set. Suitable for dev only.
    return b"\x00" * 32


def issue_decision_token(*, request_id: str, approver: str, verdict: ApprovalVerdict) -> str:
    """Mint a token binding (request_id, approver, verdict)."""
    payload = f"{request_id}|{approver}|{verdict.value}".encode()
    return hmac.new(_signing_key(), payload, hashlib.sha256).hexdigest()


def verify_decision_token(
    *, request_id: str, approver: str, verdict: ApprovalVerdict, token: str
) -> bool:
    """Constant-time verify a decision token. Returns True iff valid."""
    expected = issue_decision_token(request_id=request_id, approver=approver, verdict=verdict)
    return hmac.compare_digest(expected, token)


# ---------------------------------------------------------------------------
# Approval store — thread-safe in-memory, swappable later.
# ---------------------------------------------------------------------------


class ApprovalStore:
    """Thread-safe registry of pending and resolved approvals.

    The store is intentionally simple: in-memory dict + threading.Lock.
    Replaceable with a durable backend (Redis, Postgres) once usage
    patterns are clear. The interface only needs CRUD + a sweep for
    expired entries; nothing here assumes a particular backend.
    """

    def __init__(self) -> None:
        self._records: dict[str, ApprovalRequest] = {}
        self._lock = threading.Lock()

    def put(self, request: ApprovalRequest) -> ApprovalRequest:
        with self._lock:
            self._records[request.request_id] = request
        return request

    def get(self, request_id: str) -> ApprovalRequest:
        with self._lock:
            record = self._records.get(request_id)
        if record is None:
            raise ApprovalNotFound(request_id)
        return record

    def list_pending(
        self,
        *,
        session_id: str | None = None,
        approver: str | None = None,
        now: datetime | None = None,
    ) -> list[ApprovalRequest]:
        """Return unresolved, unexpired requests, optionally filtered."""
        now = now or datetime.now(UTC)
        with self._lock:
            records = list(self._records.values())
        out: list[ApprovalRequest] = []
        for r in records:
            if r.verdict is not ApprovalVerdict.PENDING:
                continue
            if r.is_expired(now=now):
                continue
            if session_id and r.session_id != session_id:
                continue
            if approver and r.approver_pool and approver not in r.approver_pool:
                continue
            out.append(r)
        return out

    def list_expired(self, *, now: datetime | None = None) -> list[ApprovalRequest]:
        """Return requests that are past expiry but still PENDING.

        Used by the reconciliation agent (§13.3 fourth category).
        """
        now = now or datetime.now(UTC)
        with self._lock:
            records = list(self._records.values())
        return [
            r for r in records if r.verdict is ApprovalVerdict.PENDING and r.is_expired(now=now)
        ]

    def revoke(self, request_id: str, *, actor: str, reason: str = "") -> ApprovalRequest:
        """Cancel a pending request before consumption. Audited by the caller."""
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                raise ApprovalNotFound(request_id)
            if record.verdict is not ApprovalVerdict.PENDING:
                return record
            record.verdict = ApprovalVerdict.REVOKED
            record.decisions.append(
                ApprovalDecision(
                    request_id=request_id,
                    approver=actor,
                    verdict=ApprovalVerdict.REVOKED,
                    notes=reason,
                )
            )
            return record

    def record_decision(
        self,
        *,
        request_id: str,
        approver: str,
        verdict: ApprovalVerdict,
        notes: str = "",
    ) -> ApprovalRequest:
        """Append a decision and resolve the request when quorum is reached."""
        if verdict not in (ApprovalVerdict.APPROVED, ApprovalVerdict.DENIED):
            raise ValueError(f"invalid decision verdict: {verdict}")
        with self._lock:
            record = self._records.get(request_id)
            if record is None:
                raise ApprovalNotFound(request_id)
            if record.is_expired():
                record.verdict = ApprovalVerdict.EXPIRED
                raise ApprovalExpired(request_id)
            if record.verdict is not ApprovalVerdict.PENDING:
                # Idempotent: return the resolved record without duplicating
                return record
            # Reject self-approval — out-of-band guarantee.
            if approver == record.requested_by:
                raise PermissionError(
                    "out-of-band approval: requester cannot approve their own request"
                )
            # Reject duplicate approver
            if any(d.approver == approver for d in record.decisions):
                raise PermissionError(f"approver '{approver}' already decided this request")
            token = issue_decision_token(request_id=request_id, approver=approver, verdict=verdict)
            record.decisions.append(
                ApprovalDecision(
                    request_id=request_id,
                    approver=approver,
                    verdict=verdict,
                    notes=notes,
                    token=token,
                )
            )
            # DENIED short-circuits even with quorum > 1: §5 says irreversibles
            # require 2-of-N approvals, but a single deny is final.
            if verdict is ApprovalVerdict.DENIED:
                record.verdict = ApprovalVerdict.DENIED
            elif record.is_satisfied():
                record.verdict = ApprovalVerdict.APPROVED
            return record

    def sweep_expired(self, *, now: datetime | None = None) -> int:
        """Mark expired PENDING requests as EXPIRED. Returns count swept."""
        now = now or datetime.now(UTC)
        count = 0
        with self._lock:
            for r in self._records.values():
                if r.verdict is ApprovalVerdict.PENDING and r.is_expired(now=now):
                    r.verdict = ApprovalVerdict.EXPIRED
                    count += 1
        return count

    def all_records(self) -> list[ApprovalRequest]:
        with self._lock:
            return list(self._records.values())


# ---------------------------------------------------------------------------
# Gateway abstractions
# ---------------------------------------------------------------------------


class ApprovalGateway(ABC):
    """Surface that delivers an :class:`ApprovalRequest` to its pool."""

    name: str = "abstract"

    @abstractmethod
    def request(self, approval: ApprovalRequest) -> None:
        """Deliver the request to the configured channel. Non-blocking."""

    def revoke(self, approval: ApprovalRequest, *, reason: str) -> None:
        """Optional: notify the channel that a request was withdrawn.

        Default no-op — gateways that route through chat channels override
        this to retract the original message.
        """
        return None


class LocalCLIApprovalGateway(ApprovalGateway):
    """Dev-only: writes a prompt to stderr; tester resolves via ``ApprovalStore``.

    Does NOT block waiting for input — production callers must poll
    :meth:`ApprovalStore.get` or the ``finops_pending_approvals`` MCP tool.
    Suitable for single-operator development and integration tests.
    """

    name = "local_cli"

    def request(self, approval: ApprovalRequest) -> None:
        import sys

        # framework §14.3: surface what / why / blast radius / rollback / token
        msg = (
            f"\n=== APPROVAL REQUESTED ({approval.request_id}) ===\n"
            f"  what:     {approval.action_summary}\n"
            f"  why:      {approval.rationale or '(no rationale)'}\n"
            f"  blast:    {approval.blast_radius or '(unstated)'}\n"
            f"  rollback: {approval.rollback_procedure or '(unstated)'}\n"
            f"  pool:     {approval.approver_pool or '(any approver)'}\n"
            f"  quorum:   {approval.quorum}\n"
            f"  expires:  {approval.expires_at.isoformat()}\n"
        )
        print(msg, file=sys.stderr)


class WebhookApprovalGateway(ApprovalGateway):
    """POSTs the request to a webhook (typically an internal approvals UI).

    The webhook must respond out-of-band by calling the
    ``finops_respond_approval`` MCP tool with the signed decision token.
    """

    name = "webhook"

    def __init__(self, url: str, *, timeout: float = 5.0) -> None:
        self._url = url
        self._timeout = timeout

    def request(self, approval: ApprovalRequest) -> None:
        try:
            import urllib.error
            import urllib.request

            payload = approval.model_dump(mode="json")
            data = (
                __import__("json").dumps({"event": "approval.requested", "approval": payload})
            ).encode()
            req = urllib.request.Request(
                self._url, data=data, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=self._timeout)
        except Exception as exc:  # pragma: no cover — best-effort dispatch
            import logging

            logging.getLogger(__name__).warning(
                "WebhookApprovalGateway: dispatch to %s failed: %s", self._url, exc
            )


class SlackApprovalGateway(ApprovalGateway):
    """Re-uses :class:`~core.notifications.SlackDispatcher` for delivery.

    The Slack handler must POST the operator's button-click back to the
    ``finops_respond_approval`` MCP tool with a verified token.
    """

    name = "slack"

    def __init__(self, slack_dispatcher: Any) -> None:
        # Type-hinted as Any to avoid importing notifications at module load
        self._dispatcher = slack_dispatcher

    def request(self, approval: ApprovalRequest) -> None:
        # Build a contextual alert-like payload; SlackDispatcher's interface
        # is satisfied by anything with the right .dispatch entry point.
        text = (
            f"*Approval required* `{approval.request_id}`\n"
            f"> {approval.action_summary}\n"
            f"*why:* {approval.rationale or '—'} · "
            f"*blast:* {approval.blast_radius or '—'} · "
            f"*expires:* `{approval.expires_at.isoformat()}`"
        )
        try:
            self._dispatcher.send_text(text)  # type: ignore[attr-defined]
        except AttributeError:
            # Older dispatchers expose only .dispatch(alert). Fall through to
            # a no-op rather than crash — the request is still in the store.
            import logging

            logging.getLogger(__name__).debug(
                "Slack dispatcher lacks send_text; approval %s delivered to store only",
                approval.request_id,
            )


# ---------------------------------------------------------------------------
# Convenience: produce an ApprovalRequestStep from a request, for traces.
# ---------------------------------------------------------------------------


def to_trace_step_kwargs(approval: ApprovalRequest) -> dict[str, Any]:
    """Kwargs ready to pass to :class:`~core.agent_trace.ApprovalRequestStep`."""
    return {
        "session_id": approval.session_id,
        "correlation_id": approval.correlation_id or approval.session_id,
        "request_id": approval.request_id,
        "approver_pool": list(approval.approver_pool),
        "expires_at": approval.expires_at,
        "approved": (
            True
            if approval.verdict is ApprovalVerdict.APPROVED
            else False
            if approval.verdict is ApprovalVerdict.DENIED
            else None
        ),
        "rationale": approval.rationale,
    }


def quorum_for(*, irreversible: bool) -> int:
    """Default quorum: 2-of-N for irreversibles, 1 otherwise (framework §5.3)."""
    return 2 if irreversible else 1


def default_ttl(*, batch: bool = False) -> int:
    """Framework §5.4 defaults: 15 min per-action, 24 hours batch."""
    return DEFAULT_BATCH_TTL_SECONDS if batch else DEFAULT_TTL_SECONDS


def all_pool_members(pools: Iterable[Iterable[str]]) -> list[str]:
    """Merge approver-pool lists, preserving order, removing duplicates."""
    seen: set[str] = set()
    out: list[str] = []
    for pool in pools:
        for m in pool:
            if m not in seen:
                seen.add(m)
                out.append(m)
    return out
