# ADR-002: Single CostPolicy Model Across All Cloud Providers

**Status:** Accepted
**Date:** 2026-03-26

## Context

The hub monitors AWS and GCP (with Azure planned). Each provider has distinct resource taxonomies (EC2 instances vs Compute Engine VMs), pricing models (Spot vs Preemptible), and tag systems. The options were:

1. **One `CostPolicy` model with `provider: null` for cross-cloud rules** — simpler, fewer models
2. **Provider-specific policy models** (`AWSCostPolicy`, `GCPCostPolicy`) — type-safe per provider but duplicates logic
3. **Base model + provider mixins** — flexible but complex inheritance

## Decision

Use a single `CostPolicy` model with a nullable `provider` field. When `provider` is null, the policy applies to all providers. Provider-specific fields (like `blocked_regions`, `required_purchase_type`) use generic naming that maps to each provider's vocabulary.

## Consequences

**Benefits:**
- Cross-cloud governance rules (tagging, cost caps) are defined once, not duplicated per provider
- The `PolicyEngine.evaluate_event()` loop is simple — iterate all policies, filter by provider match
- Adding a new provider requires zero changes to the policy model
- Users think in cost governance terms, not provider terms

**Tradeoffs:**
- Provider-specific concepts need generic names: `spot` covers both AWS Spot and GCP Preemptible
- Resource type strings (`ec2:instance`, `compute.instances`) follow different naming conventions per provider — no compile-time safety
- Some fields (like `min_commitment_coverage_pct`) map to different APIs per provider (AWS Savings Plans vs GCP CUDs)
