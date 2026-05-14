# Runbook — Expired approval consumed

**Severity:** Critical (governance bypass).
**Trigger:** `approval.<verdict>` audit entry where the decision timestamp is past the request's `expires_at`.
**Owner:** Security on-call + approver pool owner.

## 1. Contain (≤5 min)
- Halt the session that consumed the expired approval: `finops_halt_session(session_id=...)`.
- Reverse any action the agent took post-consumption if reversible (idempotency-key replay if available).

## 2. Preserve evidence
- Pull the `ApprovalRequest` + decision chain via `finops_pending_approvals` history search + audit query on `approval.*` actions.
- Snapshot the signing key fingerprint at the time of the decision.

## 3. Triage
- Was the decision token forged (signature mismatch) or simply consumed late (clock drift)?
- Check `approval.token.invalid` audit entries near the same correlation id — they imply token verification was attempted.

## 4. Remediate
- Forged: rotate `FINOPS_AUDIT_SIGNING_KEY` immediately. Open a SecOps incident.
- Clock drift: enforce server-side `now()` everywhere; reject decisions with `decided_at > expires_at + grace_window`.

## 5. Communicate
- Compliance + the approver pool owner — same day.
- If the approved action affected regulated data, follow §15 external comms.

## 6. Review
- Add a reconciliation regression test that proves
  `ReconciliationAgent._check_expired_approvals` reports the expired
  entry next time.
- Re-tune approval TTL defaults if expiry windows are systematically too short.
