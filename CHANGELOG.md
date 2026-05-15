# Changelog

All notable changes to this project are documented here. The format is
based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and the project adheres to [Semantic Versioning 2.0.0](https://semver.org/).

## Conventions

- **Added** — new features.
- **Changed** — changes in existing functionality.
- **Deprecated** — features that will be removed in a future release.
- **Removed** — features removed this release.
- **Fixed** — bug fixes.
- **Security** — anything related to vulnerabilities or hardening.

Entries are written **for the user**, not the implementer. A user
should be able to read a release's entries and know whether they need
to act.

## [Unreleased]

### Added
- Open-source repository hygiene per opensource.guide: CODE_OF_CONDUCT,
  GOVERNANCE, SUPPORT, MAINTAINERS, CHANGELOG, FUNDING, .editorconfig,
  .python-version, .github/release.yml.
- OSSF Scorecard, secret-scanning (gitleaks), SBOM, stale-issue
  workflows — all GitHub Actions pinned by commit SHA.
- Boundary-contract validator CI step (`scripts/ci/validate_boundary_contracts.py`).

### Changed
- README now includes Quick start, Roadmap, Community sections; table
  of contents anchors per opensource.guide.
- Issue-template chooser exposes a Documentation template; routes
  questions to GitHub Discussions where available.

## [0.3.0] — 2026-05-14

First public-template-ready release. Brings the project in line with
the agent governance framework (ADR-008) and the 2026 managed-agent
platforms (Gemini Enterprise + Bedrock AgentCore).

### Added
- Canonical agent-trace model (`core/agent_trace.py`) with
  OpenTelemetry span emission.
- Out-of-band approval gateway and signed decision tokens
  (`core/approvals.py`).
- Kill-switch backend (`core/agent_supervisor.py`) consulted by
  `tool_governor.governed_call`.
- Behavioural anomaly observer (`core/agent_observer.py`).
- Audited memory adapter (`core/memory_audit.py`) with right-to-be-
  forgotten and memory-injection defence.
- Bedrock Prompt Management Terraform resource for versioned agent
  instruction.
- Bedrock model invocation logging + expanded guardrail (denied
  topics, word filters, contextual grounding, regex PII filter).
- Bedrock CloudTrail advanced event selector for data events.
- Bedrock RETURN_CONTROL action group for destructive remediation.
- GCP Model Armor template (INSPECT_AND_BLOCK) and floor-settings
  scaffold.
- GCP Vertex AI Data-Access audit log enablement.
- ADK before/after model and tool callbacks wiring `AgentSupervisor`,
  `AgentObserver`, and platform content filters; explicit Gemini
  `safety_settings` per HarmCategory.
- Six-dimension regression eval harness under `tests/agent_eval/` with
  multi-turn case coverage.
- Reconciliation expired-approval check (framework §13.3 fourth).
- Eight incident runbooks under `docs/runbooks/`, including memory
  deletion (RtbF).
- Boundary contracts for the AWS and GCP agents under
  `docs/governance/boundary_contracts/`.

### Changed
- MCP server is now **fail-closed** by default (`hub.governor.enabled`
  defaults to `"true"`). Operators wanting the old permissive
  behaviour must set it to `"false"` explicitly.
- `BudgetTracker` is now keyed per principal, not global, to prevent
  cross-principal exhaustion.
- ADR-008 expanded with an implementation status table; cross-
  references `docs/governance/PLATFORM_GAP_ANALYSIS.md`.

### Security
- Audit log chain verification now strict-by-default across
  rotated files; chain breaks raise `AuditChainBrokenError`.
- Signed audit-manifest writer produces a daily manifest with
  Ed25519 signatures (HMAC-SHA256 fallback in dev).
- Filters (PII, secret, prompt-injection) extracted from MCP server
  and are now reused across audit redaction, notifications, and
  transcripts.

## [0.2.x and earlier] — Pre-template era

Earlier releases predate the public template. See `git log` for
commit-level history.
