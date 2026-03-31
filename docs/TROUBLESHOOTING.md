# Troubleshooting — FinOps Automation Hub

Common issues, error messages, and their solutions. Organised by category.

## Table of Contents

1. [Pre-flight Check Failures](#1-pre-flight-check-failures)
2. [Authentication Issues](#2-authentication-issues)
3. [Terraform Deployment Issues](#3-terraform-deployment-issues)
4. [Agent Issues](#4-agent-issues)
5. [MCP Server Issues](#5-mcp-server-issues)
6. [Alert & Policy Issues](#6-alert--policy-issues)
7. [Audit Trail Issues](#7-audit-trail-issues)
8. [Configuration Issues](#8-configuration-issues)
9. [Event Store Issues](#9-event-store-issues)
10. [Notification Issues](#10-notification-issues)
11. [Performance Issues](#11-performance-issues)

---

## 1. Pre-flight Check Failures

### Python version too old

```
FAIL: Python >= 3.12 required, found 3.11.x
```

**Fix**: Install Python 3.12+. On Ubuntu: `sudo add-apt-repository ppa:deadsnakes/ppa && sudo apt install python3.12`. On macOS: `brew install python@3.12`.

### Terraform version too old

```
FAIL: Terraform >= 1.14.0 required, found 1.5.x
```

**Fix**: Download the latest Terraform from [releases.hashicorp.com](https://releases.hashicorp.com/terraform/). The hub requires 1.14+ for provider compatibility.

### Missing pydantic

```
FAIL: pydantic not installed
```

**Fix**: `pip install -e ".[dev]"` from the `finops-automation-hub/` directory.

### Tests fail during pre-flight

```
FAIL: Test suite failed (X failures)
```

**Fix**: Run `pytest -v` to see which tests fail. Common causes:
- Stale `.pyc` files: `find . -name '__pycache__' -exec rm -rf {} +`
- Wrong working directory: ensure you're in `finops-automation-hub/`
- Missing dependencies: `pip install -e ".[dev]"`

---

## 2. Authentication Issues

### AWS: "Unable to locate credentials"

```
botocore.exceptions.NoCredentialsError: Unable to locate credentials
```

**Cause**: No AWS credentials configured.

**Fix** (pick one):
```bash
# SSO login
aws sso login --profile your-profile
export AWS_PROFILE=your-profile

# Or verify instance profile (on EC2)
curl -s http://169.254.169.254/latest/meta-data/iam/info
```

**Never do**: `export AWS_ACCESS_KEY_ID=...` — the hub prohibits static API keys.

### AWS: "Access Denied" on Cost Explorer

```
ClientError: An error occurred (AccessDeniedException) when calling GetCostAndUsage
```

**Cause**: Your IAM role lacks Cost Explorer permissions.

**Fix**:
1. Ensure Cost Explorer is enabled: AWS Console → Billing → Cost Explorer → Enable
2. Add `ce:GetCostAndUsage`, `ce:GetCostForecast`, `ce:GetAnomalies` to your IAM policy
3. Cost Explorer data takes 24 hours to become available after first enablement

### AWS: "Bedrock model access not enabled"

```
AccessDeniedException: You don't have access to the model
```

**Fix**: Go to AWS Console → Bedrock → Model access → Request access for your chosen model (e.g., Claude Sonnet). Access approval can take up to 24 hours.

### GCP: "Could not automatically determine credentials"

```
google.auth.exceptions.DefaultCredentialsError: Could not automatically determine credentials
```

**Fix**:
```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

### GCP: "Permission denied" on BigQuery

```
google.api_core.exceptions.Forbidden: 403 Access Denied: BigQuery
```

**Fix**:
```bash
# Grant BigQuery access to your user or service account
gcloud projects add-iam-policy-binding YOUR_PROJECT \
  --member="user:you@example.com" \
  --role="roles/bigquery.dataViewer"
```

### GCP: "API not enabled"

```
google.api_core.exceptions.PermissionDenied: Cloud Asset API has not been enabled
```

**Fix**:
```bash
gcloud services enable cloudasset.googleapis.com --project YOUR_PROJECT
# The Terraform module enables required APIs automatically,
# but if running tools before Terraform, enable manually.
```

### GCP: "Service account key detected"

If you see warnings about service account keys in the pre-flight check:

**Fix**: Remove any `GOOGLE_APPLICATION_CREDENTIALS` environment variable pointing to a JSON key file. Use `gcloud auth application-default login` instead.

---

## 3. Terraform Deployment Issues

### "Error: Unsupported Terraform Core version"

```
Error: Unsupported Terraform Core version
This configuration requires Terraform >= 1.14.0
```

**Fix**: Upgrade Terraform: `terraform -install-autocomplete` won't help. Download 1.14+ from HashiCorp.

### "Error: Provider version constraint"

```
Error: Failed to query available provider packages
Could not find a version of hashicorp/aws >= 6.0
```

**Fix**: Run `terraform init -upgrade` to fetch the latest providers. If using a private registry mirror, ensure it has AWS provider >= 6.0 and Google provider >= 6.0.

### AWS: "KMS key not found"

```
Error: creating CloudTrail: InvalidParameterValueException: KMS key not found
```

**Fix**: Ensure `kms_key_arn` in `terraform.tfvars` points to a valid, enabled KMS key in the same region. The key policy must allow CloudTrail and S3 to use it:
```bash
aws kms describe-key --key-id YOUR_KEY_ID
```

### AWS: "Bedrock agent creation failed"

```
Error: creating Bedrock Agent: ValidationException
```

**Possible causes**:
1. Model ID incorrect — verify `bedrock_model_id` matches an available model
2. Model access not granted — see [Bedrock model access](#aws-bedrock-model-access-not-enabled)
3. Region doesn't support Bedrock — use `us-east-1`, `us-west-2`, or `eu-west-1`

### GCP: "Billing account not found"

```
Error: Error creating Budget: googleapi: Error 404: Billing account not found
```

**Fix**: Either set `billing_account_id = ""` (disables budget alerts) or verify the billing account ID:
```bash
gcloud billing accounts list
```

### GCP: "WIF pool already exists"

```
Error: Error creating WorkloadIdentityPool: googleapi: Error 409: Already exists
```

**Fix**: The WIF pool may exist from a previous deployment. Either:
1. Import it: `terraform import google_iam_workload_identity_pool.github[0] projects/PROJECT/locations/global/workloadIdentityPools/POOL_ID`
2. Or set `enable_wif = false` if not needed

### State lock errors

```
Error: Error acquiring the state lock
```

**Fix**:
```bash
# Only if you're certain no other process is running:
terraform force-unlock LOCK_ID
```

---

## 4. Agent Issues

### AWS Bedrock Agent: "Agent not prepared"

```
ValidationException: Agent is not prepared
```

**Fix**: The agent must be prepared before invocation. Terraform sets `prepare_agent = true`, but if you modified the agent manually:
```bash
aws bedrock-agent prepare-agent --agent-id YOUR_AGENT_ID
```

### AWS Bedrock Agent: Slow responses

**Cause**: Bedrock agent orchestration involves multiple LLM calls and can take 10-30 seconds.

**Mitigation**:
- Use `idle_session_ttl_in_seconds = 600` to keep sessions warm
- Cache Cost Explorer results in action group Lambda functions
- Consider a smaller/faster model for time-sensitive queries

### GCP ADK Agent: "google-adk not installed"

```
WARNING: google-adk not installed. Install with: pip install google-adk
```

**Fix**: `pip install -e ".[gcp]"` — the ADK is included in the GCP extras.

### GCP ADK Agent: "Model not found"

```
google.api_core.exceptions.NotFound: Model gemini-2.5-flash not found
```

**Fix**: Ensure you have Vertex AI API enabled and the model is available in your region:
```bash
gcloud services enable aiplatform.googleapis.com --project YOUR_PROJECT
```

---

## 5. MCP Server Issues

### Hub MCP server: "Address already in use"

**Cause**: Another instance is running.

**Fix**: The hub MCP server uses stdio transport (not a port), so this shouldn't occur. If using a custom HTTP transport, find and stop the existing process:
```bash
lsof -i :PORT_NUMBER
kill PID
```

### AWS MCP server: "uvx command not found"

**Fix**:
```bash
pip install uv
# Or
pipx install uv
```

### AWS MCP server: "No module named 'awslabs'"

**Fix**: The AWS MCP servers are installed at runtime by `uvx`. Ensure `uv` is installed and you have internet access:
```bash
uvx awslabs.cost-explorer-mcp-server@latest --help
```

### MCP server: JSON parse errors

```
Invalid JSON received from MCP server
```

**Cause**: The MCP server is writing non-JSON output to stdout.

**Fix**: Ensure no `print()` statements or logging goes to stdout. All logging should go to stderr. Check with:
```bash
echo '{"method":"tools/list","id":1}' | python -m mcp_server.server 2>/dev/null
```

---

## 6. Alert & Policy Issues

### No alerts generated for expensive resources

**Possible causes**:
1. Cost is below thresholds — check thresholds via `HubConfig().get_threshold_defaults()`
2. No matching policies — run `finops_list_policies` to verify active policies
3. Resource type not recognised — check `providers/aws/resources.py` or `providers/gcp/resources.py`

**Debug**:
```python
from mcp_server.server import handle_tool_call

# List active policies
result = handle_tool_call('finops_list_policies', {})
print(result)

# Manually evaluate a resource
result = handle_tool_call('finops_evaluate_resource', {
    'provider': 'aws',
    'account_id': '123456789012',
    'region': 'eu-west-2',
    'resource_type': 'ec2:instance',
    'resource_id': 'test',
    'estimated_monthly_cost_usd': 50.0,  # Try different values
    'creator_identity': 'test-user',
})
print(result['status'])  # 'within_thresholds' or 'alert_generated'
```

### Alerts lack creator information

**Cause**: Resource creation events don't include creator email.

**Fix**: Ensure resources are tagged with an `owner` tag. The alert engine falls back to the IAM principal ARN if no email is available. Update your tagging policy:
```python
handle_tool_call('finops_update_policy', {
    'policy_id': 'default-tagging-001',
    'updates': {'require_tags': ['team', 'cost-centre', 'environment', 'owner', 'email']},
})
```

### Policy not being evaluated

**Check**:
1. Policy is enabled: `policy.enabled == True`
2. Provider matches: `policy.provider` is `None` (matches all) or matches the event provider
3. Resource type matches: `policy.resource_types` is empty (matches all) or includes the event type

---

## 7. Audit Trail Issues

### Integrity verification fails

```
{"status": "violations_detected", "violations": [...]}
```

**Cause**: An audit entry was modified after creation, breaking the SHA-256 chain.

**Fix**:
1. This is a **security event** — investigate who modified the audit files
2. Check `audit_store/audit-YYYY-MM-DD.jsonl` files for manual edits
3. If caused by a code bug during development, clear audit data and restart:
   ```bash
   rm audit_store/*.jsonl  # Development only! Never in production.
   ```
4. In production, export the audit data for forensic review before any remediation

### Audit files growing large

**Fix**: Audit files are rotated daily (`audit-YYYY-MM-DD.jsonl`). For long-running deployments:
1. Archive old files to cloud storage (S3 or GCS)
2. The `.gitignore` already excludes `audit_store/*.jsonl` from version control
3. Consider setting up log rotation via the MCP export tool:
   ```python
   handle_tool_call('finops_export_audit', {
       'since': '2026-01-01',
       'until': '2026-02-01',
   })
   ```

---

## 8. Configuration Issues

### Environment variable not taking effect

**Cause**: The environment variable name doesn't match the expected mapping.

**Fix**: Environment variables must use the exact names listed below:

| Variable | Overrides |
|----------|-----------|
| `FINOPS_POLL_INTERVAL` | `monitoring.poll_interval_seconds` |
| `FINOPS_ANOMALY_MULTIPLIER` | `thresholds.anomaly_multiplier` |
| `FINOPS_REQUIRED_TAGS` | `tags.required` (comma-separated) |
| `FINOPS_AUDIT_DIR` | `hub.audit_dir` |
| `FINOPS_EVENT_STORE` | `hub.event_store` |

**Debug**:
```python
from core.config import HubConfig
config = HubConfig()
print(config.as_dict())  # View all resolved settings
```

### YAML config file not loaded

**Cause**: The `hub_config.yaml` file must be in the working directory or the hub root.

**Fix**: Verify the file location:
```bash
ls -la hub_config.yaml
# Should be in the finops-automation-hub/ directory
```

### Threshold overrides not applied

**Cause**: Threshold defaults are nested dicts that can't be overridden via a single env var.

**Fix**: Use a `hub_config.yaml` file to customise per-resource-type thresholds:
```yaml
thresholds:
  defaults:
    ec2:instance:
      warning: 1000
      critical: 5000
      emergency: 10000
```

Or modify thresholds at runtime:
```python
from core.config import HubConfig
config = HubConfig()
config.set("thresholds.defaults.ec2:instance.warning", 1000)
```

---

## 9. Event Store Issues

### SQLite event store: "no such table: events"

**Cause**: The SQLite database was created with `:memory:` but a new connection was opened (each `:memory:` connection creates a fresh database).

**Fix**: This is handled automatically in the hub — the `SQLiteEventStore` keeps a persistent connection for `:memory:` databases. If you see this error with a file-based database, ensure the database path is writable:
```bash
ls -la events.db
# If missing, the store will create it automatically
```

### Events not persisting across restarts

**Cause**: Using the in-memory event store (default).

**Fix**: Switch to SQLite for persistence:
```bash
export FINOPS_EVENT_STORE=sqlite
```

### Duplicate events stored

**Cause**: This shouldn't happen — both event store backends deduplicate by `event_id`.

**Debug**:
```python
from core.event_store import SQLiteEventStore
store = SQLiteEventStore("events.db")
print(store.count())  # Total events
print(store.exists("your-event-id"))  # Check specific event
```

### Event queries returning empty results

**Possible causes**:
1. Wrong provider filter — use `CloudProvider.AWS` or `CloudProvider.GCP` enum values
2. Time range filter too narrow — `since` and `until` use ISO 8601 datetimes
3. No events stored yet — check `store.count()`

---

## 10. Notification Issues

### Slack notifications not arriving

**Possible causes**:
1. Webhook URL not set: `export FINOPS_SLACK_WEBHOOK="https://hooks.slack.com/services/..."`
2. Webhook URL expired — regenerate in Slack workspace settings
3. Network connectivity — the hub must be able to reach `hooks.slack.com`

**Debug**:
```bash
# Test the webhook directly
curl -X POST -H 'Content-type: application/json' \
  --data '{"text":"FinOps Hub test"}' \
  "$FINOPS_SLACK_WEBHOOK"
```

### PagerDuty alerts not firing

**Possible causes**:
1. Routing key not set: `export FINOPS_PAGERDUTY_KEY="your-routing-key"`
2. Wrong routing key — verify in PagerDuty service settings
3. Service is in maintenance mode in PagerDuty

### Webhook dispatcher returns False

**Cause**: The webhook endpoint is unreachable or returned a non-2xx status.

**Fix**:
1. Verify the endpoint URL is correct
2. Check the endpoint is accepting POST requests with JSON body
3. Review the hub logs for the error message

### Multiple notification channels

To send alerts to multiple channels simultaneously, configure all desired channels:
```bash
export FINOPS_SLACK_WEBHOOK="https://hooks.slack.com/services/..."
export FINOPS_PAGERDUTY_KEY="routing-key-123"
```

The `CompositeDispatcher` sends to all configured channels. One channel failing does not block the others.

---

## 11. Performance Issues

### Tests are slow

**Fix**: Tests should complete in under 1 second. If slow:
- Ensure no cloud API calls are being made (tests should work offline)
- Clear `__pycache__`: `find . -name '__pycache__' -exec rm -rf {} +`
- Run with `-x` to stop on first failure: `pytest -x`

### MCP server startup is slow

**Cause**: Loading policies from disk on startup.

**Fix**: Keep the number of policy files reasonable (< 100). The server loads all policies from `policies/` on startup.

### High memory usage with in-memory event store

**Cause**: Large numbers of events stored in memory.

**Fix**: Switch to SQLite for production workloads:
```bash
export FINOPS_EVENT_STORE=sqlite
```

The SQLite backend keeps data on disk and uses SQL queries, significantly reducing memory usage for large event volumes.

### High memory usage with alerts

**Cause**: Large numbers of in-memory alerts or audit entries.

**Fix**: For production at scale:
1. Use the SQLite event store instead of in-memory
2. Implement a database backend for alerts (e.g., DynamoDB or Cloud Firestore)
3. Use the BigQuery audit dataset for querying historical audit data
4. Set up periodic archival of old alerts

---

## Getting Help

1. Run `python scripts/preflight_check.py --all` to diagnose common issues
2. Check the audit trail for error entries: `finops_query_audit --action *.error`
3. Review CloudTrail (AWS) or Cloud Audit Logs (GCP) for permission issues
4. File an issue at [github.com/erayguner/fin-ai-ops/issues](https://github.com/erayguner/fin-ai-ops/issues)
