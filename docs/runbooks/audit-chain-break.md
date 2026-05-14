# Runbook — Audit chain break

**Severity:** Critical (forensic blind spot).
**Trigger:** `AuditChainBrokenError` at load time, or `finops_verify_audit_integrity` returning violations.
**Owner:** Security on-call. SRE secondary.

## 1. Contain (≤5 min)
- Stop further audit writes by halting every active session: iterate `supervisor.all_active()` and call `finops_halt_session` on each.
- The audit logger raises `AuditChainBrokenError` at load by default; ensure no service restarted with `strict=False` after the break.

## 2. Preserve evidence (≤10 min)
- Snapshot the audit directory: `tar czf audit-snapshot-$(date -u +%FT%H%MZ).tgz audit_store/`.
- Hash the snapshot (SHA-256) and store the hash separately from the snapshot.
- Pull the most recent **signed** daily manifest (`audit-YYYY-MM-DD.manifest.json`). It carries `last_checksum` and `file_sha256` — compare to the snapshot to identify the divergence point.

## 3. Triage (≤30 min)
- Locate the first violating entry: `audit_logger.verify_integrity()[0]`. Note `audit_id` and `timestamp`.
- Determine whether the break is **tamper** (someone edited an entry) or **drift** (process crash mid-write).
- Identify the actor who held the audit log at break time.

## 4. Remediate
- Tamper: open a SecOps incident; rotate signing keys (`FINOPS_AUDIT_SIGNING_KEY`); audit IAM access to the audit bucket.
- Drift: confirm the partial write, replay events from after the break point using `finops_replay_events`.
- Restart the audit logger with the chain re-anchored at the last known-good entry. Do NOT clear or rewrite earlier entries.

## 5. Communicate
- Compliance + Security leads — same-day. If regulated data passed through the affected window, follow the §15 external comms procedure.

## 6. Review (24 h)
- Add the break point to `red-team-findings.md`.
- Decide whether to migrate audit storage to a different blast radius (framework §8.5).
