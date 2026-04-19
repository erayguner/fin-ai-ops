"""Tool-call governance layer for MCP and agentic tool execution.

This module is a *separate domain* from the FinOps cost governor. Where
``PolicyEngine`` governs cloud-resource creation events, this governor
governs an LLM agent's tool-call attempts against MCP-style tool servers.

The design follows a structured-sandboxing pattern: the agent emits
``ToolRequest`` objects, never calling MCP directly; ``governed_call`` is
the sole enforcement point; every allow/deny decision is logged as a
machine-readable artifact for later audit reporting.

Key concepts:

* :class:`ToolCategory` — explicit classification (discovery / connection /
  execution / other) so separation rules can be stated declaratively.
* :class:`GovernancePolicy` — declarative allow/deny rules plus budget
  and argument constraints.
* :class:`BudgetTracker` — enforces call counts, parallelism, runtime,
  and connection/execution separation.
* :class:`governed_call` — single chokepoint; applies policy, budget,
  argument validators, and optional approval gate.
* :class:`Artifact` + :class:`AuditReportGenerator` — structured,
  machine-readable audit trail.

Fail-closed: unknown tools default to deny.
"""

from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1"

__all__ = [
    "SCHEMA_VERSION",
    "Artifact",
    "ArtifactType",
    "AuditReportGenerator",
    "BudgetLimits",
    "BudgetTracker",
    "Decision",
    "GovernancePolicy",
    "GovernorError",
    "ToolCall",
    "ToolCategory",
    "ToolRegistry",
    "ToolRequest",
    "ToolResult",
    "governed_call",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ToolCategory(StrEnum):
    """Explicit category for separation-of-duties rules.

    Substring matching on tool names is brittle; registries should assign
    an explicit category at discovery time.
    """

    DISCOVERY = "discovery"
    CONNECTION = "connection"
    EXECUTION = "execution"
    OTHER = "other"


class Decision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


class ArtifactType(StrEnum):
    POLICY_DECISION = "policy_decision"
    TOOL_CALL_LOG = "tool_call_log"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_LOG = "approval_log"
    BUDGET_STATS = "budget_stats"
    RESULT_SUMMARY = "result_summary"


class GovernorError(Exception):
    """Raised when governance blocks a call and the caller wants to abort."""


# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


class ToolRequest(BaseModel):
    """Structured request emitted by the agent. Never calls MCP directly."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    requester: str = "agent"
    reason: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolResult(BaseModel):
    """Structured result returned after (or instead of) a tool call."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    request_id: str
    tool_name: str
    decision: Decision
    allowed: bool
    output: Any = None
    error: str = ""
    error_type: str = ""
    denial_reason: str = ""
    duration_ms: float = 0.0
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ToolCall(BaseModel):
    """Registry entry describing a known tool."""

    name: str
    category: ToolCategory = ToolCategory.OTHER
    description: str = ""


class BudgetLimits(BaseModel):
    max_total_calls: int = 0  # 0 => unlimited
    max_calls_per_tool: int = 0
    max_runtime_seconds: float = 0.0
    max_parallel: int = 1
    enforce_connection_execution_separation: bool = False


class GovernancePolicy(BaseModel):
    """Declarative policy controlling tool access.

    The three canonical profiles in the notebook map cleanly:

    * Discovery-only:    ``allowed_categories={DISCOVERY}``
    * Restricted SerpApi: ``require_approval_tools={"serpapi.*"}``
    * Budget-limited:     ``budget=BudgetLimits(max_total_calls=3)``
    """

    schema_version: str = Field(default=SCHEMA_VERSION)
    name: str
    description: str = ""
    allowed_tools: set[str] = Field(default_factory=set)
    denied_tools: set[str] = Field(default_factory=set)
    allowed_categories: set[ToolCategory] = Field(default_factory=set)
    require_approval_tools: set[str] = Field(default_factory=set)
    budget: BudgetLimits = Field(default_factory=BudgetLimits)
    # fail-closed by default: unknown tools are denied
    default_allow: bool = False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ToolRegistry:
    """Maps tool names to their category. Populated from MCP discovery.

    Unknown tools default to :attr:`ToolCategory.OTHER` which, combined with
    a policy that only allows specific categories, yields fail-closed
    behaviour.
    """

    def __init__(self, entries: Iterable[ToolCall] | None = None) -> None:
        self._entries: dict[str, ToolCall] = {}
        if entries:
            for e in entries:
                self.register(e)

    def register(self, entry: ToolCall) -> None:
        self._entries[entry.name] = entry

    def category_of(self, tool_name: str) -> ToolCategory:
        entry = self._entries.get(tool_name)
        return entry.category if entry else ToolCategory.OTHER

    def known(self, tool_name: str) -> bool:
        return tool_name in self._entries

    def names(self) -> list[str]:
        return sorted(self._entries.keys())


# ---------------------------------------------------------------------------
# Budget tracker
# ---------------------------------------------------------------------------


class BudgetTracker:
    """Tracks call counts, runtime, parallelism and separation rules.

    All state is in-memory and per-tracker, matching the ephemeral-session
    model in the notebook. Persist by exporting :meth:`stats`.
    """

    def __init__(self, limits: BudgetLimits) -> None:
        self._limits = limits
        self._start = time.monotonic()
        self._total_calls = 0
        self._per_tool: Counter[str] = Counter()
        self._in_flight = 0
        self._connection_seen = False
        self._execution_seen = False

    @property
    def limits(self) -> BudgetLimits:
        return self._limits

    def can_admit(self, tool_name: str, category: ToolCategory) -> tuple[bool, str]:
        lim = self._limits
        if lim.max_total_calls and self._total_calls >= lim.max_total_calls:
            return False, f"total-call budget exhausted ({lim.max_total_calls})"
        if lim.max_calls_per_tool and self._per_tool[tool_name] >= lim.max_calls_per_tool:
            return False, f"per-tool budget exhausted for {tool_name}"
        if lim.max_runtime_seconds and self.elapsed() >= lim.max_runtime_seconds:
            return False, f"runtime budget exhausted ({lim.max_runtime_seconds:.1f}s)"
        if lim.max_parallel and self._in_flight >= lim.max_parallel:
            return False, f"parallelism cap reached ({lim.max_parallel})"
        if lim.enforce_connection_execution_separation:
            if category is ToolCategory.EXECUTION and self._connection_seen:
                return False, "separation violation: connection-then-execution"
            if category is ToolCategory.CONNECTION and self._execution_seen:
                return False, "separation violation: execution-then-connection"
        return True, ""

    def acquire(self, tool_name: str, category: ToolCategory) -> None:
        self._total_calls += 1
        self._per_tool[tool_name] += 1
        self._in_flight += 1
        if category is ToolCategory.CONNECTION:
            self._connection_seen = True
        elif category is ToolCategory.EXECUTION:
            self._execution_seen = True

    def release(self) -> None:
        if self._in_flight > 0:
            self._in_flight -= 1

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def stats(self) -> dict[str, Any]:
        return {
            "total_calls": self._total_calls,
            "per_tool": dict(self._per_tool),
            "elapsed_seconds": round(self.elapsed(), 3),
            "in_flight": self._in_flight,
            "connection_seen": self._connection_seen,
            "execution_seen": self._execution_seen,
            "limits": self._limits.model_dump(),
        }


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class Artifact(BaseModel):
    """Machine-readable governance artifact (for A2A-style audit pipelines)."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    artifact_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: ArtifactType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    request_id: str = ""
    tool_name: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Argument gates
# ---------------------------------------------------------------------------

ArgumentGate = Callable[[ToolRequest], tuple[bool, str]]


def _default_gates() -> dict[str, ArgumentGate]:
    """Baseline argument validators for common web-search tools.

    These mirror the Exa/SerpApi gates in the reference notebook: query
    length caps and result-count caps to stop bulk or runaway behaviour.
    """

    def _bounded_query(req: ToolRequest) -> tuple[bool, str]:
        q = req.arguments.get("query", "")
        if not isinstance(q, str):
            return False, "query must be string"
        if len(q) > 512:
            return False, f"query too long ({len(q)} > 512)"
        num = req.arguments.get("num_results", req.arguments.get("limit", 0))
        if isinstance(num, int) and num > 20:
            return False, f"num_results too large ({num} > 20)"
        return True, ""

    return {
        "exa_web_search": _bounded_query,
        "exa_code_search": _bounded_query,
        "serpapi_search": _bounded_query,
    }


# ---------------------------------------------------------------------------
# Approval handler protocol
# ---------------------------------------------------------------------------

ApprovalHandler = Callable[[ToolRequest], bool]


# ---------------------------------------------------------------------------
# governed_call — the one and only enforcement point
# ---------------------------------------------------------------------------


def _policy_allows(
    policy: GovernancePolicy, tool_name: str, category: ToolCategory
) -> tuple[Decision, str]:
    if tool_name in policy.denied_tools:
        return Decision.DENY, f"tool '{tool_name}' is in deny list"
    if tool_name in policy.require_approval_tools:
        return Decision.APPROVAL_REQUIRED, f"tool '{tool_name}' requires approval"
    if tool_name in policy.allowed_tools:
        return Decision.ALLOW, "tool in allow list"
    if policy.allowed_categories and category in policy.allowed_categories:
        return Decision.ALLOW, f"category '{category.value}' allowed"
    if policy.allowed_categories and category not in policy.allowed_categories:
        return Decision.DENY, f"category '{category.value}' not in allow list"
    if policy.default_allow:
        return Decision.ALLOW, "default allow"
    return Decision.DENY, "fail-closed: no matching allow rule"


def governed_call(
    request: ToolRequest,
    *,
    policy: GovernancePolicy,
    registry: ToolRegistry,
    budget: BudgetTracker,
    executor: Callable[[ToolRequest], Any],
    artifacts: list[Artifact] | None = None,
    argument_gates: dict[str, ArgumentGate] | None = None,
    approval_handler: ApprovalHandler | None = None,
) -> ToolResult:
    """Single enforcement point for all MCP access.

    All decisions — allow, deny, approval — are logged as structured
    artifacts against the shared ``artifacts`` list. The model never
    reaches ``executor`` unless the decision is ALLOW.

    Raises nothing on deny; returns a ``ToolResult`` with ``allowed=False``.
    """
    artifacts = artifacts if artifacts is not None else []
    gates = argument_gates if argument_gates is not None else _default_gates()

    def _log(
        atype: ArtifactType, payload: dict[str, Any], *, tool: str = request.tool_name
    ) -> None:
        artifacts.append(
            Artifact(
                type=atype,
                request_id=request.request_id,
                tool_name=tool,
                payload=payload,
            )
        )

    category = registry.category_of(request.tool_name)

    # 1. Policy check
    decision, reason = _policy_allows(policy, request.tool_name, category)

    # 2. Argument gate (only meaningful if policy didn't already deny)
    if decision is not Decision.DENY:
        gate = gates.get(request.tool_name)
        if gate is not None:
            ok, msg = gate(request)
            if not ok:
                decision, reason = Decision.DENY, f"argument gate: {msg}"

    # 3. Budget admission (only if still allowed/approval-required)
    if decision is not Decision.DENY:
        ok, msg = budget.can_admit(request.tool_name, category)
        if not ok:
            decision, reason = Decision.DENY, msg

    _log(
        ArtifactType.POLICY_DECISION,
        {
            "decision": decision.value,
            "reason": reason,
            "category": category.value,
            "policy": policy.name,
            "budget": budget.stats(),
        },
    )

    # 4. Approval handshake
    if decision is Decision.APPROVAL_REQUIRED:
        _log(
            ArtifactType.APPROVAL_REQUEST,
            {"reason": reason, "arguments": request.arguments},
        )
        approved = bool(approval_handler and approval_handler(request))
        _log(
            ArtifactType.APPROVAL_LOG,
            {"approved": approved, "reason": reason},
        )
        if not approved:
            result = ToolResult(
                request_id=request.request_id,
                tool_name=request.tool_name,
                decision=Decision.DENY,
                allowed=False,
                denial_reason=f"approval denied: {reason}",
            )
            _log(ArtifactType.RESULT_SUMMARY, {"status": "denied", "reason": result.denial_reason})
            return result
        decision = Decision.ALLOW

    # 5. Deny short-circuit
    if decision is Decision.DENY:
        result = ToolResult(
            request_id=request.request_id,
            tool_name=request.tool_name,
            decision=Decision.DENY,
            allowed=False,
            denial_reason=reason,
        )
        _log(ArtifactType.RESULT_SUMMARY, {"status": "denied", "reason": reason})
        return result

    # 6. Execute under budget
    budget.acquire(request.tool_name, category)
    start = time.monotonic()
    try:
        output = executor(request)
        duration_ms = (time.monotonic() - start) * 1000
        result = ToolResult(
            request_id=request.request_id,
            tool_name=request.tool_name,
            decision=Decision.ALLOW,
            allowed=True,
            output=output,
            duration_ms=duration_ms,
        )
        _log(
            ArtifactType.TOOL_CALL_LOG,
            {
                "arguments": request.arguments,
                "output": _summarise(output),
                "duration_ms": duration_ms,
            },
        )
        _log(ArtifactType.RESULT_SUMMARY, {"status": "ok", "duration_ms": duration_ms})
        return result
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000
        result = ToolResult(
            request_id=request.request_id,
            tool_name=request.tool_name,
            decision=Decision.ALLOW,
            allowed=True,
            error=str(exc),
            error_type=type(exc).__name__,
            duration_ms=duration_ms,
        )
        _log(
            ArtifactType.TOOL_CALL_LOG,
            {"arguments": request.arguments, "error": str(exc), "duration_ms": duration_ms},
        )
        _log(ArtifactType.RESULT_SUMMARY, {"status": "error", "error": str(exc)})
        return result
    finally:
        budget.release()
        _log(ArtifactType.BUDGET_STATS, budget.stats())


def _summarise(output: Any, *, max_chars: int = 512) -> Any:
    """Best-effort summary of tool output for artifact payload."""
    if output is None:
        return None
    if isinstance(output, str):
        return output if len(output) <= max_chars else output[:max_chars] + "…"
    if isinstance(output, (int, float, bool)):
        return output
    if isinstance(output, (list, tuple)):
        return {"type": "list", "length": len(output)}
    if isinstance(output, dict):
        return {"type": "dict", "keys": sorted(output)[:20]}
    return {"type": type(output).__name__}


# ---------------------------------------------------------------------------
# Audit report
# ---------------------------------------------------------------------------


class AuditReportGenerator:
    """Collects artifacts and renders machine-readable audit reports."""

    def __init__(self, artifacts: list[Artifact]) -> None:
        self._artifacts = artifacts

    def _by_type(self, atype: ArtifactType) -> list[Artifact]:
        return [a for a in self._artifacts if a.type is atype]

    def summary(self) -> dict[str, Any]:
        decisions = self._by_type(ArtifactType.POLICY_DECISION)
        allowed = sum(1 for d in decisions if d.payload.get("decision") == Decision.ALLOW.value)
        denied = sum(1 for d in decisions if d.payload.get("decision") == Decision.DENY.value)
        approvals = self._by_type(ArtifactType.APPROVAL_LOG)
        approved = sum(1 for a in approvals if a.payload.get("approved"))
        return {
            "total_requests": len(decisions),
            "allowed": allowed,
            "denied": denied,
            "approval_requests": len(approvals),
            "approved": approved,
            "tool_calls": len(self._by_type(ArtifactType.TOOL_CALL_LOG)),
        }

    def denial_breakdown(self) -> dict[str, int]:
        counter: Counter[str] = Counter()
        for d in self._by_type(ArtifactType.POLICY_DECISION):
            if d.payload.get("decision") == Decision.DENY.value:
                counter[d.payload.get("reason", "unknown")] += 1
        return dict(counter)

    def budget_snapshot(self) -> dict[str, Any]:
        stats_artifacts = self._by_type(ArtifactType.BUDGET_STATS)
        if not stats_artifacts:
            return {}
        return stats_artifacts[-1].payload

    def recommendations(self) -> list[str]:
        recs: list[str] = []
        denials = self.denial_breakdown()
        if any("budget" in r for r in denials):
            recs.append(
                "Budget exhaustion detected — consider tightening argument gates "
                "or raising limits if usage is legitimate."
            )
        if any("separation" in r for r in denials):
            recs.append(
                "Connection/execution separation violated — review agent planning "
                "to avoid mixing phases in one trajectory."
            )
        if any("fail-closed" in r for r in denials):
            recs.append(
                "Unknown-tool denials observed — extend the registry or widen "
                "the policy if these tools are expected."
            )
        if not recs:
            recs.append("No anomalies detected.")
        return recs

    def render(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "generated_at": datetime.now(UTC).isoformat(),
            "summary": self.summary(),
            "denial_breakdown": self.denial_breakdown(),
            "budget": self.budget_snapshot(),
            "recommendations": self.recommendations(),
            "artifacts": [a.model_dump(mode="json") for a in self._artifacts],
        }
