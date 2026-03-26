# FinOps Automation Hub

Autonomous cost governance for AWS and GCP using **provider-native agent frameworks** and **native MCP servers**. No API keys — all authentication is via IAM roles (AWS) and Workload Identity Federation (GCP).

## Architecture

Each cloud provider uses its **own native agent solution**:

| Provider | Agent Framework | MCP Servers | Auth Model |
|----------|----------------|-------------|------------|
| **AWS** | Amazon Bedrock Agents | [awslabs/mcp](https://github.com/awslabs/mcp) (Cost Explorer, CloudWatch, CloudFormation) | IAM roles only |
| **GCP** | Google ADK (Agent Development Kit) | [google/mcp](https://github.com/google/mcp) (BigQuery, Resource Manager) | WIF / ADC only |

Aligned with **UK NCSC Secure by Design** principles. **Terraform >= 1.14** with latest provider versions.

```
finops-automation-hub/
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
│   └── policies.py                 # Cost governance policy engine
├── providers/
│   ├── base.py                     # Provider interface (ABC)
│   ├── aws/
│   │   ├── agents/
│   │   │   └── finops_agent.py     # Bedrock Agent + action groups
│   │   ├── mcp_integration/
│   │   │   └── aws_mcp_config.py   # awslabs/mcp server configs
│   │   ├── cost_analyzer.py        # AWS cost estimation
│   │   ├── resources.py            # AWS resource catalogue (14 types)
│   │   ├── listener.py             # CloudTrail event listener
│   │   └── terraform/              # AWS IaC (Bedrock Agent, CloudTrail, EventBridge)
│   └── gcp/
│       ├── agents/
│       │   └── finops_agent.py     # Google ADK Agent + tools
│       ├── mcp_integration/
│       │   └── google_mcp_config.py # google/mcp server configs
│       ├── cost_analyzer.py        # GCP cost estimation
│       ├── resources.py            # GCP resource catalogue (13 types)
│       ├── listener.py             # Cloud Audit Log listener
│       └── terraform/              # GCP IaC (Vertex AI, Pub/Sub, WIF)
├── agents/                         # Shared agent orchestration
│   ├── cost_monitor.py             # Cross-provider cost monitoring
│   ├── alert_agent.py              # Alert lifecycle + event persistence
│   ├── report_agent.py             # Periodic cost reporting
│   ├── health_agent.py             # Self-monitoring health probes
│   └── reconciliation_agent.py     # Data drift detection & auto-repair
├── mcp_server/
│   └── server.py                   # Hub MCP server (20 tools)
├── policies/                       # Default governance policies (JSON)
├── scripts/
│   └── preflight_check.py          # Pre-flight validation (--all/--aws/--gcp)
├── docs/                           # Getting started & troubleshooting guides
├── tests/                          # 367 tests
├── SECURITY.md                     # Security policy & vulnerability reporting
└── audit_store/                    # Append-only audit logs (JSONL)
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

### AWS — Amazon Bedrock Agents

The AWS module uses [Amazon Bedrock Agents](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html) with action groups:

- **CostAnalysis** — `analyse_cost_and_usage()`, `detect_cost_anomalies()`
- **Compliance** — `check_tag_compliance()`
- **Optimisation** — `get_savings_recommendations()`, `get_budget_alerts()`

Deployed via Terraform (`aws_bedrockagent_agent`). Uses Claude on Bedrock as the foundation model. Auth via IAM roles — **zero API keys**.

**AWS MCP Servers** ([awslabs/mcp](https://github.com/awslabs/mcp)):
- `awslabs.cost-explorer-mcp-server` — Cost and usage queries
- `awslabs.cloudwatch-mcp-server` — Metrics and alarms
- `awslabs.cloudformation-mcp-server` — Infrastructure queries

### GCP — Google Agent Development Kit (ADK)

The GCP module uses [Google ADK](https://docs.cloud.google.com/agent-builder/agent-development-kit/overview) with Gemini:

- `analyse_billing_costs()` — BigQuery billing export queries
- `detect_costly_resources()` — Cloud Asset Inventory scanning
- `check_label_compliance()` — Label policy enforcement
- `get_budget_status()` — Cloud Billing Budget API
- `recommend_cost_optimisations()` — GCP Recommender API

Deployed to Vertex AI Agent Engine. Auth via WIF — **zero service account keys**.

**Google MCP Servers** ([google/mcp](https://github.com/google/mcp)):
- BigQuery MCP — Billing data queries and AI forecasting
- Cloud Resource Manager MCP — Project and resource hierarchy

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

# Run tests (367 tests, < 1 second)
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

1. **Mandatory Resource Tagging** — team, cost-centre, environment, owner
2. **High-Cost Resource Approval** — $2,000/month approval, $5,000/month hard limit
3. **GPU Instance Governance** — Extra scrutiny for GPU workloads

## Documentation

- **[Getting Started](docs/GETTING_STARTED.md)** — Zero-to-full deployment guide
- **[Troubleshooting](docs/TROUBLESHOOTING.md)** — Common issues and solutions
- **[Security Policy](SECURITY.md)** — Security model and vulnerability reporting
