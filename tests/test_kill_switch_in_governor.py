"""Verify governed_call short-circuits when the supervisor reports a halt."""

from __future__ import annotations

from core.agent_supervisor import AgentSupervisor
from core.tool_governor import (
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


def _setup():
    registry = ToolRegistry([ToolCall(name="finops_x", category=ToolCategory.EXECUTION)])
    policy = GovernancePolicy(
        name="kill-switch-test",
        allowed_categories={ToolCategory.EXECUTION},
    )
    budget = BudgetTracker(BudgetLimits())
    return registry, policy, budget


def test_halt_short_circuits_governed_call() -> None:
    registry, policy, budget = _setup()
    sup = AgentSupervisor()
    sup.halt(session_id="halted-1", operator="security", reason="anomaly")
    req = ToolRequest(tool_name="finops_x", arguments={}, session_id="halted-1")
    called = []

    def _executor(r):
        called.append(r.tool_name)
        return {"ok": True}

    result = governed_call(
        req,
        policy=policy,
        registry=registry,
        budget=budget,
        executor=_executor,
        supervisor=sup,
    )
    assert not result.allowed
    assert result.decision is Decision.DENY
    assert "halted" in result.denial_reason.lower()
    assert called == []  # executor never reached


def test_no_session_id_skips_halt_check() -> None:
    """A request without session_id should not consult the supervisor."""
    registry, policy, budget = _setup()
    sup = AgentSupervisor()
    sup.halt(session_id="halted-2", operator="security")
    req = ToolRequest(tool_name="finops_x", arguments={}, session_id="")
    result = governed_call(
        req,
        policy=policy,
        registry=registry,
        budget=budget,
        executor=lambda r: {"ok": True},
        supervisor=sup,
    )
    assert result.allowed


def test_resumed_session_allowed_again() -> None:
    registry, policy, budget = _setup()
    sup = AgentSupervisor()
    sup.halt(session_id="halt-3", operator="op")
    req = ToolRequest(tool_name="finops_x", arguments={}, session_id="halt-3")
    blocked = governed_call(
        req,
        policy=policy,
        registry=registry,
        budget=budget,
        executor=lambda r: {"ok": True},
        supervisor=sup,
    )
    assert not blocked.allowed
    sup.resume(session_id="halt-3", operator="op", reason="lifted")
    allowed = governed_call(
        req,
        policy=policy,
        registry=registry,
        budget=budget,
        executor=lambda r: {"ok": True},
        supervisor=sup,
    )
    assert allowed.allowed


def test_governed_call_without_supervisor_still_works() -> None:
    """Backwards-compatible: supervisor is optional."""
    registry, policy, budget = _setup()
    req = ToolRequest(tool_name="finops_x", arguments={}, session_id="s-1")
    result = governed_call(
        req,
        policy=policy,
        registry=registry,
        budget=budget,
        executor=lambda r: {"ok": True},
    )
    assert result.allowed
