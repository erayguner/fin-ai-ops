"""Tests for the MCP tool-call governance layer."""

from __future__ import annotations

from typing import Any

import pytest
from core.tool_governor import (
    Artifact,
    ArtifactType,
    AuditReportGenerator,
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _exec_echo(req: ToolRequest) -> dict[str, Any]:
    return {"echoed": req.arguments}


def _make_registry() -> ToolRegistry:
    return ToolRegistry(
        [
            ToolCall(name="exa_web_search", category=ToolCategory.DISCOVERY),
            ToolCall(name="exa_code_search", category=ToolCategory.DISCOVERY),
            ToolCall(name="serpapi_search", category=ToolCategory.EXECUTION),
            ToolCall(name="mcp_connect", category=ToolCategory.CONNECTION),
            ToolCall(name="mcp_execute", category=ToolCategory.EXECUTION),
        ]
    )


# ---------------------------------------------------------------------------
# Registry + categorization
# ---------------------------------------------------------------------------


class TestToolRegistry:
    def test_known_tool_returns_its_category(self) -> None:
        reg = _make_registry()
        assert reg.category_of("exa_web_search") is ToolCategory.DISCOVERY

    def test_unknown_tool_defaults_to_other(self) -> None:
        reg = _make_registry()
        assert reg.category_of("who_knows") is ToolCategory.OTHER
        assert reg.known("who_knows") is False


# ---------------------------------------------------------------------------
# Policies: discovery-only / restricted / budget-limited
# ---------------------------------------------------------------------------


class TestDiscoveryOnlyPolicy:
    def test_discovery_tool_allowed(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(
            name="discovery-only", allowed_categories={ToolCategory.DISCOVERY}
        )
        budget = BudgetTracker(BudgetLimits())
        artifacts: list[Artifact] = []

        result = governed_call(
            ToolRequest(tool_name="exa_web_search", arguments={"query": "cats"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
            artifacts=artifacts,
        )
        assert result.allowed is True
        assert result.decision is Decision.ALLOW

    def test_execution_tool_blocked(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(
            name="discovery-only", allowed_categories={ToolCategory.DISCOVERY}
        )
        budget = BudgetTracker(BudgetLimits())

        result = governed_call(
            ToolRequest(tool_name="serpapi_search", arguments={"query": "cats"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        assert result.allowed is False
        assert "execution" in result.denial_reason


class TestRestrictedApprovalPolicy:
    def test_approval_granted(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(
            name="restricted",
            allowed_categories={ToolCategory.DISCOVERY, ToolCategory.EXECUTION},
            require_approval_tools={"serpapi_search"},
        )
        budget = BudgetTracker(BudgetLimits())
        artifacts: list[Artifact] = []

        result = governed_call(
            ToolRequest(tool_name="serpapi_search", arguments={"query": "x"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
            artifacts=artifacts,
            approval_handler=lambda _req: True,
        )
        assert result.allowed is True
        approval_logs = [a for a in artifacts if a.type is ArtifactType.APPROVAL_LOG]
        assert approval_logs and approval_logs[0].payload["approved"] is True

    def test_approval_denied(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(
            name="restricted",
            allowed_categories={ToolCategory.EXECUTION},
            require_approval_tools={"serpapi_search"},
        )
        budget = BudgetTracker(BudgetLimits())

        result = governed_call(
            ToolRequest(tool_name="serpapi_search", arguments={"query": "x"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
            approval_handler=lambda _req: False,
        )
        assert result.allowed is False
        assert "approval denied" in result.denial_reason


class TestBudgetLimitedPolicy:
    def test_total_calls_exhausted_after_three(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(
            name="budget",
            allowed_categories={ToolCategory.DISCOVERY},
        )
        budget = BudgetTracker(BudgetLimits(max_total_calls=3))

        outcomes = []
        for _ in range(5):
            r = governed_call(
                ToolRequest(tool_name="exa_web_search", arguments={"query": "q"}),
                policy=policy,
                registry=reg,
                budget=budget,
                executor=_exec_echo,
            )
            outcomes.append(r.allowed)
        assert outcomes == [True, True, True, False, False]

    def test_per_tool_budget(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(
            name="budget",
            allowed_categories={ToolCategory.DISCOVERY},
        )
        budget = BudgetTracker(BudgetLimits(max_calls_per_tool=1))

        r1 = governed_call(
            ToolRequest(tool_name="exa_web_search", arguments={"query": "q"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        r2 = governed_call(
            ToolRequest(tool_name="exa_web_search", arguments={"query": "q"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        # Different tool still ok
        r3 = governed_call(
            ToolRequest(tool_name="exa_code_search", arguments={"query": "q"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        assert (r1.allowed, r2.allowed, r3.allowed) == (True, False, True)


# ---------------------------------------------------------------------------
# Separation of connection / execution
# ---------------------------------------------------------------------------


class TestSeparation:
    def test_connect_then_execute_blocked(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(
            name="separation",
            allowed_categories={ToolCategory.CONNECTION, ToolCategory.EXECUTION},
        )
        budget = BudgetTracker(BudgetLimits(enforce_connection_execution_separation=True))

        r1 = governed_call(
            ToolRequest(tool_name="mcp_connect"),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        r2 = governed_call(
            ToolRequest(tool_name="mcp_execute"),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        assert r1.allowed is True
        assert r2.allowed is False
        assert "separation" in r2.denial_reason


# ---------------------------------------------------------------------------
# Argument validation gates
# ---------------------------------------------------------------------------


class TestArgumentGates:
    def test_query_too_long_rejected(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(name="discovery", allowed_categories={ToolCategory.DISCOVERY})
        budget = BudgetTracker(BudgetLimits())

        result = governed_call(
            ToolRequest(tool_name="exa_web_search", arguments={"query": "x" * 600}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        assert result.allowed is False
        assert "query too long" in result.denial_reason

    def test_too_many_results_rejected(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(name="discovery", allowed_categories={ToolCategory.DISCOVERY})
        budget = BudgetTracker(BudgetLimits())

        result = governed_call(
            ToolRequest(
                tool_name="exa_web_search",
                arguments={"query": "q", "num_results": 100},
            ),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        assert result.allowed is False
        assert "num_results too large" in result.denial_reason


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


class TestFailClosed:
    def test_unknown_tool_denied_by_default(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(name="strict", allowed_categories={ToolCategory.DISCOVERY})
        budget = BudgetTracker(BudgetLimits())
        result = governed_call(
            ToolRequest(tool_name="mystery_tool"),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
        )
        assert result.allowed is False
        assert result.decision is Decision.DENY


# ---------------------------------------------------------------------------
# Structured artifacts + audit report
# ---------------------------------------------------------------------------


class TestArtifactsAndReport:
    def test_every_call_emits_policy_decision_and_budget_stats(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(name="x", allowed_categories={ToolCategory.DISCOVERY})
        budget = BudgetTracker(BudgetLimits())
        artifacts: list[Artifact] = []
        governed_call(
            ToolRequest(tool_name="exa_web_search", arguments={"query": "q"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
            artifacts=artifacts,
        )
        types = {a.type for a in artifacts}
        assert ArtifactType.POLICY_DECISION in types
        assert ArtifactType.TOOL_CALL_LOG in types
        assert ArtifactType.BUDGET_STATS in types
        assert ArtifactType.RESULT_SUMMARY in types

    def test_audit_report_summary(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(name="x", allowed_categories={ToolCategory.DISCOVERY})
        budget = BudgetTracker(BudgetLimits(max_total_calls=2))
        artifacts: list[Artifact] = []
        # 2 allows, 1 deny due to budget, 1 deny for unknown tool
        for _ in range(3):
            governed_call(
                ToolRequest(tool_name="exa_web_search", arguments={"query": "q"}),
                policy=policy,
                registry=reg,
                budget=budget,
                executor=_exec_echo,
                artifacts=artifacts,
            )
        governed_call(
            ToolRequest(tool_name="mystery"),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=_exec_echo,
            artifacts=artifacts,
        )

        report = AuditReportGenerator(artifacts).render()
        assert report["summary"]["allowed"] == 2
        assert report["summary"]["denied"] == 2
        breakdown = report["denial_breakdown"]
        assert any("budget" in k for k in breakdown)
        assert any("fail-closed" in k for k in breakdown) or any("category" in k for k in breakdown)
        assert isinstance(report["recommendations"], list)
        assert report["recommendations"]

    def test_executor_exception_captured_in_artifact(self) -> None:
        reg = _make_registry()
        policy = GovernancePolicy(name="x", allowed_categories={ToolCategory.DISCOVERY})
        budget = BudgetTracker(BudgetLimits())
        artifacts: list[Artifact] = []

        def bad_executor(_req: ToolRequest) -> Any:
            raise RuntimeError("boom")

        result = governed_call(
            ToolRequest(tool_name="exa_web_search", arguments={"query": "q"}),
            policy=policy,
            registry=reg,
            budget=budget,
            executor=bad_executor,
            artifacts=artifacts,
        )
        assert result.allowed is True
        assert result.error == "boom"
        assert result.error_type == "RuntimeError"
        call_logs = [a for a in artifacts if a.type is ArtifactType.TOOL_CALL_LOG]
        assert call_logs and "error" in call_logs[0].payload


# ---------------------------------------------------------------------------
# BudgetTracker unit behaviour
# ---------------------------------------------------------------------------


class TestBudgetTracker:
    def test_acquire_release_decrements_in_flight(self) -> None:
        budget = BudgetTracker(BudgetLimits(max_parallel=1))
        budget.acquire("t", ToolCategory.EXECUTION)
        ok, _ = budget.can_admit("t", ToolCategory.EXECUTION)
        assert ok is False  # parallelism cap
        budget.release()
        ok, _ = budget.can_admit("t", ToolCategory.EXECUTION)
        assert ok is True

    def test_stats_snapshot_has_required_keys(self) -> None:
        budget = BudgetTracker(BudgetLimits(max_total_calls=5))
        budget.acquire("t", ToolCategory.DISCOVERY)
        stats = budget.stats()
        for key in ("total_calls", "per_tool", "elapsed_seconds", "limits"):
            assert key in stats


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
