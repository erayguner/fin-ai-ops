"""Integration tests for self-healing infrastructure.

Tests the dead-letter queue, WAL mode, audit disk-full resilience,
and corrupt policy file handling.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.audit import AuditLogger
from core.event_store import SQLiteEventStore
from core.models import (
    CostAlert,
    CostPolicy,
)
from core.notifications import CompositeDispatcher, LogDispatcher
from core.policies import PolicyEngine

from tests.helpers import make_alert


def _make_alert() -> CostAlert:
    return make_alert(
        alert_id="alert-test",
        region="us-east-1",
        account_id="123",
        resource_id="i-123",
        resource_creator="user",
        creator_email="user@example.com",
        estimated_monthly_cost_usd=50.0,
        threshold_exceeded_usd=40.0,
        baseline_monthly_usd=30.0,
        cost_increase_percentage=66.7,
    )


class TestDeadLetterQueue:
    def test_failed_dispatch_goes_to_dead_letter(self):
        failing = MagicMock()
        failing.channel_name = "broken"
        failing.send.return_value = False
        composite = CompositeDispatcher([failing])

        alert = _make_alert()
        composite.send(alert, "test text")
        assert composite.dead_letter_count == 1

    def test_exception_goes_to_dead_letter(self):
        failing = MagicMock()
        failing.channel_name = "broken"
        failing.send.side_effect = RuntimeError("network error")
        composite = CompositeDispatcher([failing])

        alert = _make_alert()
        composite.send(alert, "test text")
        assert composite.dead_letter_count == 1

    def test_retry_dead_letters_succeeds(self):
        failing = MagicMock()
        failing.channel_name = "flaky"
        failing.send.side_effect = [False, True]  # Fail first, succeed on retry
        composite = CompositeDispatcher([failing])

        alert = _make_alert()
        composite.send(alert, "test text")
        assert composite.dead_letter_count == 1

        result = composite.retry_dead_letters()
        assert result["retried"] == 1
        assert result["succeeded"] == 1
        assert result["failed"] == 0
        assert composite.dead_letter_count == 0

    def test_retry_dead_letters_still_fails(self):
        failing = MagicMock()
        failing.channel_name = "always-broken"
        failing.send.return_value = False
        composite = CompositeDispatcher([failing])

        alert = _make_alert()
        composite.send(alert, "test text")
        result = composite.retry_dead_letters()
        assert result["failed"] == 1
        assert composite.dead_letter_count == 1

    def test_empty_dead_letter_retry(self):
        composite = CompositeDispatcher([LogDispatcher()])
        result = composite.retry_dead_letters()
        assert result == {"retried": 0, "succeeded": 0, "failed": 0, "expired": 0}

    def test_mixed_success_and_failure(self):
        good = LogDispatcher()
        bad = MagicMock()
        bad.channel_name = "broken"
        bad.send.return_value = False
        composite = CompositeDispatcher([good, bad])

        alert = _make_alert()
        success = composite.send(alert, "test")
        assert success is True  # At least one succeeded
        assert composite.dead_letter_count == 1  # One in dead letter


class TestSQLiteWAL:
    def test_wal_mode_enabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEventStore(db_path)
            with store._connect() as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode == "wal"

    def test_health_check(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            store = SQLiteEventStore(db_path)
            health = store.health_check()
            assert health["status"] == "healthy"
            assert health["events"] == 0
            assert health["integrity"] == "ok"
            assert health["backend"] == "sqlite"

    def test_in_memory_store(self):
        store = SQLiteEventStore(":memory:")
        health = store.health_check()
        assert health["status"] == "healthy"


class TestAuditDiskResilience:
    def test_audit_survives_disk_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(tmpdir)
            # Patch open to raise OSError on write
            original_open = Path.open

            def failing_open(self, *args, **kwargs):
                if "a" in args or kwargs.get("mode") == "a":
                    raise OSError("No space left on device")
                return original_open(self, *args, **kwargs)

            with patch.object(Path, "open", failing_open):
                # Should not crash
                entry = logger.log(action="test", actor="system", target="x")
                assert entry.checksum != ""

    def test_path_traversal_rejected(self):
        try:
            AuditLogger("../../../etc/evil")
            raise AssertionError("Should have raised ValueError")
        except ValueError as e:
            assert "path traversal" in str(e).lower()

    def test_base_dir_containment(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            try:
                AuditLogger("/tmp/outside", base_dir=Path(tmpdir))  # noqa: S108
                raise AssertionError("Should have raised ValueError")
            except ValueError as e:
                assert "must be within" in str(e)


class TestCorruptPolicyResilience:
    def test_load_skips_corrupt_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(Path(tmpdir) / "audit")
            policy_dir = Path(tmpdir) / "policies"
            policy_dir.mkdir()

            # Create a valid policy
            valid = CostPolicy(
                policy_id="valid-1",
                name="Valid",
                description="A valid policy",
                max_monthly_cost_usd=100.0,
            )
            (policy_dir / "valid-1.json").write_text(json.dumps(valid.model_dump(), default=str))

            # Create a corrupt policy
            (policy_dir / "corrupt.json").write_text("{bad json!!!")

            engine = PolicyEngine(policy_dir, audit)
            count = engine.load_policies()
            assert count == 1  # Only valid loaded

    def test_load_reports_errors_in_audit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(Path(tmpdir) / "audit")
            policy_dir = Path(tmpdir) / "policies"
            policy_dir.mkdir()

            (policy_dir / "bad.json").write_text("not json")

            engine = PolicyEngine(policy_dir, audit)
            engine.load_policies()

            entries = audit.get_entries(action="policy.load_error")
            assert len(entries) == 1

    def test_policy_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(Path(tmpdir) / "audit")
            try:
                PolicyEngine("../../../etc/evil", audit)
                raise AssertionError("Should have raised ValueError")
            except ValueError as e:
                assert "path traversal" in str(e).lower()
