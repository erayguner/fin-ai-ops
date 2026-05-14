# Runbook — Memory-injection payload detected

**Severity:** High.
**Trigger:** Content filter or red-team scan finds an injection payload in a Memory Bank / AgentCore Memory entry.
**Owner:** Security on-call + Agent owner.

## 1. Contain
- Purge the offending memory entry by user / session id.
- Halt sessions that have retrieved the entry recently (audit query on `agent.step.tool_invocation` where `tool_name LIKE '%memory%'`).

## 2. Preserve evidence
- Export the offending entry verbatim (encrypted at rest if possible).
- Capture the upstream write — who planted it, via which tool, when?

## 3. Triage
- Was the planted content effective (did a later session repeat the payload's instruction)?
- Is the planting actor a current employee / customer / external API?

## 4. Remediate
- Tighten the input filter stack (`PromptInjectionHeuristic` phrase list) — every retrieved memory now passes through filters per framework §11.6.
- If the planter is a customer-facing input surface, revoke the offending account.

## 5. Communicate
- Security + Compliance.

## 6. Review
- Add the payload (hashed) to a new red-team case.
- Consider rate-limiting memory writes per principal.
