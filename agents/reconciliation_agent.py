"""Reconciliation Agent — detects and repairs data drift.

Compares the state of interconnected components to find inconsistencies:
  - Events ingested but not evaluated (missed alerts)
  - Alerts generated but not dispatched (silent failures)
  - Audit chain integrity violations (tampered entries)
  - Stale events without resolution (abandoned alerts)

Runs on-demand or on a schedule. Produces a reconciliation report
with auto-remediation actions where safe.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from core.audit import AuditLogger
from core.event_store import BaseEventStore
from core.models import ActionStatus, CostAlert, Severity

logger = logging.getLogger(__name__)

__all__ = ["ReconciliationAgent", "ReconciliationReport"]


class ReconciliationReport:
    """Results of a reconciliation run."""

    def __init__(self) -> None:
        self.timestamp = datetime.now(UTC)
        self.issues: list[dict[str, Any]] = []
        self.auto_fixed: list[dict[str, Any]] = []
        self.requires_attention: list[dict[str, Any]] = []

    @property
    def is_clean(self) -> bool:
        return len(self.issues) == 0

    def add_issue(
        self,
        category: str,
        description: str,
        severity: str = "warning",
        auto_fixable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        issue = {
            "category": category,
            "description": description,
            "severity": severity,
            "auto_fixable": auto_fixable,
            "details": details or {},
        }
        self.issues.append(issue)
        if not auto_fixable:
            self.requires_attention.append(issue)

    def add_fix(self, category: str, action: str, details: dict[str, Any] | None = None) -> None:
        self.auto_fixed.append(
            {
                "category": category,
                "action": action,
                "details": details or {},
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "status": "clean" if self.is_clean else "issues_found",
            "total_issues": len(self.issues),
            "auto_fixed": len(self.auto_fixed),
            "requires_attention": len(self.requires_attention),
            "issues": self.issues,
            "fixes_applied": self.auto_fixed,
            "attention_required": self.requires_attention,
        }


class ReconciliationAgent:
    """Detects and repairs data consistency issues across hub components."""

    def __init__(
        self,
        *,
        event_store: BaseEventStore | None = None,
        audit_logger: AuditLogger | None = None,
        alerts: list[CostAlert] | None = None,
        stale_alert_hours: int = 168,  # 7 days
    ) -> None:
        self._event_store = event_store
        self._audit = audit_logger
        self._alerts = alerts or []
        self._stale_hours = stale_alert_hours

    def get_unevaluated_events(self) -> list[str]:
        """Return event IDs that were stored but never evaluated.

        These are candidates for replay through the alert pipeline.
        Uses the audit trail to determine which events have been processed.
        """
        if self._event_store is None or self._audit is None:
            return []

        audit_entries = self._audit.get_entries(limit=10000)
        evaluated_actions = {"alert.below_threshold", "alert.generated"}

        # Collect resource_ids that were evaluated (audit target = resource_id)
        evaluated_targets = {e.target for e in audit_entries if e.action in evaluated_actions}

        # Query all events and find those whose resource_id was never evaluated
        all_events = self._event_store.query(limit=10000)
        unevaluated = [e.event_id for e in all_events if e.resource_id not in evaluated_targets]
        return unevaluated

    def reconcile(self) -> ReconciliationReport:
        """Run all reconciliation checks and return a report."""
        report = ReconciliationReport()

        self._check_audit_integrity(report)
        self._check_stale_alerts(report)
        self._check_orphaned_events(report)
        self._check_alert_consistency(report)

        if report.issues:
            logger.warning(
                "Reconciliation found %d issue(s) (%d auto-fixed, %d need attention)",
                len(report.issues),
                len(report.auto_fixed),
                len(report.requires_attention),
            )
        else:
            logger.info("Reconciliation: all clean")

        return report

    def _check_audit_integrity(self, report: ReconciliationReport) -> None:
        """Verify the audit chain has no tampered entries."""
        if self._audit is None:
            report.add_issue(
                "audit_integrity",
                "Audit logger not configured — cannot verify integrity",
                severity="warning",
            )
            return

        violations = self._audit.verify_integrity()
        if violations:
            report.add_issue(
                "audit_integrity",
                f"{len(violations)} audit chain violation(s) detected — possible tampering",
                severity="critical",
                details={"violations": violations[:5]},  # Cap at 5 for brevity
            )

    def _check_stale_alerts(self, report: ReconciliationReport) -> None:
        """Find alerts that have been pending for too long."""
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=self._stale_hours)

        stale = [
            a for a in self._alerts if a.status == ActionStatus.PENDING and a.created_at < cutoff
        ]

        if not stale:
            return

        # Group by severity for the report
        by_severity: dict[str, int] = {}
        for a in stale:
            by_severity[a.severity.value] = by_severity.get(a.severity.value, 0) + 1

        severity = (
            "critical"
            if any(a.severity in (Severity.EMERGENCY, Severity.CRITICAL) for a in stale)
            else "warning"
        )

        report.add_issue(
            "stale_alerts",
            f"{len(stale)} alert(s) have been pending for over {self._stale_hours}h",
            severity=severity,
            details={
                "by_severity": by_severity,
                "oldest": stale[0].created_at.isoformat() if stale else None,
                "alert_ids": [a.alert_id for a in stale[:10]],
            },
        )

    def _check_orphaned_events(self, report: ReconciliationReport) -> None:
        """Check for events in the store that were never evaluated.

        Detected by comparing event_store count with audit entries
        for 'alert.below_threshold' and 'alert.generated' actions.
        """
        if self._event_store is None or self._audit is None:
            return

        event_count = self._event_store.count()
        audit_entries = self._audit.get_entries(limit=10000)

        # Count events that were evaluated (either generated alert or below threshold)
        evaluated_actions = {"alert.below_threshold", "alert.generated"}
        evaluated_count = sum(1 for e in audit_entries if e.action in evaluated_actions)

        gap = event_count - evaluated_count
        if gap > 0:
            report.add_issue(
                "orphaned_events",
                f"{gap} event(s) stored but not evaluated — possible pipeline gap",
                severity="warning",
                auto_fixable=True,
                details={
                    "events_stored": event_count,
                    "events_evaluated": evaluated_count,
                    "gap": gap,
                },
            )
            report.add_fix(
                "orphaned_events",
                "Re-process orphaned events through the alert pipeline",
                details={"count": gap},
            )

    def _check_alert_consistency(self, report: ReconciliationReport) -> None:
        """Check that alert state transitions are valid."""
        for alert in self._alerts:
            # Resolved alerts must have a resolver
            if alert.status == ActionStatus.RESOLVED and not alert.resolved_by:
                report.add_issue(
                    "alert_consistency",
                    f"Alert {alert.alert_id} is resolved but has no resolver recorded",
                    severity="info",
                    details={"alert_id": alert.alert_id},
                )

            # Acknowledged alerts must have an acknowledger
            if alert.status == ActionStatus.ACKNOWLEDGED and not alert.acknowledged_by:
                report.add_issue(
                    "alert_consistency",
                    f"Alert {alert.alert_id} is acknowledged but has no acknowledger",
                    severity="info",
                    details={"alert_id": alert.alert_id},
                )
