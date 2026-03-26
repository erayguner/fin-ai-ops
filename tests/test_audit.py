"""Tests for the audit logging system."""

import tempfile

from core.audit import AuditLogger
from core.models import CloudProvider


class TestAuditLogger:
    def test_log_and_retrieve(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(tmpdir)
            entry = logger.log(
                action="test.action",
                actor="test-user",
                target="test-target",
                provider=CloudProvider.AWS,
                details={"key": "value"},
            )
            assert entry.checksum != ""
            entries = logger.get_entries()
            assert len(entries) == 1
            assert entries[0].action == "test.action"

    def test_integrity_verification_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(tmpdir)
            logger.log(action="first", actor="system", target="a")
            logger.log(action="second", actor="system", target="b")
            logger.log(action="third", actor="system", target="c")
            violations = logger.verify_integrity()
            assert len(violations) == 0

    def test_integrity_detects_tampering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(tmpdir)
            logger.log(action="first", actor="system", target="a")
            logger.log(action="second", actor="system", target="b")

            # Tamper with the first entry's checksum
            logger._entries[0].checksum = "tampered"

            violations = logger.verify_integrity()
            assert len(violations) >= 1

    def test_filter_by_action(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(tmpdir)
            logger.log(action="policy.created", actor="user", target="p1")
            logger.log(action="alert.generated", actor="system", target="a1")
            logger.log(action="policy.created", actor="user", target="p2")

            entries = logger.get_entries(action="policy.created")
            assert len(entries) == 2

    def test_filter_by_provider(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(tmpdir)
            logger.log(action="test", actor="s", target="t", provider=CloudProvider.AWS)
            logger.log(action="test", actor="s", target="t", provider=CloudProvider.GCP)

            aws_entries = logger.get_entries(provider=CloudProvider.AWS)
            assert len(aws_entries) == 1

    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger1 = AuditLogger(tmpdir)
            logger1.log(action="persisted", actor="user", target="target")

            logger2 = AuditLogger(tmpdir)
            count = logger2.load_from_disk()
            assert count == 1
            entries = logger2.get_entries()
            assert entries[0].action == "persisted"

    def test_compliance_export(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = AuditLogger(tmpdir)
            logger.log(action="test", actor="user", target="target")

            exported = logger.export_for_compliance()
            assert len(exported) == 1
            assert "integrity_checksum" in exported[0]
            assert "timestamp" in exported[0]
