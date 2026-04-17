# ADR-008: Agent Governance Model — End-to-End Traceability, Approval, and Override

**Status:** Accepted
**Date:** 2026-04-17

## Context

ADRs 001–007 govern *what the hub does*: policies-as-code (001), unified
cost policy (002), per-event evaluation (003), event-driven alerts (004),
statistical thresholds (005), tagging domain (006), and the tool-call
governor for MCP (007). None of them govern the *agents themselves* —
the Bedrock Agent on AWS (`providers/aws/agents/finops_agent.py`) and the
ADK agent on GCP (`providers/gcp/agents/finops_agent.py`).

Phase 1 gap analysis (`docs/governance/PHASE1_GAP_REPORT.md`) identified
25 gaps — 11 P0 — that prevent safe, enterprise-scale agentic operation.
Paraphrased, the missing capabilities are:

- End-to-end traceability: Bedrock `TracePart` and ADK reasoning steps
  are emitted but never persisted to the audit trail.
- Explainability: no structured record of *why* an agent chose an action.
- Human override: `approval_handler` is an in-process callable; there is
  no out-of-band channel and no kill-switch.
- Behavioural anomaly detection on the agent itself (distinct from the
  existing cost anomaly detector).
- Tamper-evidence across process restarts: the chained-checksum audit
  log forks silently on rehydration.
- Fail-closed default: the tool-call governor ships fail-open.
- Provider guardrails are opt-in in code but have empty content in
  Terraform (Bedrock Guardrails) or are not wired at all (Model Armor).

The requirement is to design a governance model that makes every agent
decision, tool call, action, and outcome traceable, reviewable, and
overridable, while staying aligned with provider best practices (Bedrock
traces / RoC / Guardrails; ADK callbacks+plugins / Model Armor / Vertex
safety settings).

Options considered:

1. **Extend `CostPolicy` / `GovernancePolicy`** to carry agent-lifecycle
   concerns — violates the single-purpose decisions in ADRs 002, 006, 007
   and conflates cloud-event governance with agent-step governance.
2. **Rely purely on provider primitives** (Bedrock traces in CloudWatch,
   ADK Logging plugin to Cloud Logging) — leaves the hub blind to the
   agents it orchestrates, blocks cross-provider correlation, and leaves
   the existing `AuditLogger` authoritative only for cost events.
3. **New agent-governance domain** alongside the existing three — with
   its own models (`AgentTrace`, `AgentStep`, `DecisionRecord`), its own
   enforcement surface (approval gateway + kill-switch + behavioural
   observer), and explicit ingestion adapters that translate provider
   traces into the shared model. This is the option chosen.

## Decision

Introduce a **fourth governance domain** dedicated to agent-lifecycle
events, with provider-agnostic primitives in `core/` and thin ingestion
adapters in `providers/`.

### 1. Canonical trace model — `core/agent_trace.py`

- **`AgentTrace`**: per-session container. Fields: `session_id`,
  `agent_name`, `provider`, `started_at`, `ended_at`, `steps`,
  `verdict` (`completed` / `halted` / `failed` / `approval_denied`),
  `correlation_id`.
- **`AgentStep`**: discriminated union keyed by `step_type` —
  `ModelInvocation`, `ToolInvocation`, `GuardrailEvaluation`,
  `ApprovalRequest`, `HumanOverride`, `FilterDecision`, `Failure`.
  Every step carries `step_id`, `parent_step_id`, `timestamp`,
  `rationale` (human-readable reason, captured from the provider
  trace when available), `actor`, and a `raw` blob with the
  provider-native payload.
- **`DecisionRecord`**: emitted whenever a policy/budget/filter/approval
  gate changes the agent's trajectory. Fields: `decision`
  (`allow` / `deny` / `approval_required` / `halt`), `reason`,
  `policy_id`, `gate_name`, and a back-reference to the `AgentStep`
  that triggered it.

All models follow the established pattern: Pydantic, `schema_version`,
UTC-aware timestamps.

### 2. Ingestion adapters (provider-specific, thin)

- **AWS**: `providers/aws/agent_trace_adapter.py` parses Bedrock
  `TracePart` → `AgentStep`. `OrchestrationTrace.modelInvocationInput`
  → `ModelInvocation`. `OrchestrationTrace.invocationInput` →
  `ToolInvocation`. `GuardrailTrace` → `GuardrailEvaluation`.
  `FailureTrace` → `Failure`. `callerChain` fills `correlation_id`.
- **GCP**: `providers/gcp/agent_trace_plugin.py` is an ADK `Plugin`
  subclass wired at `Runner` creation. Its `before_model` / `after_model`
  → `ModelInvocation`. `before_tool` / `after_tool` → `ToolInvocation`.
  Model Armor verdicts → `GuardrailEvaluation`.

Adapters never hold business logic; they translate provider payloads to
the canonical model and hand off to `AuditLogger.ingest_agent_trace`.

### 3. Audit hardening — `core/audit.py`

- `load_from_disk` now **verifies** the checksum chain across all
  rotated JSONL files, not only the per-file tail. Chain breaks raise
  `AuditChainBrokenError` instead of being silently reset.
- Daily rotation writes a signed **trail-head manifest**
  (`audit-YYYY-MM-DD.manifest.json`) carrying: the last entry checksum,
  the JSONL file SHA-256, entry count, and a detached JWS signature
  (Ed25519 in prod, HMAC-SHA256 fallback for dev).
- New `export_signed(...)` returns `(payload, signature)` using the same
  signing key; verifies without needing the internal checksum chain.

### 4. Approval + override primitive — `core/approvals.py`

- **`ApprovalRequest`**: session-scoped, carrying `approver_pool`,
  `expires_at`, `context` (the `AgentStep` that triggered it), and a
  one-time signed `decision_token`.
- **`ApprovalGateway` interface** with built-in implementations:
  - `LocalCLIApprovalGateway` — dev only; prints to stderr, reads
    from stdin.
  - `WebhookApprovalGateway` — POSTs to an HTTPS endpoint, resolves on
    callback with token.
  - `SlackApprovalGateway` — reuses `SlackDispatcher`; `Approve`/`Deny`
    buttons post signed tokens back to the webhook endpoint.
- **Bedrock RoC wiring**: action groups classified `high_risk` (currently:
  destructive IaC, spend-causing actions above
  `require_approval_above_usd`) flip to `customControl = RETURN_CONTROL`
  in Terraform. The hub consumes `invocationInputs` via an
  HTTP endpoint, invokes `ApprovalGateway`, and feeds
  `returnControlInvocationResults` back to Bedrock on approve.
- **Kill-switch**: `AgentSupervisor.halt(session_id, reason, actor)`
  writes a denylist entry; every in-flight `governed_call` checks the
  denylist and, if present, short-circuits to `Decision.DENY` with
  `halt_reason`. New MCP tools: `finops_pending_approvals`,
  `finops_respond_approval`, `finops_halt_session`.

### 5. Behavioural anomaly + drift detection — `core/agent_observer.py`

- Per-session rolling window of: call rate (calls/min), tool-call
  distribution (Counter + χ² vs baseline), cumulative token count,
  cumulative estimated cost.
- Uses the statistical primitives from `ThresholdEngine` (ADR-005) —
  mean + stddev, z-score thresholding — to stay consistent.
- Emits `CostAlert`-typed alerts (severity `HIGH` → page; `CRITICAL`
  → auto-halt via §4). Observability events feed the existing alert
  pipeline (ADR-004).

### 6. Platform content filters — `core/filters.py`

- `ContentFilter` interface. Implementations: `PIIRedactor`,
  `PromptInjectionHeuristic`, `SecretScanner`. Pure-Python, no network.
- Wired into three locations:
  - `mcp_server.server._redact_arguments` (replaces the hardcoded
    4-key list with a centralised scrubber).
  - GCP ADK `Runner` via a thin `FilterPlugin`.
  - AWS Bedrock action-group Lambda layer via a shared helper module.
- Every filter decision emits a `FilterDecision` `AgentStep`, so it is
  traceable end-to-end (closes G-X5 and G-X10).

### 7. Post-action review — MCP + CLI

- New MCP tool `finops_replay_session(session_id)` that reconstructs
  the full transcript from `AgentTrace` + `AuditEntry` + `CostAlert`
  streams. Returns structured JSON **and** a Markdown render suitable
  for pasting into review documents.
- New CLI subcommand `finops session show <session_id>` wrapping the
  same code path.

### 8. Fail-closed by default

- Flip `hub.governor.enabled` default to `true` in `core/config.py`.
- Replace the single global `_GOVERNOR_BUDGET` with a per-session
  `BudgetTracker` keyed by MCP principal. `finops_session_stats`
  exposes current usage.
- Migration: the default flip is called out in the PR description and
  release notes; any integration tests relying on fail-open get an
  explicit `default_allow=True` override at setup.

### 9. Provider guardrail wiring (thin Terraform + script changes)

- AWS Terraform:
  - `aws_bedrock_model_invocation_logging_configuration` → CloudWatch
    Log Group (KMS-CMK, 365-day retention).
  - Populated `aws_bedrockagent_guardrail` with explicit denied topics,
    PII filter list, and contextual grounding ≥ 0.7.
  - CloudTrail data-event selector for `bedrock-agent-runtime:*`.
- GCP: `scripts/gcp/enable_governance.sh` idempotent runner that
  creates a Model Armor template, enables Data-Access audit logs for
  `aiplatform.googleapis.com`, and attaches the template to the Vertex
  AI endpoint.
- ADK agent: add explicit `safety_settings` block on
  `GenerateContentConfig`.

### 10. Evaluation harness (drift regression)

- New `tests/agent_eval/` directory using ADK's `evaluate` framework
  over a curated golden set of FinOps prompts.
- Weekly CI run in `finops-self-maintain.yml`. Failures page the
  maintainer via the existing dispatcher stack.

## Consequences

**Benefits:**
- Every agent decision — model step, tool call, guardrail verdict,
  approval request, kill-switch — becomes a first-class `AgentStep`
  persisted in the tamper-evident audit log.
- Human approvers are in the loop via out-of-band channels that survive
  agent-process restarts; a destructive action cannot proceed until a
  signed token is returned.
- Kill-switch is one MCP call away. Any operator can halt a misbehaving
  agent without killing the process or revoking IAM credentials.
- Behavioural anomalies (unusual tool distribution, runaway call rate,
  token-cost blow-ups) are detected in the same pipeline that already
  handles cloud-cost anomalies — operators learn one alert surface.
- Provider traces (Bedrock, ADK) feed into the canonical model, so the
  hub produces a single transcript per session regardless of provider.
- The tamper-evident claim actually holds across process restarts: the
  checksum chain verifies across rotated files, and daily manifests are
  signed.
- Defaults are safe: fail-closed governor, guardrails populated,
  per-session budgets.

**Tradeoffs:**
- Four governance domains now coexist (`CostPolicy`, `TaggingPolicy`,
  `GovernancePolicy`, `AgentTrace`). They remain decoupled — shared
  enums only, no cross-module state. Mitigation: the CLAUDE.md domain
  table gets a fourth row; ADR-008 is referenced from ADRs 002/006/007.
- Adding `RETURN_CONTROL` for high-risk Bedrock action groups introduces
  a roundtrip (agent → hub → approver → hub → agent). Latency increases
  for those actions; acceptable because they are by definition
  human-reviewable.
- The per-session budget tracker increases state footprint linearly
  with concurrent sessions. Bounded by a `max_sessions` config and an
  LRU eviction with graceful degradation to a shared tracker if the
  bound is hit.
- Model Armor GA coverage is Vertex-first; MCP-server integration is
  Preview (2025-12-10). We use the Vertex path for core coverage and
  track the MCP integration as a roadmap item.
- ADK Plugin API is still evolving. Plugins kept thin (delegating to
  `core/` code) to limit breakage if the ABI shifts.

**Migration:**
- Phase 3 (two PRs or four sub-PRs — user selected four) implements
  §1–§4 and §6 first (cross-cutting primitives), then §5, §7, §8, §9,
  §10 in follow-ups.
- The governor default flip (§8) and checksum-chain tightening (§3) are
  breaking for callers that depend on silent-reset or fail-open
  behaviour. Both are documented as breaking in the changelog and
  carry a one-release deprecation window (configurable legacy flag).
- Existing 539 tests get audited during Phase 3. Any test that
  implicitly relied on fail-open `default_allow=True` gets an explicit
  override at setup rather than changing the production default.

## Related

- ADR-001 — policies as JSON files. AgentTrace is **not** user-editable
  JSON; it is a code-level canonical model consuming provider payloads.
- ADR-002 — single `CostPolicy`. Unchanged; agent governance lives in
  a separate domain.
- ADR-004 — event-driven alert pipeline. Agent observer emits into the
  same pipeline; no new alert surface.
- ADR-005 — statistical thresholds. Reused for behavioural anomaly
  detection so operators encounter one mental model.
- ADR-006 — tagging domain. Same structural argument: separate engine,
  shared enums, decoupled from cost policy.
- ADR-007 — tool-call governor. Extended here: `governed_call` becomes
  one of several `AgentStep` sources; approval handshake upgraded from
  in-process callable to out-of-band `ApprovalGateway`.
- `docs/governance/PHASE1_GAP_REPORT.md` — the full 25-gap analysis
  this ADR addresses.
