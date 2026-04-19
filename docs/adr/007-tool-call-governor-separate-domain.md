# ADR-007: Tool-Call Governor as a Separate Domain from Cost Policy

**Status:** Accepted
**Date:** 2026-04-17

## Context

The hub exposes an MCP server (`mcp_server/server.py`) that lets LLM agents invoke operational tools — creating policies, acknowledging alerts, querying the audit trail. An agent driving the MCP surface can exhibit the same failure modes a naive agent driving any tool ecosystem does: runaway loops, capability escalation, unreviewed bulk operations, argument abuse.

The pre-existing `PolicyEngine` (ADR-002) and `TaggingPolicyEngine` (ADR-006) govern *cloud-resource creation events* — a completely different subject. Reusing them for tool-call governance would force an unnatural mapping (`ResourceCreationEvent` is a cloud event, not a tool invocation) and conflate two threat models.

Options considered:

1. **Extend `CostPolicy`** to carry tool-call budgets and allow-lists — violates ADR-002's single-purpose intent and muddles per-event cost evaluation with per-call tool governance.
2. **Rely on prompt engineering / system prompts** — not auditable, trivially bypassable, no structured artifacts.
3. **Separate `ToolGovernor` domain** — declarative `GovernancePolicy`, explicit `ToolCategory`, budget tracker, single `governed_call()` enforcement point, structured artifacts for audit.

## Decision

Introduce a **third policy domain** dedicated to governing tool calls against the MCP server:

- **Module:** `core/tool_governor.py`.
- **Request/result schemas:** `ToolRequest` and `ToolResult` (Pydantic, `schema_version`). Agents emit requests; they never call MCP directly.
- **Policy model:** `GovernancePolicy` — allow/deny lists, category allow-set, `require_approval_tools`, embedded `BudgetLimits`, fail-closed `default_allow=False`.
- **Categorization:** `ToolCategory` (`discovery` / `connection` / `execution` / `other`). Assigned at registry time, not inferred from names. Prevents substring-matching brittleness.
- **Budget tracker:** `BudgetTracker` enforces total / per-tool / runtime / parallelism caps plus optional connection↔execution separation.
- **Enforcement point:** `governed_call()` is the *sole* chokepoint. It applies the policy, runs argument gates, checks budget admission, handles the approval handshake, executes under the budget, and emits artifacts.
- **Argument gates:** baseline validators for `exa_web_search`, `exa_code_search`, `serpapi_search` (query length, num_results caps) — pluggable by caller.
- **Structured artifacts:** `policy_decision`, `tool_call_log`, `approval_request`, `approval_log`, `budget_stats`, `result_summary`.
- **Audit report:** `AuditReportGenerator` renders summary, denial breakdown, budget snapshot, recommendations from the artifact stream.
- **Integration:** `mcp_server.server.handle_tool_call()` routes every call through `governed_call()`. Disabled by default (`default_allow=True`) to preserve existing behaviour; enabled via `hub.governor.enabled=true` for fail-closed operation.

## Consequences

**Benefits:**
- Governance decisions are captured as structured JSON artifacts — trivially exportable to SIEM or compliance systems.
- Fail-closed by default when enabled: unknown tools are denied without any policy change.
- The cost governor remains lean; ADR-002's single-purpose `CostPolicy` is preserved.
- Separation-of-duties rules (connection vs execution) are declarative, not encoded in prompts.
- Budget exhaustion, runaway loops, and bulk-action attempts are caught at admission time, not after.

**Tradeoffs:**
- Three policy domains now coexist (`CostPolicy`, `TaggingPolicy`, `GovernancePolicy`). Mitigation: each lives in a separate module with no shared state; categories and severities re-use enums from `core/models.py` where relevant.
- The governor's in-memory budget tracker does not persist across process restarts. Acceptable for the current stdio-session model; long-running deployments can export `budget.stats()` at checkpoint boundaries.
- Approval workflow is currently a synchronous callback. Production deployments are expected to wire this to Slack/email/ticket queues — noted in `core/tool_governor.py` docstring.

## Related

- ADR-001 — Policy-as-code with JSON files (cost). This ADR keeps the tool governor in code, not JSON, because it operates on trusted in-process configuration, not user-editable rules.
- ADR-002 — Single `CostPolicy` model. This ADR complements it by placing tool-call governance in its own domain.
- ADR-006 — Tagging governance as a separate domain. Same structural argument: separate domain, shared enums, own engine.
