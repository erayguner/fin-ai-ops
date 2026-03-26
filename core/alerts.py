"""Alert generation and formatting engine.

Produces fully contextualised, human-readable alerts that require
no further investigation. Each alert includes:
- What happened (resource, cost, threshold breached)
- Who is accountable (creator identity, team, cost centre)
- What to do next (prioritised action list)
- Escalation path if unresolved
"""

from __future__ import annotations

from .config import HubConfig
from .models import (
    ActionStatus,
    CloudProvider,
    CostAlert,
    CostPolicy,
    CostThreshold,
    ResourceCreationEvent,
    Severity,
)
from .thresholds import ThresholdEngine

__all__ = ["AlertEngine"]


class AlertEngine:
    """Generates contextualised cost alerts from resource creation events."""

    def __init__(
        self,
        threshold_engine: ThresholdEngine,
        config: HubConfig | None = None,
    ) -> None:
        self._threshold_engine = threshold_engine
        self._config = config
        self._alerts: list[CostAlert] = []

    def evaluate_event(
        self,
        event: ResourceCreationEvent,
        policies: list[CostPolicy] | None = None,
    ) -> CostAlert | None:
        """Evaluate a resource creation event and generate an alert if warranted."""
        threshold = self._threshold_engine.calculate_threshold(event.provider, event.resource_type)
        cost = event.estimated_monthly_cost_usd
        severity = self._determine_severity(cost, threshold)

        if severity is None:
            return None

        increase_pct = self._threshold_engine.get_cost_increase_pct(event.resource_type, cost)
        matched_policy = self._match_policy(event, policies or [])
        actions = self._build_recommendations(event, severity, threshold, matched_policy)
        accountability = self._build_accountability_note(event, severity)

        alert = CostAlert(
            severity=severity,
            provider=event.provider,
            account_id=event.account_id,
            region=event.region,
            title=self._build_title(event, severity, cost),
            summary=self._build_summary(event, cost, threshold, increase_pct),
            resource_creator=event.creator_identity,
            creator_email=event.creator_email,
            team=event.tags.get("team", event.tags.get("Team", "Unknown")),
            cost_centre=event.tags.get("cost-centre", event.tags.get("CostCentre", "Unknown")),
            resource_type=event.resource_type,
            resource_id=event.resource_id,
            resource_name=event.resource_name,
            estimated_monthly_cost_usd=cost,
            threshold_exceeded_usd=self._get_exceeded_threshold(cost, threshold),
            baseline_monthly_usd=threshold.baseline_monthly_usd,
            cost_increase_percentage=increase_pct,
            recommended_actions=actions,
            accountability_note=accountability,
            escalation_path=self._build_escalation_path(severity),
            status=ActionStatus.PENDING,
            source_event_id=event.event_id,
            correlation_id=event.correlation_id,
            policy_id=matched_policy.policy_id if matched_policy else "",
        )

        self._alerts.append(alert)
        return alert

    @property
    def alerts(self) -> list[CostAlert]:
        return list(self._alerts)

    def _determine_severity(self, cost_usd: float, threshold: CostThreshold) -> Severity | None:
        if cost_usd >= threshold.emergency_usd:
            return Severity.EMERGENCY
        if cost_usd >= threshold.critical_usd:
            return Severity.CRITICAL
        if cost_usd >= threshold.warning_usd:
            return Severity.WARNING
        return None

    def _build_title(self, event: ResourceCreationEvent, severity: Severity, cost: float) -> str:
        provider_label = "AWS" if event.provider == CloudProvider.AWS else "GCP"
        return (
            f"[{severity.value.upper()}] {provider_label} Cost Alert: "
            f"{event.resource_type} in {event.account_id} "
            f"estimated at ${cost:,.2f}/month"
        )

    def _build_summary(
        self,
        event: ResourceCreationEvent,
        cost: float,
        threshold: CostThreshold,
        increase_pct: float,
    ) -> str:
        creator = event.creator_email or event.creator_identity
        name_part = f" ({event.resource_name})" if event.resource_name else ""
        increase_part = (
            f" This is {increase_pct:.1f}% above the historical baseline "
            f"of ${threshold.baseline_monthly_usd:,.2f}/month."
            if increase_pct > 0
            else ""
        )
        return (
            f"{creator} created a {event.resource_type} resource{name_part} "
            f"[{event.resource_id}] in {event.region} "
            f"with an estimated monthly cost of ${cost:,.2f}. "
            f"This exceeds the {self._get_exceeded_level(cost, threshold)} threshold "
            f"of ${self._get_exceeded_threshold(cost, threshold):,.2f}/month."
            f"{increase_part} "
            f"Account: {event.account_id}."
        )

    def _build_recommendations(
        self,
        event: ResourceCreationEvent,
        severity: Severity,
        threshold: CostThreshold,
        policy: CostPolicy | None,
    ) -> list[str]:
        actions = []

        # Tag compliance
        missing_tags = self._check_missing_tags(event, policy)
        if missing_tags:
            actions.append(
                f"IMMEDIATE: Add missing required tags: {', '.join(missing_tags)}. "
                f"Untagged resources cannot be attributed to a cost centre."
            )

        # Severity-specific actions
        if severity == Severity.EMERGENCY:
            actions.extend(
                [
                    "URGENT: Confirm this resource is authorised. If not, terminate immediately and raise an incident with the security team.",
                    "ESCALATE: Notify the Engineering Lead and Finance within 1 hour.",
                    "REVIEW: Check if this workload can use reserved/committed-use pricing to reduce costs by up to 60%.",
                ]
            )
        elif severity == Severity.CRITICAL:
            actions.extend(
                [
                    "REVIEW: Validate the resource sizing. Consider right-sizing to a smaller instance type or tier if the workload permits.",
                    "OPTIMISE: Evaluate reserved instances or committed-use discounts.",
                    "APPROVE: Obtain written cost approval from the team lead within 24 hours.",
                ]
            )
        elif severity == Severity.WARNING:
            actions.extend(
                [
                    "MONITOR: Track this resource's actual usage over the next 7 days.",
                    "OPTIMISE: Consider scheduling (auto-stop during non-business hours) to reduce costs by up to 65%.",
                    "DOCUMENT: Ensure the business justification is recorded in the resource tags or CMDB.",
                ]
            )

        actions.append(
            f"ACCOUNTABILITY: {event.creator_email or event.creator_identity} is "
            f"responsible for this resource's cost. Ensure a review is logged "
            f"within the appropriate timeframe."
        )
        return actions

    def _build_accountability_note(self, event: ResourceCreationEvent, severity: Severity) -> str:
        creator = event.creator_email or event.creator_identity
        team = event.tags.get("team", event.tags.get("Team", "their team lead"))
        if self._config:
            timeframe = self._config.get_escalation_timeframe(severity.value)
        else:
            timeframe = {
                Severity.EMERGENCY: "1 hour",
                Severity.CRITICAL: "24 hours",
                Severity.WARNING: "7 days",
                Severity.INFO: "next review cycle",
            }.get(severity, "7 days")

        return (
            f"{creator} created this resource and is the accountable owner. "
            f"They must review and respond to this alert within {timeframe}. "
            f"If unresolved, this will be escalated to {team}. "
            f"All actions taken are logged in the audit trail for governance compliance."
        )

    def _build_escalation_path(self, severity: Severity) -> str:
        paths = {
            Severity.EMERGENCY: (
                "Team Lead (1h) -> Engineering Director (2h) -> CTO/CFO (4h) -> Incident Management"
            ),
            Severity.CRITICAL: (
                "Team Lead (24h) -> Engineering Manager (48h) -> Head of Engineering (72h)"
            ),
            Severity.WARNING: "Team Lead (7d) -> Engineering Manager (14d)",
            Severity.INFO: "Included in next periodic cost review",
        }
        return paths.get(severity, "Team Lead (7d)")

    def _match_policy(
        self, event: ResourceCreationEvent, policies: list[CostPolicy]
    ) -> CostPolicy | None:
        for policy in policies:
            if not policy.enabled:
                continue
            if policy.provider and policy.provider != event.provider:
                continue
            if policy.resource_types and event.resource_type not in policy.resource_types:
                continue
            return policy
        return None

    def _check_missing_tags(
        self, event: ResourceCreationEvent, policy: CostPolicy | None
    ) -> list[str]:
        if not policy or not policy.require_tags:
            default_required = (
                self._config.get_required_tags()
                if self._config
                else ["team", "cost-centre", "environment", "owner"]
            )
            return [t for t in default_required if t not in event.tags]
        return [t for t in policy.require_tags if t not in event.tags]

    def _get_exceeded_level(self, cost: float, threshold: CostThreshold) -> str:
        if cost >= threshold.emergency_usd:
            return "emergency"
        if cost >= threshold.critical_usd:
            return "critical"
        return "warning"

    def _get_exceeded_threshold(self, cost: float, threshold: CostThreshold) -> float:
        if cost >= threshold.emergency_usd:
            return threshold.emergency_usd
        if cost >= threshold.critical_usd:
            return threshold.critical_usd
        return threshold.warning_usd
