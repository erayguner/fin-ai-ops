# Provider Audit — AGENT_GOVERNANCE_FRAMEWORK.md

**Date:** 2026-04-17
**Status:** All gaps (F-1 … F-20) **resolved** in
`AGENT_GOVERNANCE_FRAMEWORK.md` v1.2.
**Sources:** AWS Bedrock userguide + Agent docs; Google Cloud
Well-Architected (Secure AI Framework / SAIF), Vertex AI Agent Engine,
ADK Evaluate, Model Armor.

This audit checks the framework against authoritative provider
documentation for controls it *should* name but currently does not.
Each gap is tagged **P1** (material — affects production readiness
or regulator credibility) or **P2** (tightening — improves
completeness).

---

## P1 — material gaps

### F-1. No reference to industry frameworks (SAIF / NIST AI RMF / EU AI Act)

Google's Cloud Well-Architected security pillar is explicitly aligned
to the **Secure AI Framework (SAIF)**, six core elements. AWS
customers are increasingly asked for **NIST AI RMF** mapping, and EU
deployments need **EU AI Act** posture.

Our framework's 10 principles overlap with SAIF but do not cite it,
which is a miss for:
- Regulator evidence packs (§17.3).
- Security reviewers who want to see named standards.
- Mapping audits (SOC 2 / ISO 42001 — which *we* name in §18 but
  don't cross-reference back here).

**Fix:** add an "Alignment" subsection to §2 citing SAIF 6 elements,
NIST AI RMF (Govern / Map / Measure / Manage), and EU AI Act
high-risk obligations, with a mapping table in Appendix.

---

### F-2. Agent Identity (Vertex) / per-agent SPIFFE identity missing

Vertex AI Agent Engine now supports **per-agent identity** based on
SPIFFE with Google-managed Context-Aware Access and mTLS-bound tokens
("certificate-bound tokens"). This is the current-best-practice
alternative to service accounts — *each agent gets its own principal
identifier* rather than sharing a service-account.

Equivalent on AWS is emerging via **Bedrock AgentCore Identity**
(workload identity per agent resource, OAuth delegation).

Our §7.1 says "IAM role / WIF / managed identity" but doesn't name the
per-agent-identity pattern, which is now the documented
recommendation.

**Fix:** §7.1 — name "per-agent identity" as the preferred shape;
explicitly call out Vertex Agent Identity + AgentCore Identity, note
service accounts as a fallback only.

---

### F-3. No mention of Bedrock AgentCore / Vertex Agent Engine Runtime as the managed hosting pattern

Both providers now offer a **managed agent runtime** — Bedrock
AgentCore (Runtime, Memory, Gateway, Identity, Observability) and
Vertex AI Agent Engine Runtime. These bring first-class observability
(OpenTelemetry tracing, Cloud Monitoring) and security (SCC Threat
Detection, Agent Identity) that *custom Python hosts* do not get.

Our framework treats agents as generic Python workloads. For L3+
maturity, managed runtimes are the documented path.

**Fix:** §17.1 / §18 — add a note that L2+ maturity should consider
the managed runtime; list the managed-runtime feature set as a
reference point.

---

### F-4. OpenTelemetry not named in §9

Vertex AI Agent Engine tracing is **built on OpenTelemetry**. AWS
Distro for OpenTelemetry is the recommended tracing path for Bedrock
Agents hosted on ECS/Lambda. Bedrock AgentCore exposes OTel spans
directly.

Our §9.1 describes "Traces" generically. Without naming OTel we miss:
- Interoperability with existing APM (Datadog / Honeycomb / Grafana
  Tempo).
- The natural shape of provider-emitted spans.

**Fix:** §9.1 — state that `AgentTrace` serialisation should be
emittable as OTel spans; reference the `traceparent` / `span_id`
propagation.

---

### F-5. Eval criteria taxonomy missing

ADK Evaluate ships a specific, named taxonomy:

- `tool_trajectory_avg_score` — exact tool-call trajectory match.
- `response_match_score` — ROUGE-1 reference match.
- `final_response_match_v2` — LLM-judged semantic match.
- `rubric_based_final_response_quality_v1` — rubric-scored response
  quality.
- `rubric_based_tool_use_quality_v1` — rubric-scored tool use.
- `hallucinations_v1` — groundedness check.
- `safety_v1` — harmlessness check.
- `per_turn_user_simulator_quality_v1`, `multi_turn_task_success_v1`,
  `multi_turn_trajectory_quality_v1`, `multi_turn_tool_use_quality_v1`.

Our framework says "regression eval harness" without this taxonomy,
which leaves implementers guessing about coverage.

**Fix:** §16.3 / §19 — list the six eval dimensions that a compliance-
worthy harness must cover (trajectory, response match, hallucination,
safety, rubric quality, multi-turn task success).

---

### F-6. Bedrock Guardrails — missing specific filter families

Our §11.4 says "PII + prompt-attack minimum". Bedrock Guardrails
actually offers six distinct families that should be named in a
standard:

1. **Content filters** — Hate / Insults / Sexual / Violence /
   Misconduct / Prompt Attack.
2. **Denied topics** — organisation-specific forbidden topics.
3. **Word filters** — exact-match blocklist.
4. **Sensitive information filters** — PII blocking/masking + custom
   regex.
5. **Contextual grounding checks** — grounds response in retrieved
   context; flags unsupported claims. Threshold ≥ 0.7 is our
   recommendation.
6. **Automated Reasoning checks** — formal logic validation of
   responses against rules. Detects hallucinations deterministically.

**Fix:** §11.4 — enumerate the six families as the minimum-named set;
§19 — checklist items per family.

---

### F-7. Model Armor MCP integration not named

Model Armor's **floor settings** for Google-managed MCP servers
(Preview 2025-12-10) and the **monitoring dashboard** (GA 2025-12-04)
are new primitives that belong in the framework given MCP is a named
surface (§4.5).

**Fix:** §11.4 — add Model Armor MCP floor settings as a required
control for any agent connecting to a Google-managed MCP server.

---

### F-8. Memory / session storage governance absent

Vertex Agent Engine offers **Sessions** and **Memory Bank** as
first-class managed primitives. Both store data *about users* —
conversation history, retrieved preferences — and therefore inherit
data-handling obligations (§11) that the framework does not spell out.

Missing controls:
- Retention policy for session / memory stores.
- User-scoped purge on the right-to-be-forgotten path.
- Cross-session data leakage prevention.
- Memory-injection threat modelling (an attacker plants content in
  memory to influence later sessions).

**Fix:** new §11.6 "Conversational memory and session stores" with
the four controls above.

---

### F-9. Agent2Agent (A2A) protocol not governed

A2A is Google's open protocol for multi-agent delegation, now
supported on Agent Engine. Our §3.3 says "preserve callerChain /
parent-trace identity" but does not name A2A or the specific identity
propagation on that protocol.

**Fix:** §3.3 — name A2A and the Bedrock "multi-agent collaboration"
caller-chain; require `callerChain` + cryptographically-bound
identity on every hop.

---

### F-10. Agent Engine Threat Detection (SCC) missing from §12 and §15

Google Cloud Security Command Center now ships **Agent Engine Threat
Detection** (Preview) — purpose-built detections for agents (credential
abuse, tool-chain escalation, anomalous egress).

Our §12 and §15 describe generic monitoring / runbooks. Naming SCC
Agent Engine Threat Detection (or AWS equivalents — GuardDuty EKS
Runtime + CloudTrail anomaly) is required for L3+ maturity.

**Fix:** §12.5 — add managed threat-detection as a recommended
control; §15 — incident response paths should integrate SCC /
GuardDuty findings.

---

## P2 — tightening gaps

### F-11. Fairness and bias controls absent

Google SAIF / Vertex governance guidance names **Fairness Indicators**
and **Vertex Explainable AI** alongside transparency/accountability.
Our framework covers explainability well but has no fairness section.

FinOps agents are low-risk on this axis, but the framework is meant
to generalise — a customer-facing support agent would need it.

**Fix:** §10 — add a subsection on fairness-relevant roles (e.g.
agents making allocation or prioritisation decisions across customers
/ tenants / teams).

---

### F-12. Data lineage tracking not named

SAIF calls for **data lineage** — tracking origin and transformation
of data that feeds agents. Our §11 covers filters and classification
but not lineage.

**Fix:** §11.7 — "track and version the data sources and retrieval
indexes that feed the agent (knowledge bases, RAG indexes, prompt
templates)."

---

### F-13. Supply chain specifics (SLSA, signed containers)

SAIF and the Well-Architected pillar call out:
- **SLSA** levels for build provenance.
- Validated / signed container images.
- Sigstore / cosign for artifact signing.

Our §12.3 says "LLM SDKs are pinned and scanned for CVEs" but doesn't
name SLSA or container-signing as named controls.

**Fix:** §12.3 — name SLSA ≥ 2 for build provenance; require signed
container images via Sigstore or provider equivalent for L3+.

---

### F-14. Red-teaming / adversarial evaluation not named

Eval and safety testing are distinct. The framework covers regression
eval (§16.3) but not **continuous red-teaming** (adversarial prompts,
indirect-injection scenarios, memory-poisoning payloads).

Both providers publish red-teaming guidance; AWS has the **Bedrock
Red Team** reference kit and Google has the SAIF threat catalogue.

**Fix:** §12.1 — require a scheduled red-team cadence for Operator+
roles. Separate from the CI eval harness.

---

### F-15. OAuth delegation to third-party tools not named

Vertex Agent Engine documents **OAuth delegation** and API-key
storage via Secret Manager as the path for third-party tool access
from an agent. Our §7 talks about identity but not delegated authority
patterns.

**Fix:** §7.2 — add third-party tool access via OAuth delegation
(agent acts on behalf of a user, both identities appear in logs) or
Secret-Manager-scoped API keys; never shared keys.

---

### F-16. Explicit OpenTelemetry + Cloud Logging sinks

§9 covers observability surfaces but doesn't mandate a **named
destination**. Provider guidance is concrete:
- AWS: CloudWatch Logs / X-Ray / ADOT.
- GCP: Cloud Logging / Cloud Trace / OpenTelemetry.

**Fix:** §9 — telemetry must land in a provider-native sink (not only
local files), to enable correlation with infrastructure logs.

---

### F-17. Model cards / model documentation

SAIF-aligned guidance calls for **model cards** documenting the
foundation model's intended use, training-data posture, and known
limitations. Our framework does not require the agent owner to keep
one alongside the boundary contract.

**Fix:** §3.2 — boundary contract should cite the model card for the
underlying foundation model.

---

### F-18. Code execution — name specific sandboxes

Our §7.5 says "hermetic sandboxes with no network". Providers ship
specific options that should be named:
- Vertex Code Interpreter extension.
- Gemini Enterprise tool_execution.
- Agent Engine Code Execution.
- Bedrock Code Interpreter action group.

**Fix:** §7.5 — list named providers; require "do not build your
own" unless the provider options don't fit.

---

### F-19. Differential privacy + DLP

SAIF calls out **differential privacy** for training data and **DLP**
for outputs. Our §11 has filters but not DLP / DP as distinct
controls. Most FinOps agents won't need DP, but the framework is
general.

**Fix:** §11.8 — mention DLP (Google Cloud DLP / Amazon Macie) and
DP as advanced controls where training-like data flows exist.

---

### F-20. Fine-tuning governance

When agents are personalised via fine-tuning or LoRA on org data,
extra governance applies (training-data provenance, data-residency,
forgetting). Framework doesn't mention this.

**Fix:** new §16.6 "Fine-tuning and adapter governance" — require
documented training data provenance, consent for PII use, eval delta
vs base model.

---

## Summary

| Gap | Priority | Impact if ignored |
|---|---|---|
| F-1 SAIF/NIST/EU-AI-Act | P1 | Regulator pushback |
| F-2 Per-agent identity | P1 | Weaker security posture than documented best practice |
| F-3 Managed agent runtime | P1 | Missing the simplest path to L2+ |
| F-4 OpenTelemetry | P1 | Tracing doesn't integrate with APM |
| F-5 Eval taxonomy | P1 | "Eval harness" means different things to different teams |
| F-6 Bedrock Guardrail families | P1 | Under-specified mandatory filters |
| F-7 Model Armor MCP | P1 | Managed MCP traffic is unguarded |
| F-8 Memory/session governance | P1 | Memory-injection attacks unmitigated |
| F-9 A2A protocol | P1 | Cross-agent identity propagation unspecified |
| F-10 SCC / GuardDuty agent detections | P1 | Production monitoring misses agent-specific threats |
| F-11 Fairness | P2 | Gap for non-FinOps adopters |
| F-12 Data lineage | P2 | SAIF requirement |
| F-13 SLSA / signed containers | P2 | Supply chain gap |
| F-14 Red-teaming | P2 | Adversarial blind spot |
| F-15 OAuth delegation | P2 | Third-party tool access under-specified |
| F-16 Named telemetry sinks | P2 | Logs don't correlate with infra |
| F-17 Model cards | P2 | Missing foundation-model documentation |
| F-18 Named sandboxes | P2 | "Build your own" antipattern |
| F-19 DLP / DP | P2 | Advanced data controls absent |
| F-20 Fine-tuning governance | P2 | Adapter / LoRA workflows unaddressed |

**Recommendation:** land all 10 P1 fixes as a single edit to the
framework (they're concise additions, not restructures). Treat P2
items as a follow-up ticket.
