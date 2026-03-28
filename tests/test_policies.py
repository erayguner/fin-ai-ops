"""Tests for the policy engine."""

import tempfile
from datetime import UTC

from core.audit import AuditLogger
from core.models import CloudProvider, CostPolicy, ResourceCreationEvent
from core.policies import PolicyEngine

from tests.helpers import make_event


def _make_policy(**kwargs) -> CostPolicy:
    defaults = {
        "name": "Test Policy",
        "description": "A test policy",
        "provider": CloudProvider.AWS,
        "resource_types": ["ec2:instance"],
        "max_monthly_cost_usd": 1000.0,
        "require_tags": ["team", "cost-centre"],
    }
    defaults.update(kwargs)
    return CostPolicy(**defaults)


def _make_event(**kwargs) -> ResourceCreationEvent:
    defaults = {
        "resource_id": "i-test",
        "creator_identity": "user/jane",
        "estimated_monthly_cost_usd": 1500.0,
        "tags": {},
    }
    defaults.update(kwargs)
    return make_event(**defaults)


class TestPolicyEngine:
    def _create_engine(self, tmpdir):
        audit = AuditLogger(f"{tmpdir}/audit")
        return PolicyEngine(f"{tmpdir}/policies", audit)

    def test_create_and_list_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy()
            engine.create_policy(policy)

            policies = engine.get_policies()
            assert len(policies) == 1
            assert policies[0].name == "Test Policy"

    def test_update_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy()
            engine.create_policy(policy)

            updated = engine.update_policy(policy.policy_id, {"max_monthly_cost_usd": 2000.0})
            assert updated is not None
            assert updated.max_monthly_cost_usd == 2000.0

    def test_delete_policy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy()
            engine.create_policy(policy)

            assert engine.delete_policy(policy.policy_id) is True
            assert len(engine.get_policies()) == 0

    def test_evaluate_cost_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(max_monthly_cost_usd=1000.0)
            engine.create_policy(policy)

            event = _make_event(estimated_monthly_cost_usd=1500.0)
            results = engine.evaluate_event(event)
            assert len(results) == 1
            _, violations = results[0]
            assert any("exceeds policy limit" in v for v in violations)

    def test_evaluate_tag_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(require_tags=["team", "cost-centre"])
            engine.create_policy(policy)

            event = _make_event(tags={})
            results = engine.evaluate_event(event)
            assert len(results) == 1
            _, violations = results[0]
            assert any("Missing required tags" in v for v in violations)

    def test_evaluate_no_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(max_monthly_cost_usd=2000.0)
            engine.create_policy(policy)

            event = _make_event(
                estimated_monthly_cost_usd=500.0,
                tags={"team": "platform", "cost-centre": "CC-001"},
            )
            results = engine.evaluate_event(event)
            assert len(results) == 0

    def test_provider_filtering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            aws_policy = _make_policy(name="AWS Only", provider=CloudProvider.AWS)
            engine.create_policy(aws_policy)

            gcp_event = _make_event(
                provider=CloudProvider.GCP,
                resource_type="compute.instances",
            )
            results = engine.evaluate_event(gcp_event)
            assert len(results) == 0  # AWS policy shouldn't match GCP event

    def test_persist_and_reload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(f"{tmpdir}/audit")
            engine1 = PolicyEngine(f"{tmpdir}/policies", audit)
            engine1.create_policy(_make_policy(name="Persistent Policy"))

            engine2 = PolicyEngine(f"{tmpdir}/policies", audit)
            count = engine2.load_policies()
            assert count == 1
            assert engine2.get_policies()[0].name == "Persistent Policy"

    def test_blocked_region_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                blocked_regions=["us-east-1", "ap-southeast-1"],
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            event = _make_event(region="us-east-1")
            results = engine.evaluate_event(event)
            assert len(results) == 1
            _, violations = results[0]
            assert any("blocked region" in v for v in violations)

    def test_blocked_region_no_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                blocked_regions=["us-east-1"],
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            event = _make_event(region="eu-west-2")
            results = engine.evaluate_event(event)
            assert len(results) == 0

    def test_preferred_region_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                preferred_regions=["eu-north-1", "eu-west-1"],
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            event = _make_event(region="us-east-1")
            results = engine.evaluate_event(event)
            assert len(results) == 1
            _, violations = results[0]
            assert any("non-preferred region" in v for v in violations)

    def test_preferred_region_no_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                preferred_regions=["eu-west-2", "eu-north-1"],
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            event = _make_event(region="eu-west-2")
            results = engine.evaluate_event(event)
            assert len(results) == 0

    def test_required_purchase_type_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                required_purchase_type="spot",
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            event = _make_event(purchase_type="on-demand")
            results = engine.evaluate_event(event)
            assert len(results) == 1
            _, violations = results[0]
            assert any("purchase type" in v for v in violations)

    def test_required_purchase_type_no_violation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                required_purchase_type="spot",
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            event = _make_event(purchase_type="spot")
            results = engine.evaluate_event(event)
            assert len(results) == 0

    def test_schedule_violation_outside_hours(self):
        from datetime import datetime as dt

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                schedule={"active_hours": "07:00-19:00", "active_days": "mon-fri"},
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            # Create event at 22:00 UTC (outside 07:00-19:00)
            event = _make_event(
                timestamp=dt(2026, 3, 28, 22, 0, tzinfo=UTC),
            )
            results = engine.evaluate_event(event)
            assert len(results) == 1
            _, violations = results[0]
            assert any("outside active hours" in v for v in violations)

    def test_schedule_no_violation_within_hours(self):
        from datetime import datetime as dt

        with tempfile.TemporaryDirectory() as tmpdir:
            engine = self._create_engine(tmpdir)
            policy = _make_policy(
                schedule={"active_hours": "07:00-19:00", "active_days": "mon-fri"},
                require_tags=[],
                max_monthly_cost_usd=None,
            )
            engine.create_policy(policy)

            # Create event at 12:00 UTC (within 07:00-19:00)
            event = _make_event(
                timestamp=dt(2026, 3, 28, 12, 0, tzinfo=UTC),
            )
            results = engine.evaluate_event(event)
            assert len(results) == 0

    def test_load_extended_policy_fields(self):
        """Verify that policies with new fields can be persisted and reloaded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit = AuditLogger(f"{tmpdir}/audit")
            engine1 = PolicyEngine(f"{tmpdir}/policies", audit)
            policy = _make_policy(
                name="Extended Policy",
                blocked_regions=["us-east-1"],
                preferred_regions=["eu-north-1"],
                required_purchase_type="spot",
                schedule={"active_hours": "07:00-19:00"},
                min_commitment_coverage_pct=70.0,
                acknowledgement_sla_hours=4,
                resolution_sla_hours=48,
                max_account_monthly_budget_usd=25000.0,
                unit_cost_metric="cost-per-request",
                unit_cost_threshold_usd=0.005,
            )
            engine1.create_policy(policy)

            engine2 = PolicyEngine(f"{tmpdir}/policies", audit)
            count = engine2.load_policies()
            assert count == 1
            loaded = engine2.get_policies()[0]
            assert loaded.blocked_regions == ["us-east-1"]
            assert loaded.preferred_regions == ["eu-north-1"]
            assert loaded.required_purchase_type == "spot"
            assert loaded.schedule == {"active_hours": "07:00-19:00"}
            assert loaded.min_commitment_coverage_pct == 70.0
            assert loaded.acknowledgement_sla_hours == 4
            assert loaded.resolution_sla_hours == 48
            assert loaded.max_account_monthly_budget_usd == 25000.0
            assert loaded.unit_cost_metric == "cost-per-request"
            assert loaded.unit_cost_threshold_usd == 0.005
