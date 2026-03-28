# ADR-001: Policy-as-Code with JSON Files on Disk

**Status:** Accepted
**Date:** 2026-03-26

## Context

The FinOps Hub needs a mechanism to define cost governance policies (tag requirements, cost caps, approval thresholds). The options considered were:

1. **JSON files in a `policies/` directory** — version-controlled, reviewable in PRs
2. **Database (SQLite/Postgres)** — queryable, supports dynamic CRUD via API
3. **Python code** — policies as classes or decorators in source

The hub is operated by an infrastructure team that already uses git-based workflows for Terraform and CI/CD. Policy changes need the same review rigor as code changes.

## Decision

Store policies as individual JSON files in `policies/`, one file per policy. The `PolicyEngine` loads and validates them against the `CostPolicy` Pydantic model at startup.

## Consequences

**Benefits:**
- Every policy change goes through PR review with full diff visibility
- Git history provides a complete audit trail of who changed what and when
- CI can validate policy schema (`scripts/validate_policies.py`) before merge
- No database dependency — the hub can run with zero infrastructure
- Policies are portable and human-readable

**Tradeoffs:**
- No dynamic policy creation via API without a file-write mechanism
- Adding a policy requires a commit, not an API call — slower for ad-hoc needs
- File-based loading means the full policy set must fit in memory (not a concern at current scale of ~16 policies)
