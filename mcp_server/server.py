"""FinOps Automation Hub MCP Server.

A Model Context Protocol server that exposes FinOps management tools.
All operations are audited. Designed for integration with Claude Code
or any MCP-compatible client.

Usage:
    python -m mcp_server.server

Configuration via environment variables:
    FINOPS_AUDIT_DIR    - Directory for audit logs (default: ./audit_store)
    FINOPS_POLICY_DIR   - Directory for policies (default: ./policies)
    FINOPS_AWS_REGIONS  - Comma-separated AWS regions to monitor
    FINOPS_GCP_PROJECTS - Comma-separated GCP project IDs to monitor
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.alert_agent import AlertAgent
from agents.health_agent import HealthCheckAgent
from agents.reconciliation_agent import ReconciliationAgent
from agents.report_agent import ReportAgent
from core.audit import AuditLogger
from core.config import HubConfig
from core.event_store import InMemoryEventStore, SQLiteEventStore
from core.models import (
    ActionStatus,
    CloudProvider,
    CostPolicy,
    ResourceCreationEvent,
    Severity,
)
from core.notifications import (
    CompositeDispatcher,
    LogDispatcher,
    PagerDutyDispatcher,
    SlackDispatcher,
    WebhookDispatcher,
)
from core.policies import PolicyEngine
from core.pricing import LocalPricingService
from core.thresholds import ThresholdEngine
from core.validation import (
    ValidationError,
    safe_error_message,
    sanitise_string,
    validate_account_id,
    validate_cost,
    validate_dict_depth,
    validate_email,
    validate_provider,
    validate_query_limit,
    validate_resource_id,
    validate_resource_type,
    validate_severity,
    validate_status,
    validate_tags,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MCP_TOOLS",
    "handle_tool_call",
    "list_tools",
    "run_stdio_server",
]

# ---------------------------------------------------------------------------
# Hub singleton -- wired up once at import time
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent
hub_config = HubConfig()

_AUDIT_DIR = hub_config.get_str("hub.audit_dir", str(_BASE_DIR / "audit_store"))
_POLICY_DIR = hub_config.get_str("hub.policy_dir", str(_BASE_DIR / "policies"))

audit_logger = AuditLogger(_AUDIT_DIR)
threshold_engine = ThresholdEngine(config=hub_config)
policy_engine = PolicyEngine(_POLICY_DIR, audit_logger)
pricing_service = LocalPricingService()

# Event store — configurable backend
_event_store_backend = hub_config.get_str("hub.event_store_backend", "memory")
if _event_store_backend == "sqlite":
    event_store = SQLiteEventStore(hub_config.get_str("hub.event_store_path", "events.db"))
else:
    event_store = InMemoryEventStore()

# Notification dispatchers — built from config
_dispatchers = []
_slack_url = hub_config.get_str("notifications.slack_webhook")
if _slack_url:
    _dispatchers.append(SlackDispatcher(_slack_url))
_pd_key = hub_config.get_str("notifications.pagerduty_routing_key")
if _pd_key:
    _dispatchers.append(PagerDutyDispatcher(_pd_key))
_webhook_url = hub_config.get_str("notifications.webhook_url")
if _webhook_url:
    _dispatchers.append(WebhookDispatcher(_webhook_url))
if not _dispatchers:
    _dispatchers.append(LogDispatcher())
dispatcher = CompositeDispatcher(_dispatchers)

alert_agent = AlertAgent(
    threshold_engine,
    policy_engine,
    audit_logger,
    event_store=event_store,
    dispatcher=dispatcher,
    config=hub_config,
)
report_agent = ReportAgent(audit_logger)

# Self-healing agents
health_agent = HealthCheckAgent(
    event_store=event_store,
    audit_dir=Path(_AUDIT_DIR),
    policy_dir=Path(_POLICY_DIR),
    dispatchers=_dispatchers,
)
reconciliation_agent = ReconciliationAgent(
    event_store=event_store,
    audit_logger=audit_logger,
)


def _load_state() -> None:
    """Load persisted state on startup."""
    audit_logger.load_from_disk()
    policy_engine.load_policies()


_load_state()


# ---------------------------------------------------------------------------
# MCP Tool definitions
#
# Each function below represents an MCP tool. The docstring becomes the tool
# description; the parameters become the tool's input schema.
#
# All inputs from external callers are validated before reaching core logic.
# ---------------------------------------------------------------------------


# ===== Policy Management Tools =====


def finops_create_policy(
    name: str,
    description: str,
    provider: str | None = None,
    resource_types: list[str] | None = None,
    max_monthly_cost_usd: float | None = None,
    require_tags: list[str] | None = None,
    require_approval_above_usd: float | None = None,
    auto_actions: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new FinOps cost governance policy.

    Policies are evaluated against every resource creation event to determine
    whether alerts, tag compliance, or approval workflows are triggered.
    """
    # Validate inputs
    name = sanitise_string(name, "name", max_length=256)
    description = sanitise_string(description, "description", max_length=2048)
    if provider:
        provider = validate_provider(provider)
    if resource_types:
        if len(resource_types) > 50:
            raise ValidationError("resource_types: exceeds maximum of 50")
        resource_types = [validate_resource_type(rt) for rt in resource_types]
    if max_monthly_cost_usd is not None:
        max_monthly_cost_usd = validate_cost(max_monthly_cost_usd, "max_monthly_cost_usd")
    if require_tags:
        if len(require_tags) > 50:
            raise ValidationError("require_tags: exceeds maximum of 50")
        require_tags = [sanitise_string(t, "tag", max_length=128) for t in require_tags]
    if require_approval_above_usd is not None:
        require_approval_above_usd = validate_cost(
            require_approval_above_usd, "require_approval_above_usd"
        )
    if auto_actions:
        if len(auto_actions) > 20:
            raise ValidationError("auto_actions: exceeds maximum of 20")
        auto_actions = [sanitise_string(a, "action", max_length=256) for a in auto_actions]

    policy = CostPolicy(
        name=name,
        description=description,
        provider=CloudProvider(provider) if provider else None,
        resource_types=resource_types or [],
        max_monthly_cost_usd=max_monthly_cost_usd,
        require_tags=require_tags or [],
        require_approval_above_usd=require_approval_above_usd,
        auto_actions=auto_actions or [],
    )
    created = policy_engine.create_policy(policy, actor="mcp_user")
    return {"status": "created", "policy": created.model_dump(mode="json")}


def finops_list_policies(
    provider: str | None = None,
    enabled_only: bool = True,
) -> dict[str, Any]:
    """List all FinOps cost governance policies."""
    if provider:
        provider = validate_provider(provider)
    p = CloudProvider(provider) if provider else None
    policies = policy_engine.get_policies(provider=p, enabled_only=enabled_only)
    return {
        "count": len(policies),
        "policies": [pol.model_dump(mode="json") for pol in policies],
    }


def finops_update_policy(
    policy_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Update an existing cost governance policy."""
    policy_id = sanitise_string(policy_id, "policy_id", max_length=256)
    validate_dict_depth(updates, "updates", max_depth=3)
    updated = policy_engine.update_policy(policy_id, updates, actor="mcp_user")
    if updated is None:
        return {"status": "error", "message": f"Policy {policy_id} not found"}
    return {"status": "updated", "policy": updated.model_dump(mode="json")}


def finops_delete_policy(policy_id: str) -> dict[str, Any]:
    """Delete a cost governance policy."""
    policy_id = sanitise_string(policy_id, "policy_id", max_length=256)
    deleted = policy_engine.delete_policy(policy_id, actor="mcp_user")
    return {
        "status": "deleted" if deleted else "not_found",
        "policy_id": policy_id,
    }


# ===== Alert Management Tools =====


def finops_evaluate_resource(
    provider: str,
    account_id: str,
    region: str,
    resource_type: str,
    resource_id: str,
    estimated_monthly_cost_usd: float,
    creator_identity: str,
    creator_email: str = "",
    resource_name: str = "",
    tags: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Evaluate a resource creation event against policies and thresholds.

    Returns a fully contextualised alert if the resource exceeds any threshold
    or violates a policy, otherwise returns a clean status.
    """
    # Validate all inputs
    provider = validate_provider(provider)
    account_id = validate_account_id(account_id)
    region = sanitise_string(region, "region", max_length=64)
    resource_type = validate_resource_type(resource_type)
    resource_id = validate_resource_id(resource_id)
    estimated_monthly_cost_usd = validate_cost(
        estimated_monthly_cost_usd, "estimated_monthly_cost_usd"
    )
    creator_identity = sanitise_string(creator_identity, "creator_identity", max_length=512)
    creator_email = validate_email(creator_email)
    resource_name = sanitise_string(resource_name, "resource_name", max_length=256)
    tags = validate_tags(tags)

    event = ResourceCreationEvent(
        provider=CloudProvider(provider),
        account_id=account_id,
        region=region,
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name=resource_name,
        creator_identity=creator_identity,
        creator_email=creator_email,
        estimated_monthly_cost_usd=estimated_monthly_cost_usd,
        tags=tags,
    )

    alert = alert_agent.process_event(event)
    if alert:
        human_readable = AlertAgent.format_alert_for_humans(alert)
        return {
            "status": "alert_generated",
            "alert": alert.model_dump(mode="json"),
            "human_readable": human_readable,
        }
    return {
        "status": "within_thresholds",
        "resource_id": resource_id,
        "estimated_cost": estimated_monthly_cost_usd,
        "message": "Resource cost is within acceptable thresholds.",
    }


def finops_list_alerts(
    severity: str | None = None,
    status: str | None = None,
    provider: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List cost alerts with optional filters."""
    if severity:
        severity = validate_severity(severity)
    if status:
        status = validate_status(status)
    if provider:
        provider = validate_provider(provider)
    limit = validate_query_limit(limit)

    alerts = alert_agent.get_alerts(
        severity=Severity(severity) if severity else None,
        status=ActionStatus(status) if status else None,
        provider=CloudProvider(provider) if provider else None,
        limit=limit,
    )
    return {
        "count": len(alerts),
        "alerts": [a.model_dump(mode="json") for a in alerts],
    }


def finops_acknowledge_alert(
    alert_id: str,
    acknowledged_by: str,
) -> dict[str, Any]:
    """Acknowledge a cost alert. Records who took ownership."""
    alert_id = sanitise_string(alert_id, "alert_id", max_length=256)
    acknowledged_by = sanitise_string(acknowledged_by, "acknowledged_by", max_length=256)
    alert = alert_agent.acknowledge_alert(alert_id, acknowledged_by)
    if alert is None:
        return {"status": "error", "message": f"Alert {alert_id} not found"}
    return {"status": "acknowledged", "alert": alert.model_dump(mode="json")}


def finops_resolve_alert(
    alert_id: str,
    resolved_by: str,
    resolution_notes: str = "",
) -> dict[str, Any]:
    """Resolve a cost alert with notes on what action was taken."""
    alert_id = sanitise_string(alert_id, "alert_id", max_length=256)
    resolved_by = sanitise_string(resolved_by, "resolved_by", max_length=256)
    resolution_notes = sanitise_string(resolution_notes, "resolution_notes", max_length=2048)
    alert = alert_agent.resolve_alert(alert_id, resolved_by, resolution_notes)
    if alert is None:
        return {"status": "error", "message": f"Alert {alert_id} not found"}
    return {"status": "resolved", "alert": alert.model_dump(mode="json")}


def finops_alert_stats() -> dict[str, Any]:
    """Get summary statistics for all cost alerts."""
    return alert_agent.get_alert_stats()


# ===== Report Tools =====


def finops_generate_report(
    period_start: str,
    period_end: str,
    provider: str | None = None,
) -> dict[str, Any]:
    """Generate a FinOps cost report for a given period.

    Returns both structured data and a human-readable formatted report.
    """
    period_start = sanitise_string(period_start, "period_start", max_length=64)
    period_end = sanitise_string(period_end, "period_end", max_length=64)
    if provider:
        provider = validate_provider(provider)

    start = datetime.fromisoformat(period_start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(period_end).replace(tzinfo=UTC)
    prov = CloudProvider(provider) if provider else None

    events = event_store.query(provider=prov, since=start, until=end, limit=10000)
    alerts = alert_agent.get_alerts(provider=prov, limit=1000)

    report = report_agent.generate_report(events, alerts, start, end, prov)
    human_readable = report_agent.format_report_for_humans(report)

    return {
        "report": report.model_dump(mode="json"),
        "human_readable": human_readable,
    }


# ===== Audit Tools =====


def finops_query_audit(
    action: str | None = None,
    actor: str | None = None,
    provider: str | None = None,
    since: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Query the FinOps audit trail.

    Every policy change, alert, and agent action is recorded with
    tamper-detection checksums for governance compliance.
    """
    if action:
        action = sanitise_string(action, "action", max_length=256)
    if actor:
        actor = sanitise_string(actor, "actor", max_length=256)
    if provider:
        provider = validate_provider(provider)
    if since:
        since = sanitise_string(since, "since", max_length=64)
    limit = validate_query_limit(limit)

    since_dt = datetime.fromisoformat(since).replace(tzinfo=UTC) if since else None
    prov = CloudProvider(provider) if provider else None
    entries = audit_logger.get_entries(
        action=action, actor=actor, provider=prov, since=since_dt, limit=limit
    )
    return {
        "count": len(entries),
        "entries": [e.model_dump(mode="json") for e in entries],
    }


def finops_verify_audit_integrity() -> dict[str, Any]:
    """Verify the integrity of the audit trail.

    Checks the chained SHA-256 checksums to detect any tampering.
    Returns any violations found.
    """
    violations = audit_logger.verify_integrity()
    return {
        "status": "intact" if not violations else "violations_detected",
        "total_entries": len(audit_logger.get_entries(limit=10000)),
        "violations": violations,
    }


def finops_export_audit(
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Export audit entries in a compliance-friendly format."""
    if since:
        since = sanitise_string(since, "since", max_length=64)
    if until:
        until = sanitise_string(until, "until", max_length=64)

    since_dt = datetime.fromisoformat(since).replace(tzinfo=UTC) if since else None
    until_dt = datetime.fromisoformat(until).replace(tzinfo=UTC) if until else None
    entries = audit_logger.export_for_compliance(since=since_dt, until=until_dt)
    return {"count": len(entries), "entries": entries}


# ===== Cost Estimation Tools =====


def finops_estimate_cost(
    provider: str,
    resource_type: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    """Estimate the monthly cost of a cloud resource.

    Provide the provider (aws/gcp), resource type, and configuration
    to get an estimated monthly cost in USD.
    """
    provider = validate_provider(provider)
    resource_type = validate_resource_type(resource_type)
    validate_dict_depth(config, "config", max_depth=3)

    cost = pricing_service.get_monthly_cost(provider, resource_type, config)
    return {
        "provider": provider,
        "resource_type": resource_type,
        "config": config,
        "estimated_monthly_cost_usd": cost,
    }


# ===== Hub Management Tools =====


def finops_hub_status() -> dict[str, Any]:
    """Get the current status of the FinOps Automation Hub."""
    return {
        "status": "running",
        "version": hub_config.get_str("hub.version", "0.3.0"),
        "policies_loaded": len(policy_engine.get_policies(enabled_only=False)),
        "alert_stats": alert_agent.get_alert_stats(),
        "events_stored": event_store.count(),
        "event_store_backend": _event_store_backend,
        "audit_entries": len(audit_logger.get_entries(limit=10000)),
        "audit_integrity": "intact"
        if not audit_logger.verify_integrity()
        else "violations_detected",
        "notification_channels": [d.channel_name for d in dispatcher.dispatchers],
        "providers_configured": {
            "aws": bool(os.environ.get("FINOPS_AWS_REGIONS")),
            "gcp": bool(os.environ.get("FINOPS_GCP_PROJECTS")),
        },
    }


# ===== Self-Healing & Operations Tools =====


def finops_health_check() -> dict[str, Any]:
    """Run a deep health check on all hub components.

    Probes the event store, audit trail, policy directory, disk space,
    notification dispatchers, and circuit breakers. Returns an overall
    status (healthy/degraded/unhealthy) with per-component details.
    """
    report = health_agent.check_all()
    report["dead_letter_count"] = dispatcher.dead_letter_count
    return report


def finops_reconcile() -> dict[str, Any]:
    """Run a reconciliation check across all hub components.

    Detects data drift: orphaned events not evaluated, stale alerts
    pending too long, audit chain integrity violations, and alert
    state inconsistencies. Returns issues found and auto-fixes applied.
    """
    # Update reconciliation agent with current alerts
    reconciliation_agent._alerts = alert_agent.get_alerts(limit=10000)
    report = reconciliation_agent.reconcile()
    return report.to_dict()


def finops_retry_failed_notifications() -> dict[str, Any]:
    """Retry all failed notification dispatches from the dead-letter queue.

    When a Slack, PagerDuty, or webhook dispatch fails, the alert is
    saved to a dead-letter queue. This tool retries all failed dispatches.
    """
    result = dispatcher.retry_dead_letters()
    return {
        "status": "completed",
        "retried": result["retried"],
        "succeeded": result["succeeded"],
        "still_failed": result["failed"],
        "remaining_dead_letters": dispatcher.dead_letter_count,
    }


def finops_health_history(limit: int = 10) -> dict[str, Any]:
    """Get recent health check results for trend analysis."""
    limit = validate_query_limit(limit)
    history = health_agent.get_check_history(limit)
    return {
        "count": len(history),
        "checks": history,
    }


_replay_state: dict[str, Any] = {"in_progress": False, "replayed_ids": set()}
_MAX_REPLAYED_HISTORY = 50_000


def finops_replay_events() -> dict[str, Any]:
    """Replay events that were stored but never evaluated.

    After a crash or pipeline gap, some events may have been persisted
    to the event store but never evaluated against thresholds and policies.
    This tool identifies those events and re-processes them through the
    alert pipeline, ensuring no cost anomalies are silently missed.

    Guards against infinite loops:
    - A reentrancy flag prevents concurrent / recursive replays.
    - Already-replayed event IDs are tracked so the same event is never
      re-processed twice within a server session.
    """
    if _replay_state["in_progress"]:
        return {
            "status": "skipped",
            "message": "Replay already in progress — reentrancy blocked",
            "replayed": 0,
        }

    _replay_state["in_progress"] = True
    try:
        return _do_replay()
    finally:
        _replay_state["in_progress"] = False


def _do_replay() -> dict[str, Any]:
    """Inner replay logic — called only when the reentrancy guard allows."""
    replayed_ids: set[str] = _replay_state["replayed_ids"]

    # Update reconciliation agent with current alerts
    reconciliation_agent._alerts = alert_agent.get_alerts(limit=10000)
    unevaluated_ids = reconciliation_agent.get_unevaluated_events()

    # Exclude events we have already replayed this session
    unevaluated_ids = [eid for eid in unevaluated_ids if eid not in replayed_ids]

    if not unevaluated_ids:
        return {"status": "clean", "message": "No unevaluated events found", "replayed": 0}

    # Fetch the actual events from the store and re-process
    all_events = event_store.query(limit=10000)
    events_by_id = {e.event_id: e for e in all_events}

    replayed = 0
    alerts_generated = 0
    errors = 0
    for eid in unevaluated_ids:
        event = events_by_id.get(eid)
        if event is None:
            continue
        try:
            alert = alert_agent.process_event(event)
            replayed += 1
            replayed_ids.add(eid)
            if alert:
                alerts_generated += 1
        except Exception:
            errors += 1
            logger.exception("Error replaying event %s", eid)

    # Bound the replay history set to prevent unbounded memory growth
    if len(replayed_ids) > _MAX_REPLAYED_HISTORY:
        excess = len(replayed_ids) - _MAX_REPLAYED_HISTORY
        # Remove arbitrary oldest entries (set is unordered, but we just need to shed)
        for _ in range(excess):
            replayed_ids.pop()

    audit_logger.log(
        action="events.replayed",
        actor="system",
        target="event_replay",
        details={
            "replayed": replayed,
            "alerts_generated": alerts_generated,
            "errors": errors,
        },
    )

    return {
        "status": "completed",
        "unevaluated_found": len(unevaluated_ids),
        "replayed": replayed,
        "alerts_generated": alerts_generated,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# MCP Server tool registry -- maps tool names to callables
# ---------------------------------------------------------------------------

MCP_TOOLS: dict[str, dict[str, Any]] = {
    "finops_create_policy": {
        "function": finops_create_policy,
        "description": finops_create_policy.__doc__,
    },
    "finops_list_policies": {
        "function": finops_list_policies,
        "description": finops_list_policies.__doc__,
    },
    "finops_update_policy": {
        "function": finops_update_policy,
        "description": finops_update_policy.__doc__,
    },
    "finops_delete_policy": {
        "function": finops_delete_policy,
        "description": finops_delete_policy.__doc__,
    },
    "finops_evaluate_resource": {
        "function": finops_evaluate_resource,
        "description": finops_evaluate_resource.__doc__,
    },
    "finops_list_alerts": {
        "function": finops_list_alerts,
        "description": finops_list_alerts.__doc__,
    },
    "finops_acknowledge_alert": {
        "function": finops_acknowledge_alert,
        "description": finops_acknowledge_alert.__doc__,
    },
    "finops_resolve_alert": {
        "function": finops_resolve_alert,
        "description": finops_resolve_alert.__doc__,
    },
    "finops_alert_stats": {
        "function": finops_alert_stats,
        "description": finops_alert_stats.__doc__,
    },
    "finops_generate_report": {
        "function": finops_generate_report,
        "description": finops_generate_report.__doc__,
    },
    "finops_query_audit": {
        "function": finops_query_audit,
        "description": finops_query_audit.__doc__,
    },
    "finops_verify_audit_integrity": {
        "function": finops_verify_audit_integrity,
        "description": finops_verify_audit_integrity.__doc__,
    },
    "finops_export_audit": {
        "function": finops_export_audit,
        "description": finops_export_audit.__doc__,
    },
    "finops_estimate_cost": {
        "function": finops_estimate_cost,
        "description": finops_estimate_cost.__doc__,
    },
    "finops_hub_status": {
        "function": finops_hub_status,
        "description": finops_hub_status.__doc__,
    },
    "finops_health_check": {
        "function": finops_health_check,
        "description": finops_health_check.__doc__,
    },
    "finops_reconcile": {
        "function": finops_reconcile,
        "description": finops_reconcile.__doc__,
    },
    "finops_retry_failed_notifications": {
        "function": finops_retry_failed_notifications,
        "description": finops_retry_failed_notifications.__doc__,
    },
    "finops_health_history": {
        "function": finops_health_history,
        "description": finops_health_history.__doc__,
    },
    "finops_replay_events": {
        "function": finops_replay_events,
        "description": finops_replay_events.__doc__,
    },
}


def handle_tool_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Route an MCP tool call to the appropriate function.

    All exceptions are caught and returned as safe error messages that
    don't leak internal file paths, class names, or stack traces.
    """
    tool = MCP_TOOLS.get(tool_name)
    if tool is None:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}

    try:
        result = tool["function"](**arguments)
        audit_logger.log(
            action=f"mcp.tool_call.{tool_name}",
            actor="mcp_client",
            target=tool_name,
            details={
                "arguments": _redact_arguments(arguments),
                "result_status": result.get("status", "ok"),
            },
        )
        return result
    except ValidationError as e:
        # Validation errors are safe to return verbatim
        return {"status": "error", "message": str(e)}
    except Exception as e:
        audit_logger.log(
            action=f"mcp.tool_call.{tool_name}",
            actor="mcp_client",
            target=tool_name,
            details={"arguments": _redact_arguments(arguments), "error": safe_error_message(e)},
            outcome="failure",
        )
        return {"status": "error", "message": safe_error_message(e)}


def _redact_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    """Redact potentially sensitive fields from audit log arguments."""
    redacted = dict(arguments)
    sensitive_keys = {"creator_email", "creator_identity", "acknowledged_by", "resolved_by"}
    for key in sensitive_keys:
        if redacted.get(key):
            value = str(redacted[key])
            if "@" in value:
                # Redact email to first char + ***@domain
                parts = value.split("@")
                redacted[key] = f"{parts[0][0]}***@{parts[1]}"
            elif len(value) > 10:
                redacted[key] = f"{value[:8]}..."
    return redacted


def list_tools() -> list[dict[str, str]]:
    """List all available MCP tools."""
    return [
        {"name": name, "description": info["description"] or ""} for name, info in MCP_TOOLS.items()
    ]


# ---------------------------------------------------------------------------
# Stdio MCP transport (simplified)
# ---------------------------------------------------------------------------


def run_stdio_server() -> None:
    """Run the MCP server over stdin/stdout (JSON-RPC style)."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    logger.info("FinOps MCP Server starting (stdio transport)")
    logger.info("Available tools: %s", ", ".join(MCP_TOOLS.keys()))

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            method = request.get("method", "")

            if method == "tools/list":
                response = {
                    "id": request.get("id"),
                    "result": {"tools": list_tools()},
                }
            elif method == "tools/call":
                params = request.get("params", {})
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = handle_tool_call(tool_name, arguments)
                response = {
                    "id": request.get("id"),
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, default=str)}]
                    },
                }
            else:
                response = {
                    "id": request.get("id"),
                    "error": {"code": -32601, "message": f"Unknown method: {method}"},
                }

            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
        except json.JSONDecodeError:
            sys.stderr.write("Invalid JSON received\n")
        except Exception as e:
            sys.stderr.write(f"Internal error: {safe_error_message(e)}\n")


if __name__ == "__main__":
    run_stdio_server()
