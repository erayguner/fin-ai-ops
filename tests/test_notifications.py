"""Tests for notification dispatcher abstractions."""

from __future__ import annotations

from core.models import CostAlert, Severity
from core.notifications import (
    CompositeDispatcher,
    LogDispatcher,
    PagerDutyDispatcher,
    SlackDispatcher,
    WebhookDispatcher,
)

from tests.helpers import make_alert


def _make_alert(**kwargs) -> CostAlert:
    defaults = {
        "severity": Severity.CRITICAL,
        "title": "[CRITICAL] Test Alert",
        "summary": "Test summary",
        "estimated_monthly_cost_usd": 3000.0,
        "threshold_exceeded_usd": 2000.0,
        "cost_increase_percentage": 50.0,
        "accountability_note": "jane must review",
        "source_event_id": "evt-123",
        "cost_centre": "CC-001",
    }
    defaults.update(kwargs)
    return make_alert(**defaults)


class TestLogDispatcher:
    def test_always_succeeds(self):
        d = LogDispatcher()
        assert d.send(_make_alert(), "formatted text") is True

    def test_validate_config(self):
        assert LogDispatcher().validate_config() is True

    def test_channel_name(self):
        assert LogDispatcher().channel_name == "log"


class TestWebhookDispatcher:
    def test_validate_config_with_url(self):
        d = WebhookDispatcher("https://example.com/hook")
        assert d.validate_config() is True

    def test_validate_config_empty_url(self):
        d = WebhookDispatcher("")
        assert d.validate_config() is False

    def test_send_blocked_by_ssrf_localhost(self):
        d = WebhookDispatcher("http://localhost:1/nonexistent", timeout=1, allow_http=True)
        result = d.send(_make_alert(), "text")
        assert result is False

    def test_send_blocked_by_ssrf_metadata(self):
        d = WebhookDispatcher("http://169.254.169.254/latest/meta-data/", allow_http=True)
        result = d.send(_make_alert(), "text")
        assert result is False

    def test_send_empty_url_returns_false(self):
        d = WebhookDispatcher("")
        assert d.send(_make_alert(), "text") is False

    def test_validate_rejects_private_ip(self):
        d = WebhookDispatcher("https://192.168.1.1/hook")
        assert d.validate_config() is False

    def test_validate_rejects_http_by_default(self):
        d = WebhookDispatcher("http://example.com/hook")
        assert d.validate_config() is False


class TestSlackDispatcher:
    def test_validate_config(self):
        d = SlackDispatcher("https://hooks.slack.com/services/xxx")
        assert d.validate_config() is True

    def test_channel_name(self):
        d = SlackDispatcher("https://hooks.slack.com/services/xxx", channel="#finops")
        assert d.channel_name == "slack:#finops"

    def test_severity_colours(self):
        assert Severity.EMERGENCY in SlackDispatcher.SEVERITY_COLOURS
        assert Severity.WARNING in SlackDispatcher.SEVERITY_COLOURS

    def test_send_blocks_non_slack_url(self):
        d = SlackDispatcher("https://evil.com/steal-webhook-data")
        result = d.send(_make_alert(), "text")
        assert result is False


class TestPagerDutyDispatcher:
    def test_validate_config(self):
        d = PagerDutyDispatcher("routing-key-123")
        assert d.validate_config() is True

    def test_validate_empty_key(self):
        d = PagerDutyDispatcher("")
        assert d.validate_config() is False

    def test_severity_mapping(self):
        assert PagerDutyDispatcher.SEVERITY_MAP[Severity.EMERGENCY] == "critical"
        assert PagerDutyDispatcher.SEVERITY_MAP[Severity.CRITICAL] == "error"


class TestCompositeDispatcher:
    def test_dispatches_to_all(self):
        results = []

        class TrackingDispatcher(LogDispatcher):
            def send(self, alert, text):
                results.append(True)
                return True

        composite = CompositeDispatcher([TrackingDispatcher(), TrackingDispatcher()])
        composite.send(_make_alert(), "text")
        assert len(results) == 2

    def test_empty_dispatchers_falls_back_to_log(self):
        composite = CompositeDispatcher([])
        assert composite.send(_make_alert(), "text") is True

    def test_one_failure_doesnt_block_others(self):
        class FailDispatcher(LogDispatcher):
            def send(self, alert, text):
                raise RuntimeError("fail")

        composite = CompositeDispatcher([FailDispatcher(), LogDispatcher()])
        assert composite.send(_make_alert(), "text") is True

    def test_validate_config(self):
        composite = CompositeDispatcher([LogDispatcher()])
        assert composite.validate_config() is True
