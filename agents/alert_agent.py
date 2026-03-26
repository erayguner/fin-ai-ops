"""Alert Agent.

Evaluates resource creation events against cost thresholds and policies,
generates fully contextualised alerts, and dispatches them through
configured channels (webhook, email, Slack, PagerDuty).

Every alert is designed to be self-contained: a recipient should never
need to perform further investigation to understand what happened,
who is accountable, and what to do next.
"""

from __future__ import annotations

import logging
from typing import Any

from core.alerts import AlertEngine
from core.audit import AuditLogger
from core.config import HubConfig
from core.event_store import BaseEventStore, InMemoryEventStore

__all__ = ["AlertAgent"]
from core.models import (
    ActionStatus,
    CloudProvider,
    CostAlert,
    ResourceCreationEvent,
    Severity,
)
from core.notifications import BaseNotificationDispatcher, LogDispatcher
from core.policies import PolicyEngine
from core.thresholds import ThresholdEngine

logger = logging.getLogger(__name__)


class AlertAgent:
    """Evaluates events and dispatches contextualised cost alerts."""

    def __init__(
        self,
        threshold_engine: ThresholdEngine,
        policy_engine: PolicyEngine,
        audit_logger: AuditLogger,
        notification_config: dict[str, Any] | None = None,
        *,
        event_store: BaseEventStore | None = None,
        dispatcher: BaseNotificationDispatcher | None = None,
        config: HubConfig | None = None,
    ) -> None:
        self._alert_engine = AlertEngine(threshold_engine, config=config)
        self._policy_engine = policy_engine
        self._threshold_engine = threshold_engine
        self._audit = audit_logger
        self._notification_config = notification_config or {}
        self._dispatched_alerts: list[CostAlert] = []
        self._event_store = event_store or InMemoryEventStore()
        self._dispatcher = dispatcher or LogDispatcher()

    def process_event(self, event: ResourceCreationEvent) -> CostAlert | None:
        """Process a single resource creation event.

        Evaluates against thresholds and policies, generates an alert
        if warranted, dispatches notifications, and persists the event.
        """
        # Persist event
        self._event_store.store(event)

        # Evaluate against policies
        policy_violations = self._policy_engine.evaluate_event(event)
        active_policies = [pv[0] for pv in policy_violations]

        # Generate alert
        alert = self._alert_engine.evaluate_event(event, active_policies)

        if alert is None:
            self._audit.log(
                action="alert.below_threshold",
                actor="system",
                target=event.resource_id,
                provider=event.provider,
                correlation_id=event.correlation_id,
                causation_id=event.event_id,
                details={
                    "resource_type": event.resource_type,
                    "estimated_cost": event.estimated_monthly_cost_usd,
                    "creator": event.creator_identity,
                },
            )
            return None

        # Enrich with policy violations
        if policy_violations:
            violation_details = []
            for policy, violations in policy_violations:
                violation_details.append(
                    {
                        "policy": policy.name,
                        "violations": violations,
                    }
                )
            alert.recommended_actions.insert(
                0,
                f"POLICY VIOLATION: {len(policy_violations)} policy violation(s) detected. "
                f"Review and remediate immediately.",
            )

        # Record cost for future threshold calculation
        self._threshold_engine.record_cost(event.resource_type, event.estimated_monthly_cost_usd)

        # Dispatch notifications
        self._dispatch_alert(alert)

        # Audit
        self._audit.log(
            action="alert.generated",
            actor="system",
            target=event.resource_id,
            provider=event.provider,
            correlation_id=alert.correlation_id,
            causation_id=event.event_id,
            details={
                "alert_id": alert.alert_id,
                "severity": alert.severity.value,
                "estimated_cost": alert.estimated_monthly_cost_usd,
                "threshold_exceeded": alert.threshold_exceeded_usd,
                "creator": alert.resource_creator,
                "creator_email": alert.creator_email,
                "recommended_actions_count": len(alert.recommended_actions),
            },
            related_alert_id=alert.alert_id,
            related_policy_id=alert.policy_id,
        )

        self._dispatched_alerts.append(alert)
        return alert

    def process_events(self, events: list[ResourceCreationEvent]) -> list[CostAlert]:
        """Process a batch of events, returning generated alerts."""
        alerts = []
        for event in events:
            alert = self.process_event(event)
            if alert:
                alerts.append(alert)
        return alerts

    def acknowledge_alert(self, alert_id: str, acknowledged_by: str) -> CostAlert | None:
        """Mark an alert as acknowledged by a responsible person."""
        for alert in self._dispatched_alerts:
            if alert.alert_id == alert_id:
                alert.status = ActionStatus.ACKNOWLEDGED
                alert.acknowledged_by = acknowledged_by
                self._audit.log(
                    action="alert.acknowledged",
                    actor=acknowledged_by,
                    target=alert_id,
                    provider=alert.provider,
                    details={
                        "severity": alert.severity.value,
                        "resource_id": alert.resource_id,
                    },
                    related_alert_id=alert_id,
                )
                return alert
        return None

    def resolve_alert(
        self, alert_id: str, resolved_by: str, resolution_notes: str = ""
    ) -> CostAlert | None:
        """Mark an alert as resolved."""
        for alert in self._dispatched_alerts:
            if alert.alert_id == alert_id:
                alert.status = ActionStatus.RESOLVED
                alert.resolved_by = resolved_by
                self._audit.log(
                    action="alert.resolved",
                    actor=resolved_by,
                    target=alert_id,
                    provider=alert.provider,
                    details={
                        "severity": alert.severity.value,
                        "resource_id": alert.resource_id,
                        "resolution_notes": resolution_notes,
                    },
                    related_alert_id=alert_id,
                )
                return alert
        return None

    def get_alerts(
        self,
        severity: Severity | None = None,
        status: ActionStatus | None = None,
        provider: CloudProvider | None = None,
        limit: int = 50,
    ) -> list[CostAlert]:
        """Query alerts with optional filters."""
        alerts = self._dispatched_alerts
        if severity:
            alerts = [a for a in alerts if a.severity == severity]
        if status:
            alerts = [a for a in alerts if a.status == status]
        if provider:
            alerts = [a for a in alerts if a.provider == provider]
        return alerts[-limit:]

    def get_alert_stats(self) -> dict[str, Any]:
        """Get alert statistics."""
        if not self._dispatched_alerts:
            return {"total": 0}

        return {
            "total": len(self._dispatched_alerts),
            "by_severity": {
                s.value: len([a for a in self._dispatched_alerts if a.severity == s])
                for s in Severity
            },
            "by_status": {
                s.value: len([a for a in self._dispatched_alerts if a.status == s])
                for s in ActionStatus
            },
            "total_cost_impact": sum(a.estimated_monthly_cost_usd for a in self._dispatched_alerts),
            "unacknowledged": len(
                [a for a in self._dispatched_alerts if a.status == ActionStatus.PENDING]
            ),
        }

    @property
    def event_store(self) -> BaseEventStore:
        """Access the event store for querying persisted events."""
        return self._event_store

    def _dispatch_alert(self, alert: CostAlert) -> None:
        """Dispatch alert through configured notification channels."""
        formatted = self.format_alert_for_humans(alert)
        try:
            self._dispatcher.send(alert, formatted)
        except Exception:
            logger.exception("Failed to dispatch alert %s", alert.alert_id)

    @staticmethod
    def format_alert_for_humans(alert: CostAlert) -> str:
        """Format an alert as a human-readable message.

        Designed to be self-contained: the reader should need no further
        investigation to understand and act on this alert.
        """
        separator = "=" * 72

        actions_formatted = "\n".join(
            f"  {i + 1}. {action}" for i, action in enumerate(alert.recommended_actions)
        )

        return f"""
{separator}
{alert.title}
{separator}

WHAT HAPPENED:
  {alert.summary}

WHO IS ACCOUNTABLE:
  Creator:      {alert.resource_creator}
  Email:        {alert.creator_email or "Not available"}
  Team:         {alert.team}
  Cost Centre:  {alert.cost_centre}

COST IMPACT:
  Resource:             {alert.resource_type} [{alert.resource_id}]
  Name:                 {alert.resource_name or "Not named"}
  Estimated Monthly:    ${alert.estimated_monthly_cost_usd:,.2f}
  Threshold Exceeded:   ${alert.threshold_exceeded_usd:,.2f}
  Baseline (avg):       ${alert.baseline_monthly_usd:,.2f}
  Increase:             {alert.cost_increase_percentage:.1f}% above baseline

  Provider:  {alert.provider.value.upper()}
  Account:   {alert.account_id}
  Region:    {alert.region}

RECOMMENDED ACTIONS:
{actions_formatted}

ACCOUNTABILITY:
  {alert.accountability_note}

ESCALATION PATH:
  {alert.escalation_path}

STATUS: {alert.status.value.upper()}
Alert ID: {alert.alert_id}
Generated: {alert.created_at.isoformat()}
{separator}
"""
