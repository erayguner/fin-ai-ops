"""Tests for the threshold calculation engine."""

from core.models import CloudProvider
from core.thresholds import ThresholdEngine


class TestThresholdEngine:
    def test_default_thresholds(self):
        engine = ThresholdEngine()
        threshold = engine.calculate_threshold(CloudProvider.AWS, "ec2:instance")
        assert threshold.warning_usd == 500.0
        assert threshold.critical_usd == 2000.0
        assert threshold.emergency_usd == 5000.0

    def test_unknown_resource_type_uses_fallback(self):
        engine = ThresholdEngine()
        threshold = engine.calculate_threshold(CloudProvider.AWS, "unknown:thing")
        assert threshold.warning_usd == 500.0  # default fallback

    def test_dynamic_thresholds_with_history(self):
        engine = ThresholdEngine()
        # Record enough history to trigger dynamic calculation
        for cost in [100.0, 110.0, 105.0, 95.0, 100.0]:
            engine.record_cost("ec2:instance", cost)

        threshold = engine.calculate_threshold(CloudProvider.AWS, "ec2:instance")
        # Dynamic thresholds should be based on mean + stddev
        assert threshold.baseline_monthly_usd > 0
        assert threshold.warning_usd < threshold.critical_usd
        assert threshold.critical_usd < threshold.emergency_usd

    def test_is_anomaly_with_insufficient_data(self):
        engine = ThresholdEngine()
        engine.record_cost("ec2:instance", 100.0)
        # Not enough data for anomaly detection
        assert engine.is_anomaly("ec2:instance", 500.0) is False

    def test_is_anomaly_with_sufficient_data(self):
        engine = ThresholdEngine()
        for cost in [100.0, 110.0, 105.0]:
            engine.record_cost("ec2:instance", cost)
        # 500 is well above 2x the ~105 baseline
        assert engine.is_anomaly("ec2:instance", 500.0) is True

    def test_cost_increase_percentage(self):
        engine = ThresholdEngine()
        engine.record_cost("ec2:instance", 100.0)
        engine.record_cost("ec2:instance", 100.0)
        pct = engine.get_cost_increase_pct("ec2:instance", 200.0)
        assert pct == 100.0  # 100% increase

    def test_cost_increase_no_history(self):
        engine = ThresholdEngine()
        pct = engine.get_cost_increase_pct("ec2:instance", 200.0)
        assert pct == 0.0
