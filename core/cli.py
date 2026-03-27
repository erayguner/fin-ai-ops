"""FinOps Automation Hub — Command Line Interface.

Provides subcommands to start monitoring, generate reports,
check health, and manage the hub from the terminal.

Usage:
    finops-hub start [--config hub_config.yaml]
    finops-hub status
    finops-hub health
    finops-hub report --days 30
    finops-hub policies
    finops-hub preflight [--all]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = ["main"]

logger = logging.getLogger(__name__)


def _bootstrap(config_path: str | None = None) -> dict[str, Any]:
    """Initialise core hub components and return them as a dict."""
    from core.audit import AuditLogger
    from core.config import HubConfig
    from core.event_store import BaseEventStore, InMemoryEventStore, SQLiteEventStore
    from core.logging_config import configure_logging
    from core.policies import PolicyEngine
    from core.thresholds import ThresholdEngine

    configure_logging()

    config = HubConfig(config_path)

    audit_dir = config.get_str("hub.audit_dir", "audit_store")
    audit_logger = AuditLogger(audit_dir)

    event_store: BaseEventStore
    backend = config.get_str("hub.event_store_backend", "memory")
    if backend == "sqlite":
        event_store = SQLiteEventStore(config.get_str("hub.event_store_path", "events.db"))
    else:
        event_store = InMemoryEventStore()

    threshold_engine = ThresholdEngine(config=config)

    policy_dir = config.get_str("hub.policy_dir", "policies")
    policy_engine = PolicyEngine(policy_dir, audit_logger)
    policy_engine.load_policies()

    return {
        "config": config,
        "audit_logger": audit_logger,
        "event_store": event_store,
        "threshold_engine": threshold_engine,
        "policy_engine": policy_engine,
    }


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_start(args: argparse.Namespace) -> int:
    """Start the cost monitoring loop."""
    hub = _bootstrap(args.config)

    from agents.cost_monitor import CostMonitorAgent

    aws_config: dict[str, Any] | None = None
    gcp_config: dict[str, Any] | None = None

    aws_regions = hub["config"].get("aws.regions")
    if aws_regions:
        aws_config = {"regions": aws_regions}

    gcp_projects = hub["config"].get("gcp.projects")
    if gcp_projects:
        gcp_config = {"project_ids": gcp_projects}

    if not aws_config and not gcp_config:
        logger.warning(
            "No providers configured. Set aws.regions or gcp.projects in hub_config.yaml"
        )
        print("No providers configured. See config/hub_config.yaml.example", file=sys.stderr)
        return 1

    poll_interval = hub["config"].get_int("monitoring.poll_interval_seconds", 900)
    monitor = CostMonitorAgent(
        audit_logger=hub["audit_logger"],
        aws_config=aws_config,
        gcp_config=gcp_config,
        poll_interval_seconds=poll_interval,
    )

    print(f"Starting FinOps Hub (polling every {poll_interval}s) ...")
    print("Press Ctrl+C to stop.\n")

    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
        print("\nHub stopped.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show hub status via the MCP server."""
    from mcp_server.server import handle_tool_call

    result = handle_tool_call("finops_hub_status", {})
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Run health checks."""
    from mcp_server.server import handle_tool_call

    result = handle_tool_call("finops_check_health", {})
    print(json.dumps(result, indent=2, default=str))
    overall = result.get("status", "unknown")
    return 0 if overall == "healthy" else 1


def cmd_report(args: argparse.Namespace) -> int:
    """Generate a cost report for the last N days."""
    hub = _bootstrap(args.config)

    from agents.report_agent import ReportAgent

    report_agent = ReportAgent(audit_logger=hub["audit_logger"])
    now = datetime.now(UTC)
    start = now - timedelta(days=args.days)

    events = hub["event_store"].query(since=start, limit=10000)
    report = report_agent.generate_report(events, [], start, now)
    print(report_agent.format_report_for_humans(report))
    return 0


def cmd_policies(args: argparse.Namespace) -> int:
    """List loaded policies."""
    from mcp_server.server import handle_tool_call

    result = handle_tool_call("finops_list_policies", {"enabled_only": False})
    print(json.dumps(result, indent=2, default=str))
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    """Run preflight checks (delegates to scripts/preflight_check.py)."""
    import subprocess  # nosec B404

    script = Path(__file__).resolve().parent.parent / "scripts" / "preflight_check.py"
    if not script.exists():
        print(f"Preflight script not found: {script}", file=sys.stderr)
        return 1
    cmd = [sys.executable, str(script)]
    if args.check_all:
        cmd.append("--all")
    else:
        cmd.append("--local")
    return subprocess.call(cmd)  # noqa: S603  # nosec B603


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="finops-hub",
        description="FinOps Automation Hub — cost governance for AWS and GCP",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="Path to hub_config.yaml",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    # start
    sub.add_parser("start", help="Start the cost monitoring loop")

    # status
    sub.add_parser("status", help="Show hub status")

    # health
    sub.add_parser("health", help="Run health checks")

    # report
    rp = sub.add_parser("report", help="Generate a cost report")
    rp.add_argument("--days", type=int, default=30, help="Report period in days (default: 30)")

    # policies
    sub.add_parser("policies", help="List loaded policies")

    # preflight
    pf = sub.add_parser("preflight", help="Run preflight readiness checks")
    pf.add_argument("--all", dest="check_all", action="store_true", help="Run all checks")

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "start": cmd_start,
        "status": cmd_status,
        "health": cmd_health,
        "report": cmd_report,
        "policies": cmd_policies,
        "preflight": cmd_preflight,
    }
    sys.exit(dispatch[args.command](args))


if __name__ == "__main__":
    main()
