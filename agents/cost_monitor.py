"""Cost Monitor Agent.

Continuously polls cloud providers for resource creation events,
enriches them with cost estimates, and feeds them into the alert
and reporting pipeline. Runs on a configurable schedule.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from core.audit import AuditLogger
from core.models import CloudProvider, ResourceCreationEvent
from providers.aws.listener import AWSEventListener
from providers.base import BaseCloudProvider
from providers.gcp.listener import GCPEventListener

__all__ = ["CostMonitorAgent"]

logger = logging.getLogger(__name__)


class CostMonitorAgent:
    """Monitors cloud providers for costly resource creation events."""

    # Maximum number of events to keep in memory for deduplication.
    # Older events are evicted FIFO. Prevents unbounded memory growth
    # during long-running monitoring loops.
    MAX_EVENT_HISTORY = 50_000

    def __init__(
        self,
        audit_logger: AuditLogger,
        aws_config: dict[str, Any] | None = None,
        gcp_config: dict[str, Any] | None = None,
        poll_interval_seconds: int = 900,
    ) -> None:
        self._audit = audit_logger
        self._aws_config = aws_config or {}
        self._gcp_config = gcp_config or {}
        self._poll_interval = poll_interval_seconds
        self._providers: dict[CloudProvider, BaseCloudProvider] = {}
        self._event_history: list[ResourceCreationEvent] = []
        self._running = False

        if aws_config:
            self._providers[CloudProvider.AWS] = AWSEventListener()
        if gcp_config:
            self._providers[CloudProvider.GCP] = GCPEventListener()

    def start(self) -> None:
        """Start the monitoring loop."""
        self._running = True
        self._audit.log(
            action="monitor.started",
            actor="system",
            target="cost_monitor_agent",
            details={
                "providers": [p.value for p in self._providers],
                "poll_interval_seconds": self._poll_interval,
            },
        )
        logger.info(
            "Cost monitor started. Polling %d provider(s) every %ds",
            len(self._providers),
            self._poll_interval,
        )

        while self._running:
            self.poll_once()
            time.sleep(self._poll_interval)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._running = False
        self._audit.log(
            action="monitor.stopped",
            actor="system",
            target="cost_monitor_agent",
        )
        logger.info("Cost monitor stopped")

    def poll_once(self) -> list[ResourceCreationEvent]:
        """Execute a single poll cycle across all configured providers."""
        all_events: list[ResourceCreationEvent] = []

        for provider_type, provider in self._providers.items():
            config = self._aws_config if provider_type == CloudProvider.AWS else self._gcp_config
            try:
                events = provider.listen_for_events(config)
                new_events = self._deduplicate(events)
                all_events.extend(new_events)

                if new_events:
                    self._audit.log(
                        action="monitor.events_detected",
                        actor="system",
                        target=provider_type.value,
                        provider=provider_type,
                        details={
                            "event_count": len(new_events),
                            "resource_types": list({e.resource_type for e in new_events}),
                            "total_estimated_cost": sum(
                                e.estimated_monthly_cost_usd for e in new_events
                            ),
                        },
                    )
                    logger.info(
                        "Detected %d new events from %s",
                        len(new_events),
                        provider_type.value,
                    )
            except Exception:
                logger.exception("Error polling %s", provider_type.value)
                self._audit.log(
                    action="monitor.poll_error",
                    actor="system",
                    target=provider_type.value,
                    provider=provider_type,
                    outcome="failure",
                )

        self._event_history.extend(all_events)

        # Evict oldest events to bound memory usage
        if len(self._event_history) > self.MAX_EVENT_HISTORY:
            self._event_history = self._event_history[-self.MAX_EVENT_HISTORY :]

        return all_events

    def get_recent_events(
        self,
        provider: CloudProvider | None = None,
        limit: int = 50,
    ) -> list[ResourceCreationEvent]:
        """Retrieve recent events with optional provider filter."""
        events = self._event_history
        if provider:
            events = [e for e in events if e.provider == provider]
        return events[-limit:]

    def get_cost_summary(self) -> dict[str, Any]:
        """Get a summary of monitored costs."""
        if not self._event_history:
            return {"total_events": 0, "total_estimated_monthly_cost": 0.0}

        return {
            "total_events": len(self._event_history),
            "total_estimated_monthly_cost": sum(
                e.estimated_monthly_cost_usd for e in self._event_history
            ),
            "by_provider": {
                provider.value: {
                    "events": len([e for e in self._event_history if e.provider == provider]),
                    "cost": sum(
                        e.estimated_monthly_cost_usd
                        for e in self._event_history
                        if e.provider == provider
                    ),
                }
                for provider in CloudProvider
            },
            "by_resource_type": self._group_by_resource_type(),
        }

    def _deduplicate(self, events: list[ResourceCreationEvent]) -> list[ResourceCreationEvent]:
        """Remove events we've already seen."""
        seen_ids = {e.event_id for e in self._event_history}
        seen_resources = {(e.resource_id, e.resource_type) for e in self._event_history}
        new_events = []
        for event in events:
            if (
                event.event_id not in seen_ids
                and (
                    event.resource_id,
                    event.resource_type,
                )
                not in seen_resources
            ):
                new_events.append(event)
        return new_events

    def _group_by_resource_type(self) -> dict[str, dict[str, Any]]:
        groups: dict[str, dict[str, Any]] = {}
        for event in self._event_history:
            rt = event.resource_type
            if rt not in groups:
                groups[rt] = {"count": 0, "total_cost": 0.0}
            groups[rt]["count"] += 1
            groups[rt]["total_cost"] += event.estimated_monthly_cost_usd
        return groups
