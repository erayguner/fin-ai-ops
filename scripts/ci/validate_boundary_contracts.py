#!/usr/bin/env python3
"""CI gate — validate every boundary contract against a structural schema.

Framework §3.2 requires every agent to ship with a boundary contract.
This script enforces that:

* Each YAML under docs/governance/boundary_contracts/ declares the
  minimum-required fields.
* Each declared role uses one of the four taxonomy values.
* boundary_review.next_due is a real ISO date and is not already in
  the past (a renewal cadence breach is a finding, not silently OK).

Exit 0 on success, 1 on any structural issue.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

REQUIRED_TOP_LEVEL = {
    "schema_version",
    "agent_id",
    "agent_name",
    "provider",
    "role",
    "purpose",
    "foundation_model",
    "in_scope_tools",
    "out_of_scope_systems",
    "data_classes_handled",
    "approval_class",
    "owner",
    "maturity_level",
    "boundary_review",
}
VALID_ROLES = {"observer", "advisor", "operator", "autonomous_operator"}
VALID_PROVIDERS = {"aws", "gcp", "azure"}
VALID_MATURITY = {"L1", "L2", "L3", "L4"}
VALID_APPROVAL_CLASSES = {"per_call", "batch", "none"}


def parse_yaml(text: str) -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        sys.stderr.write("pyyaml not installed; run pip install pyyaml\n")
        sys.exit(2)
    return yaml.safe_load(text)


def check_one(path: Path) -> list[str]:
    issues: list[str] = []
    try:
        doc = parse_yaml(path.read_text()) or {}
    except Exception as exc:
        return [f"{path.name}: failed to parse YAML — {exc}"]

    missing = REQUIRED_TOP_LEVEL - set(doc.keys())
    if missing:
        issues.append(f"{path.name}: missing required fields: {sorted(missing)}")
    if doc.get("role") not in VALID_ROLES:
        issues.append(f"{path.name}: role '{doc.get('role')}' not in {sorted(VALID_ROLES)}")
    if doc.get("provider") not in VALID_PROVIDERS:
        issues.append(
            f"{path.name}: provider '{doc.get('provider')}' not in {sorted(VALID_PROVIDERS)}"
        )
    if doc.get("maturity_level") not in VALID_MATURITY:
        issues.append(
            f"{path.name}: maturity_level '{doc.get('maturity_level')}' not in {sorted(VALID_MATURITY)}"
        )
    if doc.get("approval_class") not in VALID_APPROVAL_CLASSES:
        issues.append(
            f"{path.name}: approval_class '{doc.get('approval_class')}' not in "
            f"{sorted(VALID_APPROVAL_CLASSES)}"
        )

    review = doc.get("boundary_review") or {}
    next_due = review.get("next_due")
    if isinstance(next_due, str):
        try:
            next_due_date = date.fromisoformat(next_due)
        except ValueError:
            issues.append(f"{path.name}: boundary_review.next_due is not an ISO date: {next_due!r}")
            next_due_date = None
        if next_due_date and next_due_date < date.today():
            issues.append(
                f"{path.name}: boundary_review.next_due {next_due} is in the past — "
                "renewal cadence breach (framework §17.2)"
            )
    elif isinstance(next_due, date):
        if next_due < date.today():
            issues.append(
                f"{path.name}: boundary_review.next_due {next_due.isoformat()} is in the past"
            )

    # Operator+ roles must declare an approver pool.
    role = doc.get("role")
    if role in {"operator", "autonomous_operator"}:
        approvers = doc.get("approver_pool") or []
        if not approvers:
            issues.append(
                f"{path.name}: role '{role}' requires non-empty approver_pool (framework §5.3)"
            )

    return issues


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent.parent
    contracts_dir = repo_root / "docs" / "governance" / "boundary_contracts"
    if not contracts_dir.exists():
        sys.stderr.write(f"FAIL: {contracts_dir} does not exist\n")
        return 1
    files = sorted(contracts_dir.glob("*.yaml")) + sorted(contracts_dir.glob("*.yml"))
    if not files:
        sys.stderr.write(f"FAIL: no boundary contracts found in {contracts_dir}\n")
        return 1
    all_issues: list[str] = []
    for file in files:
        all_issues.extend(check_one(file))
    if all_issues:
        sys.stderr.write("Boundary contract validation FAILED:\n")
        for issue in all_issues:
            sys.stderr.write(f"  - {issue}\n")
        return 1
    print(f"OK: {len(files)} boundary contract(s) validated successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
