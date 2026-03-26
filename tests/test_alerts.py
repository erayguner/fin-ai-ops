"""Tests for the alert generation engine."""

from core.alerts import AlertEngine
from core.models import CloudProvider, CostPolicy, ResourceCreationEvent, Severity
from core.thresholds import ThresholdEngine

from tests.helpers import make_event


def _make_event(
    cost: float = 3000.0,
    resource_type: str = "ec2:instance",
    provider: CloudProvider = CloudProvider.AWS,
    tags: dict | None = None,
) -> ResourceCreationEvent:
    return make_event(
        estimated_monthly_cost_usd=cost,
        resource_type=resource_type,
        provider=provider,
        resource_id="i-test123",
        tags=tags or {},
    )


class TestAlertEngine:
    def test_no_alert_below_threshold(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=50.0)
        alert = engine.evaluate_event(event)
        assert alert is None

    def test_warning_alert(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=600.0)
        alert = engine.evaluate_event(event)
        assert alert is not None
        assert alert.severity == Severity.WARNING

    def test_critical_alert(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=3000.0)
        alert = engine.evaluate_event(event)
        assert alert is not None
        assert alert.severity == Severity.CRITICAL

    def test_emergency_alert(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=6000.0)
        alert = engine.evaluate_event(event)
        assert alert is not None
        assert alert.severity == Severity.EMERGENCY

    def test_alert_contains_accountability(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=3000.0)
        alert = engine.evaluate_event(event)
        assert alert is not None
        assert "jane.doe@example.com" in alert.accountability_note
        assert (
            "jane.doe@example.com" in alert.resource_creator or "jane.doe" in alert.resource_creator
        )

    def test_alert_contains_recommendations(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=3000.0)
        alert = engine.evaluate_event(event)
        assert alert is not None
        assert len(alert.recommended_actions) > 0

    def test_alert_contains_escalation_path(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=6000.0)
        alert = engine.evaluate_event(event)
        assert alert is not None
        assert "CTO" in alert.escalation_path or "Director" in alert.escalation_path

    def test_alert_flags_missing_tags(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(cost=600.0, tags={})
        alert = engine.evaluate_event(event)
        assert alert is not None
        tag_action = [a for a in alert.recommended_actions if "tag" in a.lower()]
        assert len(tag_action) > 0

    def test_alert_with_policy(self):
        engine = AlertEngine(ThresholdEngine())
        policy = CostPolicy(
            name="Limit EC2",
            description="Limit EC2 costs",
            provider=CloudProvider.AWS,
            resource_types=["ec2:instance"],
            max_monthly_cost_usd=500.0,
            require_tags=["team", "cost-centre"],
        )
        event = _make_event(cost=3000.0)
        alert = engine.evaluate_event(event, policies=[policy])
        assert alert is not None
        assert alert.policy_id == policy.policy_id

    def test_gcp_alert(self):
        engine = AlertEngine(ThresholdEngine())
        event = _make_event(
            cost=3000.0,
            resource_type="compute.instances",
            provider=CloudProvider.GCP,
        )
        alert = engine.evaluate_event(event)
        assert alert is not None
        assert alert.provider == CloudProvider.GCP
        assert "GCP" in alert.title
