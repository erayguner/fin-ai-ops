"""Shared test fixtures and helpers for FinOps Automation Hub tests.

Centralises factory functions so test files do not duplicate boilerplate.
Import directly: ``from tests.conftest import make_event, make_alert``
or let pytest auto-discover fixtures.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from core.models import (
    ActionStatus,
    CloudProvider,
    CostAlert,
    ResourceCreationEvent,
    Severity,
)


def make_event(**overrides: Any) -> ResourceCreationEvent:
    """Create a ``ResourceCreationEvent`` with sensible defaults.

    Any keyword accepted by ``ResourceCreationEvent`` can be passed to
    override the default value.
    """
    defaults: dict[str, Any] = {
        "provider": CloudProvider.AWS,
        "timestamp": datetime.now(UTC),
        "account_id": "123456789012",
        "region": "eu-west-2",
        "resource_type": "ec2:instance",
        "resource_id": "i-abc123",
        "resource_name": "test-instance",
        "creator_identity": "arn:aws:iam::123456789012:user/jane.doe",
        "creator_email": "jane.doe@example.com",
        "estimated_monthly_cost_usd": 500.0,
        "tags": {"team": "platform"},
        "raw_event": {},
    }
    defaults.update(overrides)
    return ResourceCreationEvent(**defaults)


def make_alert(**overrides: Any) -> CostAlert:
    """Create a ``CostAlert`` with sensible defaults.

    Supports a convenience key ``created_hours_ago`` (int) that is
    translated into a ``created_at`` timestamp.
    """
    created_hours_ago = overrides.pop("created_hours_ago", 0)
    defaults: dict[str, Any] = {
        "source_event_id": "evt-1",
        "title": "Test Alert",
        "summary": "test alert",
        "severity": Severity.WARNING,
        "status": ActionStatus.PENDING,
        "provider": CloudProvider.AWS,
        "account_id": "123456789012",
        "region": "eu-west-2",
        "resource_type": "ec2:instance",
        "resource_id": "i-abc123",
        "resource_creator": "arn:aws:iam::123456789012:user/jane.doe",
        "creator_email": "jane.doe@example.com",
        "estimated_monthly_cost_usd": 500.0,
        "threshold_exceeded_usd": 400.0,
        "baseline_monthly_usd": 250.0,
        "cost_increase_percentage": 100.0,
        "accountability_note": "Platform team should review",
        "team": "platform",
        "recommended_actions": ["Review sizing"],
        "created_at": datetime.now(UTC) - timedelta(hours=created_hours_ago),
    }
    defaults.update(overrides)
    return CostAlert(**defaults)
