"""Tests for the ReportAgent."""

import tempfile
from datetime import UTC, datetime, timedelta

from agents.report_agent import ReportAgent
from core.audit import AuditLogger
from core.models import (
    ActionStatus,
    CloudProvider,
    CostAlert,
    ResourceCreationEvent,
    Severity,
)


def _make_event(
    resource_id: str = "res-1",
    resource_type: str = "ec2:instance",
    cost: float = 100.0,
    team: str = "platform",
    ts: datetime | None = None,
) -> ResourceCreationEvent:
    return ResourceCreationEvent(
        provider=CloudProvider.AWS,
        timestamp=ts or datetime.now(UTC),
        account_id="123",
        region="eu-west-2",
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_id,
        creator_identity="arn:aws:iam::123:user/alice",
        creator_email="alice@example.com",
        estimated_monthly_cost_usd=cost,
        tags={"team": team, "cost-centre": "eng"},
        raw_event={},
    )


def _make_alert(
    severity: Severity = Severity.WARNING,
    status: ActionStatus = ActionStatus.PENDING,
    team: str = "platform",
    ts: datetime | None = None,
) -> CostAlert:
    return CostAlert(
        resource_id="res-1",
        resource_type="ec2:instance",
        provider=CloudProvider.AWS,
        severity=severity,
        title="Cost cap exceeded",
        summary="Resource res-1 exceeds cost cap of $500",
        resource_creator="alice",
        creator_email="alice@example.com",
        estimated_monthly_cost_usd=500.0,
        threshold_exceeded_usd=400.0,
        baseline_monthly_usd=200.0,
        cost_increase_percentage=150.0,
        recommended_actions=["Review resource sizing", "Apply cost cap policy"],
        accountability_note="alice@example.com is responsible for res-1",
        source_event_id="evt-123",
        policy_id="pol-1",
        team=team,
        cost_centre="eng",
        account_id="123",
        region="eu-west-2",
        status=status,
        created_at=ts or datetime.now(UTC),
    )


class TestReportGeneration:
    def test_generate_empty_report(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        report = agent.generate_report(
            events=[],
            alerts=[],
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        assert report.total_cost_usd == 0
        assert report.alerts_generated == 0

    def test_generate_report_with_events(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        events = [
            _make_event(resource_id="r1", cost=100.0, ts=now - timedelta(hours=1)),
            _make_event(resource_id="r2", cost=200.0, resource_type="rds:db", ts=now - timedelta(hours=2)),
        ]
        report = agent.generate_report(
            events=events,
            alerts=[],
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        assert report.total_cost_usd == 300.0
        assert "ec2:instance" in report.cost_by_resource_type
        assert "rds:db" in report.cost_by_resource_type

    def test_generate_report_with_alerts(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        events = [_make_event(ts=now - timedelta(hours=1))]
        alerts = [_make_alert(ts=now - timedelta(hours=1))]
        report = agent.generate_report(
            events=events,
            alerts=alerts,
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        assert report.alerts_generated == 1
        assert report.anomalies_detected == 1  # cost_increase_percentage=150 > 100

    def test_generate_report_filters_by_provider(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        events = [_make_event(ts=now - timedelta(hours=1))]
        report = agent.generate_report(
            events=events,
            alerts=[],
            period_start=now - timedelta(days=1),
            period_end=now,
            provider=CloudProvider.GCP,  # No GCP events exist
        )
        assert report.total_cost_usd == 0


class TestReportFormatting:
    def test_format_report_for_humans(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        events = [_make_event(ts=now - timedelta(hours=1), cost=500.0)]
        alerts = [_make_alert(ts=now - timedelta(hours=1))]
        report = agent.generate_report(
            events=events,
            alerts=alerts,
            period_start=now - timedelta(days=7),
            period_end=now,
        )
        text = agent.format_report_for_humans(report)
        assert "FINOPS COST REPORT" in text
        assert "EXECUTIVE SUMMARY" in text
        assert "COST BY RESOURCE TYPE" in text
        assert "TOP COST CREATORS" in text
        assert "RECOMMENDATIONS" in text
        assert report.report_id in text


class TestReportRecommendations:
    def test_untagged_resource_recommendation(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        event = _make_event(ts=now - timedelta(hours=1))
        event.tags = {}  # No team tag
        report = agent.generate_report(
            events=[event],
            alerts=[],
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        assert any("tagging" in r.lower() for r in report.recommendations)

    def test_critical_alert_recommendation(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        events = [_make_event(ts=now - timedelta(hours=1))]
        alerts = [_make_alert(severity=Severity.CRITICAL, ts=now - timedelta(hours=1))]
        report = agent.generate_report(
            events=events,
            alerts=alerts,
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        assert any("critical" in r.lower() or "unresolved" in r.lower() for r in report.recommendations)

    def test_high_cost_recommendation(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        events = [_make_event(ts=now - timedelta(hours=1), cost=1000.0)]
        report = agent.generate_report(
            events=events,
            alerts=[],
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        assert any("reserved" in r.lower() or "committed" in r.lower() for r in report.recommendations)


class TestAccountabilitySummary:
    def test_accountability_by_team(self):
        agent = ReportAgent(audit_logger=AuditLogger(tempfile.mkdtemp()))
        now = datetime.now(UTC)
        events = [
            _make_event(resource_id="r1", team="alpha", ts=now - timedelta(hours=1), cost=100.0),
            _make_event(resource_id="r2", team="beta", ts=now - timedelta(hours=2), cost=200.0),
        ]
        report = agent.generate_report(
            events=events,
            alerts=[],
            period_start=now - timedelta(days=1),
            period_end=now,
        )
        teams = {item["team"] for item in report.accountability_summary}
        assert "alpha" in teams
        assert "beta" in teams
