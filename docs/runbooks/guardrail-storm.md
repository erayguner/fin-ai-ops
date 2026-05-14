# Runbook — Guardrail intervention storm

**Severity:** High.
**Trigger:** `guardrail.triggered == true` rate > 3× the 30-day baseline for > 5 minutes.
**Owner:** Security on-call (jailbreak pressure) + Agent owner (regression).

## 1. Contain (≤5 min)
- Halt the offending session(s): identify by `finops_session_stats` → `last_severity = critical`.
- If multiple sessions are flagged simultaneously, halt the **entire agent** by setting `hub.governor.enabled = false` is *not* an option — instead push an empty allow-list policy via `finops_update_policy`.

## 2. Preserve evidence
- Pull every `GuardrailEvaluationStep` from the impacted correlation IDs.
- Capture the prompt previews (already redacted) plus the `triggered_filters` list.

## 3. Triage
- Are the triggers concentrated on one filter family? (Hate, prompt-injection, PII, etc.)
- Are the prompts coming from one principal or many? Cross-check `_principal_id` in the audit details.
- Is there a recent prompt template change that could have widened the surface?

## 4. Remediate
- Single offender → revoke the principal's token; raise IAM rotation.
- Pattern → tighten the prompt-injection heuristic phrase list in `core/filters.py`; consider adding a Bedrock Guardrails denied-topic.
- Persistent pattern → escalate to red-team (`red-team-findings.md`).

## 5. Communicate
- Slack `#finops-incidents` + Security.
- If exfiltration succeeded, follow EU AI Act / GDPR breach notification windows.

## 6. Review
- Add a regression eval case under `tests/agent_eval/cases/` that reproduces the storm trigger.
- File an issue against the boundary contract if the storm exposed a missing filter category.
