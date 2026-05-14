# Runbook — Agent-specific threat detection finding

**Severity:** High.
**Trigger:** SCC Agent Engine Threat Detection / GuardDuty / CloudTrail Insights flag on the agent's identity or runtime.
**Owner:** Security on-call.

## 1. Contain
- Halt the agent if the finding implicates the runtime (credential abuse, anomalous egress).
- Rotate the agent's identity token (Vertex Agent Identity / AgentCore Identity) if credential abuse is indicated.

## 2. Preserve evidence
- Snapshot the finding payload + correlation IDs.
- Pull the matching `AgentTrace` via `finops_replay_session`.

## 3. Triage
- Which detection fired? Map the finding name → relevant framework section:
  - `agent.credential.abuse` → §7 + §12.2
  - `agent.tool_chain.escalation` → §3.2 (boundary contract) + §4
  - `agent.runtime.anomalous_egress` → §7.5 (perimeter)

## 4. Remediate
- Per detection-specific guidance (see provider docs).
- Re-issue identity credentials with tighter Context-Aware Access policy.

## 5. Communicate
- Security + Agent owner.

## 6. Review
- Capture the trigger pattern as a red-team case in `red-team-findings.md`.
