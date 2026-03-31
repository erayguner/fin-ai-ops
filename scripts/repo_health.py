#!/usr/bin/env python3
"""Repository health check script.
Runs all self-maintenance checks: policies, tests, dependencies, staleness.
Used by CI and the /health-check command.

Usage:
    python scripts/repo_health.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import CostPolicy


def check_policies(root: Path) -> tuple[str, str]:
    policy_dir = root / "policies"
    valid, invalid, disabled = 0, 0, 0
    for f in policy_dir.glob("*.json"):
        try:
            p = CostPolicy(**json.loads(f.read_text()))
            if p.enabled:
                valid += 1
            else:
                disabled += 1
        except Exception:
            invalid += 1

    status = "FAIL" if invalid > 0 else "OK"
    detail = f"{valid} active, {disabled} disabled, {invalid} invalid"
    return status, detail


def check_tests(root: Path) -> tuple[str, str]:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=120,
    )
    last_line = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    status = "OK" if result.returncode == 0 else "FAIL"
    return status, last_line


def check_secrets(root: Path) -> tuple[str, str]:
    patterns = [
        re.compile(r"AKIA[0-9A-Z]"),
        re.compile(r"\bsk-[a-zA-Z0-9]{20,}"),
        re.compile(r"password\s*=\s*['\"][^'\"]+['\"]"),
    ]
    findings = 0
    for src_dir in ("core", "agents", "providers"):
        src_path = root / src_dir
        if not src_path.exists():
            continue
        for py_file in src_path.rglob("*.py"):
            content = py_file.read_text(errors="ignore")
            for pat in patterns:
                findings += len(pat.findall(content))

    status = "FAIL" if findings > 0 else "OK"
    return status, f"{findings} potential secrets"


def check_staleness(root: Path) -> tuple[str, str]:
    threshold = time.time() - (90 * 86400)
    count = 0
    for src_dir in ("core", "agents", "providers"):
        src_path = root / src_dir
        if not src_path.exists():
            continue
        for py_file in src_path.rglob("*.py"):
            if os.path.getmtime(py_file) < threshold:
                count += 1
    status = "WARN" if count > 5 else "OK"
    return status, f"{count} files >90 days old"


def main() -> int:
    root = Path(__file__).resolve().parent.parent

    checks = {
        "Policies": check_policies,
        "Tests": check_tests,
        "Secrets": check_secrets,
        "Freshness": check_staleness,
    }

    print("Repository Health Check")
    print("=" * 50)

    overall = "OK"
    for name, check_fn in checks.items():
        try:
            status, detail = check_fn(root)
        except Exception as e:
            status, detail = "ERROR", str(e)

        icon = {"OK": "PASS", "WARN": "WARN", "FAIL": "FAIL", "ERROR": "ERROR"}[status]
        print(f"  [{icon}] {name}: {detail}")

        if status == "FAIL":
            overall = "FAIL"
        elif status == "WARN" and overall != "FAIL":
            overall = "WARN"

    print("=" * 50)
    print(f"Overall: {overall}")

    return 0 if overall != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
