"""Tests for the CostMonitorAgent."""

import tempfile
from datetime import UTC, datetime
from unittest.mock import MagicMock

from agents.cost_monitor import CostMonitorAgent
from core.audit import AuditLogger
from core.models import CloudProvider, ResourceCreationEvent


def _make_event(
    resource_id: str = "res-1",
    resource_type: str = "ec2:instance",
    cost: float = 100.0,
    provider: CloudProvider = CloudProvider.AWS,
) -> ResourceCreationEvent:
    return ResourceCreationEvent(
        provider=provider,
        timestamp=datetime.now(UTC),
        account_id="123",
        region="eu-west-2",
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_id,
        creator_identity="user",
        creator_email="user@test.com",
        estimated_monthly_cost_usd=cost,
        raw_event={},
    )


class TestCostMonitorInit:
    def test_default_init(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        assert agent._running is False
        assert agent._event_history == []
        assert agent._providers == {}

    def test_init_with_aws_config(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit, aws_config={"regions": ["eu-west-2"]})
        assert CloudProvider.AWS in agent._providers

    def test_init_with_gcp_config(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit, gcp_config={"project_ids": ["proj"]})
        assert CloudProvider.GCP in agent._providers


class TestCostMonitorPoll:
    def test_poll_once_no_providers(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        events = agent.poll_once()
        assert events == []

    def test_poll_once_with_mock_provider(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        mock_provider = MagicMock()
        mock_provider.listen_for_events.return_value = [_make_event()]
        agent._providers[CloudProvider.AWS] = mock_provider
        agent._aws_config = {"regions": ["eu-west-2"]}

        events = agent.poll_once()
        assert len(events) == 1
        assert len(agent._event_history) == 1

    def test_deduplication(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        event = _make_event()
        agent._event_history = [event]

        mock_provider = MagicMock()
        mock_provider.listen_for_events.return_value = [event]
        agent._providers[CloudProvider.AWS] = mock_provider
        agent._aws_config = {"regions": ["eu-west-2"]}

        events = agent.poll_once()
        assert len(events) == 0

    def test_poll_handles_provider_error(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        mock_provider = MagicMock()
        mock_provider.listen_for_events.side_effect = RuntimeError("boom")
        agent._providers[CloudProvider.AWS] = mock_provider
        agent._aws_config = {"regions": ["eu-west-2"]}

        events = agent.poll_once()
        assert events == []

    def test_event_history_bounded(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        agent.MAX_EVENT_HISTORY = 5
        agent._event_history = [_make_event(resource_id=f"r-{i}") for i in range(5)]

        mock_provider = MagicMock()
        mock_provider.listen_for_events.return_value = [_make_event(resource_id="r-new")]
        agent._providers[CloudProvider.AWS] = mock_provider
        agent._aws_config = {}

        agent.poll_once()
        assert len(agent._event_history) <= 5


class TestCostMonitorQueries:
    def test_get_recent_events_all(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        agent._event_history = [_make_event(resource_id=f"r-{i}") for i in range(10)]
        events = agent.get_recent_events(limit=5)
        assert len(events) == 5

    def test_get_recent_events_filter_provider(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        agent._event_history = [
            _make_event(resource_id="aws-1", provider=CloudProvider.AWS),
            _make_event(resource_id="gcp-1", provider=CloudProvider.GCP),
        ]
        events = agent.get_recent_events(provider=CloudProvider.GCP)
        assert len(events) == 1
        assert events[0].provider == CloudProvider.GCP

    def test_get_cost_summary_empty(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        summary = agent.get_cost_summary()
        assert summary["total_events"] == 0

    def test_get_cost_summary_with_events(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        agent._event_history = [
            _make_event(cost=100.0),
            _make_event(resource_id="r-2", resource_type="rds:db", cost=200.0),
        ]
        summary = agent.get_cost_summary()
        assert summary["total_events"] == 2
        assert summary["total_estimated_monthly_cost"] == 300.0
        assert "by_provider" in summary
        assert "by_resource_type" in summary


class TestCostMonitorStartStop:
    def test_stop_sets_running_false(self):
        audit = AuditLogger(tempfile.mkdtemp())
        agent = CostMonitorAgent(audit_logger=audit)
        agent._running = True
        agent.stop()
        assert agent._running is False
