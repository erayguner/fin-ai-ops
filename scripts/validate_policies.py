#!/usr/bin/env python3
"""Policy validation script for CI and local use.

Validates all policy JSON files against the CostPolicy model,
checks for conflicts, and reports coverage gaps. Exits non-zero on errors.

Usage:
    python scripts/validate_policies.py [--strict]
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import CostPolicy


def validate_policies(policy_dir: Path, strict: bool = False) -> int:
    """Validate all policy files. Returns exit code (0=pass, 1=fail)."""
    errors: list[str] = []
    warnings: list[str] = []
    policies: list[CostPolicy] = []

    # Phase 1: Schema validation
    for f in sorted(policy_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            policy = CostPolicy(**data)
            policies.append(policy)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            errors.append(f"INVALID {f.name}: {e}")

    # Phase 2: Consistency checks
    all_tags: set[str] = set()
    resource_policies: dict[str, list[str]] = defaultdict(list)

    for p in policies:
        all_tags.update(p.require_tags)
        for rt in p.resource_types:
            resource_policies[rt].append(p.name)

        if not p.description:
            warnings.append(f"{p.policy_id}: empty description")

        if (
            p.max_monthly_cost_usd is not None
            and p.require_approval_above_usd is not None
            and p.require_approval_above_usd > p.max_monthly_cost_usd
        ):
            warnings.append(
                f"{p.policy_id}: approval threshold "
                f"(${p.require_approval_above_usd:,.0f}) > "
                f"cost cap (${p.max_monthly_cost_usd:,.0f})"
            )

    # Phase 3: Tag naming consistency
    kebab = [t for t in all_tags if "-" in t]
    snake = [t for t in all_tags if "_" in t]
    if kebab and snake:
        warnings.append(
            f"Mixed tag naming conventions: "
            f"kebab-case ({', '.join(sorted(kebab)[:3])}) "
            f"and snake_case ({', '.join(sorted(snake)[:3])})"
        )

    # Phase 4: Conflict detection
    for rt, pnames in resource_policies.items():
        if len(pnames) > 1:
            matching = [p for p in policies if rt in p.resource_types]
            costs = [p.max_monthly_cost_usd for p in matching if p.max_monthly_cost_usd is not None]
            if costs and max(costs) > 3 * min(costs):
                warnings.append(
                    f"Resource type '{rt}' has wide cost cap range: "
                    f"${min(costs):,.0f}-${max(costs):,.0f} across {pnames}"
                )

    # Report
    total = len(list(policy_dir.glob("*.json")))
    print(f"Policies: {len(policies)}/{total} valid")

    for e in errors:
        print(f"  ERROR: {e}")
    for w in warnings:
        print(f"  WARN: {w}")

    if errors:
        print(f"\nFAILED: {len(errors)} error(s)")
        return 1

    if strict and warnings:
        print(f"\nFAILED (strict): {len(warnings)} warning(s)")
        return 1

    print("\nPASSED")
    return 0


if __name__ == "__main__":
    strict = "--strict" in sys.argv
    policy_path = Path(__file__).resolve().parent.parent / "policies"
    sys.exit(validate_policies(policy_path, strict=strict))
