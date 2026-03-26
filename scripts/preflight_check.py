#!/usr/bin/env python3
"""FinOps Automation Hub — Pre-flight Check Script.

Validates that all prerequisites are met before deploying the hub.
Supports checking local tools, AWS readiness, GCP readiness, Terraform,
and post-deployment verification.

Usage:
    python scripts/preflight_check.py --all
    python scripts/preflight_check.py --local
    python scripts/preflight_check.py --aws
    python scripts/preflight_check.py --gcp
    python scripts/preflight_check.py --terraform
    python scripts/preflight_check.py --verify-deployment
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Colour helpers (disabled when stdout is not a terminal)
# ---------------------------------------------------------------------------

_COLOURS = sys.stdout.isatty()


def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m" if _COLOURS else text


def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m" if _COLOURS else text


def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m" if _COLOURS else text


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _COLOURS else text


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

_passes: list[str] = []
_warnings: list[str] = []
_failures: list[str] = []


def _pass(msg: str) -> None:
    _passes.append(msg)
    print(f"  {_green('PASS')}: {msg}")


def _warn(msg: str) -> None:
    _warnings.append(msg)
    print(f"  {_yellow('WARN')}: {msg}")


def _fail(msg: str) -> None:
    _failures.append(msg)
    print(f"  {_red('FAIL')}: {msg}")


def _run(cmd: list[str], *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result. Never raises on non-zero exit."""
    try:
        return subprocess.run(  # noqa: S603  # nosec B603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return subprocess.CompletedProcess(
            cmd, returncode=127, stdout="", stderr="command not found"
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, returncode=124, stdout="", stderr="timed out")


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string like '3.12.1' into a tuple of ints."""
    parts = []
    for part in version_str.strip().split("."):
        digits = ""
        for ch in part:
            if ch.isdigit():
                digits += ch
            else:
                break
        if digits:
            parts.append(int(digits))
    return tuple(parts)


# ---------------------------------------------------------------------------
# Check: Local tools
# ---------------------------------------------------------------------------


def check_local() -> None:
    """Validate local tool versions and Python packages."""
    print(f"\n{_bold('=== Local Environment Checks ===')}")

    # -- Python version --
    v = sys.version_info
    if (v.major, v.minor) >= (3, 12):
        _pass(f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        _fail(f"Python >= 3.12 required, found {v.major}.{v.minor}.{v.micro}")

    # -- pydantic --
    try:
        import pydantic

        ver = pydantic.__version__
        if _parse_version(ver) >= (2, 7):
            _pass(f"pydantic {ver}")
        else:
            _fail(f"pydantic >= 2.7 required, found {ver}")
    except ImportError:
        _fail("pydantic not installed")

    # -- pytest --
    try:
        import pytest

        _pass(f"pytest {pytest.__version__}")
    except ImportError:
        _warn("pytest not installed — run: pip install -e '.[dev]'")

    # -- Git --
    result = _run(["git", "--version"])
    if result.returncode == 0:
        _pass(f"git ({result.stdout.strip()})")
    else:
        _warn("git not found")

    # -- uv (for MCP servers) --
    if shutil.which("uvx"):
        result = _run(["uvx", "--version"])
        ver_text = result.stdout.strip() if result.returncode == 0 else "version unknown"
        _pass(f"uvx available ({ver_text})")
    elif shutil.which("uv"):
        _pass("uv available (uvx may be accessible via 'uv tool run')")
    else:
        _warn("uv/uvx not installed — needed for AWS MCP servers. Install: pip install uv")

    # -- Hub directory --
    hub_dir = Path(__file__).resolve().parent.parent
    if (hub_dir / "core" / "models.py").exists():
        _pass(f"Hub directory: {hub_dir}")
    else:
        _fail(f"Hub directory structure not found at {hub_dir}")

    # -- Test suite --
    print("\n  Running test suite …")
    result = _run(
        [sys.executable, "-m", "pytest", "-x", "-q", "--tb=line"],
        timeout=120,
    )
    if result.returncode == 0:
        # Extract summary line (e.g. "95 passed")
        last_lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        summary = last_lines[-1] if last_lines else "passed"
        _pass(f"Test suite: {summary}")
    else:
        output = result.stdout + result.stderr
        lines = [ln for ln in output.strip().splitlines() if ln.strip()]
        summary = lines[-1] if lines else "unknown failure"
        _fail(f"Test suite failed ({summary})")


# ---------------------------------------------------------------------------
# Check: Terraform
# ---------------------------------------------------------------------------


def check_terraform() -> None:
    """Validate Terraform version and provider compatibility."""
    print(f"\n{_bold('=== Terraform Checks ===')}")

    result = _run(["terraform", "version", "-json"])
    if result.returncode != 0:
        # Try plain text version
        result = _run(["terraform", "version"])
        if result.returncode != 0:
            _fail(
                "Terraform not found — install from https://developer.hashicorp.com/terraform/install"
            )
            return

    # Parse version
    try:
        data = json.loads(result.stdout)
        tf_version = data.get("terraform_version", "0.0.0")
    except (json.JSONDecodeError, KeyError):
        # Parse from text output
        first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        # "Terraform v1.14.2"
        tf_version = first_line.replace("Terraform v", "").replace("Terraform ", "").strip()

    parsed = _parse_version(tf_version)
    if parsed >= (1, 14):
        _pass(f"Terraform {tf_version}")
    else:
        _fail(f"Terraform >= 1.14.0 required, found {tf_version}")

    # Check AWS terraform directory
    hub_dir = Path(__file__).resolve().parent.parent
    aws_tf = hub_dir / "providers" / "aws" / "terraform"
    gcp_tf = hub_dir / "providers" / "gcp" / "terraform"

    if aws_tf.exists() and (aws_tf / "main.tf").exists():
        _pass(f"AWS Terraform module found: {aws_tf}")
    else:
        _warn("AWS Terraform module not found")

    if gcp_tf.exists() and (gcp_tf / "main.tf").exists():
        _pass(f"GCP Terraform module found: {gcp_tf}")
    else:
        _warn("GCP Terraform module not found")


# ---------------------------------------------------------------------------
# Check: AWS
# ---------------------------------------------------------------------------


def check_aws() -> None:
    """Validate AWS CLI, credentials, and required permissions."""
    print(f"\n{_bold('=== AWS Readiness Checks ===')}")

    # -- AWS CLI --
    result = _run(["aws", "--version"])
    if result.returncode != 0:
        _fail("AWS CLI not found — install: pip install awscli")
        return

    version_text = result.stdout.strip() or result.stderr.strip()
    _pass(f"AWS CLI ({version_text.splitlines()[0]})")

    # -- Reject static API keys --
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        _warn(
            "AWS_ACCESS_KEY_ID is set — the hub prohibits static API keys. "
            "Use IAM roles, SSO, or instance profiles instead."
        )

    # -- Credentials / identity --
    result = _run(["aws", "sts", "get-caller-identity"])
    if result.returncode != 0:
        _fail("AWS credentials not configured — run: aws sso login --profile <profile>")
        return

    try:
        identity = json.loads(result.stdout)
        account = identity.get("Account", "unknown")
        arn = identity.get("Arn", "unknown")
        _pass(f"AWS identity: {arn} (account {account})")
    except json.JSONDecodeError:
        _fail("Could not parse AWS identity response")
        return

    # -- Cost Explorer access --
    result = _run(
        [
            "aws",
            "ce",
            "get-cost-and-usage",
            "--time-period",
            "Start=2026-03-01,End=2026-03-02",
            "--granularity",
            "DAILY",
            "--metrics",
            "UnblendedCost",
        ]
    )
    if result.returncode == 0:
        _pass("Cost Explorer: accessible")
    else:
        err = result.stderr.strip()
        if "AccessDeniedException" in err:
            _fail("Cost Explorer: access denied — add ce:GetCostAndUsage to IAM policy")
        elif "not enabled" in err.lower() or "OptIn" in err:
            _fail("Cost Explorer: not enabled — enable in AWS Console → Billing → Cost Explorer")
        else:
            _warn(f"Cost Explorer: could not verify ({err[:120]})")

    # -- Bedrock access --
    result = _run(["aws", "bedrock", "list-foundation-models", "--max-results", "1"])
    if result.returncode == 0:
        _pass("Bedrock: accessible")
    else:
        _warn("Bedrock: could not verify — ensure Bedrock model access is enabled")

    # -- KMS key (check environment hint) --
    print("  INFO: Ensure you have a KMS key ARN ready for terraform.tfvars")


# ---------------------------------------------------------------------------
# Check: GCP
# ---------------------------------------------------------------------------


def check_gcp() -> None:
    """Validate gcloud CLI, credentials, and required APIs."""
    print(f"\n{_bold('=== GCP Readiness Checks ===')}")

    # -- gcloud CLI --
    result = _run(["gcloud", "version", "--format=json"])
    if result.returncode != 0:
        result = _run(["gcloud", "version"])
        if result.returncode != 0:
            _fail("gcloud CLI not found — install from https://cloud.google.com/sdk/docs/install")
            return

    _pass("gcloud CLI available")

    # -- Reject service account key files --
    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if creds_path and creds_path.endswith(".json"):
        _warn(
            f"GOOGLE_APPLICATION_CREDENTIALS points to {creds_path} — "
            "the hub prohibits service account JSON keys. Use gcloud auth application-default login."
        )

    # -- Application Default Credentials --
    result = _run(["gcloud", "auth", "application-default", "print-access-token"])
    if result.returncode == 0:
        _pass("GCP Application Default Credentials: configured")
    else:
        _fail("GCP ADC not configured — run: gcloud auth application-default login")
        return

    # -- Project --
    result = _run(["gcloud", "config", "get-value", "project"])
    project = result.stdout.strip()
    if project and project != "(unset)":
        _pass(f"GCP project: {project}")
    else:
        _fail("GCP project not set — run: gcloud config set project YOUR_PROJECT_ID")
        return

    # -- Required APIs --
    required_apis = [
        "logging.googleapis.com",
        "monitoring.googleapis.com",
        "pubsub.googleapis.com",
        "bigquery.googleapis.com",
        "cloudasset.googleapis.com",
        "cloudresourcemanager.googleapis.com",
        "billingbudgets.googleapis.com",
        "recommender.googleapis.com",
        "aiplatform.googleapis.com",
    ]

    result = _run(
        [
            "gcloud",
            "services",
            "list",
            "--enabled",
            "--format=value(config.name)",
            "--project",
            project,
        ]
    )
    if result.returncode == 0:
        enabled = set(result.stdout.strip().splitlines())
        missing = [api for api in required_apis if api not in enabled]
        if not missing:
            _pass(f"All {len(required_apis)} required APIs enabled")
        else:
            _warn(
                f"{len(missing)} APIs not enabled: {', '.join(missing[:3])}"
                + (f" (+{len(missing) - 3} more)" if len(missing) > 3 else "")
                + " — Terraform will enable them, or run: gcloud services enable <api>"
            )
    else:
        _warn("Could not list enabled APIs")

    # -- BigQuery billing export --
    result = _run(["bq", "ls", "--project_id", project, "--max_results=5"])
    if result.returncode == 0:
        _pass("BigQuery: accessible")
    else:
        _warn("BigQuery: could not verify — ensure billing export is configured")


# ---------------------------------------------------------------------------
# Check: Post-deployment verification
# ---------------------------------------------------------------------------


def check_deployment() -> None:
    """Verify that deployed infrastructure is operational."""
    print(f"\n{_bold('=== Post-Deployment Verification ===')}")

    # -- Hub MCP server --
    hub_dir = Path(__file__).resolve().parent.parent
    print("  Checking hub MCP server …")
    result = _run(
        [sys.executable, "-c", "from mcp_server.server import MCP_TOOLS; print(len(MCP_TOOLS))"],
        timeout=30,
    )
    if result.returncode == 0:
        tool_count = result.stdout.strip()
        _pass(f"Hub MCP server: {tool_count} tools registered")
    else:
        _fail(f"Hub MCP server failed to load: {result.stderr.strip()[:200]}")

    # -- Policies loaded --
    result = _run(
        [
            sys.executable,
            "-c",
            """
from core.audit import AuditLogger
from core.policies import PolicyEngine
audit = AuditLogger(audit_dir='audit_store')
engine = PolicyEngine(policy_dir='policies', audit_logger=audit)
count = engine.load_policies()
print(count)
""",
        ],
        timeout=15,
    )
    if result.returncode == 0:
        count = result.stdout.strip()
        _pass(f"Policy engine: {count} policies loaded")
    else:
        _warn("Policy engine: could not verify")

    # -- Audit trail --
    audit_dir = hub_dir / "audit_store"
    if audit_dir.exists():
        jsonl_files = list(audit_dir.glob("*.jsonl"))
        _pass(f"Audit store: {len(jsonl_files)} log file(s)")
    else:
        _pass("Audit store: directory will be created on first write")

    # -- AWS resources (if AWS CLI available) --
    if shutil.which("aws"):
        print(f"\n  {_bold('AWS Resources:')}")
        # Bedrock agent
        result = _run(["aws", "bedrock-agent", "list-agents"])
        if result.returncode == 0:
            try:
                agents = json.loads(result.stdout).get("agentSummaries", [])
                finops_agents = [a for a in agents if "finops" in a.get("agentName", "").lower()]
                if finops_agents:
                    _pass(f"Bedrock agent found: {finops_agents[0]['agentName']}")
                else:
                    _warn("No FinOps Bedrock agent found — deploy with Terraform")
            except (json.JSONDecodeError, KeyError):
                _warn("Could not parse Bedrock agent list")
        else:
            _warn("Bedrock agent: could not verify")

        # Cost anomaly monitor
        result = _run(["aws", "ce", "get-anomaly-monitors"])
        if result.returncode == 0:
            try:
                monitors = json.loads(result.stdout).get("AnomalyMonitors", [])
                if monitors:
                    _pass(f"Cost anomaly monitor: {len(monitors)} monitor(s)")
                else:
                    _warn("No cost anomaly monitors found")
            except (json.JSONDecodeError, KeyError):
                _warn("Could not parse anomaly monitors")

    # -- GCP resources (if gcloud available) --
    if shutil.which("gcloud"):
        print(f"\n  {_bold('GCP Resources:')}")
        result = _run(["gcloud", "config", "get-value", "project"])
        project = result.stdout.strip()
        if project and project != "(unset)":
            # Pub/Sub topics
            result = _run(
                ["gcloud", "pubsub", "topics", "list", "--project", project, "--format=value(name)"]
            )
            if result.returncode == 0:
                topics = [t for t in result.stdout.strip().splitlines() if "finops" in t.lower()]
                if topics:
                    _pass(f"Pub/Sub topic found: {topics[0].split('/')[-1]}")
                else:
                    _warn("No FinOps Pub/Sub topics found — deploy with Terraform")

            # Log sinks
            result = _run(
                ["gcloud", "logging", "sinks", "list", "--project", project, "--format=value(name)"]
            )
            if result.returncode == 0:
                sinks = [s for s in result.stdout.strip().splitlines() if "finops" in s.lower()]
                if sinks:
                    _pass(f"Log sink found: {sinks[0]}")
                else:
                    _warn("No FinOps log sinks found — deploy with Terraform")


# ---------------------------------------------------------------------------
# Summary and entry point
# ---------------------------------------------------------------------------


def print_summary() -> None:
    """Print final summary of all checks."""
    total = len(_passes) + len(_warnings) + len(_failures)
    print(f"\n{_bold('=== Summary ===')}")
    print(
        f"  {_green(f'{len(_passes)} passed')}, {_yellow(f'{len(_warnings)} warnings')}, {_red(f'{len(_failures)} failures')} ({total} checks)"
    )

    if _failures:
        print(f"\n{_bold('Failures:')}")
        for f in _failures:
            print(f"  {_red('✗')} {f}")
        print("\nSee docs/TROUBLESHOOTING.md for solutions.")

    if _warnings:
        print(f"\n{_bold('Warnings:')}")
        for w in _warnings:
            print(f"  {_yellow('!')} {w}")

    if not _failures:
        print(f"\n{_green('All critical checks passed.')}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="FinOps Automation Hub — Pre-flight Check Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--all", action="store_true", help="Run all checks (local + AWS + GCP + Terraform)"
    )
    parser.add_argument(
        "--local", action="store_true", help="Check local tools and Python packages"
    )
    parser.add_argument(
        "--aws", action="store_true", help="Check AWS CLI, credentials, and permissions"
    )
    parser.add_argument(
        "--gcp", action="store_true", help="Check gcloud CLI, credentials, and APIs"
    )
    parser.add_argument(
        "--terraform", action="store_true", help="Check Terraform version and modules"
    )
    parser.add_argument(
        "--verify-deployment", action="store_true", help="Verify deployed infrastructure"
    )

    args = parser.parse_args()

    # Default to --local if nothing specified
    if not any([args.all, args.local, args.aws, args.gcp, args.terraform, args.verify_deployment]):
        print("No check specified. Running --local by default. Use --all for everything.\n")
        args.local = True

    print(_bold("FinOps Automation Hub — Pre-flight Checks"))
    print("=" * 50)

    # Change to hub directory for test execution
    hub_dir = Path(__file__).resolve().parent.parent
    original_cwd = os.getcwd()
    os.chdir(hub_dir)

    try:
        if args.all or args.local:
            check_local()
        if args.all or args.terraform:
            check_terraform()
        if args.all or args.aws:
            check_aws()
        if args.all or args.gcp:
            check_gcp()
        if args.verify_deployment:
            check_deployment()

        print_summary()
    finally:
        os.chdir(original_cwd)

    sys.exit(1 if _failures else 0)


if __name__ == "__main__":
    main()
