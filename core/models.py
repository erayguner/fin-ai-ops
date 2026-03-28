"""Data models for the FinOps Automation Hub.

All models use Pydantic for validation and serialisation,
ensuring auditability and type safety at system boundaries.

Schema version is embedded in every model for forward-compatible
serialisation. Consumers should check schema_version before deserialising.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "2"

__all__ = [
    "SCHEMA_VERSION",
    "ActionStatus",
    "AuditEntry",
    "CloudProvider",
    "CostAlert",
    "CostPolicy",
    "CostReport",
    "CostThreshold",
    "ResourceCreationEvent",
    "Severity",
]


class CloudProvider(StrEnum):
    AWS = "aws"
    GCP = "gcp"
    AZURE = "azure"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    EMERGENCY = "emergency"


class ActionStatus(StrEnum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class ResourceCreationEvent(BaseModel):
    """Represents a cloud resource creation event captured from provider audit logs."""

    schema_version: str = Field(default=SCHEMA_VERSION, description="Model schema version")
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="End-to-end trace ID linking event → alert → audit → dispatch",
    )
    provider: CloudProvider
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    account_id: str = Field(description="AWS Account ID or GCP Project ID")
    region: str
    resource_type: str = Field(description="e.g. ec2:instance, compute.instances")
    resource_id: str
    resource_name: str = ""
    creator_identity: str = Field(description="IAM principal who created the resource")
    creator_email: str = ""
    estimated_monthly_cost_usd: float = 0.0
    purchase_type: str = Field(
        default="on-demand",
        description="Purchase type: on-demand, spot, preemptible, reserved",
    )
    tags: dict[str, str] = Field(default_factory=dict)
    raw_event: dict[str, Any] = Field(default_factory=dict)


class CostThreshold(BaseModel):
    """Dynamic cost threshold for triggering alerts."""

    threshold_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: CloudProvider
    resource_type: str
    warning_usd: float = Field(description="Monthly cost that triggers a warning")
    critical_usd: float = Field(description="Monthly cost that triggers critical alert")
    emergency_usd: float = Field(description="Monthly cost that triggers emergency escalation")
    baseline_monthly_usd: float = Field(
        description="Rolling average monthly cost for this resource type"
    )
    anomaly_multiplier: float = Field(
        default=2.0, description="How many times above baseline triggers anomaly"
    )
    last_updated: datetime = Field(default_factory=lambda: datetime.now(UTC))


class CostAlert(BaseModel):
    """A fully contextualised, human-readable cost alert."""

    schema_version: str = Field(default=SCHEMA_VERSION, description="Model schema version")
    alert_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    severity: Severity
    provider: CloudProvider
    account_id: str
    region: str

    # What happened
    title: str
    summary: str = Field(description="Plain-English summary requiring no further investigation")

    # Who is accountable
    resource_creator: str
    creator_email: str
    team: str = ""
    cost_centre: str = ""

    # Cost impact
    resource_type: str
    resource_id: str
    resource_name: str = ""
    estimated_monthly_cost_usd: float
    threshold_exceeded_usd: float
    baseline_monthly_usd: float
    cost_increase_percentage: float

    # Recommendations
    recommended_actions: list[str] = Field(description="Ordered list of recommended next steps")
    accountability_note: str = Field(
        description="Clear statement of who is responsible and what they should do"
    )
    escalation_path: str = ""

    # Status
    status: ActionStatus = ActionStatus.PENDING
    acknowledged_by: str = ""
    resolved_by: str = ""

    # Audit / tracing
    source_event_id: str
    correlation_id: str = Field(
        default="",
        description="End-to-end trace ID inherited from the source event",
    )
    policy_id: str = ""


class CostPolicy(BaseModel):
    """Defines a cost governance policy."""

    policy_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str
    provider: CloudProvider | None = None
    resource_types: list[str] = Field(default_factory=list)
    max_monthly_cost_usd: float | None = None
    require_tags: list[str] = Field(
        default_factory=list, description="Tags that must be present on resources"
    )
    require_approval_above_usd: float | None = Field(
        default=None, description="Cost threshold requiring manual approval"
    )
    auto_actions: list[str] = Field(
        default_factory=list,
        description="Actions to take automatically (e.g. notify, tag, restrict, auto_stop)",
    )
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # --- Extended policy fields (2026 FinOps best practices) ---
    blocked_regions: list[str] = Field(
        default_factory=list,
        description="Regions where resource creation is forbidden (e.g. high-carbon regions)",
    )
    preferred_regions: list[str] = Field(
        default_factory=list,
        description="Preferred regions for sustainability or cost; violations are warnings",
    )
    required_purchase_type: str | None = Field(
        default=None,
        description="Required purchase type for matching resources: spot, preemptible, reserved",
    )
    schedule: dict[str, str] = Field(
        default_factory=dict,
        description="Time-based schedule e.g. {'active_hours': '07:00-19:00', 'active_days': 'mon-fri'}",
    )
    min_commitment_coverage_pct: float | None = Field(
        default=None,
        description="Minimum RI/Savings Plan coverage percentage (evaluated at account level)",
    )
    acknowledgement_sla_hours: int | None = Field(
        default=None,
        description="Max hours to acknowledge a cost alert before auto-escalation",
    )
    resolution_sla_hours: int | None = Field(
        default=None,
        description="Max hours to resolve a cost alert before auto-escalation",
    )
    max_account_monthly_budget_usd: float | None = Field(
        default=None,
        description="Hard budget ceiling per AWS account or GCP project",
    )
    unit_cost_metric: str | None = Field(
        default=None,
        description="Metric name for unit economics tracking (e.g. cost-per-request)",
    )
    unit_cost_threshold_usd: float | None = Field(
        default=None,
        description="Maximum acceptable cost per unit for unit economics policies",
    )


class AuditEntry(BaseModel):
    """Immutable audit log entry for all hub actions."""

    schema_version: str = Field(default=SCHEMA_VERSION, description="Model schema version")
    audit_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: str = Field(description="What action was taken")
    actor: str = Field(description="Who or what initiated the action (system/user)")
    target: str = Field(description="What was acted upon")
    provider: CloudProvider | None = None
    correlation_id: str = Field(
        default="",
        description="End-to-end trace ID linking related operations",
    )
    causation_id: str = Field(
        default="",
        description="ID of the event/action that directly caused this entry",
    )
    details: dict[str, Any] = Field(default_factory=dict)
    outcome: str = Field(default="success", description="success | failure | skipped")
    related_alert_id: str = ""
    related_policy_id: str = ""
    checksum: str = Field(default="", description="SHA-256 of entry for tamper detection")


class CostReport(BaseModel):
    """Periodic cost report with trends and recommendations."""

    report_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    period_start: datetime
    period_end: datetime
    provider: CloudProvider | None = None
    total_cost_usd: float
    cost_by_resource_type: dict[str, float] = Field(default_factory=dict)
    cost_by_team: dict[str, float] = Field(default_factory=dict)
    cost_by_account: dict[str, float] = Field(default_factory=dict)
    top_cost_creators: list[dict[str, Any]] = Field(default_factory=list)
    anomalies_detected: int = 0
    alerts_generated: int = 0
    alerts_resolved: int = 0
    trend_vs_previous_period_pct: float = 0.0
    recommendations: list[str] = Field(default_factory=list)
    accountability_summary: list[dict[str, str]] = Field(
        default_factory=list,
        description="Per-team/person accountability breakdown",
    )
