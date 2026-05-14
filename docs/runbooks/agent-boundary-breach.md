# Runbook — Agent boundary breach

**Severity:** Critical.
**Trigger:** An agent invoked a tool / data source not in its boundary contract.
**Owner:** Agent owner. Security secondary.

## 1. Contain (≤5 min)
- Halt the agent: `finops_halt_session` against every active session for this agent.
- If multiple breaches: disable the agent alias in Bedrock (`agent_alias_state = "DISABLED"`) or scale Cloud Run to 0 instances.

## 2. Preserve evidence
- Export signed audit bundle for the impacted correlation IDs.
- Snapshot the Bedrock / ADK trace via `finops_replay_session`.

## 3. Triage
- Was the breach an **allow-list regression** (policy widened in a recent PR), a **tool-chain escalation** (combination of allowed tools yielding a forbidden outcome), or a **delegated A2A hop**?
- Cross-reference the boundary contract: `docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md#32-agent-boundary-contract`.

## 4. Remediate
- Allow-list regression → revert the offending PR; re-prepare the agent.
- Tool-chain escalation → add an argument gate or split the tool category (DISCOVERY → CONNECTION) so separation rules trip on the combination.
- Delegated hop → tighten the upstream agent's `delegatable_set`.

## 5. Communicate
- Security + Compliance.
- If regulated data was reached, follow §15 external comms.

## 6. Review
- Add a regression case proving the boundary catches this composition next time.
- Update the boundary contract; rev the version.
