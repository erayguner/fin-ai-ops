"""Tests for core data models."""

from core.models import (
    ActionStatus,
    AuditEntry,
    CloudProvider,
    CostAlert,
    CostPolicy,
    CostThreshold,
    ResourceCreationEvent,
    Severity,
)


class TestResourceCreationEvent:
    def test_create_event_with_defaults(self):
        event = ResourceCreationEvent(
            provider=CloudProvider.AWS,
            account_id="123456789012",
            region="eu-west-2",
            resource_type="ec2:instance",
            resource_id="i-0123456789abcdef0",
            creator_identity="arn:aws:iam::123456789012:user/jane.doe",
        )
        assert event.provider == CloudProvider.AWS
        assert event.estimated_monthly_cost_usd == 0.0
        assert event.event_id  # UUID generated
        assert event.timestamp.tzinfo is not None

    def test_create_event_with_full_data(self):
        event = ResourceCreationEvent(
            provider=CloudProvider.GCP,
            account_id="my-project",
            region="europe-west2",
            resource_type="compute.instances",
            resource_id="instance-1",
            resource_name="web-server-01",
            creator_identity="jane@example.com",
            creator_email="jane@example.com",
            estimated_monthly_cost_usd=1500.00,
            tags={"team": "platform", "cost-centre": "CC-001", "environment": "production"},
        )
        assert event.estimated_monthly_cost_usd == 1500.00
        assert event.tags["team"] == "platform"


class TestCostThreshold:
    def test_create_threshold(self):
        threshold = CostThreshold(
            provider=CloudProvider.AWS,
            resource_type="ec2:instance",
            warning_usd=500.0,
            critical_usd=2000.0,
            emergency_usd=5000.0,
            baseline_monthly_usd=250.0,
        )
        assert threshold.anomaly_multiplier == 2.0
        assert threshold.warning_usd < threshold.critical_usd < threshold.emergency_usd


class TestCostAlert:
    def test_create_alert(self):
        alert = CostAlert(
            severity=Severity.CRITICAL,
            provider=CloudProvider.AWS,
            account_id="123456789012",
            region="eu-west-2",
            title="Test Alert",
            summary="Test summary",
            resource_creator="arn:aws:iam::123456789012:user/jane.doe",
            creator_email="jane@example.com",
            resource_type="ec2:instance",
            resource_id="i-123",
            estimated_monthly_cost_usd=3000.0,
            threshold_exceeded_usd=2000.0,
            baseline_monthly_usd=500.0,
            cost_increase_percentage=500.0,
            recommended_actions=["Review resource sizing"],
            accountability_note="jane@example.com is responsible",
            source_event_id="evt-123",
        )
        assert alert.status == ActionStatus.PENDING
        assert alert.severity == Severity.CRITICAL


class TestCostPolicy:
    def test_create_policy(self):
        policy = CostPolicy(
            name="Test Policy",
            description="Test description",
            provider=CloudProvider.AWS,
            resource_types=["ec2:instance"],
            max_monthly_cost_usd=1000.0,
            require_tags=["team", "cost-centre"],
        )
        assert policy.enabled is True
        assert len(policy.require_tags) == 2


class TestAuditEntry:
    def test_create_entry(self):
        entry = AuditEntry(
            action="test.action",
            actor="test-user",
            target="test-target",
        )
        assert entry.outcome == "success"
        assert entry.audit_id  # UUID generated
