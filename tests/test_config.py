"""Tests for the configuration provider."""

from __future__ import annotations

import os
from unittest.mock import patch

from core.config import HubConfig


class TestHubConfig:
    def test_defaults_loaded(self):
        config = HubConfig()
        assert config.get("hub.version") == "0.3.0"
        assert config.get_float("thresholds.anomaly_multiplier") == 2.0

    def test_get_str(self):
        config = HubConfig()
        assert config.get_str("hub.audit_dir") == "audit_store"

    def test_get_int(self):
        config = HubConfig()
        assert config.get_int("monitoring.poll_interval_seconds") == 900

    def test_get_list(self):
        config = HubConfig()
        tags = config.get_list("tags.required")
        assert "team" in tags
        assert "owner" in tags

    def test_get_required_tags(self):
        config = HubConfig()
        tags = config.get_required_tags()
        assert len(tags) == 4
        assert "cost-centre" in tags

    def test_get_threshold_defaults(self):
        config = HubConfig()
        thresholds = config.get_threshold_defaults()
        assert "ec2:instance" in thresholds
        assert "compute.instances" in thresholds
        assert thresholds["ec2:instance"]["warning"] == 500

    def test_escalation_timeframe(self):
        config = HubConfig()
        assert config.get_escalation_timeframe("emergency") == "1 hour"
        assert config.get_escalation_timeframe("critical") == "24 hours"
        assert config.get_escalation_timeframe("warning") == "7 days"

    def test_env_override(self):
        with patch.dict(os.environ, {"FINOPS_POLL_INTERVAL": "60"}):
            config = HubConfig()
            assert config.get_int("monitoring.poll_interval_seconds") == 60

    def test_env_override_float(self):
        with patch.dict(os.environ, {"FINOPS_ANOMALY_MULTIPLIER": "3.5"}):
            config = HubConfig()
            assert config.get_float("thresholds.anomaly_multiplier") == 3.5

    def test_env_override_list(self):
        with patch.dict(os.environ, {"FINOPS_REQUIRED_TAGS": "team,env,owner"}):
            config = HubConfig()
            tags = config.get_required_tags()
            assert tags == ["team", "env", "owner"]

    def test_set_runtime_override(self):
        config = HubConfig()
        config.set("custom.key", "value")
        assert config.get("custom.key") == "value"

    def test_as_dict(self):
        config = HubConfig()
        d = config.as_dict()
        assert isinstance(d, dict)
        assert "hub.version" in d

    def test_new_resource_type_thresholds(self):
        config = HubConfig()
        thresholds = config.get_threshold_defaults()
        assert "ebs:volume" in thresholds
        assert "run.services" in thresholds
        assert "dataflow.jobs" in thresholds

    def test_missing_key_returns_default(self):
        config = HubConfig()
        assert config.get("nonexistent.key", "fallback") == "fallback"
        assert config.get_str("nonexistent", "fallback") == "fallback"
