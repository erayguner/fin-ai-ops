# ADR-003: Per-Event Policy Evaluation (Not Account-Level Aggregation)

**Status:** Accepted
**Date:** 2026-03-26

## Context

Cost policy violations can be detected at two levels:

1. **Per-event** — evaluate each `ResourceCreationEvent` individually against policies
2. **Account-level** — aggregate all resource costs per account/project and compare against budget ceilings

Per-event catches individual violations in real time. Account-level catches cumulative overspend that no single resource would trigger.

## Decision

The `PolicyEngine.evaluate_event()` method evaluates one event at a time. Each event is checked against all active policies for tag compliance, cost caps, approval thresholds, region restrictions, and purchase type requirements.

Account-level policies (`max_account_monthly_budget_usd`, `min_commitment_coverage_pct`) are stored in the same `CostPolicy` model but are metadata fields — they are consumed by separate agents (cost monitor, report agent) that have access to aggregated data, not by the per-event evaluation path.

## Consequences

**Benefits:**
- Each violation maps to exactly one event and one policy — clear accountability
- Fully testable with unit tests (no aggregation state needed)
- Audit trail links every violation to a specific resource creation
- Real-time: violations are detected as events arrive, no batch delay

**Tradeoffs:**
- Cannot detect "death by a thousand cuts" — 100 resources each under the cap but collectively over budget
- Account-level policies need a separate evaluation path (not yet implemented as of 2026-03-28)
- The `CostPolicy` model contains fields (`max_account_monthly_budget_usd`) that are never checked by `_check_violations()` — could confuse contributors
