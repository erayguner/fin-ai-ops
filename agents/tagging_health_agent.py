"""Tagging Health Agent — tagging/labelling governance across providers.

Single-responsibility agent focused exclusively on tagging consistency
and compliance. Given a stream of resource creation events (or a live
feed from the CostMonitorAgent), it:

  1. Resolves the appropriate provider-scoped TaggingPolicy.
  2. Classifies each resource as compliant, non-compliant, non-taggable,
     or exempt.
  3. Tracks weekly trends, unattributed spend, and per-team gaps.
  4. Produces an actionable weekly report with remediation priorities
     ranked by cost impact and ownership.

The agent does NOT mutate resources, generate cost alerts, or hold
cross-cutting responsibility for generic policy enforcement —
that remains the CostMonitorAgent / AlertAgent / PolicyEngine domain.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from core.audit import AuditLogger
from core.models import CloudProvider, ResourceCreationEvent
from core.tagging import (
    ResourceTagAudit,
    TagComplianceStatus,
    TaggingHealthReport,
    TaggingPolicyEngine,
    aggregate_audits,
    build_resource_audit,
)

__all__ = ["TaggingHealthAgent"]

logger = logging.getLogger(__name__)

# Default report cadence (weekly). Kept as a module-level constant so
# the MCP server and CLI can reference the same value.
DEFAULT_REPORT_WINDOW_DAYS = 7

# Keep the last N reports in memory for trend analysis without
# unbounded growth.
MAX_REPORT_HISTORY = 52  # one year of weekly reports


class TaggingHealthAgent:
    """Scans events for tag compliance and issues weekly health reports.

    Usage:
        engine = TaggingPolicyEngine(policy_dir="policies/tagging", audit_logger=audit)
        engine.load_policies()
        agent = TaggingHealthAgent(policy_engine=engine, audit_logger=audit)

        # Ad-hoc scan
        audits = agent.scan(events)

        # Weekly report
        report = agent.generate_weekly_report(events)
        print(agent.format_report_for_humans(report))
    """

    def __init__(
        self,
        policy_engine: TaggingPolicyEngine,
        audit_logger: AuditLogger,
        *,
        report_window_days: int = DEFAULT_REPORT_WINDOW_DAYS,
    ) -> None:
        if report_window_days <= 0:
            raise ValueError("report_window_days must be positive")
        self._policies = policy_engine
        self._audit = audit_logger
        self._window_days = report_window_days
        self._report_history: list[TaggingHealthReport] = []

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(
        self,
        events: list[ResourceCreationEvent],
        *,
        provider: CloudProvider | None = None,
    ) -> list[ResourceTagAudit]:
        """Evaluate a batch of events and return per-resource audits.

        Events are filtered by provider when specified. The scan is
        side-effect-free apart from a single aggregate audit log entry.
        """
        scoped = [e for e in events if provider is None or e.provider == provider]

        audits = [build_resource_audit(event, self._policies.resolve(event)) for event in scoped]

        non_compliant_count = sum(
            1 for a in audits if a.status == TagComplianceStatus.NON_COMPLIANT
        )

        self._audit.log(
            action="tagging.scan_completed",
            actor="system",
            target="tagging_health_agent",
            provider=provider,
            details={
                "evaluated": len(audits),
                "non_compliant": non_compliant_count,
                "non_taggable": sum(
                    1 for a in audits if a.status == TagComplianceStatus.NON_TAGGABLE
                ),
                "exempt": sum(1 for a in audits if a.status == TagComplianceStatus.EXEMPT),
            },
        )

        return audits

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def generate_weekly_report(
        self,
        events: list[ResourceCreationEvent],
        *,
        period_end: datetime | None = None,
        provider: CloudProvider | None = None,
    ) -> TaggingHealthReport:
        """Generate a tagging health report for the last ``window_days``.

        Events outside the window are ignored. Trend comparison uses
        the most recent prior report with matching provider scope.
        """
        end = period_end or datetime.now(UTC)
        start = end - timedelta(days=self._window_days)

        filtered = [
            e
            for e in events
            if start <= e.timestamp <= end and (provider is None or e.provider == provider)
        ]

        audits = [build_resource_audit(event, self._policies.resolve(event)) for event in filtered]
        aggregates = aggregate_audits(audits)

        report = TaggingHealthReport(
            period_start=start,
            period_end=end,
            provider=provider,
            total_resources=aggregates["total"],
            compliant=aggregates["compliant"],
            non_compliant=aggregates["non_compliant"],
            non_taggable=aggregates["non_taggable"],
            exempt=aggregates["exempt"],
            compliance_rate_pct=aggregates["compliance_rate_pct"],
            missing_tag_counts=aggregates["missing_tag_counts"],
            non_compliance_by_account=aggregates["non_compliance_by_account"],
            non_compliance_by_resource_type=aggregates["non_compliance_by_resource_type"],
            non_compliance_by_team=aggregates["non_compliance_by_team"],
            unattributed_monthly_cost_usd=aggregates["unattributed_monthly_cost_usd"],
            trend_vs_previous_period_pct=self._compute_trend(
                aggregates["compliance_rate_pct"], provider
            ),
            remediation_priorities=self._rank_remediation(audits),
            recommendations=self._recommendations(aggregates, audits),
        )

        self._report_history.append(report)
        if len(self._report_history) > MAX_REPORT_HISTORY:
            self._report_history = self._report_history[-MAX_REPORT_HISTORY:]

        self._audit.log(
            action="tagging.report_generated",
            actor="system",
            target=report.report_id,
            provider=provider,
            details={
                "period": f"{start.isoformat()} to {end.isoformat()}",
                "total": report.total_resources,
                "compliance_rate_pct": report.compliance_rate_pct,
                "unattributed_monthly_cost_usd": report.unattributed_monthly_cost_usd,
            },
        )

        return report

    def get_report_history(self, limit: int = 10) -> list[TaggingHealthReport]:
        return self._report_history[-limit:]

    def format_report_for_humans(self, report: TaggingHealthReport) -> str:
        """Render a TaggingHealthReport as a human-readable text block."""
        separator = "=" * 72
        provider_label = report.provider.value.upper() if report.provider else "ALL PROVIDERS"

        missing_lines = (
            "\n".join(
                f"    {tag:<30} {count:>6}"
                for tag, count in sorted(
                    report.missing_tag_counts.items(), key=lambda x: x[1], reverse=True
                )
            )
            or "    None"
        )

        account_lines = (
            "\n".join(
                f"    {acct:<30} {count:>6}"
                for acct, count in sorted(
                    report.non_compliance_by_account.items(), key=lambda x: x[1], reverse=True
                )
            )
            or "    None"
        )

        team_lines = (
            "\n".join(
                f"    {team:<30} {count:>6}"
                for team, count in sorted(
                    report.non_compliance_by_team.items(), key=lambda x: x[1], reverse=True
                )
            )
            or "    None"
        )

        priority_lines = (
            "\n".join(
                f"    {i + 1}. [{p['severity']}] {p['resource_type']} "
                f"({p['account_id']}) — ${p['estimated_monthly_cost_usd']:,.2f}/mo, "
                f"owner: {p['owner']}"
                for i, p in enumerate(report.remediation_priorities[:10])
            )
            or "    None"
        )

        recommendation_lines = (
            "\n".join(f"    {i + 1}. {rec}" for i, rec in enumerate(report.recommendations))
            or "    No recommendations"
        )

        return f"""
{separator}
FINOPS TAGGING HEALTH REPORT — {provider_label}
Period: {report.period_start.strftime("%Y-%m-%d")} to {report.period_end.strftime("%Y-%m-%d")}
Generated: {report.generated_at.strftime("%Y-%m-%d %H:%M UTC")}
{separator}

EXECUTIVE SUMMARY:
    Total Resources Evaluated:   {report.total_resources}
    Compliant:                   {report.compliant}
    Non-Compliant:               {report.non_compliant}
    Non-Taggable:                {report.non_taggable}
    Exempt (no policy):          {report.exempt}
    Compliance Rate:             {report.compliance_rate_pct:.1f}%
    Trend vs Previous Period:    {report.trend_vs_previous_period_pct:+.1f}%
    Unattributed Monthly Spend:  ${report.unattributed_monthly_cost_usd:,.2f}

MISSING REQUIRED TAGS (count):
{missing_lines}

NON-COMPLIANCE BY ACCOUNT:
{account_lines}

NON-COMPLIANCE BY TEAM (Untagged = no owner):
{team_lines}

REMEDIATION PRIORITIES (ranked by cost impact):
{priority_lines}

RECOMMENDATIONS:
{recommendation_lines}

Report ID: {report.report_id}
{separator}
"""

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _compute_trend(
        self,
        current_rate: float,
        provider: CloudProvider | None,
    ) -> float:
        """Compute percentage-point change vs the most recent prior report."""
        prior = [r for r in self._report_history if r.provider == provider]
        if not prior:
            return 0.0
        previous = prior[-1]
        return round(current_rate - previous.compliance_rate_pct, 2)

    @staticmethod
    def _rank_remediation(audits: list[ResourceTagAudit]) -> list[dict[str, Any]]:
        """Rank non-compliant audits by cost impact for actionable output."""
        violations = [a for a in audits if a.status == TagComplianceStatus.NON_COMPLIANT]
        violations.sort(key=lambda a: a.estimated_monthly_cost_usd, reverse=True)

        return [
            {
                "resource_id": a.resource_id,
                "resource_type": a.resource_type,
                "account_id": a.account_id,
                "region": a.region,
                "severity": a.severity.value,
                "missing_required_tags": a.missing_required_tags,
                "missing_recommended_tags": a.missing_recommended_tags,
                "invalid_tag_values": a.invalid_tag_values,
                "estimated_monthly_cost_usd": round(a.estimated_monthly_cost_usd, 2),
                "owner": a.creator_email or a.creator_identity or "unknown",
                "policy_name": a.policy_name,
                "recommended_action": (
                    f"Add missing tags: {', '.join(a.missing_required_tags)}"
                    if a.missing_required_tags
                    else "Correct invalid tag values: " + ", ".join(a.invalid_tag_values.keys())
                ),
            }
            for a in violations
        ]

    @staticmethod
    def _recommendations(
        aggregates: dict[str, Any],
        audits: list[ResourceTagAudit],
    ) -> list[str]:
        """Generate plain-English recommendations for the report."""
        recs: list[str] = []

        if aggregates["non_compliant"] == 0 and aggregates["total"] > 0:
            recs.append("All resources are tag-compliant. Maintain current enforcement.")
            return recs

        if aggregates["compliance_rate_pct"] < 80.0:
            recs.append(
                f"Compliance rate is {aggregates['compliance_rate_pct']:.1f}% — "
                "below 80% target. Escalate to cloud platform team for enforcement at "
                "resource-creation time (IaC guardrails, SCPs, org policies)."
            )

        top_missing = sorted(
            aggregates["missing_tag_counts"].items(), key=lambda x: x[1], reverse=True
        )
        if top_missing:
            tag, count = top_missing[0]
            recs.append(
                f"'{tag}' is the most frequently missing tag ({count} resource(s)). "
                "Add it to the IaC template defaults and CI validation."
            )

        untagged_owners = aggregates["non_compliance_by_team"].get("Untagged", 0)
        if untagged_owners:
            recs.append(
                f"{untagged_owners} non-compliant resource(s) have no team tag. "
                "Block further creation without a 'team' tag via preventative policy."
            )

        if aggregates["unattributed_monthly_cost_usd"] > 0:
            recs.append(
                f"${aggregates['unattributed_monthly_cost_usd']:,.2f}/month of spend "
                "cannot be attributed due to missing tags. This is the maximum "
                "potential savings from better cost allocation hygiene."
            )

        if aggregates["non_taggable"]:
            recs.append(
                f"{aggregates['non_taggable']} resource(s) are non-taggable by "
                "provider design — attribute them via their parent resource or "
                "project/account-level tags."
            )

        return recs
