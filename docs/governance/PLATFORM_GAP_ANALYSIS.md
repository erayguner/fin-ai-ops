# Platform Gap Analysis — Gemini Enterprise + Bedrock AgentCore (2026)

**Date:** 2026-05-14
**Baseline docs:**
- [Gemini Enterprise Agent Platform / Build](https://docs.cloud.google.com/gemini-enterprise-agent-platform/build)
- [Gemini Enterprise Agent Platform / Scale](https://docs.cloud.google.com/gemini-enterprise-agent-platform/scale)
- [Amazon Bedrock AgentCore overview](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html)
- [Amazon Bedrock API Reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/welcome.html)

This document compares the project's current agent architecture (after
ADR-008 implementation, commit `b6194d1`) against the latest Google and
AWS managed-agent platforms. Each gap is rated by impact and selected
for implementation, documentation, or deferral.

---

## 1. Platform inventory

### 1.1 Gemini Enterprise Agent Platform (mostly GA)

| Capability | Status | Used here? |
|---|---|---|
| **Agent Runtime** (managed serverless) | GA | ❌ Cloud Run instead — documented L2 graduation |
| **Agent Gateway** (centralized routing/auth) | GA | ❌ Not used |
| **Private Service Connect (PSC) interface** | GA | ⚠️ Cloud Run ingress=INTERNAL_LOAD_BALANCER (partial) |
| **Sessions** (managed conversation state) | GA | ❌ Custom session_id only |
| **IAM Conditions for Sessions / Memory** | GA | ❌ Not wired |
| **Memory Bank** (long-term user memory) | GA | ❌ Not wired — see §3.1 |
| **Memory Profiles / Revisions / Generate Memories** | GA | ❌ Not wired |
| **Code Execution** (managed sandbox) | GA | ❌ N/A (no code-exec tools today) |
| **Computer Use** (browser interaction) | Preview | ❌ N/A |
| **Agent Identity** (SPIFFE per-agent principal) | GA | ⚠️ Scaffolded only (shared SA) |
| **Model Armor** (content/PI filter) | GA on scale page / Preview on build page | ✅ Wired via Terraform (commit b6194d1) |
| **Security Command Center: Agent Engine Threat Detection** | GA | ❌ Not enabled — documented runbook |
| **Agent Topology** (visual graph) | GA | ❌ Not used |
| **Cloud Trace** (OTel native) | GA | ⚠️ AgentTrace has `to_otel_spans()` but no exporter |
| **Cloud Logging** + Built-in metrics | GA | ⚠️ Default Cloud Run logging only |
| **Agent Evaluation** — Offline / Simulated / Online Monitors | GA | ⚠️ Offline only (`tests/agent_eval/`) |
| **Quality Alerts** | GA | ❌ Not wired — see §3.4 |
| **Optimize Agent Prompts** (auto-rewrite) | GA | ❌ Not used |
| **Tool Calling** w/ OAuth 1L/2L/3L, API key auth | GA | ⚠️ Manual via SDK |
| **RAG Engine 2.0** + Vector Search + Reranking + CMEK | GA | ⚠️ Vector Search index used, reranking not enabled |
| **Example Store** (few-shot examples) | GA | ❌ Not used |
| **Semantic Governance Policies** | GA | ⚠️ Replaced by our `tool_governor` + `core/approvals.py` |
| **A2A Protocol** (multi-agent) | GA | ⚠️ Bedrock adapter parses callerChain; A2A endpoint not exposed |
| **ADK / LangChain / LangGraph / AG2 / LlamaIndex frameworks** | GA | ✅ ADK |

### 1.2 Bedrock AgentCore (modular agent platform)

| Service | Status | Used here? |
|---|---|---|
| **Runtime** (serverless, session-isolated) | GA | ❌ Plain Lambda action groups |
| **Memory** (short + long term, cross-agent) | GA | ❌ Not wired — see §3.1 |
| **Gateway** (APIs / Lambda → MCP tools) | GA | ⚠️ We host our own MCP server |
| **Identity** (per-agent, IdP-compatible: Cognito/Okta/Entra/Auth0) | GA | ⚠️ Scaffolded only (shared IAM role) |
| **Code Interpreter** (Python/JS/TS sandbox) | GA | ❌ N/A |
| **Browser** (managed cloud browser) | GA | ❌ N/A |
| **Observability** (OTel-compatible) | GA | ⚠️ AgentTrace has OTel emission, no exporter |
| **Payments** (x402 microtransactions) | GA | ❌ Irrelevant for FinOps |
| **Evaluations** (sessions/traces/spans, OTel/OpenInference) | GA | ⚠️ We have offline; integration path documented |
| **Policy** (natural language / Cedar) | GA | ⚠️ Our `tool_governor.GovernancePolicy` covers same ground |
| **Registry** (catalog of agents/tools/MCP servers) | GA | ❌ Not used |

### 1.3 Bedrock API surfaces

| API | Endpoint | Used here? |
|---|---|---|
| `bedrock` (control plane) | `bedrock.amazonaws.com` | ✅ via Terraform |
| `bedrock-runtime` (`InvokeModel` / `Converse` / `ApplyGuardrail`) | `bedrock-runtime.amazonaws.com` | ⚠️ Indirect via the Agent |
| `bedrock-agent` (`CreateAgent`, action groups, Knowledge Base, **Prompt Management**, **Flows**, **AutomatedReasoningPolicy**) | `bedrock-agent.amazonaws.com` | ⚠️ Agent + KB only — Prompt Management and Automated Reasoning not used |
| `bedrock-agent-runtime` (`InvokeAgent`, `Retrieve`, `RetrieveAndGenerate`, `return_control`) | `bedrock-agent-runtime.amazonaws.com` | ✅ via the agent + RoC on `remediation_tools` |

---

## 2. High-impact gaps (rated)

| # | Gap | Provider | Impact | Verdict |
|---|---|---|---|---|
| G1 | No first-class **memory** primitive with audit / filter / right-to-be-forgotten | Both | High — §11.6 names but not wired | **Implement** |
| G2 | Agent instruction lives inline in Terraform; no versioned **Prompt Management** | AWS | Medium — §11.7 data lineage gap | **Implement** |
| G3 | No **boundary contract** artefact per agent (§3.2) | Both | Medium — governance docs gap | **Implement** |
| G4 | No **quality alerts / online monitors** on agent behaviour | Both | Medium — §17.2 weekly review gap | **Implement (Terraform stubs)** |
| G5 | No managed **Agent Runtime** (Cloud Run instead) | GCP | Low (operational not security) | **Document migration path** |
| G6 | No managed **Agent Engine Sessions** (custom session_id only) | GCP | Low — sessions covered by ADK Sessions when used | **Document** |
| G7 | **Automated Reasoning Policy** not attached to Bedrock guardrail | AWS | High — §11.4 #6, but provider Preview | **Document; add resource when GA** |
| G8 | **AgentCore Identity** / **Vertex Agent Identity** per-agent SPIFFE | Both | High — §7.1 / F-2, but provider Preview/rolling out | **Already scaffolded; track L2 graduation** |
| G9 | **OpenTelemetry exporter** wiring (we emit, never ship) | Both | Medium — §9.1 | **Document; emit examples** |
| G10 | **A2A endpoint** exposed (not just inbound parsing) | Both | Low for single-agent FinOps | **Defer** |
| G11 | **AgentCore Gateway** wrapping the MCP server | AWS | Low — we already enforce via `governed_call` | **Defer** |
| G12 | **AgentCore Registry** entry for the FinOps agent | AWS | Low | **Defer** |
| G13 | **Example Store** / few-shot examples | GCP | Low | **Defer** |
| G14 | **Optimize Agent Prompts** (auto-rewrite) | GCP | Low — we want deterministic prompts | **Skip** |
| G15 | **Computer Use** / **Browser** managed sandboxes | Both | Low — no UI-driving FinOps actions | **Skip** |
| G16 | **AgentCore Payments** (x402) | AWS | None — irrelevant | **Skip** |
| G17 | **Bedrock Flows** orchestration | AWS | Low — our workflow is linear | **Skip** |
| G18 | **Bedrock Converse API** unified surface | AWS | Low — we go through the Agent | **Skip** |
| G19 | RAG **Reranking** | GCP | Low — single corpus | **Skip / config flag** |

---

## 3. Selected refactors (this change)

### 3.1 Memory governance primitive (G1)

Both platforms now ship managed memory (Memory Bank on GCP, AgentCore
Memory on AWS). Framework §11.6 already declares the four required
controls (retention, user-scoped deletion, cross-session isolation,
memory-injection threat model) but the codebase has no plumbing.

**Implementation:**
- `MemoryOperationStep` added to `core/agent_trace.py` (discriminated union).
- New `core/memory_audit.py`:
  - `MemoryRecord` Pydantic model (`user_id`, `session_id`, `content`, `provenance`, `created_at`, TTL).
  - `MemoryBackend` ABC + `InMemoryMemoryBackend` for tests.
  - `MemoryAdapter` orchestrator: writes pass through filter stack (§11.2), reads pass through filter stack again (§11.6 memory-injection), every operation emits a `MemoryOperationStep` + audit entry.
  - `delete_for_user(user_id)` — right-to-be-forgotten, audited.
  - `expire(now)` reconciliation hook.
- 3 new MCP tools: `finops_memory_write`, `finops_memory_read`, `finops_memory_forget_user`.
- Tests cover: write-through-filter, read-through-filter (memory-injection scenario), cross-user isolation, RtbF removes every entry for a subject, TTL expiry.

### 3.2 Bedrock Prompt Management (G2)

Agent instruction strings move into versioned `aws_bedrockagent_prompt`
resources. The agent references the prompt by ARN, and the prompt
version pins the exact text used in production. Closes §11.7 data
lineage gap and §16.1 version-control-for-prompts.

### 3.3 Boundary contracts (G3)

New `docs/governance/boundary_contracts/` with one YAML per agent
(AWS Bedrock FinOps, GCP ADK FinOps). Each carries the framework
§3.2 fields: purpose, role, in-scope tools, out-of-scope systems,
data classes, foundation model card reference, owner, approver pool.
The YAML format is machine-readable so CI can lint deltas in future.

### 3.4 Quality alerts / online monitors (G4)

- AWS: `aws_cloudwatch_log_metric_filter` over `bedrock_invocations`
  matching `GUARDRAIL_INTERVENED`, fed into an
  `aws_cloudwatch_metric_alarm` that emits to the SNS topic on
  sustained anomalous rates.
- GCP: `google_monitoring_alert_policy` on a custom metric placeholder
  for Model Armor block rate (Cloud Monitoring custom metric must be
  produced by the workload; we add the alert policy stub + doc).

### 3.5 Multi-turn eval (G9 partial)

Adds one multi-turn case to `tests/agent_eval/cases/` exercising the
ADK Evaluate `multi_turn_task_success_v1` dimension. Documents the
production hook to **AgentCore Evaluations** (OTel-emitted traces feed
the managed evaluator).

---

## 4. Architecture deltas

```
┌────────────────────────────────────────────────────────────────────┐
│                       Caller (user / MCP client)                   │
└──────────────────────────────┬─────────────────────────────────────┘
                               │
              ┌────────────────▼────────────────┐
              │  MCP Server  (governed_call,    │
              │  per-principal budgets,         │  ← unchanged
              │  kill-switch, replay)           │
              └──┬──────────┬──────────┬────────┘
                 │          │          │
        ┌────────▼──┐  ┌────▼─────┐  ┌─▼──────────────┐
        │ Approval  │  │ Observer │  │ Memory Adapter │  ← NEW (G1)
        │ Gateway   │  │ (anomaly │  │  (filter pass, │
        │           │  │  detect) │  │   audit, RtbF) │
        └───────────┘  └──────────┘  └─┬──────────────┘
                                        │
                              ┌─────────▼─────────────┐
                              │  MemoryBackend ABC    │
                              │  ├─ InMemoryBackend   │  ← shipped
                              │  ├─ MemoryBankClient  │  ← stub (Vertex Memory Bank)
                              │  └─ AgentCoreMemory   │  ← stub
                              └───────────────────────┘
```

---

## 5. Security & governance considerations

| Consideration | Where addressed |
|---|---|
| Memory writes never carry secrets / PII | `MemoryAdapter` runs `SecretScanner`/`PIIRedactor` on write |
| Retrieved memory is untrusted input | `MemoryAdapter` runs `PromptInjectionHeuristic` on read |
| Cross-user / cross-session isolation | `MemoryRecord.user_id` is mandatory; `read_for_user` filters by key |
| Right-to-be-forgotten is tested | `delete_for_user` + dedicated test |
| Memory operations land in the audit chain | Every op emits `MemoryOperationStep` ingested by `AuditLogger` |
| Prompt template changes are versioned | Bedrock Prompt Management (G2) |
| Boundary contracts are version-controlled | `docs/governance/boundary_contracts/` |

No control is weakened by this change. The memory adapter, in particular, is **off by default** — no agent currently exercises it. The 643 existing tests are not affected; new tests are additive.

---

## 6. What we did **not** adopt and why

- **AgentCore Gateway** — we already enforce identical policy in
  `governed_call`. Moving to Gateway would be lift-and-shift, not
  governance improvement.
- **AgentCore Registry** — single-agent deployment; the registry
  overhead is bigger than the value.
- **AgentCore Payments** — irrelevant for a FinOps governance agent
  that never makes microtransactions.
- **Bedrock Flows** — visual orchestration. Our agent has linear
  cost-monitor → alert → remediate flow; Flows would obscure rather
  than clarify.
- **Computer Use / Browser** — no UI-driving FinOps use case.
- **Optimize Agent Prompts** — auto-rewriting compromises §16.6
  fine-tuning controls and deterministic regression eval. Skip.
- **Bedrock Converse API direct** — we route through the Agent, not
  raw InvokeModel.
- **Example Store / RAG reranking** — current eval shows acceptable
  quality without; add when retrievals get noisier.

---

## 7. Migration roadmap

| Milestone | Target | Trigger |
|---|---|---|
| L2 graduation: managed runtime | Agent Engine Runtime (GCP) / AgentCore Runtime (AWS) | When provider Terraform exposes the resource |
| Per-agent identity | Vertex Agent Identity / Bedrock AgentCore Identity | Provider GA in target region |
| Automated Reasoning Policy | `aws_bedrockagent_automated_reasoning_policy` | When the Terraform AWS provider supports it |
| Online evaluation | AgentCore Evaluations / Vertex Online Monitors | Eval harness emits OTel traces to the managed evaluator |
| A2A endpoint | A2A protocol exposure | When a second agent is in scope |

---

## 8. Validation

After this change:

- `pytest tests/ -q --deselect ...` → expected: 643 + 12 new tests = 655 passed.
- `ruff check core/ agents/ mcp_server/ providers/ tests/` → clean.
- `mypy core/ providers/ agents/ mcp_server/` → clean.
- `python scripts/governor_dry_run.py` → all 4 invariants pass.

No pre-existing behaviour is regressed. New surfaces are additive,
opt-in, and off-by-default unless explicitly wired by an operator.
