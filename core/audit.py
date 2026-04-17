"""Immutable audit trail for all FinOps hub actions.

Every decision, alert, policy change, and user action is recorded with
tamper-detection checksums. Aligned with UK NCSC logging guidance for
security-relevant events and ADR-008 §3 requirements:

* Cross-file checksum-chain verification — ``load_from_disk`` verifies
  the chain across all rotated JSONL files. A chain break raises
  :class:`AuditChainBrokenError` instead of being silently reset.
* Daily signed trail-head manifest — ``write_daily_manifest`` produces a
  machine-readable JSON manifest carrying the last entry checksum, the
  JSONL file SHA-256, entry count, and a detached signature (Ed25519 in
  prod, HMAC-SHA256 fallback for dev). Verifiable without the internal
  state of the ``AuditLogger``.
* ``export_signed`` — returns ``(payload, signature)`` over the export,
  establishing chain-of-custody once data leaves the process.
* ``ingest_agent_trace`` — persists each :class:`~core.agent_trace.AgentStep`
  and :class:`~core.agent_trace.DecisionRecord` as an ``AuditEntry`` with
  continuation of the chain (ADR-008 §1).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .models import AuditEntry, CloudProvider

if TYPE_CHECKING:
    from .agent_trace import AgentTrace

__all__ = [
    "AUDIT_SIGNING_KEY_ENV",
    "AuditChainBrokenError",
    "AuditLogger",
]


AUDIT_SIGNING_KEY_ENV = "FINOPS_AUDIT_SIGNING_KEY"
"""Environment variable holding the signing key.

Accepts either:
* An Ed25519 private key in PEM format (preferred, prod).
* A shared HMAC secret (>= 32 chars, dev fallback).

If unset, manifests and signed exports use a zero-key HMAC which still
detects accidental corruption but is **not** a trust boundary.
"""


class AuditChainBrokenError(RuntimeError):
    """Raised when the persisted audit chain fails verification."""


class AuditLogger:
    """Append-only audit logger with integrity verification.

    Chain model: every entry's checksum is ``SHA-256(prev_checksum || entry_data)``.
    The chain is continuous across day-rolled files: the first entry of
    ``audit-2026-04-18.jsonl`` must chain into the last entry of
    ``audit-2026-04-17.jsonl``. ``verify_integrity`` and ``load_from_disk``
    both validate this invariant.
    """

    def __init__(self, audit_dir: str | Path, *, base_dir: Path | None = None) -> None:
        audit_path = Path(audit_dir).resolve()
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

    # ------------------------------------------------------------------
    # Log + query
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Integrity
    # ------------------------------------------------------------------

    def verify_integrity(self) -> list[dict[str, str]]:
        """Verify the integrity chain of all in-memory audit entries.

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

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist_entry(self, entry: AuditEntry) -> None:
        """Persist audit entry to the append-only log file."""
        date_str = entry.timestamp.strftime("%Y-%m-%d")
        log_file = self._audit_dir / f"audit-{date_str}.jsonl"
        try:
            with log_file.open("a") as f:
                f.write(json.dumps(entry.model_dump(), default=str) + "\n")
        except OSError as e:
            import sys

            print(
                f"AUDIT WRITE FAILED ({e}): {entry.action} by {entry.actor} on {entry.target}",
                file=sys.stderr,
            )

    def load_from_disk(self, *, strict: bool = True) -> int:
        """Load existing audit entries from disk with chain verification.

        Iterates files in ascending date order (filename sort is date-
        sorted because of the ``audit-YYYY-MM-DD.jsonl`` format) and
        verifies every checksum against the running chain. If a break is
        detected and ``strict=True`` (default), raises
        :class:`AuditChainBrokenError`. In ``strict=False`` mode the
        break is logged to stderr and the rest of the file is still
        loaded — useful for forensic recovery of partially-corrupted
        trails, but never during normal startup.
        """
        count = 0
        prev_checksum = ""
        log_files = sorted(self._audit_dir.glob("audit-*.jsonl"))
        # Skip manifest files (audit-YYYY-MM-DD.manifest.json is distinct).
        log_files = [p for p in log_files if p.suffix == ".jsonl"]

        for log_file in log_files:
            with log_file.open() as f:
                for line_number, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    entry = AuditEntry(**data)
                    expected = self._compute_checksum(entry, override_previous=prev_checksum)
                    if entry.checksum != expected:
                        message = (
                            f"audit chain broken in {log_file.name}:{line_number} "
                            f"audit_id={entry.audit_id} "
                            f"expected={expected} actual={entry.checksum}"
                        )
                        if strict:
                            raise AuditChainBrokenError(message)
                        import sys

                        print(f"WARNING: {message}", file=sys.stderr)
                    self._entries.append(entry)
                    prev_checksum = entry.checksum
                    count += 1

        self._previous_checksum = prev_checksum
        return count

    # ------------------------------------------------------------------
    # Signed manifests + exports (ADR-008 §3)
    # ------------------------------------------------------------------

    def write_daily_manifest(self, date: datetime) -> Path:
        """Write a signed trail-head manifest for a single day's JSONL.

        The manifest carries the last entry's checksum, the JSONL file's
        SHA-256, the entry count, and a detached signature. Verifiable
        against the raw file without replaying the chain.
        """
        date_str = date.strftime("%Y-%m-%d")
        jsonl = self._audit_dir / f"audit-{date_str}.jsonl"
        if not jsonl.exists():
            raise FileNotFoundError(f"no audit file for {date_str}: {jsonl}")

        file_bytes = jsonl.read_bytes()
        file_sha = hashlib.sha256(file_bytes).hexdigest()
        entry_count = sum(1 for line in file_bytes.splitlines() if line.strip())
        last_checksum = ""
        for line in reversed(file_bytes.splitlines()):
            line = line.strip()
            if not line:
                continue
            last_checksum = json.loads(line).get("checksum", "")
            break

        payload = {
            "schema_version": "1",
            "date": date_str,
            "file": jsonl.name,
            "file_sha256": file_sha,
            "entry_count": entry_count,
            "last_checksum": last_checksum,
        }
        signature = _sign(json.dumps(payload, sort_keys=True).encode())
        manifest = {"payload": payload, "signature": signature}

        manifest_path = self._audit_dir / f"audit-{date_str}.manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2))
        return manifest_path

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

    def export_signed(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """Export + sign: returns ``{payload, signature, algorithm}``.

        The signature covers the canonical-JSON serialisation of the
        payload (sorted keys, no whitespace). Verifiable by any party
        holding the public key (Ed25519) or the shared secret (HMAC).
        """
        payload = {
            "schema_version": "1",
            "exported_at": datetime.now().astimezone().isoformat(),
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "entries": self.export_for_compliance(since=since, until=until),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature, algorithm = _sign_with_algo(encoded)
        return {"payload": payload, "signature": signature, "algorithm": algorithm}

    # ------------------------------------------------------------------
    # AgentTrace ingestion (ADR-008 §1)
    # ------------------------------------------------------------------

    def ingest_agent_trace(self, trace: AgentTrace) -> list[AuditEntry]:
        """Persist every step + decision of an :class:`AgentTrace`.

        Each step becomes one ``AuditEntry`` with
        ``action = "agent.step.<step_type>"``, chained into the integrity
        trail. The session's ``correlation_id`` is propagated onto every
        entry so the transcript can be reconstructed by querying on it.
        """
        written: list[AuditEntry] = []
        correlation_id = trace.correlation_id or trace.session_id

        for step in trace.steps:
            written.append(
                self.log(
                    action=f"agent.step.{step.step_type.value}",
                    actor=step.actor or trace.agent_name,
                    target=trace.session_id,
                    provider=trace.provider,
                    details={
                        "step_id": step.step_id,
                        "parent_step_id": step.parent_step_id,
                        "rationale": step.rationale,
                        "payload": step.model_dump(mode="json", exclude={"raw"}),
                    },
                    correlation_id=correlation_id,
                    causation_id=step.parent_step_id,
                )
            )

        for decision in trace.decisions:
            written.append(
                self.log(
                    action=f"agent.decision.{decision.decision.value}",
                    actor=decision.actor,
                    target=f"{trace.session_id}:{decision.gate_name}",
                    provider=trace.provider,
                    details={
                        "decision_id": decision.decision_id,
                        "gate_name": decision.gate_name,
                        "reason": decision.reason,
                        "policy_id": decision.policy_id,
                        "triggering_step_id": decision.triggering_step_id,
                    },
                    correlation_id=correlation_id,
                    causation_id=decision.triggering_step_id,
                )
            )

        return written


# ---------------------------------------------------------------------------
# Signing helpers
# ---------------------------------------------------------------------------


def _sign(data: bytes) -> str:
    """Sign ``data`` with the configured key. Returns hex-encoded signature.

    Kept as a compatibility shim for the daily-manifest format which
    does not need to convey algorithm choice.
    """
    signature, _ = _sign_with_algo(data)
    return signature


def _sign_with_algo(data: bytes) -> tuple[str, str]:
    """Sign and return ``(signature_hex, algorithm_name)``.

    Algorithm selection:

    * If ``FINOPS_AUDIT_SIGNING_KEY`` parses as an Ed25519 PEM, use that.
    * Else if it is a ``>= 32``-char string, use HMAC-SHA256 over the UTF-8
      encoding of the secret.
    * Else fall back to HMAC-SHA256 with a zero key. This still detects
      accidental corruption but is **not** a trust boundary — log a
      warning once per process so operators notice.
    """
    key_material = os.environ.get(AUDIT_SIGNING_KEY_ENV, "")

    ed_key = _try_load_ed25519(key_material)
    if ed_key is not None:
        signature = ed_key.sign(data)
        return signature.hex(), "ed25519"

    if len(key_material) >= 32:
        sig = hmac.new(key_material.encode(), data, hashlib.sha256).hexdigest()
        return sig, "hmac-sha256"

    _warn_unsigned_once()
    sig = hmac.new(b"\x00" * 32, data, hashlib.sha256).hexdigest()
    return sig, "hmac-sha256-unkeyed"


_UNSIGNED_WARNING_EMITTED = False


def _warn_unsigned_once() -> None:
    global _UNSIGNED_WARNING_EMITTED
    if _UNSIGNED_WARNING_EMITTED:
        return
    import sys

    print(
        f"WARNING: {AUDIT_SIGNING_KEY_ENV} not set — audit manifests "
        "and exports are using an unkeyed HMAC. Suitable for "
        "development only. Set an Ed25519 PEM or a >=32-char shared "
        "secret for production.",
        file=sys.stderr,
    )
    _UNSIGNED_WARNING_EMITTED = True


def _try_load_ed25519(material: str) -> Any:
    """Return an Ed25519PrivateKey if ``material`` is a PEM, else None."""
    if not material or "BEGIN" not in material:
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
    except ImportError:
        return None
    try:
        key = serialization.load_pem_private_key(material.encode(), password=None)
    except Exception:
        return None
    if isinstance(key, Ed25519PrivateKey):
        return key
    return None
