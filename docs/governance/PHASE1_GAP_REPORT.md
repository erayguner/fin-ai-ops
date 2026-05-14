# Phase 1 — Agent Governance Gap Report

**Status:** Draft for review
**Date:** 2026-04-17
**Scope:** AWS Bedrock Agents + GCP ADK/Vertex AI agent surfaces, the MCP
server, and cross-cutting governance primitives in `core/`.

This report compares the current implementation against provider-aligned
standards sourced directly from official documentation (AWS Bedrock
userguide, ADK docs, Google Cloud Vertex AI / Model Armor docs). It
identifies gaps that block safe, enterprise-scale agentic operation and
proposes a prioritised remediation list that will become the scope of
Phase 2 (ADR) and Phase 3 (implementation).

No source code is changed by this report.

---

## 1. Provider-aligned governance baseline

The table summarises the canonical controls each provider ships. These
are the reference points against which we assess the current system.

### 1.1 AWS Bedrock Agents (authoritative)

| Control | What it provides | Primary source |
|---|---|---|
| **Agent traces** (`TracePart`) | Per-step reasoning: `PreProcessingTrace`, `OrchestrationTrace`, `PostProcessingTrace`, `GuardrailTrace`, `FailureTrace`, `RoutingClassifierTrace`, `CustomOrchestrationTrace`, each with `ModelInvocationInput` (prompt, model, inference config), `sessionId`, `callerChain`. | `bedrock/latest/userguide/trace-events.html` |
| **Bedrock Guardrails** | Content filters (Hate/Insults/Sexual/Violence/Misconduct/Prompt Attack), Denied topics, Word filters, Sensitive information (PII) filters, Contextual grounding, Automated Reasoning. Callable standalone via `ApplyGuardrail`. | `bedrock/latest/userguide/guardrails.html` |
| **Return-of-Control (RoC)** | Action group `customControl = RETURN_CONTROL` causes the agent to emit `invocationInputs` to the caller instead of executing Lambda; caller returns `returnControlInvocationResults`. Canonical primitive for human-approval gates and deterministic executors. | `bedrock/latest/userguide/agents-returncontrol.html` |
| **CloudTrail management events** | All `bedrock-agent-runtime` operations recorded with IAM principal, source IP, timestamp, request params. | `bedrock/latest/userguide/logging-using-cloudtrail.html` |
| **Model invocation logging** | Full request + response + metadata to CloudWatch Logs or S3. Off by default; must be enabled explicitly. | `bedrock/latest/userguide/model-invocation-logging.html` |
| **Observability** | Dedicated observability subsystem (CloudWatch metrics, logs, trace IDs). | `bedrock/latest/userguide/security.html` |

### 1.2 Google ADK / Vertex AI (authoritative)

| Control | What it provides | Primary source |
|---|---|---|
| **Callbacks** | `before_agent`, `before_model`, `before_tool`, plus `after_*` equivalents. Return `None` to continue; return a typed object to override/short-circuit. | `adk.dev/callbacks/` |
| **Plugins** | Runner-scoped reusable callback packages. Official guidance: *"use Plugins for security guardrails over Callbacks"*. Pre-built: Logging, Gemini-as-a-Judge, Model Armor, PII Redaction, Reflect-and-Retry, BigQuery Analytics. | `adk.dev/plugins/`, `adk.dev/safety/` |
| **Model Armor** | Screens prompts/responses for prompt injection, jailbreak, PII, responsible-AI categories. Modes: `INSPECT_ONLY` and `INSPECT_AND_BLOCK`. GA integration with Vertex AI (2025-12-03) and Google-managed MCP servers (Preview 2025-12-10). | `docs.cloud.google.com/model-armor/overview` |
| **Gemini safety settings** | Non-configurable (CSAM, PII) + configurable thresholds per HarmCategory in `GenerateContentConfig.safety_settings`. | `adk.dev/safety/` |
| **Vertex AI audit logs** | `aiplatform.googleapis.com` Admin-Activity (on) + Data-Access (opt-in). Standard `protoPayload`/`LogEntry` format. | `docs.cloud.google.com/vertex-ai/docs/general/audit-logging` |
| **Sandboxed code execution** | Hermetic envs — no network, cleanup between runs. Built-in executors (Vertex Code Interpreter, Gemini Enterprise) preferred. | `adk.dev/safety/` |
| **Evaluation framework** | `adk.dev/evaluate/` — quality/relevance/correctness scoring for regression and drift detection. | `adk.dev/safety/` |
| **VPC-SC perimeters** | Coarse-grained data-exfiltration containment. Agents should deploy inside a perimeter. | `adk.dev/safety/` |

---

## 2. Current implementation inventory

Read from the repository at commit `22dd117` (main). Relevant surfaces:

| File | What governance it provides today |
|---|---|
| `core/audit.py` (`AuditLogger`) | Append-only JSONL, chained SHA-256 checksum, query API, compliance export, integrity verification. |
| `core/tool_governor.py` | `governed_call` single enforcement point; fail-closed `GovernancePolicy`; `BudgetTracker` (total / per-tool / runtime / parallelism / connection-execution separation); argument gates; `Artifact` stream + `AuditReportGenerator`. |
| `mcp_server/server.py` | Every MCP tool call routed through `governed_call`; per-call `AuditLogger.log`; basic arg redaction for email + identity fields. |
| `core/validation.py` | Boundary validation (path traversal, dict depth, size caps, safe error messages). |
| `core/policies.py` + `policies/*.json` | 16 cost policies; approval-threshold and required-tags enforcement for resource-creation events (different domain from agent governance). |
| `providers/aws/agents/finops_agent.py` | Bedrock Agent config (foundation model, action groups, idle TTL); boto3 action-group implementations using IAM role credentials only. |
| `providers/aws/terraform/main.tf` + `variables.tf` | `enable_guardrails=true` default; inference-profile model ID; IAM policy split for inference profile + embedding model. |
| `providers/gcp/agents/finops_agent.py` | ADK `Agent` with tool functions; optional `McpToolset` pointing at `https://bigquery.googleapis.com/mcp`. |
| `providers/gcp/listener.py` | Cloud Logging filter for GCP resource creation events with RFC-3339 time bound. |

Security posture already in place:
- No static credentials anywhere; IAM roles (AWS) and ADC/WIF (GCP).
- Path-containment, checksum chain, fail-closed governor default *when enabled*.
- Tamper-evident audit log (JSONL + chained checksum).

---

## 3. Gap analysis

Each gap is tagged `G-A*` (AWS), `G-G*` (GCP), or `G-X*` (cross-cutting).
Severity uses a three-tier scale aligned with the user's requirement of
"safe, enterprise-scale agentic operations":

- **P0 — blocking** — absence violates the transparency / traceability /
  approval requirements explicitly called out in the Phase 0 brief.
- **P1 — required for enterprise** — expected by one or both providers'
  official guidance for production deployment.
- **P2 — hardening** — improves robustness and operator ergonomics.

### 3.1 AWS Bedrock Agent gaps

| ID | Gap | Severity | Evidence |
|---|---|---|---|
| **G-A1** | Bedrock `TracePart`/`OrchestrationTrace` output is never captured into the local audit trail. Reasoning, tool invocation, and guardrail outcomes emitted by InvokeAgent are ephemeral — the hub persists nothing from the agent's step-by-step thinking. | **P0** | `trace-events.html`: trace contains `ModelInvocationInput`, `FailureTrace`, `GuardrailTrace`. No code in `mcp_server/` or `providers/aws/` reads or stores this. |
| **G-A2** | Model invocation logging (CloudWatch / S3 full-payload capture) is not provisioned by Terraform. Because it is off by default, the raw prompt/response pair for every agent step is *not* being written anywhere durable. | **P0** | `model-invocation-logging.html`: "Model invocation logging is disabled by default." No `aws_bedrock_model_invocation_logging_configuration` in `providers/aws/terraform/`. |
| **G-A3** | No action group is declared with `customControl = RETURN_CONTROL`. All tool invocation today is direct Lambda → no human-in-the-loop primitive at the Bedrock layer. | **P0** | `agents-returncontrol.html` documents RoC as the canonical approval primitive. `providers/aws/agents/finops_agent.py` → `get_bedrock_agent_config` declares only `functions` in each action group. |
| **G-A4** | `enable_guardrails = true` is set but the guardrail *content* (denied topics, PII filter list, contextual grounding threshold, blocked words) is not defined anywhere. Result: guardrail resource may be created empty or with defaults only. | **P1** | `guardrails.html` enumerates the six filter families that should be explicitly configured. No corresponding Terraform resource contents in `main.tf`. |
| **G-A5** | CloudTrail data-event logging is not configured; only management events are captured by default. Tool-call content for `InvokeAgent` and model data events are therefore not in the corporate trail. | **P1** | `logging-using-cloudtrail.html`: "Data events are … high-volume activities that CloudTrail doesn't log by default." |
| **G-A6** | No correlation between Bedrock `sessionId` / `callerChain` and the local `AuditLogger` `correlation_id` / `causation_id` fields. End-to-end traceability (user → agent → tool → cloud effect) is broken. | **P1** | `AuditEntry` in `core/models.py` already has `correlation_id`/`causation_id` but nothing populates them from Bedrock traces. |

### 3.2 GCP ADK / Vertex AI gaps

| ID | Gap | Severity | Evidence |
|---|---|---|---|
| **G-G1** | `create_gcp_finops_agent()` wires zero callbacks. No `before_model`, `before_tool`, `after_tool`, `after_model`. Hence no observability of reasoning, no input/output validation, no caching, no redaction at the ADK layer. | **P0** | `providers/gcp/agents/finops_agent.py:385-417`. ADK docs explicitly position callbacks + plugins as the primary observability/guardrail surface. |
| **G-G2** | No `Runner` is instantiated and no ADK Plugins are registered. Per `adk.dev/safety/#callbacks-and-plugins-for-security-guardrails`: *"use ADK Plugins for better modularity and flexibility than Callbacks"*. | **P0** | Same file. Agent is defined but there is no runner wiring, no `Logging` plugin, no `PII Redaction` plugin, no `Gemini-as-a-Judge` plugin. |
| **G-G3** | Model Armor is not provisioned. No template created, no integration with Vertex AI endpoint, no configuration for `INSPECT_AND_BLOCK` on the BigQuery MCP traffic. | **P0** | `model-armor/overview`: Model Armor is the documented PII/prompt-injection defence for Vertex + Google-managed MCP. No Terraform for GCP exists (only AWS), and no gcloud invocation script is present. |
| **G-G4** | Gemini `GenerateContentConfig.safety_settings` is left at defaults. No explicit thresholds per `HarmCategory` (hate/harassment/sexual/dangerous). | **P1** | `adk.dev/safety/` example shows explicit `safety_settings` block as the expected minimum. |
| **G-G5** | Vertex AI Data-Access audit logs (`aiplatform.googleapis.com`) are off by default and not enabled anywhere in documentation or scripts. Admin-Activity-only coverage misses `InvokeAgent`-level payloads. | **P1** | `vertex-ai/docs/general/audit-logging`: Data Access audit logs disabled unless explicitly enabled. |
| **G-G6** | No documented VPC-SC posture for the GCP agent. `docs/GETTING_STARTED.md` does not mention perimeter creation or the `aiplatform.googleapis.com` inclusion. | **P2** | ADK safety guidance recommends perimeter containment as a coarse exfiltration control. |
| **G-G7** | No ADK evaluation harness. Agent quality / drift is not measured, so *regression of the agent itself* (as opposed to the code) would go undetected. | **P2** | `adk.dev/evaluate/` exists as the canonical framework. `tests/` covers tools but not agent-level eval. |

### 3.3 Cross-cutting platform gaps

| ID | Gap | Severity | Evidence / why |
|---|---|---|---|
| **G-X1** | No unified **`AgentTrace` / `DecisionRecord`** model. `tool_governor.Artifact` captures tool decisions but not LLM reasoning, model input/output, or the causal chain linking model-step → tool-decision → cloud-effect. | **P0** | Required for "end-to-end traceability for every agent decision, tool call, action, and outcome". |
| **G-X2** | `AuditLogger._previous_checksum` resets per-process. `load_from_disk()` overwrites it per-entry rather than *verifying* continuity across day-rolled JSONL files. A restart produces a checksum chain fork that the integrity check does not detect. | **P0** | `core/audit.py:150-163`. Tamper-evident claim in `CLAUDE.md` and the class docstring is weaker than advertised. |
| **G-X3** | No behavioural anomaly / drift detection on the agent itself. `ThresholdEngine` detects cost anomalies on cloud events but not on agent tool-call patterns, model-token usage, or deviations in typical tool distribution. | **P0** | Required for "real-time monitoring, anomaly and drift detection, unexpected behaviour detection". |
| **G-X4** | No out-of-band human-override channel. `approval_handler` in `governed_call` is an in-process Python callable; there is no Slack-button, webhook endpoint, or signed-URL mechanism for an approver to respond asynchronously. No kill-switch (pause / revoke session) exists at all. | **P0** | Required for "clear human override mechanisms". |
| **G-X5** | No explainability surface. Nothing captures or renders *why* the agent chose a tool (model rationale) in a human-readable form attached to the decision record. | **P0** | Required for "explainability of agent reasoning and actions". |
| **G-X6** | No post-action review UX. `AuditReportGenerator` produces a dict summary but there is no CLI / MCP tool that reconstructs an end-to-end session transcript (model trace + tool calls + alerts + cloud effects) for review. | **P1** | Required for "post-action reviewability". |
| **G-X7** | **Governor fails open by default** (`_GOVERNOR_ENABLED = hub.governor.enabled == "true"`, otherwise `default_allow=True`). Production-readiness requires inverse default. | **P1** | `mcp_server/server.py:167-192`. |
| **G-X8** | Single global `BudgetTracker` shared across all MCP callers. One runaway client exhausts the shared pool; no per-session / per-principal isolation. | **P1** | `mcp_server/server.py:171-192`. |
| **G-X9** | No structured policy enforcement for LLM input/output at the platform layer (prompt injection, PII, output filtering). Relies entirely on cloud-provider built-ins which are not wired (see G-A4, G-G3). | **P1** | Cross-provider portability requires a platform-level control even when provider guardrails exist. |
| **G-X10** | Redaction in `_redact_arguments` covers only 4 specific keys and is substring-based. No centralised PII / secret scrubber reused across audit writes, notifications, and transcripts. | **P1** | `mcp_server/server.py:876-889`. |
| **G-X11** | No signed audit exports. `export_for_compliance` returns entries with embedded checksums but the export itself carries no detached signature / JWS, so chain-of-custody is not established once the data leaves the process. | **P2** | Enterprise compliance workflows typically require a signed manifest. |
| **G-X12** | No ADR documenting the agent-governance model. ADRs 001–007 cover cost/tagging/tool-call governors but there is no architectural decision record for agent traceability, approval, or override. | **P2** | ADR-driven workflow established in `docs/adr/`. |

---

## 4. Prioritised remediation (P0 scope for Phase 2 + 3)

Items below are grouped by the governance primitive they deliver. Each
row maps to one or more gaps. Each will become a sub-section in the
forthcoming ADR-008 and a concrete file-level change set in Phase 3.

### 4.1 End-to-end trace primitive — closes G-A1, G-A6, G-G1, G-X1, G-X5

- New `core/agent_trace.py` with `AgentTrace`, `AgentStep`, and
  `DecisionRecord` Pydantic models (schema_version-aware, matching the
  existing pattern).
- `AgentStep` discriminated union covers: `ModelInvocation`,
  `ToolInvocation`, `GuardrailEvaluation`, `ApprovalRequest`,
  `HumanOverride`, `Failure`.
- Every step carries `correlation_id`, `causation_id`, `session_id`,
  `actor`, `rationale` (natural-language reason captured from the
  model trace where available), plus provider-specific `raw` blob.
- AWS ingestion adapter: parse Bedrock `TracePart` → `AgentStep`.
- GCP ingestion adapter: an ADK `LoggingPlugin` subclass that emits
  `AgentStep` into the same stream.
- `AuditLogger` gains an `ingest_agent_trace(trace)` method that
  persists each step as an `AuditEntry` with chained checksum.

### 4.2 Tamper-evident audit hardening — closes G-X2

- Fix `load_from_disk` to *verify* the checksum chain across JSONL
  files, not silently accept a reset.
- Add a daily "trail-head" manifest: at end-of-day rotation, write a
  signed manifest containing the last checksum, the JSONL file SHA-256,
  and the entry count.
- Add `export_signed(...)` that emits a detached JWS over the export
  payload (HS256/Ed25519 depending on deployment) — closes G-X11.

### 4.3 Approval + override primitive — closes G-A3, G-X4

- New `core/approvals.py` with `ApprovalRequest`, `ApprovalDecision`,
  and an `ApprovalGateway` interface.
- Built-in gateways:
  - `LocalCLIApprovalGateway` (dev / single-operator).
  - `WebhookApprovalGateway` (prod: signed URL the operator clicks).
  - `SlackApprovalGateway` (existing `SlackDispatcher` already wired).
- New MCP tool `finops_pending_approvals` + `finops_respond_approval`.
- Terraform change: flip the destructive action groups to
  `customControl = RETURN_CONTROL`, and wire the RoC roundtrip
  through `ApprovalGateway`.
- Kill-switch: `finops_halt_session(session_id, reason)` sets a
  denylist entry the governor consults; any in-flight tool call
  observing a halted session returns `Decision.DENY` with reason
  `"session halted by <actor>"`.

### 4.4 Behavioural anomaly + drift detection — closes G-X3

- New `core/agent_observer.py` that maintains a rolling window of
  per-session tool-call distribution, per-minute call rate, and
  per-session token/cost totals.
- Re-use `ThresholdEngine` statistical primitives (mean + stddev) so
  detection is consistent with ADR-005.
- Emits `CostAlert`-style alerts into the existing pipeline when
  thresholds breach, including `severity=CRITICAL` for kill-switch
  triggers.

### 4.5 Provider guardrail wiring — closes G-A2, G-A4, G-A5, G-G3, G-G4, G-G5

- Terraform additions:
  - `aws_bedrock_model_invocation_logging_configuration` →
    CloudWatch Log Group with KMS-CMK encryption, 365-day retention.
  - Populated `aws_bedrockagent_guardrail` resource with explicit
    denied topics (credential exfiltration, destructive IaC, cost
    bomb), PII filter list, and contextual grounding ≥ 0.7.
  - CloudTrail data-event selector for Bedrock runtime.
- GCP setup script (`scripts/gcp/enable_governance.sh`) that:
  - Creates a Model Armor template (floor settings, block mode).
  - Enables Data-Access audit logs for `aiplatform.googleapis.com`.
  - Attaches the Model Armor template to the Vertex AI endpoint.
- ADK agent update: explicit `safety_settings` block on
  `GenerateContentConfig`.

### 4.6 Platform input/output filters — closes G-X9, G-X10, G-G1, G-G2

- New `core/filters.py` with a `ContentFilter` interface:
  `PIIRedactor`, `PromptInjectionHeuristic`, `SecretScanner`.
- Wire into:
  - `_redact_arguments` (replacing the hardcoded key set).
  - An ADK `Plugin` (registered on a new `Runner` in GCP agent).
  - A pre-step hook in the Bedrock agent action-group Lambda layer.
- Unified `FilterDecision` captured into the `AgentTrace` as an
  `AgentStep`.

### 4.7 Post-action review surface — closes G-X6

- New MCP tool `finops_replay_session(session_id)` that reconstructs
  the end-to-end transcript and returns both structured data and a
  human-readable Markdown render.
- New CLI subcommand `finops session show <id>`.

### 4.8 Fail-closed governor by default — closes G-X7, G-X8

- Flip `hub.governor.enabled` default to `true` in `core/config.py`
  (with migration note in Phase 2 ADR).
- Replace single global `BudgetTracker` with a per-session tracker
  keyed by MCP principal. Add `finops_session_stats(session_id)` for
  operator visibility.

### 4.9 ADR + eval harness — closes G-G7, G-X12

- New `docs/adr/008-agent-governance-model.md` formalising the above.
- New `tests/agent_eval/` harness using ADK's `evaluate` module with a
  curated golden set of FinOps prompts; run it in the existing
  `finops-self-maintain.yml` workflow on a weekly cadence.

---

## 5. Phase boundaries and sequencing

- **Phase 2 (ADR)**: consolidate §4 into `docs/adr/008-agent-governance-model.md`. No code.
- **Phase 3 (core implementation)**: §4.1, §4.2, §4.3, §4.4, §4.6 —
  the cross-cutting primitives. Land in `core/`, plumbed through
  `mcp_server/` and existing agents. Everything is provider-agnostic.
- **Phase 4 (provider integration)**: §4.5, §4.7, §4.8, §4.9 — wiring
  into Terraform, the GCP setup script, CLI, and CI.

Phases 3 and 4 can ship as two PRs or four sub-PRs depending on review
appetite.

---

## 6. Risks and non-goals

- **Non-goal: replacing provider guardrails.** We wire them, not replace
  them. Bedrock Guardrails and Model Armor remain authoritative for
  content-level filtering.
- **Risk: provider documentation drift.** Model Armor MCP-server
  integration is still in Preview (2025-12-10). We treat it as a roadmap
  item and use the GA Vertex AI integration path for core coverage.
- **Risk: ADK plugin ABI stability.** The Plugin system is stable in
  docs as of 2025-11 but still evolving. We keep our plugin
  implementations thin to limit blast radius if the ABI shifts.
- **Risk: existing 539 tests** — the default-flip (§4.8) will break any
  test that implicitly relied on `default_allow`. We front-load a test
  audit during Phase 3.

---

## 7. Sign-off checklist for moving to Phase 2

- [ ] User confirms the severity assignments (especially the P0 set).
- [ ] User confirms Phase 2 produces only ADR-008, no code.
- [ ] User picks PR-slicing preference (two PRs vs four).
- [ ] User confirms the scope does not need to extend to a third
      provider (Azure) — current repo is AWS + GCP only.
