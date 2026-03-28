# ADR-005: Dynamic Thresholds with Statistical Baselines

**Status:** Accepted
**Date:** 2026-03-26

## Context

Cost alerts need thresholds to distinguish normal spending from anomalies. The options were:

1. **Static thresholds** — hardcoded dollar amounts per resource type (e.g., EC2 warning at $500)
2. **Dynamic thresholds** — calculated from rolling historical baselines using mean + standard deviation
3. **ML-based anomaly detection** — trained model predicts expected spend, flags deviations

## Decision

The `ThresholdEngine` uses dynamic statistical thresholds. It maintains a cost history per resource type and calculates:

- **Warning** = mean + 1 stddev
- **Critical** = mean + 2 stddev
- **Emergency** = mean + 3 stddev

When fewer than 3 data points exist (configurable via `thresholds.min_datapoints`), it falls back to static defaults from `HubConfig`. An anomaly is flagged when cost exceeds `baseline * anomaly_multiplier` (default 2.0x).

## Consequences

**Benefits:**
- Thresholds adapt automatically as spending patterns change — no manual tuning needed
- The 3-sigma model is well-understood and explainable to finance teams
- Configurable via YAML/env vars — `anomaly_multiplier`, `min_datapoints`, per-resource-type defaults
- Works across providers without provider-specific tuning

**Tradeoffs:**
- Needs a warm-up period — first 3 events for a new resource type use static fallbacks, which may be too high or too low
- Assumes roughly normal cost distribution — a bimodal pattern (e.g., monthly batch jobs) could produce poor thresholds
- Standard deviation is sensitive to outliers — one massive spike shifts the baseline for all future evaluations
- No seasonality awareness (weekly/monthly patterns) — a Monday spike looks the same as a Friday spike
