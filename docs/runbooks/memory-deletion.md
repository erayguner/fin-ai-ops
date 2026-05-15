# Runbook — Right-to-be-forgotten / memory deletion

**Severity:** Critical (regulatory).
**Trigger:** A subject (user / tenant / customer) requests deletion of
all data the agent retained about them — typically via GDPR / CCPA
data-subject-access channels.
**Owner:** Compliance + Agent owner.

## 1. Receive the request (≤ same day)

- Confirm the requester's identity matches the `user_id` recorded in
  memory. Reject the request if identity verification fails (this is
  itself an audited event).
- Capture the request: subject, channel, jurisdiction, scope.

## 2. Execute deletion (≤ regulatory window)

- Call `finops_memory_forget_user(user_id, actor="compliance-<name>")`.
  The MCP tool deletes every record across every session in the active
  backend (Vertex Memory Bank / Bedrock AgentCore Memory / in-memory).
- Capture the returned `records_deleted` count.

```bash
# Via the MCP server CLI (or any MCP client)
echo '{"method":"tools/call","params":{"name":"finops_memory_forget_user","arguments":{"user_id":"<subject-id>","actor":"compliance-<name>"}}}' \
  | python -m mcp_server.server
```

## 3. Verify deletion

- Call `finops_memory_read(user_id="<subject-id>")` — expect 0 records.
- Verify the audit chain shows the deletion event:
  ```bash
  finops_query_audit --action agent.step.memory_operation \
    --target "*" --limit 100 | jq '.entries[] | select(.details.payload.operation=="delete")'
  ```
- Confirm `delete_for_user` ran across **every** environment hosting
  the agent (dev, staging, prod) — RtbF is global per subject.

## 4. Confirm to requester

- Provide the deletion confirmation with the records_deleted count, the
  date/time of the operation, and an audit reference (the
  `audit_id` of the deletion step).
- Retention: keep the deletion proof for the regulatory window
  (GDPR Art. 17 + Art. 5 — typically the full statute of limitations).

## 5. Review (24 h)

- Was the deletion truly exhaustive? Check:
  - All memory backends (each adapter instance, not just the default).
  - Any cached copies in vector stores / RAG indexes (Knowledge Base,
    Vector Search).
  - Any session replays / audit exports that embedded the subject's
    content (those stay in the audit trail per framework §8.4 retention
    rules — but personal data not necessary for the audit chain itself
    can be redacted via signed manifests).
- If the subject's content reached training data for a tuned model
  (§16.6), the fine-tune is non-compliant — retrain on the cleaned
  dataset or retire the adapter.

## Cross-references

- `core/memory_audit.py::MemoryAdapter.delete_for_user`
- `docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md#116-conversational-memory-and-session-stores`
- `docs/runbooks/cross-tenant-memory.md` for the isolation-failure case.
