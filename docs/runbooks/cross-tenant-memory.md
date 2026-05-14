# Runbook — Cross-tenant or cross-session memory access

**Severity:** Critical (always — even single occurrence).
**Trigger:** Memory retrieval returns a record keyed to a different
user / tenant / session than the active one.
**Owner:** Security on-call.

## 1. Contain (≤5 min)
- Halt every session against the affected memory store. Disable Memory Bank / Bedrock AgentCore Memory writes by revoking the agent SA's `memory:Write` permission temporarily.

## 2. Preserve evidence
- Snapshot the memory store at the time of breach.
- Pull `agent.step.*` audit entries with `target = <session>:memory_*`.

## 3. Triage
- Is the breach a key-namespacing bug (memory writes used a wider scope than they should) or a leaked retrieval filter?
- Cross-check tenant boundaries with the boundary contract's `data_classes_handled` declarations.

## 4. Remediate
- Patch the namespacing bug; re-deploy the agent.
- Purge all cross-keyed entries; document the purge in the audit trail.
- Re-encrypt memory if the encryption key was shared cross-tenant (worst case).

## 5. Communicate
- Affected tenants — within regulatory windows (GDPR: 72h to supervisor).
- Compliance + Legal.

## 6. Review
- Add a regression test asserting the namespacing on writes.
- Consider moving memory to a per-tenant Memory Bank instance.
