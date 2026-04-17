"""Tests for ADR-008 §3 audit hardening: cross-file chain verification,
signed manifests, export signing, and AgentTrace ingestion."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from core.agent_trace import (
    AgentTrace,
    DecisionRecord,
    DecisionVerdict,
    ModelInvocationStep,
    ToolInvocationStep,
)
from core.audit import (
    AUDIT_SIGNING_KEY_ENV,
    AuditChainBrokenError,
    AuditLogger,
)
from core.models import CloudProvider


class TestCrossFileChainVerification:
    def test_loads_clean_multi_day_chain(self, tmp_path: Path):
        """Two separate daily files must chain cleanly."""
        log = AuditLogger(tmp_path)
        # Day 1: write two entries
        e1 = log.log(action="a1", actor="sys", target="t")
        e2 = log.log(action="a2", actor="sys", target="t")
        # Move day-1's file to simulate day rollover naming
        day1 = tmp_path / f"audit-{e1.timestamp.strftime('%Y-%m-%d')}.jsonl"
        assert day1.exists()

        # Simulate a day-2 rollover: write entries with tomorrow's timestamp.
        tomorrow = datetime.now(UTC) + timedelta(days=1)
        # Append directly to the next day's file, continuing the chain.
        next_day_file = tmp_path / f"audit-{tomorrow.strftime('%Y-%m-%d')}.jsonl"
        from core.models import AuditEntry

        prev = e2.checksum
        e3 = AuditEntry(action="a3", actor="sys", target="t", timestamp=tomorrow)
        # Manually compute chained checksum using same algorithm.
        import hashlib

        payload = (
            f"{prev}|{e3.audit_id}|{e3.timestamp.isoformat()}|"
            f"{e3.action}|{e3.actor}|{e3.target}|{e3.outcome}"
        )
        e3.checksum = hashlib.sha256(payload.encode()).hexdigest()
        with next_day_file.open("a") as f:
            f.write(json.dumps(e3.model_dump(), default=str) + "\n")

        # Load from disk on a fresh logger and verify count.
        fresh = AuditLogger(tmp_path)
        count = fresh.load_from_disk()
        assert count == 3

    def test_chain_break_raises_by_default(self, tmp_path: Path):
        log = AuditLogger(tmp_path)
        log.log(action="a1", actor="sys", target="t")
        e2 = log.log(action="a2", actor="sys", target="t")

        # Tamper: rewrite e2's checksum on disk.
        date_str = e2.timestamp.strftime("%Y-%m-%d")
        f = tmp_path / f"audit-{date_str}.jsonl"
        lines = f.read_text().splitlines()
        data = json.loads(lines[-1])
        data["checksum"] = "0" * 64
        lines[-1] = json.dumps(data)
        f.write_text("\n".join(lines) + "\n")

        fresh = AuditLogger(tmp_path)
        with pytest.raises(AuditChainBrokenError):
            fresh.load_from_disk()

    def test_chain_break_warns_in_non_strict_mode(self, tmp_path: Path, capsys):
        log = AuditLogger(tmp_path)
        e1 = log.log(action="a1", actor="sys", target="t")
        date_str = e1.timestamp.strftime("%Y-%m-%d")
        f = tmp_path / f"audit-{date_str}.jsonl"
        data = json.loads(f.read_text().strip())
        data["checksum"] = "f" * 64
        f.write_text(json.dumps(data) + "\n")

        fresh = AuditLogger(tmp_path)
        count = fresh.load_from_disk(strict=False)
        assert count == 1
        captured = capsys.readouterr()
        assert "chain broken" in captured.err


class TestSignedManifest:
    def test_daily_manifest_contains_expected_fields(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(AUDIT_SIGNING_KEY_ENV, "x" * 32)
        log = AuditLogger(tmp_path)
        entry = log.log(action="a", actor="sys", target="t")
        manifest_path = log.write_daily_manifest(entry.timestamp)
        doc = json.loads(manifest_path.read_text())
        assert doc["payload"]["entry_count"] == 1
        assert doc["payload"]["last_checksum"] == entry.checksum
        assert doc["payload"]["file_sha256"]
        assert doc["signature"]

    def test_missing_day_raises(self, tmp_path: Path):
        log = AuditLogger(tmp_path)
        with pytest.raises(FileNotFoundError):
            log.write_daily_manifest(datetime.now(UTC))

    def test_unkeyed_warning_emits_once(self, tmp_path: Path, monkeypatch, capsys):
        monkeypatch.delenv(AUDIT_SIGNING_KEY_ENV, raising=False)
        # Reset the module-level flag so the warning can fire in this test.
        import core.audit as audit_mod

        audit_mod._UNSIGNED_WARNING_EMITTED = False

        log = AuditLogger(tmp_path)
        entry = log.log(action="a", actor="sys", target="t")
        log.write_daily_manifest(entry.timestamp)
        captured = capsys.readouterr()
        assert AUDIT_SIGNING_KEY_ENV in captured.err
        assert "development only" in captured.err


class TestSignedExport:
    def test_export_signed_returns_payload_and_signature(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(AUDIT_SIGNING_KEY_ENV, "x" * 32)
        log = AuditLogger(tmp_path)
        log.log(action="a", actor="sys", target="t")
        doc = log.export_signed()
        assert "payload" in doc
        assert "signature" in doc
        assert doc["algorithm"] == "hmac-sha256"
        assert doc["payload"]["entries"]

    def test_hmac_signature_is_deterministic(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv(AUDIT_SIGNING_KEY_ENV, "y" * 32)
        log = AuditLogger(tmp_path)
        log.log(action="a", actor="sys", target="t")
        doc1 = log.export_signed()
        # Re-signing the same payload must yield the same signature.
        import hashlib
        import hmac

        encoded = json.dumps(
            doc1["payload"], sort_keys=True, separators=(",", ":")
        ).encode()
        expected = hmac.new(b"y" * 32, encoded, hashlib.sha256).hexdigest()
        assert doc1["signature"] == expected


class TestAgentTraceIngestion:
    def test_every_step_becomes_audit_entry(self, tmp_path: Path):
        log = AuditLogger(tmp_path)
        trace = AgentTrace(
            agent_name="aws-finops",
            provider=CloudProvider.AWS,
            correlation_id="c-123",
        )
        trace.add_step(ModelInvocationStep(session_id="", model_id="claude-4"))
        trace.add_step(ToolInvocationStep(session_id="", tool_name="finops_list_alerts"))
        trace.add_decision(
            DecisionRecord(
                session_id="",
                decision=DecisionVerdict.ALLOW,
                gate_name="governor",
                reason="allowed",
            )
        )

        written = log.ingest_agent_trace(trace)
        assert len(written) == 3
        actions = [e.action for e in written]
        assert actions == [
            "agent.step.model_invocation",
            "agent.step.tool_invocation",
            "agent.decision.allow",
        ]
        # All entries share the correlation_id.
        assert {e.correlation_id for e in written} == {"c-123"}

    def test_ingested_entries_preserve_chain(self, tmp_path: Path):
        log = AuditLogger(tmp_path)
        log.log(action="bootstrap", actor="sys", target="t")
        trace = AgentTrace(agent_name="a", provider=CloudProvider.GCP)
        trace.add_step(ToolInvocationStep(session_id="", tool_name="t1"))
        log.ingest_agent_trace(trace)
        violations = log.verify_integrity()
        assert violations == []
