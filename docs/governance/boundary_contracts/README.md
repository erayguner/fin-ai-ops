# Agent Boundary Contracts

Framework §3.2 — every agent ships with a **boundary contract**, a
versioned document declaring its purpose, role, in-scope tools,
out-of-scope systems, delegatable downstream agents, data classes,
approval class, owner, on-call rotation, and the foundation model card
reference.

This directory holds the live contracts. The format is YAML so CI can
diff and validate. A new agent does not ship without a contract.

## Files

| File | Agent |
|---|---|
| [aws-bedrock-finops.yaml](aws-bedrock-finops.yaml) | AWS Bedrock FinOps governance agent (`providers/aws/agents/finops_agent.py`) |
| [gcp-adk-finops.yaml](gcp-adk-finops.yaml) | GCP ADK FinOps agent (`providers/gcp/agents/finops_agent.py`) |

## Schema (informal)

```yaml
schema_version: "1"
agent_id: <stable-id>
agent_name: <display-name>
provider: aws | gcp | azure
role: observer | advisor | operator | autonomous_operator
purpose: <one-sentence purpose>
foundation_model:
  card_reference: <url-or-doc-id>
  model_id: <bedrock-or-vertex-id>
  pinned_version: <version>
in_scope_tools:
  - <tool name>
out_of_scope_systems:
  - <system explicitly never touched>
delegatable_downstream_agents: []        # A2A allow-list
data_classes_handled:
  - <internal | confidential | regulated>
approval_class: per_call | batch | none
approver_pool:
  - <approver>
owner: <human or team>
on_call_rotation: <rotation-name-or-url>
maturity_level: L1 | L2 | L3 | L4        # framework §18
prompt_pin:                              # framework §16.1
  source: <git-path or bedrock-prompt-arn>
  version: <version>
data_lineage:
  retrieval_sources:
    - name: <KB / index>
      version: <commit-or-index-version>
boundary_review:
  last_renewed: <YYYY-MM-DD>             # framework §17.2 quarterly
  next_due: <YYYY-MM-DD>
notes: |
  Free-form context that doesn't fit the structured fields.
```

## Change-management

A change to **any** field in a boundary contract is a §16-governed PR
that requires the agent owner + a security peer. Promotion between
roles (Advisor → Operator → Autonomous Operator) is a change-management
event, not a runtime flag.
