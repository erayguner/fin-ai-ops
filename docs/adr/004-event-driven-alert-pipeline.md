# ADR-004: Event-Driven Alert Pipeline

**Status:** Accepted
**Date:** 2026-03-26

## Context

When a costly resource is created, the hub needs to detect it, evaluate policies, generate an alert, and notify the responsible team. The options were:

1. **Event-driven pipeline** — `ResourceCreationEvent` flows through `ThresholdEngine` -> `AlertEngine` -> `CostAlert` -> `NotificationDispatcher`
2. **Inline blocking** — intercept the cloud API call and block resource creation if it violates a policy (like AWS Service Control Policies)
3. **Batch processing** — periodically scan all resources and compare against policies

## Decision

Event-driven pipeline. Cloud provider listeners (`AWSEventListener`, `GCPEventListener`) emit `ResourceCreationEvent` objects. These flow through the alert and policy engines, producing `CostAlert` objects that are dispatched to notification channels. Every step is logged to the `AuditLogger`.

All components communicate through shared domain objects (`ResourceCreationEvent`, `CostAlert`), not through a message bus or queue.

## Consequences

**Benefits:**
- Each stage is independently testable (376 tests, all passing)
- The pipeline is observable — the `ReconciliationAgent` can detect gaps (events that were never evaluated, alerts that were never dispatched)
- Adding a new evaluation step (e.g., carbon-aware region check) is a single method addition
- No infrastructure dependency on a message broker

**Tradeoffs:**
- Detection is reactive, not preventive — the resource already exists by the time the alert fires
- In-process pipeline means a crash loses in-flight events (mitigated by the `EventStore` persisting events to disk)
- No backpressure mechanism — a burst of events could overwhelm the alert engine (mitigated by the `CircuitBreaker`)
