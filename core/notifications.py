"""Notification dispatcher abstraction.

Pluggable backends for sending cost alerts to different channels.
Ships with webhook, Slack, and PagerDuty dispatchers. Additional
backends can be added by implementing BaseNotificationDispatcher.

All dispatchers that make outbound HTTP requests validate URLs against
SSRF protections (no internal IPs, metadata endpoints, or private ranges).
"""

from __future__ import annotations

import json
import logging
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from .models import CostAlert, Severity
from .validation import ValidationError, validate_webhook_url

logger = logging.getLogger(__name__)

__all__ = [
    "BaseNotificationDispatcher",
    "CompositeDispatcher",
    "LogDispatcher",
    "PagerDutyDispatcher",
    "SlackDispatcher",
    "WebhookDispatcher",
]


class BaseNotificationDispatcher(ABC):
    """Abstract interface for alert notification delivery."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Human-readable channel name for logging."""

    @abstractmethod
    def send(self, alert: CostAlert, formatted_text: str) -> bool:
        """Send an alert. Returns True on success."""

    @abstractmethod
    def validate_config(self) -> bool:
        """Check that this dispatcher is correctly configured."""


class WebhookDispatcher(BaseNotificationDispatcher):
    """Sends alerts to an HTTP webhook endpoint.

    URLs are validated against SSRF protections on construction.
    Only HTTPS is allowed by default; set allow_http=True for development.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: int = 10,
        allow_http: bool = False,
    ) -> None:
        self._url = url
        self._allow_http = allow_http
        # Validate URL on construction (skip empty — handled by send/validate_config)
        if url:
            try:
                validate_webhook_url(url, allow_http=allow_http)
            except ValidationError:
                logger.warning("Webhook URL failed SSRF validation: will be rejected on send")
        self._headers = headers or {}
        self._timeout = timeout

    @property
    def channel_name(self) -> str:
        return f"webhook:{self._url}"

    def send(self, alert: CostAlert, formatted_text: str) -> bool:
        if not self._url:
            return False
        try:
            validate_webhook_url(self._url, allow_http=self._allow_http)
        except ValidationError as e:
            logger.warning("Webhook blocked by SSRF protection: %s", e)
            return False

        payload = json.dumps(alert.model_dump(), default=str).encode()
        headers = {"Content-Type": "application/json", **self._headers}
        req = urllib.request.Request(self._url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                logger.info("Webhook sent for alert %s: %d", alert.alert_id, resp.status)
                return resp.status < 400
        except Exception:
            logger.exception("Webhook failed for alert %s", alert.alert_id)
            return False

    def validate_config(self) -> bool:
        if not self._url:
            return False
        try:
            validate_webhook_url(self._url, allow_http=self._allow_http)
            return True
        except ValidationError:
            return False


class SlackDispatcher(BaseNotificationDispatcher):
    """Sends alerts to a Slack channel via incoming webhook.

    Only accepts hooks.slack.com URLs to prevent SSRF via Slack config.
    """

    SEVERITY_COLOURS: ClassVar[dict[Severity, str]] = {
        Severity.EMERGENCY: "#FF0000",
        Severity.CRITICAL: "#FF6600",
        Severity.WARNING: "#FFCC00",
        Severity.INFO: "#36A64F",
    }

    def __init__(self, webhook_url: str, *, channel: str = "", timeout: int = 10) -> None:
        self._webhook_url = webhook_url
        self._channel = channel
        self._timeout = timeout
        # Validate Slack webhook URL format
        if webhook_url and not webhook_url.startswith("https://hooks.slack.com/"):
            logger.warning("Slack webhook URL doesn't match hooks.slack.com — may be rejected")

    @property
    def channel_name(self) -> str:
        return f"slack:{self._channel or 'default'}"

    def send(self, alert: CostAlert, formatted_text: str) -> bool:
        if not self._webhook_url:
            return False

        # SSRF: only allow hooks.slack.com
        if not self._webhook_url.startswith("https://hooks.slack.com/"):
            logger.warning("Slack webhook blocked: URL must start with https://hooks.slack.com/")
            return False

        colour = self.SEVERITY_COLOURS.get(alert.severity, "#CCCCCC")
        payload: dict[str, Any] = {
            "attachments": [
                {
                    "color": colour,
                    "title": alert.title,
                    "text": alert.summary,
                    "fields": [
                        {"title": "Severity", "value": alert.severity.value.upper(), "short": True},
                        {
                            "title": "Cost",
                            "value": f"${alert.estimated_monthly_cost_usd:,.2f}/mo",
                            "short": True,
                        },
                        {
                            "title": "Creator",
                            "value": alert.creator_email or alert.resource_creator,
                            "short": True,
                        },
                        {"title": "Team", "value": alert.team, "short": True},
                        {
                            "title": "Resource",
                            "value": f"{alert.resource_type} [{alert.resource_id}]",
                            "short": False,
                        },
                    ],
                    "footer": f"FinOps Hub | Alert {alert.alert_id}",
                }
            ]
        }
        if self._channel:
            payload["channel"] = self._channel

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                logger.info("Slack sent for alert %s", alert.alert_id)
                return resp.status < 400
        except Exception:
            logger.exception("Slack failed for alert %s", alert.alert_id)
            return False

    def validate_config(self) -> bool:
        return bool(self._webhook_url)


class PagerDutyDispatcher(BaseNotificationDispatcher):
    """Sends alerts to PagerDuty via Events API v2."""

    SEVERITY_MAP: ClassVar[dict[Severity, str]] = {
        Severity.EMERGENCY: "critical",
        Severity.CRITICAL: "error",
        Severity.WARNING: "warning",
        Severity.INFO: "info",
    }

    def __init__(self, routing_key: str, *, timeout: int = 10) -> None:
        self._routing_key = routing_key
        self._timeout = timeout

    @property
    def channel_name(self) -> str:
        return "pagerduty"

    def send(self, alert: CostAlert, formatted_text: str) -> bool:
        if not self._routing_key:
            return False

        pd_severity = self.SEVERITY_MAP.get(alert.severity, "warning")
        payload = {
            "routing_key": self._routing_key,
            "event_action": "trigger",
            "dedup_key": alert.alert_id,
            "payload": {
                "summary": alert.title,
                "source": "finops-automation-hub",
                "severity": pd_severity,
                "custom_details": {
                    "cost": f"${alert.estimated_monthly_cost_usd:,.2f}/month",
                    "creator": alert.creator_email or alert.resource_creator,
                    "team": alert.team,
                    "resource": f"{alert.resource_type} [{alert.resource_id}]",
                    "region": alert.region,
                    "account": alert.account_id,
                    "recommendations": alert.recommended_actions,
                },
            },
        }

        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            "https://events.pagerduty.com/v2/enqueue",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # nosec B310
                logger.info("PagerDuty sent for alert %s", alert.alert_id)
                return resp.status < 400
        except Exception:
            logger.exception("PagerDuty failed for alert %s", alert.alert_id)
            return False

    def validate_config(self) -> bool:
        return bool(self._routing_key)


class LogDispatcher(BaseNotificationDispatcher):
    """Logs alerts to the Python logger (always available, for dev/fallback)."""

    @property
    def channel_name(self) -> str:
        return "log"

    def send(self, alert: CostAlert, formatted_text: str) -> bool:
        logger.info("COST ALERT:\n%s", formatted_text)
        return True

    def validate_config(self) -> bool:
        return True


class CompositeDispatcher(BaseNotificationDispatcher):
    """Dispatches to multiple channels with dead-letter queue for failures.

    Failed dispatches are captured in a dead-letter queue and can be
    retried later via retry_dead_letters(). This prevents silent alert loss.

    Guards against resource exhaustion:
    - Dead-letter queue is bounded to MAX_DEAD_LETTERS entries (oldest dropped).
    - Each dead-letter entry tracks retry attempts; entries exceeding
      MAX_RETRY_ATTEMPTS are discarded on the next retry pass.
    """

    MAX_DEAD_LETTERS = 1_000
    MAX_RETRY_ATTEMPTS = 3

    def __init__(self, dispatchers: list[BaseNotificationDispatcher]) -> None:
        self._dispatchers = dispatchers
        self._dead_letters: list[dict[str, Any]] = []

    @property
    def channel_name(self) -> str:
        return "composite"

    def send(self, alert: CostAlert, formatted_text: str) -> bool:
        if not self._dispatchers:
            LogDispatcher().send(alert, formatted_text)
            return True
        success = False
        for dispatcher in self._dispatchers:
            try:
                if dispatcher.send(alert, formatted_text):
                    success = True
                else:
                    self._enqueue_dead_letter(
                        alert,
                        formatted_text,
                        dispatcher.channel_name,
                        "send returned False",
                    )
            except Exception as e:
                logger.exception("Dispatcher %s failed", dispatcher.channel_name)
                self._enqueue_dead_letter(
                    alert,
                    formatted_text,
                    dispatcher.channel_name,
                    str(e),
                )
        return success

    def _enqueue_dead_letter(
        self,
        alert: CostAlert,
        formatted_text: str,
        channel: str,
        reason: str,
    ) -> None:
        """Add a failed dispatch to the dead-letter queue (bounded)."""
        if len(self._dead_letters) >= self.MAX_DEAD_LETTERS:
            dropped = self._dead_letters.pop(0)
            logger.warning(
                "Dead-letter queue full (%d); dropping oldest entry for alert %s",
                self.MAX_DEAD_LETTERS,
                dropped["alert_id"],
            )
        self._dead_letters.append(
            {
                "alert_id": alert.alert_id,
                "channel": channel,
                "alert": alert,
                "formatted_text": formatted_text,
                "reason": reason,
                "retry_count": 0,
            }
        )

    def retry_dead_letters(self) -> dict[str, int]:
        """Retry dead-lettered dispatches. Returns {retried, succeeded, failed, expired}.

        Each entry is attempted up to MAX_RETRY_ATTEMPTS times.  Entries that
        exceed the limit are discarded to prevent infinite retry loops.
        """
        if not self._dead_letters:
            return {"retried": 0, "succeeded": 0, "failed": 0, "expired": 0}

        retried = len(self._dead_letters)
        succeeded = 0
        expired = 0
        still_failed: list[dict[str, Any]] = []

        dispatcher_map = {d.channel_name: d for d in self._dispatchers}

        for entry in self._dead_letters:
            # Discard entries that have exhausted their retry budget
            if entry.get("retry_count", 0) >= self.MAX_RETRY_ATTEMPTS:
                expired += 1
                logger.warning(
                    "Dead-letter expired after %d retries: alert %s on %s",
                    entry["retry_count"],
                    entry["alert_id"],
                    entry["channel"],
                )
                continue

            dispatcher = dispatcher_map.get(entry["channel"])
            if dispatcher is None:
                entry["retry_count"] = entry.get("retry_count", 0) + 1
                still_failed.append(entry)
                continue
            try:
                if dispatcher.send(entry["alert"], entry["formatted_text"]):
                    succeeded += 1
                else:
                    entry["retry_count"] = entry.get("retry_count", 0) + 1
                    still_failed.append(entry)
            except Exception:
                entry["retry_count"] = entry.get("retry_count", 0) + 1
                still_failed.append(entry)

        self._dead_letters = still_failed
        logger.info(
            "Dead-letter retry: %d retried, %d succeeded, %d still failed, %d expired",
            retried,
            succeeded,
            len(still_failed),
            expired,
        )
        return {
            "retried": retried,
            "succeeded": succeeded,
            "failed": len(still_failed),
            "expired": expired,
        }

    @property
    def dead_letter_count(self) -> int:
        return len(self._dead_letters)

    def validate_config(self) -> bool:
        return all(d.validate_config() for d in self._dispatchers)

    @property
    def dispatchers(self) -> list[BaseNotificationDispatcher]:
        return list(self._dispatchers)
