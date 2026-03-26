"""Report Agent.

Generates periodic cost reports with:
- Spend breakdown by provider, team, resource type, and account
- Top cost creators with accountability attribution
- Anomaly detection and trend analysis
- Actionable recommendations
- Full audit trail reference
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.audit import AuditLogger
from core.models import CloudProvider, CostAlert, CostReport, ResourceCreationEvent, Severity

__all__ = ["ReportAgent"]


class ReportAgent:
    """Generates comprehensive FinOps cost reports."""

    def __init__(self, audit_logger: AuditLogger) -> None:
        self._audit = audit_logger

    def generate_report(
        self,
        events: list[ResourceCreationEvent],
        alerts: list[CostAlert],
        period_start: datetime,
        period_end: datetime,
        provider: CloudProvider | None = None,
    ) -> CostReport:
        """Generate a comprehensive cost report for the given period."""
        filtered_events = self._filter_events(events, period_start, period_end, provider)
        filtered_alerts = self._filter_alerts(alerts, period_start, period_end, provider)

        total_cost = sum(e.estimated_monthly_cost_usd for e in filtered_events)

        report = CostReport(
            period_start=period_start,
            period_end=period_end,
            provider=provider,
            total_cost_usd=round(total_cost, 2),
            cost_by_resource_type=self._group_cost_by_resource_type(filtered_events),
            cost_by_team=self._group_cost_by_team(filtered_events),
            cost_by_account=self._group_cost_by_account(filtered_events),
            top_cost_creators=self._get_top_creators(filtered_events),
            anomalies_detected=len(
                [a for a in filtered_alerts if a.cost_increase_percentage > 100]
            ),
            alerts_generated=len(filtered_alerts),
            alerts_resolved=len([a for a in filtered_alerts if a.status.value == "resolved"]),
            recommendations=self._generate_recommendations(filtered_events, filtered_alerts),
            accountability_summary=self._build_accountability_summary(
                filtered_events, filtered_alerts
            ),
        )

        self._audit.log(
            action="report.generated",
            actor="system",
            target=report.report_id,
            provider=provider,
            details={
                "period": f"{period_start.isoformat()} to {period_end.isoformat()}",
                "total_cost": report.total_cost_usd,
                "events_analysed": len(filtered_events),
                "alerts_included": len(filtered_alerts),
            },
        )

        return report

    def format_report_for_humans(self, report: CostReport) -> str:
        """Format a report as a human-readable text document."""
        separator = "=" * 72
        provider_label = report.provider.value.upper() if report.provider else "ALL PROVIDERS"

        # Cost by resource type
        resource_lines = "\n".join(
            f"    {rt:<35} ${cost:>12,.2f}"
            for rt, cost in sorted(
                report.cost_by_resource_type.items(),
                key=lambda x: x[1],
                reverse=True,
            )
        )

        # Cost by team
        team_lines = "\n".join(
            f"    {team:<35} ${cost:>12,.2f}"
            for team, cost in sorted(
                report.cost_by_team.items(),
                key=lambda x: x[1],
                reverse=True,
            )
        )

        # Top creators
        creator_lines = "\n".join(
            f"    {i + 1}. {c['creator']:<30} ${c['total_cost']:>12,.2f}  "
            f"({c['resource_count']} resources)"
            for i, c in enumerate(report.top_cost_creators[:10])
        )

        # Accountability
        accountability_lines = "\n".join(
            f"    - {item['team']}: {item['summary']}" for item in report.accountability_summary
        )

        # Recommendations
        recommendation_lines = "\n".join(
            f"    {i + 1}. {rec}" for i, rec in enumerate(report.recommendations)
        )

        return f"""
{separator}
FINOPS COST REPORT - {provider_label}
Period: {report.period_start.strftime("%Y-%m-%d")} to {report.period_end.strftime("%Y-%m-%d")}
Generated: {report.generated_at.strftime("%Y-%m-%d %H:%M UTC")}
{separator}

EXECUTIVE SUMMARY:
    Total Estimated Monthly Cost:  ${report.total_cost_usd:,.2f}
    Trend vs Previous Period:      {report.trend_vs_previous_period_pct:+.1f}%
    Alerts Generated:              {report.alerts_generated}
    Alerts Resolved:               {report.alerts_resolved}
    Anomalies Detected:            {report.anomalies_detected}

COST BY RESOURCE TYPE:
{resource_lines or "    No resources in this period"}

COST BY TEAM:
{team_lines or "    No team data available"}

TOP COST CREATORS (Accountability):
{creator_lines or "    No creator data available"}

ACCOUNTABILITY SUMMARY:
{accountability_lines or "    No accountability data available"}

RECOMMENDATIONS:
{recommendation_lines or "    No recommendations at this time"}

Report ID: {report.report_id}
{separator}
"""

    def _filter_events(
        self,
        events: list[ResourceCreationEvent],
        start: datetime,
        end: datetime,
        provider: CloudProvider | None,
    ) -> list[ResourceCreationEvent]:
        filtered = [e for e in events if start <= e.timestamp <= end]
        if provider:
            filtered = [e for e in filtered if e.provider == provider]
        return filtered

    def _filter_alerts(
        self,
        alerts: list[CostAlert],
        start: datetime,
        end: datetime,
        provider: CloudProvider | None,
    ) -> list[CostAlert]:
        filtered = [a for a in alerts if start <= a.created_at <= end]
        if provider:
            filtered = [a for a in filtered if a.provider == provider]
        return filtered

    def _group_cost_by_resource_type(self, events: list[ResourceCreationEvent]) -> dict[str, float]:
        groups: dict[str, float] = {}
        for event in events:
            groups[event.resource_type] = (
                groups.get(event.resource_type, 0) + event.estimated_monthly_cost_usd
            )
        return {k: round(v, 2) for k, v in groups.items()}

    def _group_cost_by_team(self, events: list[ResourceCreationEvent]) -> dict[str, float]:
        groups: dict[str, float] = {}
        for event in events:
            team = event.tags.get("team", event.tags.get("Team", "Untagged"))
            groups[team] = groups.get(team, 0) + event.estimated_monthly_cost_usd
        return {k: round(v, 2) for k, v in groups.items()}

    def _group_cost_by_account(self, events: list[ResourceCreationEvent]) -> dict[str, float]:
        groups: dict[str, float] = {}
        for event in events:
            groups[event.account_id] = (
                groups.get(event.account_id, 0) + event.estimated_monthly_cost_usd
            )
        return {k: round(v, 2) for k, v in groups.items()}

    def _get_top_creators(self, events: list[ResourceCreationEvent]) -> list[dict[str, Any]]:
        creators: dict[str, dict[str, Any]] = {}
        for event in events:
            key = event.creator_email or event.creator_identity
            if key not in creators:
                creators[key] = {
                    "creator": key,
                    "total_cost": 0.0,
                    "resource_count": 0,
                    "resource_types": set(),
                }
            creators[key]["total_cost"] += event.estimated_monthly_cost_usd
            creators[key]["resource_count"] += 1
            creators[key]["resource_types"].add(event.resource_type)

        result = sorted(creators.values(), key=lambda x: x["total_cost"], reverse=True)
        for item in result:
            item["total_cost"] = round(item["total_cost"], 2)
            item["resource_types"] = list(item["resource_types"])
        return result

    def _generate_recommendations(
        self,
        events: list[ResourceCreationEvent],
        alerts: list[CostAlert],
    ) -> list[str]:
        recommendations = []

        # Check for untagged resources
        untagged = [e for e in events if not e.tags.get("team")]
        if untagged:
            recommendations.append(
                f"{len(untagged)} resource(s) lack team tags. Enforce tagging "
                f"policies to ensure cost attribution and accountability."
            )

        # Check for high-severity unresolved alerts
        unresolved_critical = [
            a
            for a in alerts
            if a.severity in (Severity.CRITICAL, Severity.EMERGENCY) and a.status.value == "pending"
        ]
        if unresolved_critical:
            recommendations.append(
                f"{len(unresolved_critical)} critical/emergency alert(s) remain "
                f"unresolved. Prioritise acknowledgement and remediation."
            )

        # Check for cost concentration
        if events:
            top_creator_events = self._get_top_creators(events)
            if top_creator_events:
                top = top_creator_events[0]
                total_cost = sum(e.estimated_monthly_cost_usd for e in events)
                if total_cost > 0 and top["total_cost"] / total_cost > 0.5:
                    recommendations.append(
                        f"{top['creator']} accounts for "
                        f"{top['total_cost'] / total_cost * 100:.0f}% of total cost. "
                        f"Review whether this concentration is intentional."
                    )

        # Reserved/committed use
        high_cost_types = {e.resource_type for e in events if e.estimated_monthly_cost_usd > 500}
        if high_cost_types:
            recommendations.append(
                f"Consider reserved instances/committed-use discounts for "
                f"high-cost resource types: {', '.join(sorted(high_cost_types))}. "
                f"Potential savings of 30-60%."
            )

        if not recommendations:
            recommendations.append("No immediate actions required. Continue monitoring.")

        return recommendations

    def _build_accountability_summary(
        self,
        events: list[ResourceCreationEvent],
        alerts: list[CostAlert],
    ) -> list[dict[str, str]]:
        """Build per-team accountability summary."""
        team_data: dict[str, dict[str, Any]] = {}

        for event in events:
            team = event.tags.get("team", event.tags.get("Team", "Untagged"))
            if team not in team_data:
                team_data[team] = {
                    "cost": 0.0,
                    "resources": 0,
                    "alerts": 0,
                    "unresolved": 0,
                }
            team_data[team]["cost"] += event.estimated_monthly_cost_usd
            team_data[team]["resources"] += 1

        for alert in alerts:
            team = alert.team or "Untagged"
            if team in team_data:
                team_data[team]["alerts"] += 1
                if alert.status.value == "pending":
                    team_data[team]["unresolved"] += 1

        return [
            {
                "team": team,
                "summary": (
                    f"${data['cost']:,.2f} across {data['resources']} resource(s), "
                    f"{data['alerts']} alert(s) ({data['unresolved']} unresolved)"
                ),
            }
            for team, data in sorted(team_data.items(), key=lambda x: x[1]["cost"], reverse=True)
        ]
