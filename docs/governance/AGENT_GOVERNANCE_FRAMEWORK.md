# Agent Governance Framework

**Version:** 1.0
**Status:** Adopted
**Derived from:** fin-ai-ops architecture (ADRs 001–008, Phase 1 gap
report). Generalised for any agentic workflow.

This document is a **practical, implementation-ready standard** for
operating AI agents safely, transparently, and at scale. It defines
principles, controls, and an operating model that produce fully
traceable, reviewable, and compliant agent behaviour with no black-box
operations.

It is written to be re-usable: every control is stated abstractly, with
a reference implementation drawn from this repository that teams can
copy or adapt.

---

## Table of contents

1. [Scope and audience](#1-scope-and-audience)
2. [Guiding principles](#2-guiding-principles)
3. [Agent roles and boundaries](#3-agent-roles-and-boundaries)
4. [Tool and MCP access controls](#4-tool-and-mcp-access-controls)
5. [Approval gates](#5-approval-gates)
6. [Policy enforcement](#6-policy-enforcement)
7. [Least-privilege execution](#7-least-privilege-execution)
8. [Auditability](#8-auditability)
9. [Observability](#9-observability)
10. [Explainability](#10-explainability)
11. [Data handling](#11-data-handling)
12. [Security](#12-security)
13. [Resilience](#13-resilience)
14. [Human oversight](#14-human-oversight)
15. [Incident response](#15-incident-response)
16. [Change management](#16-change-management)
17. [Operating model](#17-operating-model)
18. [Maturity levels](#18-maturity-levels)
19. [Minimum compliance checklist](#19-minimum-compliance-checklist)

---

## 1. Scope and audience

**In scope.** Any workflow where an LLM or AI agent autonomously selects
and invokes tools — whether through a provider-native framework
(Amazon Bedrock Agents, Google ADK, Vertex AI Agent Engine) or a bespoke
MCP integration. The framework applies equally to single-agent systems,
multi-agent swarms, and human-in-the-loop copilots.

**Out of scope.** Stateless LLM chat with no tool access; classical ML
pipelines; RPA without language-model reasoning. These warrant their
own controls.

**Audience.** Platform teams, security engineers, and compliance owners
responsible for deploying AI agents that touch production systems,
sensitive data, or spend authority.

---

## 2. Guiding principles

### 2.1 Alignment with external frameworks

This framework is compatible with, and maps onto, the following
authoritative sources. Adopters use the mapping to produce regulator
evidence packs without re-writing controls.

| Framework | Element | Maps to |
|---|---|---|
| Google **SAIF** (6 core elements) | Expand strong security foundations; extend detection & response; automate defences; harmonize platform controls; adapt controls for feedback loops; contextualize risks | §7 (least privilege), §8 (audit), §9 (observability), §12 (security), §15 (incident response) |
| **NIST AI RMF 1.0** | Govern / Map / Measure / Manage | §17 (operating model) / §3 (boundaries) / §9 (observability) / §14–§16 (oversight, IR, change) |
| **EU AI Act** high-risk obligations | Risk management system, logging, transparency, human oversight, accuracy/robustness | §3, §8, §10, §14, §16 |
| **ISO/IEC 42001** | AI management system | §17, §18 (maturity) |
| **SOC 2** CC-series | Logical access, change mgmt, monitoring | §7, §8, §16 |
| **UK NCSC** secure AI guidelines | Secure design, development, deployment, operation | §3, §12, §13, §15 |

### 2.2 Principles

The ten principles below are load-bearing. Every control later in the
document enforces at least one of them.

1. **Fail-closed by default.** Unknown tools, unsigned configs, and
   undefined policies deny rather than allow.
2. **Single enforcement point.** Every tool call passes through one
   governable function; there are no side channels.
3. **Every decision is a record.** Allow, deny, redact, approve,
   override, halt — all emit a structured, machine-readable artifact.
4. **Traceability is append-only.** Audit trails are immutable, chained
   for tamper evidence, and exportable with a signature.
5. **Humans remain in charge of high-risk actions.** Destructive or
   above-threshold operations require a signed approval out of band.
6. **Explainability is a required output, not a debugging feature.**
   Every consequential action carries a natural-language rationale the
   operator can read.
7. **Provider controls are wired, not assumed.** Bedrock Guardrails and
   Model Armor must be populated with rules; defaults are not a
   compliance position.
8. **Least privilege at every layer.** Identity, permissions, tool
   scope, and data scope are each sized to the current task.
9. **Reversibility before power.** Prefer narrow, dry-run, or
   RETURN_CONTROL tools over broad, auto-executing ones. When power is
   required, approval and observability grow in proportion.
10. **Change is reviewed like code.** Policies, prompts, tool catalogues,
    and approvers are version-controlled, peer-reviewed, and release-gated.

---

## 3. Agent roles and boundaries

### 3.1 Role taxonomy

Classify every agent into exactly one of:

| Role | Permitted actions | Example |
|---|---|---|
| **Observer** | Read-only. No tool call may write, mutate, or transact. | Cost analyst agent reading CUR/billing export. |
| **Advisor** | Read + prepare-drafts. Produces plans, PRs, tickets, but does not apply them. | An agent that opens a remediation PR but never merges. |
| **Operator** | Read + write under policy + per-action approval. | FinOps agent that tags resources after approver clicks through. |
| **Autonomous operator** | Write under policy with *rate- and blast-limited* budgets; subject to kill-switch and real-time anomaly detection. | Remediation agent that auto-stops idle dev instances during business hours. |

Promotion between roles (e.g. Advisor → Operator) is a change-management
event (see §16), not a runtime flag.

### 3.2 Agent boundary contract

Every agent ships with a **boundary contract** — a versioned document
co-located with the agent definition, declaring:

- **Purpose.** One sentence.
- **Role** from §3.1.
- **In-scope tools** (allow-list).
- **Out-of-scope systems.** Explicit negative list, e.g. "never touches
  production DNS."
- **Delegatable downstream agents** (A2A allow-list, §3.3).
- **Data classes handled** (see §11).
- **Approval class** (per-call / batch / none).
- **Owner** and **on-call rotation**.
- **Foundation model card reference** — link to the model card for
  the underlying LLM (intended use, training-data posture, known
  limitations, evaluation results). A boundary contract without a
  model-card reference is incomplete.

Reference: a FinOps agent's boundary contract is codified as its
`GovernancePolicy` (allow-list + categories) plus the action-group
Terraform definition.

### 3.3 Multi-agent boundaries

When agents delegate to other agents (Bedrock multi-agent collaboration,
ADK sub-agents, **Agent2Agent (A2A) protocol** on Vertex AI Agent
Engine), the `callerChain` / parent-trace identity **must** be
preserved end-to-end. The trace model makes this explicit
(`AgentStep.parent_step_id`, `AgentTrace.correlation_id`). Cross-agent
delegation without preserved provenance is a finding.

Additional requirements for A2A / multi-agent:

- Each hop carries a **cryptographically-bound caller identity** (mTLS
  binding in Vertex Agent Engine's Context-Aware Access; signed
  `callerChain` entries in Bedrock).
- The downstream agent evaluates the caller against its own boundary
  contract; upstream authorisation is not transitive.
- Approval gates (§5) apply per hop, not just at the human entrypoint.
- Each agent's boundary contract lists the **delegatable set** of
  downstream agents — outbound delegation is allow-listed.

---

## 4. Tool and MCP access controls

### 4.1 Structured sandboxing

All tool invocation — including MCP tool calls — passes through a
single enforcement function (the *governor*). The governor takes a
structured `ToolRequest`, applies policy, argument validators, and
budget admission, and emits a `ToolResult`. The model never calls
tools directly.

Reference: `core/tool_governor.py::governed_call`.

### 4.2 Declarative policy

Policies are data, not code. Each declares:

- **Allow-list** (named tools) and/or **category allow-set** (classes of
  tools).
- **Deny-list** for explicit kills.
- **Approval-required** set for high-risk tools.
- **Budget limits** — total calls, per-tool calls, runtime, parallelism,
  and separation-of-duties (e.g. connection vs execution).
- **Argument gates** — per-tool validators that cap size, shape, and
  range of arguments.
- **Default disposition** — fail-closed (`default_allow=False`) in
  production.

Reference: `core/tool_governor.py::GovernancePolicy` + `BudgetLimits`.

### 4.3 Registry-led categorisation

Tools are registered with an explicit category (discovery, connection,
execution, other) at registration time. Substring matching on tool
names is prohibited — it is brittle and attacker-controlled.

### 4.4 Per-principal budgets

Budgets are per-session / per-principal, not global. A runaway agent
must not exhaust a budget shared with a different operator.

### 4.5 MCP server posture

Any MCP server the agent connects to is:

- **Identified** by URL and version pin.
- **Authenticated** via IAM / WIF / mTLS — no shared bearer tokens.
- **Reviewed** in the change-management process before first use.
- **Replayable** — the tool catalogue is fetched, hashed, and diffed
  at start of session; unexpected additions log a finding.

---

## 5. Approval gates

### 5.1 When approval is required

Approval is **required** when any of the following is true:

- The action has non-zero financial impact above a role-specific
  threshold.
- The action is irreversible (DROP, DELETE, force-push, credential
  rotation).
- The action leaves the normal blast radius (cross-account, cross-VPC,
  cross-region).
- The policy explicitly lists the tool as `require_approval`.

### 5.2 Approval primitive

The provider-native primitive is preferred where available:

- **Bedrock:** action group `customControl = RETURN_CONTROL`. The agent
  hands the request back; an out-of-band approver authorises before the
  action is executed.
- **ADK / Vertex:** a `before_tool` callback that short-circuits and
  defers to an approval gateway.

Where neither is available, the platform provides an `ApprovalGateway`:

- `ApprovalRequest` with session scope, expiry, and a one-time signed
  decision token.
- Channels: CLI (dev), HTTPS webhook (service-to-service), chat
  (Slack buttons) with token-returning callbacks.
- Approvals are always **out-of-band**: the same agent process must not
  be able to approve its own request.

Reference: ADR-008 §4.

### 5.3 Approver pools and quorum

Each approval class names a **pool** of eligible approvers (not a
single person). Destructive actions default to **2-of-N quorum**.
Pools are version-controlled.

### 5.4 Time-boxing

Approvals expire — typical defaults: 15 minutes for per-action, 24
hours for batch. Expired requests are denied and audited.

### 5.5 Override and revocation

An approval can be revoked before it is consumed. The revocation is
itself an audited action.

---

## 6. Policy enforcement

### 6.1 Policy as code

Policies — cost, tagging, tool governance, content safety — are
JSON/YAML files in version control. The engine loads them at startup
and validates every change against a schema.

Reference: `policies/*.json`, `scripts/validate_policies.py`.

### 6.2 Per-event evaluation

Every actionable event (resource creation, tool call, model
invocation) is evaluated against all applicable policies. Policies are
composable and orthogonal — cost policies do not mix with content
policies do not mix with tool governance.

### 6.3 Fail-closed defaults

Unknown tools deny. Unparseable policies raise. Missing
provider-guardrail content raises — a guardrail resource with no rules
is treated as a configuration error, not a pass.

### 6.4 No silent bypass

Bypass flags (`--no-verify`, `default_allow=True`) are legitimate only
in development and require explicit operator input. They cannot be set
by the agent, cannot be set by a prompt, and their use is itself
audited.

---

## 7. Least-privilege execution

### 7.1 Identity

Agents run under workload-scoped identities. The **preferred shape is
a per-agent identity**, not a shared service account:

- **GCP:** Vertex AI Agent Engine **Agent Identity** — SPIFFE-based
  principal, Google-managed Context-Aware Access (CAA) policy,
  mTLS-bound certificate credentials. Service accounts are a fallback
  when per-agent identity is unavailable.
- **AWS:** **Bedrock AgentCore Identity** (per-agent workload identity
  with OAuth delegation) where available; otherwise IAM roles scoped
  to a single agent resource. Cross-account access via STS AssumeRole
  with session tags carrying the agent's `session_id`.
- **Azure:** managed identity per agent resource; workload identity
  federation for external callers.

Long-lived access keys, service account keys, and shared bearer tokens
are prohibited. Shared service accounts (one identity across many
agents) are tolerated only in L1 deployments and must be flagged in
the boundary contract for graduation to per-agent identity at L2.

### 7.2 Permissions

The identity is sized to the agent's boundary contract. `*:*` is
never acceptable. Where possible, split permissions across:

- A **reader** identity for discovery.
- A **writer** identity that is only assumed after approval.

**Third-party tool access.** When the agent calls non-cloud-provider
APIs, use one of the following patterns — *never* a shared API key or
long-lived bearer token in the agent's environment:

- **OAuth delegation** — the agent acts on behalf of a user; both the
  user's identity and the agent's identity appear in downstream logs.
  The preferred shape on Vertex Agent Engine (documented delegation
  flow) and applicable to Bedrock via AgentCore Gateway.
- **Secret-Manager-scoped API keys** — keys are stored in a vault
  (Google Secret Manager, AWS Secrets Manager) and retrieved at
  invocation time using the agent's per-agent identity. Access to the
  secret is itself audited.
- **Workload identity federation to the third party** where the third
  party supports OIDC (e.g. GitHub Actions-style federation).

Any third-party integration that cannot be wired through one of the
three patterns is documented in the boundary contract as a
compensating risk.

### 7.3 Tool scope

Tool allow-lists mirror the permissions. An agent with `ec2:Describe*`
should not have `ec2:Terminate*` in its tool allow-list even if the
underlying IAM role has it — the governor tightens further.

### 7.4 Data scope

Queries and reads are scoped by project, account, tag, or label.
Wildcarded `SELECT *` / `ListAllBuckets` is flagged unless the boundary
contract documents it.

### 7.5 Network scope

Production agents deploy inside a perimeter: VPC Service Controls
(GCP), VPC endpoints + SCP (AWS), and egress is denied by default.
Code execution uses **hermetic** sandboxes with no network and
per-run cleanup.

Use a **named, provider-managed sandbox** rather than building one:

- **GCP:** Vertex AI Code Interpreter extension, Gemini Enterprise
  `tool_execution`, or Agent Engine **Code Execution**.
- **AWS:** Bedrock **Code Interpreter** action group, AgentCore Code
  Interpreter.

"Build your own" sandboxes are an antipattern — they accumulate CVEs
and drift away from provider hardening. Deviate only when a named
option cannot host the workload, and document the deviation in the
boundary contract.

### 7.6 Managed agent runtime (preferred at L2+)

Use a provider-managed agent runtime rather than a bespoke Python host
wherever the provider offers one. Managed runtimes bring first-class
identity, observability, and threat detection that a custom host must
replicate manually.

- **GCP:** Vertex AI Agent Engine Runtime (supports ADK, LangChain,
  LlamaIndex, A2A, CrewAI), with built-in OpenTelemetry tracing, Cloud
  Monitoring, Cloud Logging, Agent Identity, Agent Engine Threat
  Detection, Sessions, Memory Bank, Code Execution, Example Store.
- **AWS:** Amazon Bedrock **AgentCore** — Runtime + Identity + Memory
  + Gateway + Observability + Code Interpreter. Pairs with Bedrock
  Guardrails and CloudWatch / X-Ray.

L1 deployments on bespoke hosts are allowed; boundary contract must
note the migration path to a managed runtime for L2 graduation.

---

## 8. Auditability

### 8.1 What is recorded

Every one of the following is recorded as an append-only audit entry:

- Policy create / update / delete.
- Tool call (request, decision, result, duration).
- Model invocation (prompt preview, token counts, inference config).
- Guardrail / Model Armor verdict.
- Approval request, response, and override.
- Human action (acknowledge, resolve, halt, resume).
- Notification dispatch and dead-letter movement.

### 8.2 Tamper evidence

- Append-only JSONL on disk.
- SHA-256 **chained** checksum — each entry's checksum depends on the
  previous one, so any edit invalidates all subsequent entries.
- Chain is **continuous across file rotations** (e.g. daily files). A
  chain break at load time raises, not resets.
- Daily **signed manifest** — last checksum, file SHA-256, entry count,
  signed Ed25519 (prod) or HMAC-SHA256 (dev).
- Signed exports — `(payload, signature, algorithm)` with canonical JSON
  serialisation.

Reference: `core/audit.py::AuditLogger`, ADR-008 §3.

### 8.3 Correlation

Every record carries a `correlation_id` (end-to-end trace) and
`causation_id` (direct parent). Reconstructing a session is a single
query on `correlation_id`.

### 8.4 Retention

- Minimum 365 days for cost-impacting actions.
- Minimum 7 years for actions on regulated data (GDPR / SOX / HIPAA /
  PCI), aligned to the relevant regime.
- Retention is encoded in infrastructure (CloudWatch Log Group
  retention, S3 lifecycle, Cloud Logging sinks), not documented as a
  wish.

### 8.5 Independent storage

Audit data is written to a storage location with **different blast
radius** from the agent: different account, different project, with
IAM that denies the agent's identity `Delete*` / `PutObject` with
overwrite. Write-once-read-many (WORM) where available.

---

## 9. Observability

### 9.1 Three surfaces

1. **Traces.** Per-session `AgentTrace` with one `AgentStep` per model
   invocation, tool call, guardrail verdict, approval, override, and
   filter decision. Each step has a timestamp, rationale, and
   provider-native raw payload. Traces **must be emittable as
   OpenTelemetry spans** — `correlation_id` → `trace_id`, `step_id`
   → `span_id`, `parent_step_id` → `parent_span_id` — so they
   integrate with any OTel-compatible APM (Datadog, Honeycomb,
   Grafana Tempo, Cloud Trace, X-Ray).
2. **Metrics.** Per-session and aggregate: call rate (calls/min), tool
   distribution, tokens in/out, cost, guardrail trigger rate, approval
   latency.
3. **Logs.** Structured JSON; one record per step. Correlatable via
   `correlation_id` with the trace.

**Named sinks.** Telemetry lands in a provider-native destination, not
only local files:

- GCP: Cloud Trace (OTel), Cloud Monitoring, Cloud Logging.
- AWS: X-Ray / AWS Distro for OpenTelemetry (ADOT), CloudWatch
  Metrics, CloudWatch Logs.
- Bedrock AgentCore and Vertex Agent Engine emit OTel spans natively;
  adopters should *consume* those, not re-invent them.

Reference: `core/agent_trace.py`.

### 9.2 Anomaly and drift detection

Run live statistical detection on at least:

- **Call rate.** Z-score against rolling mean per session and per
  agent.
- **Tool distribution.** χ² against a 30-day baseline; sudden shifts
  in which tools an agent picks are flagged.
- **Token and cost.** Cumulative, with warning at 75 % and auto-halt at
  100 % of session budget.
- **Guardrail triggers.** Rate of `GUARDRAIL_INTERVENED` — sustained
  rise implies jailbreak pressure.

Reference: ADR-008 §5, ADR-005 (statistical thresholds).

### 9.3 Replay

A `finops_replay_session(session_id)` equivalent must exist. It
reconstructs the full transcript (model → tool → approval → cloud
effect) as structured JSON **and** a Markdown render suitable for
review documents.

### 9.4 Health probes

Kubernetes-style liveness / readiness / deep probes on the governance
plane itself. The audit logger, approval gateway, and governor are
first-class dependencies; their failure is treated as an incident.

---

## 10. Explainability

### 10.1 Required rationale

Every `AgentStep` carries a `rationale` field — a short
natural-language reason captured from the provider trace where
available, or synthesised by the adapter. "The agent chose tool X
because Y" is persisted, not just inferred at debug time.

### 10.2 Decision records

Every gate emits a `DecisionRecord` with:

- The verdict (allow / deny / approval_required / halt).
- The gate that made the decision.
- A human-readable reason.
- The `AgentStep` that triggered it.

### 10.3 Post-action reports

For Operator and Autonomous roles, every session produces a
human-readable session report at close:

- What was done.
- What decisions were taken and by which gates.
- What was denied and why.
- Remaining budget and anomaly signals.

No session completes without this report.

### 10.4 Prohibition on unexplained actions

A production session that produces an action with an empty `rationale`
is a bug. Adapters that cannot extract rationale from provider traces
must synthesise one from the structural signals available (model, tool
name, arguments) — never leave it blank.

### 10.5 Fairness and bias

Agents that make **allocative or prioritisation decisions across
people, teams, tenants, or customers** carry fairness obligations
that pure data-retrieval agents do not. When the boundary contract
marks an agent as allocative:

- **Protected attributes** are enumerated in the boundary contract
  (e.g. team size, cost-centre budget tier, customer region). Protected
  attributes must not flow into model prompts or tool arguments unless
  their use is justified and documented.
- **Fairness evaluation** runs alongside the safety eval. On GCP use
  **Vertex Fairness Indicators** and **Vertex Explainable AI** to
  measure group-wise behaviour; equivalent rubric metrics
  (`rubric_based_final_response_quality_v1` with fairness rubrics) for
  workflows without tabular baselines.
- **Decision reviews** sample allocative decisions weekly for manual
  fairness assessment. A single skewed outcome is an input signal,
  not yet an incident; a pattern is an incident (§15).

FinOps agents are typically low-risk on this axis (they allocate
findings, not opportunities), but the framework applies to any
adopter, so fairness is named explicitly.

---

## 11. Data handling

### 11.1 Data classification

Every agent declares the data classes it may encounter:

| Class | Examples | Handling |
|---|---|---|
| Public | open-source code, press releases | No special handling. |
| Internal | architecture docs, cost numbers | No external egress. |
| Confidential | customer data, secrets, credentials | Encrypted at rest & in transit; never in prompts. |
| Regulated | PII, PHI, PCI | Above + provider PII filters + DLP scanning. |

### 11.2 Input filters

Before a prompt or tool argument reaches a model / external service, a
platform filter stack runs:

- **PII redactor** — emails, IBANs, cards, NINOs, SSNs, phones.
- **Secret scanner** — cloud access keys, GitHub tokens, GCP service
  account files, bearer tokens. Blocks on match.
- **Prompt-injection heuristic** — narrow, high-precision phrase
  match. Complements provider guardrails (Bedrock Prompt Attack filter,
  Model Armor), never replaces them.

Reference: `core/filters.py`.

### 11.3 Output filters

Model outputs traverse the same filter stack before being returned to
the caller or persisted.

### 11.4 Provider guardrails are mandatory

**Bedrock Guardrails** — six named filter families. Populate at minimum
the first four; the last two are strongly recommended for
retrieval-augmented or regulated workloads:

1. **Content filters** — Hate / Insults / Sexual / Violence /
   Misconduct / Prompt Attack, each configurable by strength.
2. **Denied topics** — organisation-specific forbidden topics (e.g.
   credential exfiltration, destructive IaC, competitor disclosure).
3. **Word filters** — exact-match blocklist (profanity, competitor
   names, release-embargoed terms).
4. **Sensitive information filters** — PII blocking / masking and
   custom regex (e.g. internal ticket IDs).
5. **Contextual grounding checks** — require the response to be
   grounded in retrieved context; threshold ≥ 0.7 for RAG workloads.
6. **Automated Reasoning checks** — formal logic validation against
   declared rules; deterministic hallucination detection for
   compliance-critical responses.

An empty guardrail resource is a configuration error, not a pass.

**Vertex AI Model Armor:**

- Template attached to every Vertex AI endpoint in
  `INSPECT_AND_BLOCK` mode.
- **Model Armor floor settings** for Google-managed MCP servers
  (BigQuery MCP, etc.) — baseline filters that apply to every MCP
  call regardless of caller template.
- Model Armor monitoring dashboard reviewed weekly for trigger
  patterns.

**Gemini safety settings.** `GenerateContentConfig.safety_settings`
explicitly set per HarmCategory (hate / harassment / sexual /
dangerous), never left at defaults.

### 11.5 Minimisation

Store previews, not raw payloads. A `prompt_preview` capped at 512
chars satisfies review without hosting multi-MB prompts in the audit
log. The raw payload stays in the provider's secure log destination
with appropriate retention.

### 11.6 Conversational memory and session stores

Managed session / memory primitives — **Vertex Agent Engine Sessions
and Memory Bank**, **Bedrock AgentCore Memory** — store
user-associated content across interactions. They inherit the full
data-handling obligations of §11 *plus* four memory-specific
controls:

1. **Retention policy.** Declared per memory store, defaulting to the
   minimum required for the agent's purpose. Expired entries are
   purged, not archived.
2. **User-scoped deletion.** A right-to-be-forgotten request deletes
   every memory entry associated with the user across every session.
   This is a tested procedure, not a wish.
3. **Cross-session isolation.** Memory entries are keyed by user /
   tenant; no retrieval leaks across boundaries. Cross-tenant
   retrieval is a security incident (§15).
4. **Memory-injection threat model.** Treat retrieved memory as
   untrusted input — it passes through the same input filters (§11.2)
   as user prompts. An attacker who plants content in memory must not
   be able to steer a later session.

Session + memory stores write audit entries for create / read /
delete events, scoped to the same correlation IDs as the active
session.

### 11.7 Data lineage

Track the origin and transformation of every data source the agent
reads — knowledge bases, RAG indexes, retrieved documents, prompt
templates, memory stores. Lineage answers "where did this answer come
from?" in a way that survives audit.

Concrete controls:

- Each retrieval source is **versioned** (commit hash for document
  stores, index version for vector DBs, template version for prompts).
  The version is recorded on every `AgentStep` that consumed it.
- Prompt templates are **stored in git**, never edited in place. A
  change to a template is a change-management event (§16).
- On GCP, **Data Catalog** / **Dataplex Data Lineage** is the
  documented surface. On AWS, **DataZone** lineage or custom tagging
  on S3 / OpenSearch. Either way, the lineage graph is queryable by
  the agent's corpus owner.
- "Unknown provenance" data cannot be ingested into a knowledge base
  that feeds an agent; the ingestion pipeline is itself subject to
  §16 change management.

### 11.8 DLP and differential privacy

Where agents handle data that is sensitive enough to warrant more than
pattern-based redaction (§11.2), apply provider-native DLP and, where
training-like flows exist, differential privacy.

- **DLP:** Google Cloud **Sensitive Data Protection** (formerly DLP
  API) for inspection + de-identification of inputs and outputs;
  Amazon **Macie** for S3 data classification and findings routed
  into the incident pipeline (§15).
- **Differential privacy:** BigQuery DP aggregations for any agent-
  served analytics over user populations. DP is orthogonal to output
  filtering — it protects the *training-time* or *aggregation-time*
  population from re-identification.
- **k-anonymity thresholds** on cohort-level outputs: an agent that
  returns grouped statistics must refuse groups below a declared `k`.

Most FinOps agents do not need these controls; they are mandatory
for agents serving end-user populations or handling regulated data
(HIPAA, PCI, GDPR special-category).

---

## 12. Security

### 12.1 Threat model

Agents are assumed to be target-rich for prompt injection (direct and
indirect via tool outputs), context poisoning, tool exfiltration, and
privilege escalation via chained tool calls. Controls must defend
against adversarial inputs at every step, not only at the user
boundary.

### 12.2 Secret hygiene

- No hardcoded credentials.
- No credentials in prompts, tool arguments, or audit logs (enforced
  by the secret scanner at filter time).
- Secrets fetched from a vault scoped to the agent identity; access
  itself is audited.

### 12.3 Supply chain

- Model IDs and inference profiles are pinned. Upgrades are change-
  management events.
- MCP servers are pinned by URL and version; catalogue drift is
  detected at start of session.
- LLM SDKs are pinned and scanned for CVEs.
- **SLSA ≥ Level 2** for build provenance on every agent container
  and tool-side Lambda / Cloud Run deployment. L3+ maturity requires
  SLSA Level 3.
- Container images are **signed** — Sigstore / cosign (preferred,
  keyless via OIDC), or provider-native (AWS Signer, Binary
  Authorization on GKE / Cloud Run). Deployments verify signatures
  before admission; unsigned images are rejected at the admission
  controller, not retried.
- Third-party MCP server binaries are verified against a known hash
  before execution; `npx @vendor/mcp-server@latest` without a pinned
  hash is prohibited in production.

### 12.4 Signed artifacts

- Policy files are signed or stored in a write-protected bucket.
- Audit manifests are signed (Ed25519 in prod).
- Exported audit bundles are signed at the payload boundary.

### 12.5 Boundary hardening

- VPC-SC / SCP perimeters.
- Egress denylist by default.
- Outbound HTTPS only to an allow-listed set of endpoints.
- Hermetic code execution sandboxes.

### 12.6 Managed agent threat detection

Enable provider-managed threat detections that target agent-specific
attack patterns. Generic anomaly detection is insufficient.

- **GCP:** Security Command Center **Agent Engine Threat Detection**
  (Preview) — purpose-built for agents deployed to Vertex AI Agent
  Engine; flags credential abuse, tool-chain escalation, anomalous
  egress from the agent runtime.
- **AWS:** GuardDuty (EKS Runtime Monitoring for self-hosted agents),
  CloudTrail Insights for anomalous Bedrock API patterns, and
  Bedrock AgentCore's built-in observability signals.
- Findings feed the incident pipeline (§15) with the same correlation
  IDs as the agent trace.

### 12.7 Red-teaming and adversarial evaluation

Regression eval (§16.3) proves the agent still *does the right thing*
on the happy path. Red-teaming proves the agent *refuses the wrong
thing* under pressure. They are distinct activities with distinct
cadences.

Required red-team coverage:

- **Direct prompt injection** — user-authored attempts to override the
  system prompt, reveal instructions, or repurpose the agent.
- **Indirect prompt injection** — payloads planted in tool outputs,
  retrieved documents, session memory (§11.6), or web-fetched content.
- **Tool-chain escalation** — sequences of benign tool calls that
  combine into a forbidden outcome.
- **Credential / data exfiltration** — attempts to get the agent to
  emit secrets, PII, or internal identifiers.
- **Sandbox escape** — where code-execution tools exist.

Cadence: at minimum **quarterly** for Operator roles, **monthly**
for Autonomous roles. Results feed a red-team findings log that is
reviewed in the quarterly operating cycle (§17.2). A novel finding
produces a new automated regression case before close-out.

References: AWS **Bedrock red-team reference kit**, Google **SAIF
threat catalogue**.

---

## 13. Resilience

### 13.1 Circuit breakers

Every external dependency (model provider, MCP server, approval
gateway) sits behind a circuit breaker with documented open / half-
open / closed thresholds. An open breaker denies cleanly; it does not
fail silently.

Reference: `core/circuit_breaker.py`.

### 13.2 Dead-letter queues

Notifications, approvals, and audit writes that fail transiently go to
a dead-letter queue with retry. A non-empty DLQ is a monitored alert
condition.

Reference: `core.notifications.CompositeDispatcher.retry_dead_letters`.

### 13.3 Reconciliation

A reconciliation pass runs on a schedule to detect:

- Unevaluated events (stored but never processed).
- Stale alerts (pending past SLA).
- Audit chain integrity gaps.
- Approval requests past expiry.

Findings are either auto-fixed (e.g. replay) or paged.

Reference: `agents/reconciliation_agent.py`.

### 13.4 Degraded modes

When the governance plane is partially down, agents **default to safe
degradation**:

- Audit unavailable → tool calls are **denied**, not logged-and-allowed.
- Approval gateway unavailable → `require_approval` tools are denied.
- Content filter unavailable → strings are treated as untrusted and
  tool calls that would carry them are denied.

"Available but broken" is worse than "unavailable"; choose fail-closed.

### 13.5 Replay and idempotency

Any automatable action must be idempotent by natural key or tagged by
request ID so a safe replay does not produce double execution.

---

## 14. Human oversight

### 14.1 Kill-switch

An operator **must** be able to halt an agent mid-session in under a
minute, without killing the process or rotating IAM credentials.
Implementation: an MCP tool / API that writes a denylist entry the
governor consults; any in-flight tool call observing a halted session
short-circuits to `Decision.DENY` with `halt_reason`.

Reference: ADR-008 §4 `AgentSupervisor.halt(session_id, reason, actor)`.

### 14.2 Override API

Humans can:

- Approve or deny pending approval requests.
- Halt and resume a session.
- Revoke a prior approval before consumption.
- Fast-forward a stuck trajectory by supplying a result manually
  (with audit).

Every override is itself an `AgentStep` (`HumanOverrideStep`) in the
trace.

### 14.3 Interactive review

High-risk actions (Operator+Autonomous roles) surface approval
requests in a chat or dashboard. The request carries:

- What the agent wants to do (plain English).
- Why (rationale from the last model step).
- Blast radius estimate.
- Rollback procedure.
- Signed decision token.

### 14.4 Transparency for affected parties

Downstream stakeholders — resource owners, cost centres, security
reviewers — receive **contextualised** notifications, not just
identifiers. Every alert names *who* created the resource, *what* the
cost impact is, *what to do next*, and *the escalation path* so no
recipient needs to look anything up. Attribution is not optional.

---

## 15. Incident response

### 15.1 Definition

An *agent incident* is any of:

- An agent took an action outside its boundary contract.
- A guardrail intervention rate > baseline for > 5 minutes.
- An audit chain break.
- An approval was consumed after expiry.
- A kill-switch was used in anger.
- A managed threat-detection finding (SCC Agent Engine Threat
  Detection, GuardDuty, CloudTrail Insights) flagged the agent's
  identity or runtime.
- Cross-tenant / cross-session memory access detected (§11.6).
- Memory-injection payload identified in a session store.

### 15.2 Runbook structure

Every incident class has a runbook with:

1. **Contain.** Halt the session(s). Revoke outstanding approvals.
2. **Preserve evidence.** Export signed audit bundle for the impacted
   correlation IDs. Snapshot provider traces.
3. **Triage.** Was it unsafe, unauthorised, or unexpected? Who, what,
   when, blast radius.
4. **Remediate.** Revert changes if possible. Disable the agent if
   scope is unclear.
5. **Communicate.** Internal stakeholders on a known cadence; external
   if regulated data touched.
6. **Review.** Post-incident: what controls failed, what changes land.

### 15.3 Forensic guarantees

Because audit is chained, signed, and has independent blast radius
(§8.5), the incident team can trust the evidence even if the agent
process itself was compromised.

### 15.4 Blameless culture, enforceable controls

Incident reviews target controls, not individuals. But controls that
were bypassed by a human operator are gated by the change-management
process (§16), not just exhortation.

---

## 16. Change management

### 16.1 Version control for everything

- Policies (JSON).
- Prompts / system instructions.
- Tool catalogues.
- Approver pools.
- Boundary contracts.
- Model IDs and inference profiles.

All live in git; changes land via pull request.

### 16.2 Review requirements

| Change type | Reviewers | Additional gate |
|---|---|---|
| Policy add / update | 1 peer + policy owner | Drift check (e.g. Terraform-to-policy mapping). |
| Policy delete / disable | 2 peers including security | Explicit justification in PR body. |
| Approver pool | Security + pool owner | Pool diff captured in audit on merge. |
| Prompt / instruction | Agent owner + 1 peer | Regression eval harness passes. |
| Model pin upgrade | Agent owner + platform | Eval harness + guardrail compatibility check. |
| Tool added to allow-list | Agent owner + security | Boundary contract updated in same PR. |

### 16.3 Pre-flight gates

CI runs pre-flight checks on every PR:

- Policy schema validation.
- Tool-call governor dry-run against the new policy (rejects go/no-go
  matrix).
- Content-filter smoke tests.
- Agent regression eval harness — see eval taxonomy below.

**Eval taxonomy (minimum dimensions).** A compliance-worthy harness
covers at least the following six dimensions; named criteria shown
use the ADK Evaluate vocabulary — AWS equivalents exist via Bedrock
evaluation jobs and custom metrics.

| Dimension | ADK criterion | What it catches |
|---|---|---|
| Tool trajectory | `tool_trajectory_avg_score` | Wrong tool chosen or wrong order |
| Response match | `response_match_score` / `final_response_match_v2` | Regression in answer content |
| Response quality | `rubric_based_final_response_quality_v1` | Degradation vs rubric (concise, on-brand, complete) |
| Tool-use quality | `rubric_based_tool_use_quality_v1` | Over-calling, wrong args, missing required tool |
| Groundedness | `hallucinations_v1` | Claims unsupported by retrieved context |
| Safety | `safety_v1` | Harmful / unsafe response slipped past guardrails |

Multi-turn agents additionally cover `multi_turn_task_success_v1`,
`multi_turn_trajectory_quality_v1`, `multi_turn_tool_use_quality_v1`.
Model-upgrade PRs (model pin change) run the full suite; prompt-only
PRs may run the first four as a fast gate with nightly full runs.

### 16.4 Rollout

Staged rollout: dev → staging → a canary subset of prod → full prod.
Each stage has a **burn-in window** with automatic rollback on anomaly
detection.

### 16.5 Deprecation

Controls and capabilities are deprecated with a **one-release
deprecation window** and a legacy flag, never hard-removed without
notice. Breaking defaults (e.g. flipping `default_allow` to `False`)
are explicit in release notes.

### 16.6 Fine-tuning and adapter governance

When an agent's underlying model is personalised — full fine-tune,
LoRA adapter, or prompt-tuning on organisation data — additional
governance applies because the model itself becomes a piece of
regulated artefacts, not just an inference client.

Required controls:

- **Training-data provenance.** Every record in the tuning set has a
  documented source and a consent basis. Personal data requires a
  lawful-basis record (GDPR Art. 6) before ingestion.
- **Data minimisation.** Remove fields irrelevant to the target task;
  apply DLP de-identification (§11.8) before training.
- **Right-to-be-forgotten.** The pipeline supports removing a subject
  from the training set and re-tuning on request. If retraining is
  infeasible, the fine-tune is not permitted.
- **Eval delta vs base model.** Pre-deployment eval (§16.3) runs
  against both the base model and the tuned model; regression on any
  named dimension blocks the rollout.
- **Adapter artefacts are signed and versioned.** Tuned checkpoints
  are stored in a registry with SLSA provenance (§12.3) and signed
  with the organisation's key.
- **Residency and export control.** Tuning runs in a region consistent
  with the data-residency commitments; cross-region adapter transfer
  is a change-management event.

An agent using a tuned model cites the **adapter version** alongside
the foundation model card in its boundary contract (§3.2).

---

## 17. Operating model

### 17.1 Roles

| Role | Responsibility |
|---|---|
| **Agent owner** | Purpose, boundary contract, approver pool, on-call. |
| **Platform team** | Governor, audit, approval gateway, content filters, observability. |
| **Security** | Threat model, guardrail content, supply-chain integrity, incident lead. |
| **Compliance** | Retention regimes, signed-export chain-of-custody, regulator interface. |
| **SRE / operators** | Kill-switch, reconciliation, DLQ handling, runbooks. |

One human holds each role per agent. RACI is explicit.

### 17.2 Review cadence

- **Weekly.** Agent-level metrics dashboard; guardrail trigger review.
- **Monthly.** Policy drift review; anomaly false-positive tuning.
- **Quarterly.** Boundary contract renewal; approver pool review; DR
  exercise (forced kill-switch + recovery).
- **Annually.** Threat model refresh; regulator evidence pack.

### 17.3 Evidence pack

At any time, the governance plane produces an evidence pack containing:

- Active policies + their signatures.
- Signed audit export for a specified window.
- Per-agent boundary contracts.
- Approver pool snapshot.
- Eval harness results.
- Incident log.

Evidence packs are regulator-ready without additional engineering.

---

## 18. Maturity levels

Organisations rarely adopt the framework in full on day one. The
levels below define an explicit progression.

### L1 — Foundational (first 30 days)

- All agents classified as Observer or Advisor.
- Tool governor live with fail-closed default.
- Chained audit log + daily manifest.
- Provider guardrails populated with at minimum PII + prompt-attack
  filters.
- Kill-switch available.
- Secret scanner in filter stack.

### L2 — Production-ready (90–180 days)

- Operator role permitted with per-call approval gateway (RoC or
  equivalent) on destructive actions.
- End-to-end trace model wired through provider adapters.
- Behavioural anomaly detection on call rate, tool distribution, token
  usage.
- Signed audit exports used in compliance reporting.
- Reconciliation agent running on schedule.
- Regression eval harness in CI.

### L3 — Autonomous-capable

- Autonomous operator role permitted for a narrow, reviewed set of
  actions.
- 2-of-N approval quorum for irreversibles.
- Per-session per-principal budget isolation.
- Model Armor / equivalent in `INSPECT_AND_BLOCK` mode org-wide.
- Independent-blast-radius audit storage, WORM where available.
- Quarterly DR exercise.
- Annual regulator evidence pack produced automatically.

### L4 — Systemic assurance

- Multi-agent delegation with preserved provenance and quorum at the
  boundary.
- Formal control attestations (e.g. SOC 2, ISO 42001) mapped to this
  framework.
- Cross-provider parity: identical controls regardless of which cloud
  hosts the agent.
- Continuous control monitoring (CCM) with drift detection between
  declared policy and observed behaviour.

---

## 19. Minimum compliance checklist

Applicable to any agent system claiming conformance. One checkbox per
line; every unchecked box is a finding.

### Identity and access

- [ ] Agent runs under IAM role / WIF / managed identity — no static keys.
- [ ] IAM permissions ⊆ boundary contract.
- [ ] Tool allow-list ⊆ IAM permissions.

### Governor

- [ ] Single enforcement point for all tool calls.
- [ ] `default_allow = False` in production.
- [ ] Policy schema-validated at load.
- [ ] Per-principal budgets, not global.

### Provider controls

- [ ] Bedrock Guardrails populated with content + denied-topics +
      word + PII filters; contextual grounding configured for RAG;
      Automated Reasoning for compliance-critical surfaces.
- [ ] Model Armor template attached to every Vertex endpoint in
      `INSPECT_AND_BLOCK`; floor settings enabled for Google-managed
      MCP servers.
- [ ] Gemini `safety_settings` set explicitly per HarmCategory.
- [ ] Model invocation logging (CloudWatch / S3) enabled.
- [ ] `aiplatform.googleapis.com` Data-Access logs enabled.
- [ ] Per-agent identity in use (Vertex Agent Identity / Bedrock
      AgentCore Identity) or documented migration path from shared
      service account.
- [ ] Managed agent threat detection wired (SCC Agent Engine Threat
      Detection / GuardDuty / CloudTrail Insights) for L2+.

### Evaluation

- [ ] Regression eval harness covers: tool trajectory, response match,
      response quality, tool-use quality, groundedness, safety.
- [ ] Model pin upgrades run the full suite + guardrail compatibility
      check.
- [ ] Eval results are a merge gate on prompt / model / tool changes.

### Memory and sessions

- [ ] Retention policy declared per memory / session store.
- [ ] User-scoped deletion procedure tested (right-to-be-forgotten).
- [ ] Cross-tenant / cross-session retrieval denied by construction.
- [ ] Retrieved memory passes through input filters before reaching
      the model.

### Data lineage and DLP

- [ ] Every retrieval source versioned; version recorded on consuming
      `AgentStep`.
- [ ] Prompt templates stored in git; edits are PRs.
- [ ] Data Catalog / Dataplex / DataZone lineage queryable by corpus
      owner.
- [ ] Sensitive Data Protection / Macie enabled where data classes
      warrant; DP aggregations / k-anonymity thresholds declared where
      cohort outputs exist.

### Supply chain

- [ ] SLSA ≥ Level 2 (Level 3 at L3+) for agent containers and tool
      Lambdas / Cloud Run services.
- [ ] Container images signed (Sigstore / cosign / provider-native);
      admission controller verifies before deploy.
- [ ] MCP server binaries verified against pinned hash.

### Third-party tool access

- [ ] OAuth delegation, Secret-Manager-scoped keys, or OIDC
      federation — never shared long-lived tokens.
- [ ] Logs show both user and agent identity on delegated calls.

### Fairness (allocative agents only)

- [ ] Protected attributes declared in boundary contract.
- [ ] Fairness eval (Vertex Fairness Indicators or rubric equivalent)
      runs alongside safety eval.
- [ ] Weekly decision sampling with manual fairness review.

### Red-teaming

- [ ] Quarterly red-team for Operator, monthly for Autonomous.
- [ ] Coverage spans direct + indirect prompt injection, tool-chain
      escalation, exfiltration, sandbox escape.
- [ ] Novel findings produce regression cases before close-out.

### Code execution

- [ ] Named provider-managed sandbox (Vertex Code Interpreter, Gemini
      Enterprise `tool_execution`, Agent Engine Code Execution,
      Bedrock Code Interpreter, AgentCore Code Interpreter). No bespoke
      sandboxes.

### Model documentation

- [ ] Foundation model card referenced in boundary contract.
- [ ] For tuned models: adapter version + SLSA provenance cited.

### Fine-tuning (if applicable)

- [ ] Training-data provenance + lawful-basis record per subject.
- [ ] DLP de-identification before training.
- [ ] Right-to-be-forgotten pipeline tested.
- [ ] Eval delta vs base model — regression blocks rollout.
- [ ] Adapter artefacts signed and versioned.
- [ ] Data residency respected on tuning runs.

### Approvals

- [ ] Out-of-band approval gateway wired.
- [ ] Destructive actions use RoC (or equivalent deferred executor).
- [ ] Approver pool ≥ 2 for irreversibles.
- [ ] Approvals expire; expired requests deny.

### Audit

- [ ] Chained checksum; chain-break load-time error.
- [ ] Daily signed manifest.
- [ ] `export_signed` available.
- [ ] Independent storage, different blast radius.

### Observability

- [ ] Per-session `AgentTrace` persisted.
- [ ] Rationale populated for every step.
- [ ] Call-rate + tool-distribution + token anomaly detectors live.
- [ ] Replay tool available.

### Content filters

- [ ] PII redactor on input and output.
- [ ] Secret scanner blocks on match.
- [ ] Prompt-injection heuristic live alongside provider filter.

### Human oversight

- [ ] Kill-switch halts a session in < 1 minute.
- [ ] Override API emits `HumanOverrideStep`.
- [ ] Alerts are contextualised (who / what / why / next / escalation).

### Change management

- [ ] Policies, prompts, approver pools all in git.
- [ ] Pre-flight CI gates (policy schema, regression eval, drift check).
- [ ] Staged rollout with canary burn-in.
- [ ] Deprecation window on breaking defaults.

### Incident response

- [ ] Runbook per incident class.
- [ ] Forensic export procedure documented.
- [ ] Quarterly DR / kill-switch exercise.

---

## Appendix A — mapping to this repository

The framework was distilled from `fin-ai-ops`. The table below maps
each control domain to its reference implementation so adopting teams
can copy the shape, not just the words.

| Control | Reference |
|---|---|
| Single enforcement point | `core/tool_governor.py::governed_call` |
| Declarative policy | `core/tool_governor.py::GovernancePolicy`, `policies/*.json` |
| Structured sandboxing | `core/tool_governor.py` (ToolRequest → governed_call → Artifact) |
| Fail-closed default | `GovernancePolicy(default_allow=False)` |
| Audit trail | `core/audit.py::AuditLogger` (chained SHA-256 + manifest + signed export) |
| Chain-across-file verification | `AuditLogger.load_from_disk(strict=True)` |
| Signed exports | `AuditLogger.export_signed` |
| Agent trace model | `core/agent_trace.py` |
| Bedrock trace adapter | `providers/aws/agent_trace_adapter.py` |
| ADK trace plugin | `providers/gcp/agent_trace_plugin.py` |
| Content filters | `core/filters.py` (PII / prompt-injection / secrets) |
| Circuit breaker | `core/circuit_breaker.py` |
| Reconciliation | `agents/reconciliation_agent.py` |
| Health probes | `agents/health_agent.py` |
| Statistical thresholds | `core/thresholds.py`, ADR-005 |
| Policy-as-code | ADR-001 |
| Per-event evaluation | ADR-003 |
| Event-driven alert pipeline | ADR-004 |
| Agent governance decision | ADR-008 |
| Phase-1 findings that drove design | `docs/governance/PHASE1_GAP_REPORT.md` |

---

## Appendix B — external framework cross-reference

Condensed mapping from this framework's sections to the most common
external requirements encountered during audit.

### Google SAIF (6 elements)

| SAIF element | Our sections |
|---|---|
| Expand strong security foundations to the AI ecosystem | §7, §12 |
| Extend detection and response to bring AI into the org's threat universe | §9, §12.6, §15 |
| Automate defences to keep pace with existing and new threats | §13, §16.3 |
| Harmonize platform-level controls to ensure consistent security | §6, §11.4 |
| Adapt controls to adjust mitigations and create feedback loops | §9.2, §16 |
| Contextualize AI system risks in surrounding business processes | §3.2, §17 |

### NIST AI RMF 1.0

| Function | Our sections |
|---|---|
| Govern | §3 (roles/boundaries), §16 (change mgmt), §17 (operating model) |
| Map | §3.2 (boundary contract), §11.1 (data classification), §12.1 (threat model) |
| Measure | §9 (observability), §16.3 (eval) |
| Manage | §5 (approvals), §13 (resilience), §14 (oversight), §15 (IR) |

### EU AI Act high-risk obligations

| Obligation | Our sections |
|---|---|
| Risk management system | §3, §12.1 |
| Data and data governance | §11 |
| Technical documentation | §3.2 (boundary contract), model cards (§3.2) |
| Record-keeping / logging | §8 |
| Transparency and provision of information | §10, §14.4 |
| Human oversight | §14 |
| Accuracy, robustness, cybersecurity | §12, §13, §16.3 (eval) |
| Quality management system | §17 |

---

## Appendix C — glossary

- **Boundary contract.** Versioned document declaring an agent's
  purpose, role, scope, and owner.
- **Governor.** Single enforcement function through which all tool
  calls pass.
- **Agent trace.** Canonical session-scoped record of every step the
  agent took, including rationale.
- **Decision record.** Structured allow/deny/approval/halt verdict
  from a gate.
- **Provider guardrail.** Cloud-native content safety (Bedrock
  Guardrails, Model Armor).
- **Platform filter.** Provider-agnostic content filter in
  `core/filters.py`; complements but does not replace the guardrail.
- **Approval gateway.** Out-of-band channel that returns a signed
  decision token before a high-risk action executes.
- **Kill-switch.** Operator-accessible API that halts a session in
  progress without killing the process.
- **Evidence pack.** Regulator-ready bundle of signed audit, policies,
  contracts, and eval results.
