# FinOps Automation Hub

[![CI](https://github.com/erayguner/fin-ai-ops/actions/workflows/ci.yml/badge.svg)](https://github.com/erayguner/fin-ai-ops/actions/workflows/ci.yml)
[![CodeQL](https://github.com/erayguner/fin-ai-ops/actions/workflows/codeql.yml/badge.svg)](https://github.com/erayguner/fin-ai-ops/actions/workflows/codeql.yml)
[![OSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/erayguner/fin-ai-ops/badge)](https://securityscorecards.dev/viewer/?uri=github.com/erayguner/fin-ai-ops)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Contributor Covenant](https://img.shields.io/badge/Contributor%20Covenant-2.1-4baaaa.svg)](CODE_OF_CONDUCT.md)

> Autonomous cost governance for AWS and GCP using **provider-native agent
> frameworks** and **native MCP servers**. No API keys — all authentication
> is via IAM roles (AWS) and Workload Identity Federation (GCP).

## Status

`v0.3.0` — **production-ready core, public-template-ready.** The
agent-governance plane (ADR-008) and the 2026 platform-alignment work
(PLATFORM_GAP_ANALYSIS.md) are landed. New providers, managed-runtime
graduation, and per-agent identity migration are tracked in
[CHANGELOG.md](./CHANGELOG.md) under `[Unreleased]`.

## Table of contents

- [Why](#why) — what problem this solves
- [Architecture](#architecture) — the 2026 native-agent stack
- [Quick start](#quick-start) — `git clone` to first audit
- [Quality gates](#quality-gates) — what runs in CI
- [Documentation](#documentation) — index of docs, ADRs, runbooks
- [Community](#community) — how to get help, contribute, raise concerns
- [Roadmap](#roadmap) — what's next
- [Security](#security) — vulnerability reporting
- [License](#license)

## Why

Cloud spend is governed by humans who sit between budget owners, the
teams that provision resources, and the security reviewers who care
about credential abuse. That role doesn't scale. An agent can if and
only if its reasoning, tool calls, and side-effects are *traceable*,
*reviewable*, and *overridable* — none of which is true of bare LLM
agents. This project is the smallest viable governance plane that
makes a FinOps agent safe to operate on production cloud spend.

Aligned with the
[Agent Governance Framework](./docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md)
and external standards: Google SAIF, NIST AI RMF 1.0, EU AI Act
high-risk obligations, ISO/IEC 42001, SOC 2 CC-series, UK NCSC.

## Architecture

Each cloud provider uses its **own native agent solution** (2026 stack):

| Provider | Agent Framework | Native Capabilities | Auth Model |
|----------|----------------|---------------------|------------|
| **AWS** | Amazon Bedrock Agents + AgentCore | Action Groups (OpenAPI), Knowledge Base (OpenSearch Serverless + Titan v2), Guardrails, Production Alias | IAM roles only |
| **GCP** | Google ADK + Vertex AI Agent Builder | Cloud Run v2 runtime, Vertex AI Vector Search, Discovery Engine chat engine, Gemini 2.5 | WIF / ADC only |

Aligned with **UK NCSC Secure by Design** principles. **Terraform >= 1.14** with AWS provider >= 6.0 and Google provider >= 6.0.

```
fin-ai-ops/
├── core/                           # Shared core engine
│   ├── models.py                   # Pydantic data models (schema-versioned)
│   ├── config.py                   # Layered config: defaults → YAML → env vars
│   ├── event_store.py              # Event persistence (in-memory / SQLite)
│   ├── notifications.py            # Alert dispatchers (webhook/Slack/PagerDuty)
│   ├── pricing.py                  # Pluggable pricing service
│   ├── thresholds.py               # Dynamic cost threshold calculation
│   ├── validation.py               # Input validation & SSRF protection
│   ├── circuit_breaker.py          # Circuit breaker for fault tolerance
│   ├── retry.py                    # Retry with exponential backoff
│   ├── lifecycle.py                # Agent state machine (EDA pattern)
│   ├── alerts.py                   # Contextualised alert generation
│   ├── audit.py                    # Immutable audit trail (SHA-256 chain)
│   ├── policies.py                 # Cost governance policy engine
│   ├── tagging.py                  # Tagging governance domain (policies, taggability, audits)
│   └── tool_governor.py            # MCP tool-call governor (policy + budget + approval + audit)
├── providers/
│   ├── base.py                     # Provider interface (ABC)
│   ├── aws/
│   │   ├── agents/finops_agent.py  # Bedrock Agent + action groups
│   │   ├── mcp_integration/        # awslabs/mcp server configs
│   │   ├── cost_analyzer.py        # AWS cost estimation
│   │   ├── resources.py            # AWS resource catalogue
│   │   ├── listener.py             # CloudTrail event listener
│   │   └── terraform/              # AWS IaC (Bedrock Agent + KB + Guardrails + Alias)
│   └── gcp/
│       ├── agents/finops_agent.py  # Google ADK Agent + tools
│       ├── mcp_integration/        # google/mcp server configs
│       ├── cost_analyzer.py        # GCP cost estimation
│       ├── resources.py            # GCP resource catalogue
│       ├── listener.py             # Cloud Audit Log listener
│       └── terraform/              # GCP IaC (Cloud Run ADK runtime + Vector Search + Agent Builder)
├── agents/                         # Shared agent orchestration
│   ├── cost_monitor.py             # Cross-provider cost monitoring
│   ├── alert_agent.py              # Alert lifecycle + event persistence
│   ├── report_agent.py             # Periodic cost reporting
│   ├── health_agent.py             # Self-monitoring health probes
│   ├── reconciliation_agent.py     # Data drift detection & auto-repair
│   └── tagging_health_agent.py     # Weekly tagging compliance governance
├── mcp_server/server.py            # Hub MCP server
├── policies/                       # Cost governance policies (JSON)
│   └── tagging/                    # Per-provider tagging/labelling policies
├── scripts/
│   ├── preflight_check.py          # Pre-flight validation (--all/--aws/--gcp)
│   ├── validate_policies.py        # CostPolicy schema validator
│   ├── drift_check.py              # Terraform-to-policy alignment
│   └── repo_health.py              # Full repo health check
├── docs/                           # Getting started & troubleshooting guides
├── tests/                          # 539 tests
├── SECURITY.md                     # Security policy & vulnerability reporting
└── audit_store/                    # Append-only audit logs (JSONL, runtime)
```

## Pluggable Abstractions

The hub is built on four pluggable interfaces for long-term extensibility:

| Abstraction | Backends Included | Add Your Own |
|---|---|---|
| **Event Store** | In-memory, SQLite | Implement `BaseEventStore` (e.g. PostgreSQL, DynamoDB) |
| **Notifications** | Webhook, Slack, PagerDuty, Log | Implement `BaseNotificationDispatcher` |
| **Pricing** | Local pricing tables, Cached wrapper | Implement `BasePricingService` (e.g. AWS Pricing API) |
| **Configuration** | Defaults → YAML → Env vars | Set `FINOPS_*` env vars or edit `hub_config.yaml` |

All configuration is externalised — thresholds, required tags, escalation timeframes, notification channels, and pricing regions are configurable without code changes.

## Native Agent Frameworks

### AWS — Amazon Bedrock Agents (AgentCore-aligned)

The AWS Terraform module deploys a full 2026 Bedrock agent stack:

- **Agent + Production Alias** (`aws_bedrockagent_agent` + `aws_bedrockagent_agent_alias`) — blue/green promotion target.
- **Action Groups** — `cost_tools` and `tagging_tools`, Lambda-backed, OpenAPI 3.0 schemas.
- **Knowledge Base** — OpenSearch Serverless VECTORSEARCH collection + S3 corpus, Titan v2 embeddings.
- **Guardrails** — PII blocking (AWS keys, SSN, CC), content filters, prompt-attack detection.
- Foundation model: Claude Sonnet 4.5 on Bedrock. Auth via IAM roles — **zero API keys**.

**AWS MCP Servers** ([awslabs/mcp](https://github.com/awslabs/mcp)):
- `awslabs.cost-explorer-mcp-server` — Cost and usage queries
- `awslabs.cloudwatch-mcp-server` — Metrics and alarms
- `awslabs.cloudformation-mcp-server` — Infrastructure queries

### GCP — Google ADK + Vertex AI Agent Builder

The GCP Terraform module deploys a 2026 ADK-native runtime:

- **Cloud Run v2 service** — ADK agent server with Gemini 2.5 Pro, stateful sessions, WIF-bound service account.
- **Vertex AI Vector Search** — tree-AH index + public endpoint for RAG retrieval.
- **Vertex AI Agent Builder** — Discovery Engine data store + chat engine for conversational grounding.
- **Artifact Registry** — immutable Docker repository for the ADK container image.
- **Secret Manager** — ADK runtime config (non-credential; WIF covers identity).

Auth via WIF — **zero service account keys**.

**Google MCP Servers** ([docs.cloud.google.com/mcp/overview](https://docs.cloud.google.com/mcp/overview)):
- BigQuery MCP (`https://bigquery.googleapis.com/mcp`) — Billing data queries via streamable HTTP, OAuth 2.0 via ADC/WIF

## Zero API Keys Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  NO API KEYS ANYWHERE                    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  AWS:  IAM Roles → STS AssumeRole → Bedrock Agent      │
│        GitHub OIDC → IAM Role → CI/CD                   │
│        EC2 Instance Profile → boto3                     │
│                                                         │
│  GCP:  WIF → Service Account → ADK Agent                │
│        GitHub OIDC → WIF Pool → Service Account         │
│        ADC → google-cloud-* libraries                   │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

## Human-Readable Alerts

Every alert is **self-contained** — no further investigation needed:

```
========================================================================
[CRITICAL] AWS Cost Alert: ec2:instance in 123456789012 at $3,000/month
========================================================================

WHAT HAPPENED:
  jane.doe@example.com created an ec2:instance resource (web-server-01)
  [i-0123456789abcdef0] in eu-west-2 with an estimated monthly cost of
  $3,000.00. This exceeds the critical threshold of $2,000.00/month.

WHO IS ACCOUNTABLE:
  Creator:      jane.doe@example.com
  Team:         data-platform
  Cost Centre:  CC-001

RECOMMENDED ACTIONS:
  1. IMMEDIATE: Add missing required tags: environment, owner
  2. REVIEW: Validate resource sizing — consider right-sizing
  3. OPTIMISE: Evaluate reserved instances (save up to 60%)
  4. APPROVE: Obtain written cost approval within 24 hours

ACCOUNTABILITY:
  jane.doe@example.com must review within 24 hours.
  Escalation: Team Lead (24h) → Engineering Manager (48h)
========================================================================
```

Alerts are dispatched to Slack, PagerDuty, webhooks, or logs — configurable per environment.

## Quick Start

```bash
# Install with provider dependencies
pip install -e ".[gcp]"     # GCP + Google ADK
pip install -e ".[aws]"     # AWS + boto3
pip install -e ".[dev]"     # Development tools

# Run tests (539 tests, ~11s)
pytest

# Run pre-flight checks
python scripts/preflight_check.py --local

# Deploy infrastructure
cd providers/aws/terraform && terraform init && terraform plan
cd providers/gcp/terraform && terraform init && terraform plan
```

### Configuration

All settings can be customised via environment variables:

```bash
export FINOPS_SLACK_WEBHOOK="https://hooks.slack.com/services/..."
export FINOPS_PAGERDUTY_KEY="routing-key-123"
export FINOPS_EVENT_STORE="sqlite"           # or "memory" (default)
export FINOPS_REQUIRED_TAGS="team,env,owner" # override default tags
export FINOPS_ANOMALY_MULTIPLIER="3.0"       # anomaly sensitivity
export FINOPS_POLL_INTERVAL="300"            # seconds between polls
```

Or create a `hub_config.yaml` file for full control.

### Claude Code MCP Integration

```json
{
  "mcpServers": {
    "finops-hub": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/finops-automation-hub"
    },
    "aws-cost-explorer": {
      "command": "uvx",
      "args": ["awslabs.cost-explorer-mcp-server@latest"],
      "env": { "AWS_REGION": "eu-west-2" }
    }
  }
}
```

## Terraform Requirements

- **Terraform** >= 1.14.0
- **AWS Provider** >= 6.0
- **Google Provider** >= 6.0

## UK NCSC Secure by Design Alignment

| NCSC Principle | Implementation |
|---|---|
| Secure by default | WIF/IAM roles, mandatory tagging, encryption, no public access |
| Defence in depth | Multi-tier thresholds, policy layers, budget alerts, anomaly detection |
| Least privilege | Per-agent IAM roles, minimal scopes, no long-lived credentials |
| Audit & accountability | Chained SHA-256 checksums, immutable logs, identity attribution |
| Resilience | Multi-region trails, versioned storage, dead-letter topics |

## Public API Security

All external inputs pass through `core/validation.py` before reaching core logic:

| Protection | Scope |
|---|---|
| **Input validation** | String length, format, type coercion on all MCP tool parameters |
| **SSRF protection** | Webhook URLs blocked for private IPs, metadata endpoints, non-HTTPS |
| **Path traversal prevention** | Audit and policy directories reject `..` and enforce base-dir containment |
| **Error sanitisation** | No file paths, line numbers, or class names in public error messages |
| **Size limits** | Max 50 tags, 10K query results, 5-level nesting depth, $1B cost cap |
| **Explicit exports** | All modules define `__all__` for clear public API boundaries |

See **[SECURITY.md](SECURITY.md)** for the full security model and vulnerability reporting.

## Self-Healing & Operational Excellence

The hub includes built-in resilience patterns for unattended operation:

| Capability | Component | How It Works |
|---|---|---|
| **Circuit Breaker** | `core/circuit_breaker.py` | Three-state (CLOSED/OPEN/HALF_OPEN) breaker prevents cascading failures to external services |
| **Retry with Backoff** | `core/retry.py` | Exponential backoff with configurable attempts, integrates with circuit breaker |
| **Health Probes** | `agents/health_agent.py` | Kubernetes-style liveness/readiness/deep checks for all components |
| **Reconciliation** | `agents/reconciliation_agent.py` | Detects orphaned events, stale alerts, audit tampering, and state inconsistencies |
| **Dead-Letter Queue** | `core/notifications.py` | Failed notification dispatches are captured and retried on demand |
| **Crash Resilience** | `core/audit.py`, `core/event_store.py` | Audit falls back to stderr on disk-full; SQLite uses WAL mode for crash safety |
| **Corrupt File Handling** | `core/policies.py` | Skips corrupt policy files and logs errors instead of crashing |
| **Replay Reentrancy Guard** | `mcp_server/server.py` | Flag prevents concurrent/recursive event replays; dedup set skips already-replayed events |
| **Bounded Dead-Letter Queue** | `core/notifications.py` | Queue capped at 1 000 entries (FIFO eviction); per-entry retry limit of 3 before expiry |
| **Audit Query Cap** | `core/audit.py` | `get_entries()` silently caps `limit` at 10 000 to prevent OOM on large audit trails |

Aligned with **event-driven architecture (EDA) best practices** for AI agent systems:

| EDA Pattern | Implementation |
|---|---|
| **Correlation IDs** | End-to-end trace ID flows from event → alert → audit → dispatch |
| **Causation IDs** | Each audit entry records which event directly caused it |
| **Event sourcing** | Immutable append-only event store + audit trail enables full replay |
| **Idempotent processing** | `INSERT OR IGNORE` in SQLite, dedup index in InMemory store |
| **Event replay** | `finops_replay_events` re-processes orphaned events after crash |
| **Agent lifecycle** | State machine (CREATED → INITIALIZED → ACTIVE → DEGRADED → ERROR → TERMINATED) |
| **Backpressure** | Bounded event history (50K max) prevents unbounded memory growth |

Five MCP tools expose these capabilities: `finops_health_check`, `finops_reconcile`, `finops_retry_failed_notifications`, `finops_health_history`, `finops_replay_events`.

## Default Policies

The hub ships with 16 cost policies (`policies/*.json`) and 4 tagging policies (`policies/tagging/*.json`):

**Cost policies** — high-cost approval gates, GPU governance, dev-env caps, database governance, AI/ML training caps, K8s node pools, storage lifecycle, spot/preemptible mandate, carbon-aware placement, commitment coverage, anomaly SLAs, multi-account budgets, unit economics, auto-shutdown schedules, data transfer, and mandatory tagging.

**Tagging policies (2026 FinOps Foundation)** — per-provider required tags (`team`, `cost-centre`/`cost_centre`, `environment`, `owner`, `application`, `managed-by`, `data-classification`), with stricter `critical`-severity policies for AI/ML workloads (Bedrock, SageMaker, Vertex AI, Discovery Engine) requiring `model-family`, `workload-class`, and `ai-workload-phase` tags.

## Tagging Governance

The `TaggingHealthAgent` (`agents/tagging_health_agent.py`) runs weekly compliance scans across both providers:

- Resolves per-provider `TaggingPolicy` for each resource (or skips if exempt/non-taggable).
- Classifies resources as `compliant`, `non_compliant`, `non_taggable`, or `exempt`.
- Distinguishes provider-native non-taggable types (e.g. `route53:hostedzone-record`, `iam.serviceaccounts`) using the built-in taggability registry (~100 AWS + ~70 GCP resource types covered).
- Generates a weekly `TaggingHealthReport` with compliance trend, unattributed monthly spend, remediation priorities ranked by cost, and actionable recommendations.

## Tool-Call Governance

`core/tool_governor.py` is a separate domain that governs an LLM agent's MCP tool usage (distinct from the cloud-resource governor). It implements the structured-sandboxing pattern:

- **`ToolRequest` / `ToolResult`** — LangGraph-style structured requests; the agent never calls MCP directly.
- **`governed_call()`** — the single enforcement point. Every policy, budget, argument-gate and approval decision flows through here.
- **`ToolCategory`** — explicit classification (`discovery` / `connection` / `execution` / `other`) so separation-of-duties rules are declarative, not substring-matched.
- **`GovernancePolicy`** — allow/deny lists, category allow-lists, approval-required tools, fail-closed default.
- **`BudgetTracker`** — total-call / per-tool / runtime / parallelism caps plus optional connection↔execution separation.
- **Structured artifacts** — `policy_decision`, `tool_call_log`, `approval_request`, `approval_log`, `budget_stats`, `result_summary`; rendered by `AuditReportGenerator` into a machine-readable report with denial breakdowns and recommendations.

The MCP server wires every `handle_tool_call()` through `governed_call()`. **Fail-closed by default** as of ADR-008 (`hub.governor.enabled=true` is the default). The kill-switch (`AgentSupervisor`), out-of-band approval gateway (`ApprovalStore`), behavioural anomaly detection (`AgentObserver`), and audited memory adapter (`MemoryAdapter`) all hang off this enforcement point.

## Quick start

```bash
# Prerequisites: Python 3.12+, Terraform 1.14+ (only if deploying infra)
git clone https://github.com/erayguner/fin-ai-ops.git
cd fin-ai-ops
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install

# Quick smoke test — pure-Python, no cloud creds.
pytest -q
python scripts/governor_dry_run.py
python scripts/ci/validate_boundary_contracts.py
```

The MCP server starts locally with:

```bash
python -m mcp_server.server  # speaks JSON-RPC on stdio
```

For cloud deployment, see [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

## Quality gates

CI runs the following on every PR; reproduce locally with the same
commands:

- `ruff check . && ruff format --check .` — lint + format.
- `mypy core providers agents mcp_server` — type check.
- `pytest -q` — 660+ tests.
- `python scripts/governor_dry_run.py` — governor invariants.
- `python scripts/ci/validate_boundary_contracts.py` — agent boundary contracts.
- `python scripts/validate_policies.py --strict` — JSON cost policies.
- `python scripts/drift_check.py` — Terraform / policy alignment.
- `pytest tests/agent_eval -q` — six-dimension regression eval (`tool_trajectory`, `response_match`, `response_quality`, `tool_use_quality`, `hallucinations`, `safety`).
- OSSF Scorecard, gitleaks, CycloneDX/SPDX SBOM, zizmor (workflow lint).

All GitHub Actions are pinned by commit SHA, not floating tags.

## Documentation

| Where | What |
|---|---|
| [docs/GETTING_STARTED.md](./docs/GETTING_STARTED.md) | Zero-to-full deployment walkthrough. |
| [docs/TROUBLESHOOTING.md](./docs/TROUBLESHOOTING.md) | Common issues and resolutions. |
| [docs/adr/](./docs/adr/) | Architecture decision records (8 ADRs). |
| [docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md](./docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md) | The canonical governance framework these agents follow. |
| [docs/governance/PLATFORM_GAP_ANALYSIS.md](./docs/governance/PLATFORM_GAP_ANALYSIS.md) | 2026 Gemini Enterprise + Bedrock AgentCore vs this project. |
| [docs/governance/boundary_contracts/](./docs/governance/boundary_contracts/) | Per-agent boundary contracts (in-scope tools, out-of-scope systems, foundation-model card, approver pool, maturity level). |
| [docs/runbooks/](./docs/runbooks/) | Incident-class runbooks (kill-switch, audit chain break, guardrail storm, RtbF memory deletion, …). |

## Community

| | |
|---|---|
| Code of Conduct | [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) — Contributor Covenant 2.1 |
| How to contribute | [CONTRIBUTING.md](./CONTRIBUTING.md) |
| Governance & decision-making | [GOVERNANCE.md](./GOVERNANCE.md) |
| Maintainer roster | [MAINTAINERS.md](./MAINTAINERS.md) |
| Getting help | [SUPPORT.md](./SUPPORT.md) |
| Release notes | [CHANGELOG.md](./CHANGELOG.md) |
| Sponsorship | [.github/FUNDING.yml](./.github/FUNDING.yml) |

**Where to ask:**

- Bug or unexpected behaviour → [open an issue](https://github.com/erayguner/fin-ai-ops/issues/new?template=bug_report.yml).
- Feature request → [open a feature issue](https://github.com/erayguner/fin-ai-ops/issues/new?template=feature_request.yml).
- Documentation problem → [open a docs issue](https://github.com/erayguner/fin-ai-ops/issues/new?template=documentation.yml).
- Question / discussion → [GitHub Discussions](https://github.com/erayguner/fin-ai-ops/discussions) (enable in repo settings).
- Security vulnerability → [Private Security Advisory](https://github.com/erayguner/fin-ai-ops/security/advisories/new). **Never as a public issue.**

We aim for **48-hour first response** on security reports and best-
effort triage within a week for everything else. opensource.guide is
the reference for how the project tries to operate.

## Roadmap

Live status in [CHANGELOG.md](./CHANGELOG.md) `[Unreleased]`. Major
deferred items, tracked in [docs/governance/PLATFORM_GAP_ANALYSIS.md](./docs/governance/PLATFORM_GAP_ANALYSIS.md):

- L2 graduation to **Bedrock AgentCore Runtime** + **Vertex AI Agent Engine Runtime** when provider Terraform exposes the resources.
- Per-agent identity (Vertex Agent Identity / Bedrock AgentCore Identity) once GA in the Terraform AWS provider.
- Automated Reasoning Policy attachment when the AWS provider exposes the resource type.
- A2A protocol exposure when a second agent enters scope.

## Security

See [SECURITY.md](./SECURITY.md) for the full security model, supported
versions, and the vulnerability-disclosure process. Reports go through
[GitHub Security Advisories](https://github.com/erayguner/fin-ai-ops/security/advisories/new) — please don't file vulnerabilities as
public issues.

## License

[MIT](./LICENSE). By contributing you agree your contributions are
licensed under the same terms — see [CONTRIBUTING.md](./CONTRIBUTING.md).
