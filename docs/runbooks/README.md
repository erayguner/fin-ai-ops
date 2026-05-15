# Incident Runbooks

Framework §15.2 requires every incident class to have a runbook with the
five steps: **Contain → Preserve Evidence → Triage → Remediate →
Communicate → Review**. This directory holds the runbooks. Each runbook
follows the same skeleton so on-call engineers find the same shape
regardless of the incident class.

## Incident classes covered

| File | Trigger | Severity floor |
|---|---|---|
| [agent-boundary-breach.md](agent-boundary-breach.md) | Agent acted outside its boundary contract (§3.2). | Critical |
| [audit-chain-break.md](audit-chain-break.md) | `AuditChainBrokenError` at load or mid-run. | Critical |
| [guardrail-storm.md](guardrail-storm.md) | Guardrail intervention rate > baseline for > 5 minutes. | High |
| [kill-switch-used.md](kill-switch-used.md) | Operator halted a session in anger. | Medium → High |
| [expired-approval-consumed.md](expired-approval-consumed.md) | An approval was consumed past its expiry. | Critical |
| [cross-tenant-memory.md](cross-tenant-memory.md) | Cross-session / cross-tenant memory retrieval detected. | Critical |
| [agent-threat-detection.md](agent-threat-detection.md) | SCC Agent Engine Threat Detection / GuardDuty finding. | High |
| [memory-injection.md](memory-injection.md) | Indirect-injection payload detected in a session store. | High |
| [memory-deletion.md](memory-deletion.md) | Right-to-be-forgotten / GDPR Art. 17 request. | Critical |

## On-call expectations

- Acknowledge inside 5 minutes.
- Contain inside 15 minutes.
- Preserve evidence (signed audit export) before any restorative action.
- Communicate on a known cadence (Slack `#finops-incidents` + email).
- Open a post-incident review issue within 24 hours.

## Quarterly DR exercise

Framework §17.2 — every quarter the team executes a forced kill-switch
+ recovery drill against a staging session. Log the run in
[red-team-findings.md](red-team-findings.md) under "DR exercises".
