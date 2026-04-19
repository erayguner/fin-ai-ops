# ADR-006: Tagging Governance as a Separate Domain from Cost Policy

**Status:** Accepted
**Date:** 2026-04-16

## Context

Tagging compliance started as a single field (`require_tags`) on `CostPolicy` (see ADR-002). As the hub matured, several needs emerged that the unified model could not express cleanly:

1. **Per-provider tag conventions differ**: AWS tags use kebab-case keys (`cost-centre`), GCP labels use snake_case (`cost_centre`) with stricter value constraints.
2. **Taggability is a resource-intrinsic property, not a policy choice**: Some resources (Route53 record sets, GCP service accounts, DNS records) are non-taggable by provider design. Flagging them as non-compliant is a false positive.
3. **Value validation** (regex patterns for `cost-centre`, `environment`, `data-classification`) has no natural home in `CostPolicy`.
4. **Severity differs by domain**: Missing `model-family` on a Bedrock workload is a compliance-critical event; missing `project` on a dev sandbox is a warning.
5. **Weekly reporting has a different cadence** from per-event cost alerting.

Options considered:

1. **Extend `CostPolicy`** with every tagging field — bloats the model; provider-scoping rules become conditional logic.
2. **Separate `TaggingPolicy` domain** — small, focused model + dedicated engine + dedicated reports directory.
3. **Two policy directories, one model** — confusing; users wouldn't know where to put a policy.

## Decision

Introduce a **second policy domain** dedicated to tagging governance:

- **Model:** `TaggingPolicy` in `core/tagging.py` (Pydantic, versioned via `schema_version`).
- **Storage:** JSON files under `policies/tagging/` (separate from top-level `policies/` so `CostPolicy` validators are unaffected).
- **Engine:** `TaggingPolicyEngine` loads policies, resolves the most-specific match per event (scoped resource-type > provider-wide), and skips exempt types.
- **Taggability registry:** `TAGGABILITY_REGISTRY` in `core/tagging.py` classifies AWS/GCP resource types as `TAGGABLE`, `PARTIAL`, or `NON_TAGGABLE` — independent of any policy.
- **Agent:** `TaggingHealthAgent` runs weekly scans, produces `TaggingHealthReport` with remediation priorities ranked by cost.

The cost policy engine's `require_tags` field is retained for simple cost-policy-level enforcement but should not be used for comprehensive tagging governance.

## Consequences

**Benefits:**
- `TaggingPolicy` has first-class support for `recommended_tags`, `tag_value_patterns`, `exempt_resource_types`, and `severity_when_missing` — none of which fit naturally on `CostPolicy`.
- Taggability classification is a pure registry lookup, not entangled with policy matching.
- `CostPolicy` remains lean and single-purpose; per-event cost evaluation is unaffected.
- AI/ML workloads can enforce stricter rules (`model-family`, `workload-class`, `ai-workload-phase`) via scoped policies without inflating the cost-policy schema.
- Weekly tagging health reports run independently of the per-event alert pipeline (no shared queue, no blocking).

**Tradeoffs:**
- Two policy schemas to maintain. Mitigated by sharing `CloudProvider` and `Severity` enums from `core/models.py`.
- Operators need to know which policy type covers which concern. The directory layout (`policies/*.json` vs `policies/tagging/*.json`) makes this explicit.
- `CostPolicy.require_tags` and `TaggingPolicy.required_tags` can in theory drift. Mitigation: `policy-guardian` agent audits tag consistency across both domains.

## Related

- ADR-001 — Policy-as-code with JSON files. `TaggingPolicy` follows the same pattern.
- ADR-002 — Unified `CostPolicy` model for cost governance. This ADR complements it; tagging governance is a separate domain.
