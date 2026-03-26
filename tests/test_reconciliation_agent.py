"""Tests for the Reconciliation Agent."""

import tempfile

from agents.reconciliation_agent import ReconciliationAgent, ReconciliationReport
from core.audit import AuditLogger
from core.event_store import InMemoryEventStore
from core.models import (
    ActionStatus,
    CostAlert,
    ResourceCreationEvent,
    Severity,
)

from tests.helpers import make_alert, make_event


def _make_event(event_id: str = "evt-1") -> ResourceCreationEvent:
    return make_event(event_id=event_id, region="us-east-1", estimated_monthly_cost_usd=100.0)


def _make_alert(
    alert_id: str = "alert-1",
    status: ActionStatus = ActionStatus.PENDING,
    severity: Severity = Severity.WARNING,
    created_hours_ago: int = 0,
    resolved_by: str = "",
    acknowledged_by: str = "",
) -> CostAlert:
    return make_alert(
        alert_id=alert_id,
        status=status,
        severity=severity,
        created_hours_ago=created_hours_ago,
        resolved_by=resolved_by,
        acknowledged_by=acknowledged_by,
        region="us-east-1",
        estimated_monthly_cost_usd=100.0,
        threshold_exceeded_usd=80.0,
        baseline_monthly_usd=50.0,
        resource_creator="test-user",
        creator_email="test@example.com",
    )


class TestReconciliationReport:
    def test_clean_report(self):
        report = ReconciliationReport()
        assert report.is_clean is True
        d = report.to_dict()
        assert d["status"] == "clean"
        assert d["total_issues"] == 0

    def test_report_with_issues(self):
        report = ReconciliationReport()
        report.add_issue("test", "something broke", severity="warning")
        assert report.is_clean is False
        d = report.to_dict()
        assert d["status"] == "issues_found"
        assert d["total_issues"] == 1
        assert d["requires_attention"] == 1

    def test_auto_fixable_not_in_attention(self):
        report = ReconciliationReport()
        report.add_issue("test", "auto fixable", auto_fixable=True)
        assert len(report.requires_attention) == 0
        assert len(report.issues) == 1

    def test_add_fix(self):
        report = ReconciliationReport()
        report.add_fix("test", "did something")
        d = report.to_dict()
        assert d["auto_fixed"] == 1


class TestReconciliationAgent:
    def test_clean_run_no_components(self):
        agent = ReconciliationAgent()
        report = agent.reconcile()
        # Only audit_integrity warning (not configured)
        assert any(i["category"] == "audit_integrity" for i in report.issues)

    def test_audit_integrity_clean(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(tmpdir)
            audit.log(action="test", actor="system", target="x")
            audit.log(action="test2", actor="system", target="y")
            agent = ReconciliationAgent(audit_logger=audit)
            report = agent.reconcile()
            integrity_issues = [i for i in report.issues if i["category"] == "audit_integrity"]
            assert len(integrity_issues) == 0

    def test_audit_integrity_tampered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(tmpdir)
            entry = audit.log(action="test", actor="system", target="x")
            # Tamper with the entry
            entry.checksum = "tampered"
            agent = ReconciliationAgent(audit_logger=audit)
            report = agent.reconcile()
            integrity_issues = [i for i in report.issues if i["category"] == "audit_integrity"]
            assert len(integrity_issues) == 1
            assert integrity_issues[0]["severity"] == "critical"

    def test_stale_alerts_detected(self):
        stale_alert = _make_alert(
            status=ActionStatus.PENDING,
            created_hours_ago=200,
        )
        agent = ReconciliationAgent(alerts=[stale_alert], stale_alert_hours=168)
        report = agent.reconcile()
        stale_issues = [i for i in report.issues if i["category"] == "stale_alerts"]
        assert len(stale_issues) == 1

    def test_no_stale_alerts_when_recent(self):
        recent_alert = _make_alert(
            status=ActionStatus.PENDING,
            created_hours_ago=1,
        )
        agent = ReconciliationAgent(alerts=[recent_alert], stale_alert_hours=168)
        report = agent.reconcile()
        stale_issues = [i for i in report.issues if i["category"] == "stale_alerts"]
        assert len(stale_issues) == 0

    def test_resolved_alerts_not_stale(self):
        old_resolved = _make_alert(
            status=ActionStatus.RESOLVED,
            created_hours_ago=500,
            resolved_by="admin",
        )
        agent = ReconciliationAgent(alerts=[old_resolved], stale_alert_hours=168)
        report = agent.reconcile()
        stale_issues = [i for i in report.issues if i["category"] == "stale_alerts"]
        assert len(stale_issues) == 0

    def test_orphaned_events_detected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = InMemoryEventStore()
            store.store(_make_event("evt-1"))
            store.store(_make_event("evt-2"))
            audit = AuditLogger(tmpdir)
            # Only one event was evaluated
            audit.log(action="alert.generated", actor="system", target="evt-1")
            agent = ReconciliationAgent(event_store=store, audit_logger=audit)
            report = agent.reconcile()
            orphan_issues = [i for i in report.issues if i["category"] == "orphaned_events"]
            assert len(orphan_issues) == 1
            assert orphan_issues[0]["details"]["gap"] == 1

    def test_no_orphaned_events_when_all_evaluated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = InMemoryEventStore()
            store.store(_make_event("evt-1"))
            audit = AuditLogger(tmpdir)
            audit.log(action="alert.generated", actor="system", target="evt-1")
            agent = ReconciliationAgent(event_store=store, audit_logger=audit)
            report = agent.reconcile()
            orphan_issues = [i for i in report.issues if i["category"] == "orphaned_events"]
            assert len(orphan_issues) == 0

    def test_alert_consistency_resolved_no_resolver(self):
        alert = _make_alert(status=ActionStatus.RESOLVED, resolved_by="")
        agent = ReconciliationAgent(alerts=[alert])
        report = agent.reconcile()
        consistency = [i for i in report.issues if i["category"] == "alert_consistency"]
        assert len(consistency) == 1
        assert "no resolver" in consistency[0]["description"]

    def test_alert_consistency_acknowledged_no_acknowledger(self):
        alert = _make_alert(status=ActionStatus.ACKNOWLEDGED, acknowledged_by="")
        agent = ReconciliationAgent(alerts=[alert])
        report = agent.reconcile()
        consistency = [i for i in report.issues if i["category"] == "alert_consistency"]
        assert len(consistency) == 1
        assert "no acknowledger" in consistency[0]["description"]

    def test_stale_critical_alerts_severity(self):
        stale_critical = _make_alert(
            status=ActionStatus.PENDING,
            severity=Severity.CRITICAL,
            created_hours_ago=200,
        )
        agent = ReconciliationAgent(alerts=[stale_critical], stale_alert_hours=168)
        report = agent.reconcile()
        stale_issues = [i for i in report.issues if i["category"] == "stale_alerts"]
        assert stale_issues[0]["severity"] == "critical"
