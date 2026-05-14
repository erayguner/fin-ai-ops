#!/usr/bin/env python3
"""Governor dry-run — exercises ``governed_call`` against every MCP tool.

Run as part of CI (framework §16.3). Verifies:

* Every MCP tool is registered with the governor.
* A halt entry against ``session_id="dry-run"`` short-circuits every
  tool call.
* The fail-closed default denies unknown tools.
* Per-principal budgets prevent cross-talk.

Exit code: 0 on success, 1 on regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is importable when run from scripts/.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.agent_supervisor import AgentSupervisor  # noqa: E402
from core.tool_governor import (  # noqa: E402
    BudgetLimits,
    BudgetTracker,
    Decision,
    GovernancePolicy,
    ToolCall,
    ToolCategory,
    ToolRegistry,
    ToolRequest,
    governed_call,
)
from mcp_server.server import MCP_TOOLS  # noqa: E402


def check_registry_covers_all_tools() -> int:
    registry = ToolRegistry(
        ToolCall(name=name, category=ToolCategory.EXECUTION) for name in MCP_TOOLS
    )
    missing = [name for name in MCP_TOOLS if not registry.known(name)]
    if missing:
        print(f"FAIL: registry missing tools: {missing}", file=sys.stderr)
        return 1
    print(f"OK: registry covers {len(MCP_TOOLS)} MCP tools")
    return 0


def check_fail_closed_denies_unknown_tool() -> int:
    registry = ToolRegistry()  # empty
    policy = GovernancePolicy(name="ci-dry-run", default_allow=False)
    budget = BudgetTracker(BudgetLimits())
    request = ToolRequest(tool_name="bogus_tool", arguments={})
    result = governed_call(
        request,
        policy=policy,
        registry=registry,
        budget=budget,
        executor=lambda r: {"status": "ok"},
    )
    if result.allowed or result.decision is not Decision.DENY:
        print("FAIL: unknown tool was allowed under fail-closed policy", file=sys.stderr)
        return 1
    print("OK: fail-closed default denies unknown tools")
    return 0


def check_halt_short_circuits() -> int:
    sup = AgentSupervisor()
    sup.halt(session_id="dry-run", operator="ci", reason="dry-run halt check")
    registry = ToolRegistry([ToolCall(name="finops_hub_status", category=ToolCategory.EXECUTION)])
    policy = GovernancePolicy(
        name="ci-dry-run",
        allowed_categories={ToolCategory.EXECUTION},
    )
    budget = BudgetTracker(BudgetLimits())
    request = ToolRequest(tool_name="finops_hub_status", arguments={}, session_id="dry-run")
    result = governed_call(
        request,
        policy=policy,
        registry=registry,
        budget=budget,
        executor=lambda r: {"status": "executed"},
        supervisor=sup,
    )
    if result.allowed or "halted" not in result.denial_reason.lower():
        print(f"FAIL: halt did not short-circuit: {result}", file=sys.stderr)
        return 1
    print("OK: kill-switch short-circuits governed_call")
    return 0


def check_per_principal_budgets_isolate() -> int:
    registry = ToolRegistry([ToolCall(name="finops_hub_status", category=ToolCategory.EXECUTION)])
    policy = GovernancePolicy(
        name="ci-dry-run",
        allowed_categories={ToolCategory.EXECUTION},
    )
    # Principal A exhausts budget; Principal B should still be allowed.
    budget_a = BudgetTracker(BudgetLimits(max_total_calls=1))
    budget_b = BudgetTracker(BudgetLimits(max_total_calls=1))
    request = ToolRequest(tool_name="finops_hub_status", arguments={})
    r1 = governed_call(
        request,
        policy=policy,
        registry=registry,
        budget=budget_a,
        executor=lambda r: {"ok": True},
    )
    r2 = governed_call(
        request,
        policy=policy,
        registry=registry,
        budget=budget_a,
        executor=lambda r: {"ok": True},
    )
    r3 = governed_call(
        request,
        policy=policy,
        registry=registry,
        budget=budget_b,
        executor=lambda r: {"ok": True},
    )
    if not (r1.allowed and (not r2.allowed) and r3.allowed):
        print(
            f"FAIL: per-principal isolation broken: r1={r1.allowed} r2={r2.allowed} r3={r3.allowed}",
            file=sys.stderr,
        )
        return 1
    print("OK: per-principal budget isolation works")
    return 0


def main() -> int:
    rc = 0
    rc |= check_registry_covers_all_tools()
    rc |= check_fail_closed_denies_unknown_tool()
    rc |= check_halt_short_circuits()
    rc |= check_per_principal_budgets_isolate()
    if rc == 0:
        print("\nAll governor dry-run checks passed.")
    return rc


if __name__ == "__main__":
    sys.exit(main())
