# Getting Started — FinOps Automation Hub

This guide takes you from zero to a fully operational FinOps Automation Hub. It covers local development setup, configuration, cloud infrastructure deployment, agent configuration, and verification.

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Local Setup](#2-local-setup)
3. [Configuration](#3-configuration)
4. [Run Pre-flight Checks](#4-run-pre-flight-checks)
5. [Deploy AWS Infrastructure](#5-deploy-aws-infrastructure)
6. [Deploy GCP Infrastructure](#6-deploy-gcp-infrastructure)
7. [Configure MCP Servers](#7-configure-mcp-servers)
8. [Verify Deployment](#8-verify-deployment)
9. [First Cost Evaluation](#9-first-cost-evaluation)
10. [Ongoing Operations](#10-ongoing-operations)

---

## 1. Prerequisites

### Required Software

| Tool | Minimum Version | Purpose | Install |
|------|----------------|---------|---------|
| Python | 3.12+ | Hub runtime | [python.org](https://www.python.org/downloads/) |
| Terraform | 1.14+ | Infrastructure as Code | [terraform.io](https://developer.hashicorp.com/terraform/install) |
| AWS CLI | 2.x | AWS credential management | `pip install awscli` |
| gcloud CLI | Latest | GCP credential management | [cloud.google.com/sdk](https://cloud.google.com/sdk/docs/install) |
| uv | Latest | MCP server runner | `pip install uv` |
| Git | 2.x | Version control | System package manager |

### Required Accounts & Permissions

**AWS** (for AWS module):
- AWS account with Cost Explorer enabled
- IAM permissions: `ce:*`, `budgets:*`, `tag:*`, `cloudwatch:*`, `bedrock:*`, `iam:*`, `s3:*`, `sns:*`, `cloudtrail:*`, `events:*`
- A KMS key ARN for encryption at rest
- Bedrock model access enabled for your chosen foundation model

**GCP** (for GCP module):
- GCP project with billing enabled
- IAM roles: `roles/logging.admin`, `roles/pubsub.admin`, `roles/bigquery.admin`, `roles/iam.admin`, `roles/billing.viewer`, `roles/serviceusage.serviceUsageAdmin`
- Billing export to BigQuery already configured (or you'll set it up)

> **Note**: You can deploy either AWS, GCP, or both. Each provider module is independent.

---

## 2. Local Setup

### Clone and Install

```bash
# Clone the repository
git clone https://github.com/erayguner/fin-ai-ops.git
cd fin-ai-ops

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# Install core + development dependencies
pip install -e ".[dev]"

# Install provider-specific dependencies (pick one or both)
pip install -e ".[aws]"          # AWS: boto3
pip install -e ".[gcp]"          # GCP: google-adk + google-cloud-*
pip install -e ".[all-providers]" # Both providers
```

### Configure Authentication

**AWS — IAM Role (no API keys)**:
```bash
# Option 1: SSO (recommended)
aws sso login --profile your-profile
export AWS_PROFILE=your-profile

# Option 2: Instance profile (on EC2/ECS/Lambda — automatic)
# No configuration needed

# Option 3: OIDC federation (CI/CD — configured via Terraform)
# See Section 5

# Verify
aws sts get-caller-identity
```

**GCP — Application Default Credentials (no service account keys)**:
```bash
# Option 1: User credentials (development)
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID

# Option 2: Workload Identity (production on GKE/Cloud Run — automatic)
# No configuration needed

# Option 3: WIF for GitHub Actions (CI/CD — configured via Terraform)
# See Section 6

# Verify
gcloud auth application-default print-access-token > /dev/null && echo "GCP auth OK"
```

> **Security**: Never use `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` or service account JSON keys. The hub enforces keyless authentication everywhere.

---

## 3. Configuration

The hub uses a **layered configuration** system. Settings are resolved in order of precedence:

1. **Built-in defaults** — sensible values for all settings
2. **YAML file** — `hub_config.yaml` in the project root (optional)
3. **Environment variables** — `FINOPS_*` prefixed vars (highest precedence)

### Environment Variables

| Variable | Config Key | Default | Description |
|----------|-----------|---------|-------------|
| `FINOPS_POLL_INTERVAL` | `monitoring.poll_interval_seconds` | `900` | Seconds between cost monitoring polls |
| `FINOPS_ANOMALY_MULTIPLIER` | `thresholds.anomaly_multiplier` | `2.0` | Multiplier for anomaly detection sensitivity |
| `FINOPS_REQUIRED_TAGS` | `tags.required` | `team,cost-centre,environment,owner` | Comma-separated required resource tags |
| `FINOPS_AUDIT_DIR` | `hub.audit_dir` | `audit_store` | Directory for audit log files |
| `FINOPS_EVENT_STORE` | `hub.event_store` | `memory` | Event store backend: `memory` or `sqlite` |
| `FINOPS_SLACK_WEBHOOK` | — | — | Slack webhook URL for notifications |
| `FINOPS_PAGERDUTY_KEY` | — | — | PagerDuty routing key for alerts |

### YAML Configuration

Create `hub_config.yaml` for full control over all settings:

```yaml
hub:
  version: "0.3.0"
  audit_dir: audit_store
  event_store: sqlite

monitoring:
  poll_interval_seconds: 300

thresholds:
  anomaly_multiplier: 3.0

tags:
  required:
    - team
    - cost-centre
    - environment
    - owner

escalation:
  emergency: "1 hour"
  critical: "24 hours"
  warning: "7 days"
  info: "30 days"
```

### Event Store Backends

The hub persists resource creation events via a pluggable event store:

| Backend | Use Case | Configuration |
|---------|----------|---------------|
| **In-memory** (default) | Development, testing | `FINOPS_EVENT_STORE=memory` |
| **SQLite** | Single-node production | `FINOPS_EVENT_STORE=sqlite` |
| **Custom** | Scale-out (DynamoDB, PostgreSQL, etc.) | Implement `BaseEventStore` |

### Notification Channels

Alerts can be dispatched to multiple channels simultaneously:

| Channel | Configuration | Notes |
|---------|---------------|-------|
| **Log** (default) | Always active | Writes to Python logger |
| **Slack** | `FINOPS_SLACK_WEBHOOK=https://hooks.slack.com/...` | Color-coded by severity |
| **PagerDuty** | `FINOPS_PAGERDUTY_KEY=routing-key-123` | Severity-mapped events |
| **Webhook** | `FINOPS_WEBHOOK_URL=https://your-endpoint/hook` | Generic HTTP POST |

Multiple channels can be active at once. If none are configured, alerts fall back to the log dispatcher.

### Pricing Service

Cost estimation uses a pluggable pricing service:

| Backend | Description |
|---------|-------------|
| **LocalPricingService** (default) | Offline pricing tables for 26+ resource types |
| **CachedPricingService** | TTL-based cache wrapper around any pricing backend |
| **Custom** | Implement `BasePricingService` (e.g., AWS Pricing API, GCP Cloud Billing API) |

---

## 4. Run Pre-flight Checks

Before deploying infrastructure, run the pre-flight checks to validate everything is ready:

```bash
# Check everything (local tools, AWS, GCP)
python scripts/preflight_check.py --all

# Check only local tools (no cloud credentials needed)
python scripts/preflight_check.py --local

# Check only AWS readiness
python scripts/preflight_check.py --aws

# Check only GCP readiness
python scripts/preflight_check.py --gcp

# Check Terraform readiness
python scripts/preflight_check.py --terraform
```

The pre-flight script validates:
- Python version and required packages
- Terraform version (>= 1.14)
- AWS CLI version and credentials
- GCP CLI version and credentials
- Required IAM permissions
- KMS key accessibility (AWS)
- API enablement (GCP)
- MCP server tool availability
- Test suite passes (539 tests)

Fix any failures before proceeding. See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues.

---

## 5. Deploy AWS Infrastructure

### 5.1 Configure Variables

```bash
cd providers/aws/terraform

# Create your variable file
cat > terraform.tfvars <<'EOF'
name_prefix                 = "finops"
kms_key_arn                 = "arn:aws:kms:eu-west-2:123456789012:key/your-key-id"
alert_email_addresses       = ["finops-team@example.com"]
monthly_budget_usd          = 10000
# Claude Sonnet 4 / 4.5 are cross-region inference-profile only. Prefix with
# eu./us./etc. to match your deployment region (see AWS Bedrock docs).
bedrock_model_id            = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0"
bedrock_embedding_model_id  = "amazon.titan-embed-text-v2:0"
enable_knowledge_base       = true
enable_guardrails           = true
anomaly_threshold_usd       = 100

# Optional: GitHub Actions OIDC
enable_github_oidc = true
github_repository  = "your-org/your-repo"

tags = {
  ManagedBy   = "terraform"
  Project     = "finops-automation-hub"
  Environment = "production"
}
EOF
```

### 5.2 Deploy

```bash
terraform init
terraform plan -out=tfplan

# Review the plan carefully, then apply
terraform apply tfplan
```

### 5.3 Note the Outputs

```bash
terraform output
# Save these values:
# - bedrock_agent_id                  # agent identifier
# - bedrock_agent_alias_id            # invoke this alias from applications
# - bedrock_agent_alias_arn
# - action_group_cost_tools_lambda    # Lambda backing cost_tools action group
# - action_group_tagging_tools_lambda # Lambda backing tagging_tools action group
# - knowledge_base_id
# - knowledge_base_corpus_bucket      # drop FinOps runbooks here for RAG
# - opensearch_collection_endpoint
# - guardrail_id + guardrail_version
# - sns_topic_arn
# - anomaly_monitor_arn
```

### What Gets Created

| Resource | Purpose |
|----------|---------|
| CloudTrail | Captures 2026 resource creation events (incl. SageMaker, Bedrock, EMR Serverless, OpenSearch) |
| S3 Bucket (trail) | Stores CloudTrail logs (encrypted, versioned) |
| S3 Bucket (KB corpus) | Runbook / policy / escalation-ownership documents for RAG |
| EventBridge Rule | Routes creation events to SNS |
| SNS Topic | Alert distribution (encrypted) |
| Bedrock Agent + Alias | FinOps governance AI agent (blue/green production alias) |
| Bedrock Action Groups | `cost_tools` + `tagging_tools` Lambda-backed OpenAPI tools |
| Bedrock Knowledge Base | OpenSearch Serverless vector store (Titan v2 embeddings) |
| Bedrock Guardrail | PII blocking + content filters + prompt-attack detection |
| Lambda Functions | Action-group backends (stub handlers; replace with project code) |
| Cost Anomaly Monitor | Detects unusual spending |
| Budget Alarm | 80%/100% budget notifications |
| OIDC Provider | Keyless GitHub Actions auth (optional) |

---

## 6. Deploy GCP Infrastructure

### 6.1 Configure Variables

```bash
cd providers/gcp/terraform

cat > terraform.tfvars <<'EOF'
project_id          = "your-gcp-project-id"
region              = "europe-west2"
name_prefix         = "finops"
company_name        = "Acme Corp"
billing_account_id  = "012345-6789AB-CDEF01"  # Optional
monthly_budget_usd  = 10000

# ADK agent runtime (2026 Google ADK / Agent Builder stack)
gemini_model             = "gemini-2.5-pro"
embedding_dimensions     = 768
adk_agent_image          = "europe-west2-docker.pkg.dev/YOUR_PROJECT/finops-adk-agent/agent:v1"
enable_cloud_run_runtime = true
enable_vector_search     = true
enable_agent_builder     = true

# Optional: GitHub Actions WIF
enable_wif        = true
github_repository = "your-org/your-repo"

labels = {
  managed-by  = "terraform"
  project     = "finops-automation-hub"
  environment = "production"
}
EOF
```

### 6.2 Deploy

```bash
terraform init
terraform plan -out=tfplan

# Review the plan carefully, then apply
terraform apply tfplan
```

### 6.3 Note the Outputs

```bash
terraform output
# Save these values:
# - pubsub_topic
# - bigquery_dataset
# - finops_hub_service_account
# - adk_agent_service_account            # runs the ADK agent on Cloud Run
# - adk_artifact_registry_repo           # push your ADK container image here
# - adk_artifacts_bucket                 # RAG corpus + session state
# - adk_config_secret                    # Secret Manager config
# - adk_cloud_run_url                    # ADK runtime HTTPS endpoint
# - vector_search_index_id / endpoint_id # Vertex AI Vector Search
# - agent_builder_engine_id              # Discovery Engine chat engine
```

### What Gets Created

| Resource | Purpose |
|----------|---------|
| Pub/Sub Topic + Subscription | Alert distribution |
| Log Sink | Routes 2026 resource creation events to Pub/Sub (Compute, AlloyDB, Cloud Run, Vertex AI, BigQuery) |
| BigQuery Dataset | Billing export storage |
| Service Accounts (2) | `finops-hub` + `adk-agent` (keyless, WIF) |
| Artifact Registry (Docker) | ADK agent container image repository |
| Cloud Run v2 Service | ADK agent runtime (Gemini 2.5 Pro, WIF-bound) |
| GCS Bucket | ADK session state + RAG corpus (versioned, public-access-prevented) |
| Secret Manager Secret | ADK runtime config (non-credential) |
| Vertex AI Vector Search | Tree-AH index + endpoint for RAG retrieval |
| Discovery Engine | Data store + chat engine (Vertex AI Agent Builder) |
| WIF Pool + Provider | Keyless GitHub Actions auth (optional) |
| Budget Alert | 50%/80%/100% budget notifications |
| Required APIs | Logging, Monitoring, Pub/Sub, BigQuery, AI Platform, Discovery Engine, Cloud Run, etc. |

---

## 7. Configure MCP Servers

### 7.1 Hub MCP Server (Custom)

Add to your Claude Code settings (`.claude/settings.json`):

```json
{
  "mcpServers": {
    "finops-hub": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/finops-automation-hub"
    }
  }
}
```

### 7.2 AWS Native MCP Servers

```json
{
  "mcpServers": {
    "aws-cost-explorer": {
      "command": "uvx",
      "args": ["awslabs.cost-explorer-mcp-server@latest"],
      "env": { "AWS_REGION": "eu-west-2" }
    },
    "aws-cloudwatch": {
      "command": "uvx",
      "args": ["awslabs.cloudwatch-mcp-server@latest"],
      "env": { "AWS_REGION": "eu-west-2" }
    },
    "aws-cloudformation": {
      "command": "uvx",
      "args": ["awslabs.cloudformation-mcp-server@latest"],
      "env": { "AWS_REGION": "eu-west-2" }
    }
  }
}
```

### 7.3 GCP Native MCP Servers

The ADK agent calls Google's remote MCP endpoints directly via
`StreamableHTTPConnectionParams`. For direct use from an MCP host, register
the BigQuery MCP server endpoint
(`https://bigquery.googleapis.com/mcp`, per
[docs.cloud.google.com/bigquery/docs/use-bigquery-mcp](https://docs.cloud.google.com/bigquery/docs/use-bigquery-mcp));
transport is HTTP and authentication is OAuth 2.0 via ADC/WIF. Consult your
MCP host's docs for the exact remote-server registration form — there is no
officially published local `npx` server for Google Cloud at this time.

---

## 8. Verify Deployment

### Run Post-deployment Checks

```bash
python scripts/preflight_check.py --verify-deployment
```

### Manual Verification

**AWS:**
```bash
# Check Bedrock agent exists
aws bedrock-agent list-agents --query 'agentSummaries[?agentName==`finops-finops-governance`]'

# Check CloudTrail is logging
aws cloudtrail get-trail-status --name finops-finops-trail

# Check Cost Anomaly Monitor
aws ce get-anomaly-monitors --query 'AnomalyMonitors[0].MonitorName'
```

**GCP:**
```bash
# Check Pub/Sub topic
gcloud pubsub topics describe finops-finops-alerts --project YOUR_PROJECT

# Check Log Sink
gcloud logging sinks describe finops-resource-creation-sink --project YOUR_PROJECT

# Check BigQuery dataset
bq show --project_id YOUR_PROJECT finops_finops_billing
```

**Hub MCP Server:**
```bash
# Test the MCP server responds
echo '{"method": "tools/list", "id": 1}' | python -m mcp_server.server
```

---

## 9. First Cost Evaluation

### Using the MCP Server

```bash
# Start a Python session
python -c "
from mcp_server.server import handle_tool_call
import json

# Check hub status
status = handle_tool_call('finops_hub_status', {})
print(json.dumps(status, indent=2))

# Evaluate a resource
result = handle_tool_call('finops_evaluate_resource', {
    'provider': 'aws',
    'account_id': '123456789012',
    'region': 'eu-west-2',
    'resource_type': 'ec2:instance',
    'resource_id': 'i-example123',
    'estimated_monthly_cost_usd': 3000.0,
    'creator_identity': 'arn:aws:iam::123456789012:user/jane.doe',
    'creator_email': 'jane.doe@example.com',
    'tags': {'team': 'data-platform'},
})
print(result.get('human_readable', json.dumps(result, indent=2)))
"
```

### Using the AWS Bedrock Agent

```bash
aws bedrock-agent-runtime invoke-agent \
  --agent-id YOUR_AGENT_ID \
  --agent-alias-id YOUR_ALIAS_ID \
  --session-id "first-test" \
  --input-text "What are our top 5 cost drivers this month?"
```

### Using the GCP ADK Agent

```python
from providers.gcp.agents.finops_agent import create_gcp_finops_agent

agent = create_gcp_finops_agent()
# Use with ADK runner: adk run or adk web
```

---

## 10. Ongoing Operations

### Daily Checklist

- [ ] Review any new alerts: `finops_list_alerts --status pending`
- [ ] Acknowledge critical alerts within SLA (1h emergency, 24h critical)
- [ ] Verify audit trail integrity: `finops_verify_audit_integrity`
- [ ] Run health check: `finops_health_check`

### Weekly Checklist

- [ ] Generate cost report: `finops_generate_report --period LAST_7_DAYS`
- [ ] Review tag compliance across both providers
- [ ] Check budget utilisation trends
- [ ] Run reconciliation: `finops_reconcile` (detects orphaned events, stale alerts, audit drift)
- [ ] Retry any failed notifications: `finops_retry_failed_notifications`

### Monthly Checklist

- [ ] Review and update cost policies as needed
- [ ] Export audit trail for compliance: `finops_export_audit`
- [ ] Update cost thresholds based on new baselines
- [ ] Review and action optimisation recommendations
- [ ] Review health history: `finops_health_history`

### Self-Healing & Tracing

The hub traces every action end-to-end using **correlation IDs** and **causation IDs**:

- **Correlation ID**: A unique trace ID assigned when a resource creation event is ingested. It flows automatically from event → alert → audit entry → notification dispatch. Use it to trace the full lifecycle of any cost event.
- **Causation ID**: Recorded on each audit entry to link it back to the specific event or action that triggered it. Useful for debugging chains of automated actions.

If the hub crashes or misses events, use `finops_replay_events` to identify and re-process orphaned events (events stored but never evaluated through the alert pipeline).

### Updating Policies

```bash
# Via MCP tool
python -c "
from mcp_server.server import handle_tool_call
handle_tool_call('finops_create_policy', {
    'name': 'New Database Limit',
    'description': 'Limit RDS costs to \$2,000/month per instance',
    'provider': 'aws',
    'resource_types': ['rds:db'],
    'max_monthly_cost_usd': 2000.0,
    'require_tags': ['team', 'cost-centre', 'environment'],
})
"
```

---

## Next Steps

- Read [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for common issues and solutions
- Customise policies in `policies/` to match your organisation's requirements
- Configure notification channels (Slack, PagerDuty, webhooks) via environment variables
- Set up SQLite event store for production: `export FINOPS_EVENT_STORE=sqlite`
- Configure billing export to BigQuery (GCP) if not already done
- Enable Cost Explorer (AWS) if not already active
