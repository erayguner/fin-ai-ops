<!--
Thanks for the PR! A complete template helps reviewers respond faster.

If you're unsure about any item, leave it unchecked — a reviewer will
discuss it with you. New contributors: see CONTRIBUTING.md.
-->

## Summary

<!-- One paragraph. What does this PR change, and why? Link the issue with `Closes #NNN` if applicable. -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (API / schema)
- [ ] Refactor (no functional change)
- [ ] Documentation
- [ ] CI / infrastructure / dependencies
- [ ] Security hardening

## Scope check

- [ ] The change is in scope per [GOVERNANCE.md](../GOVERNANCE.md#scope).
- [ ] The change does not widen the project's scope; if it does, an
      issue was opened first and consensus reached.

## Quality gates (run locally)

- [ ] `ruff check .` and `ruff format --check .` pass.
- [ ] `mypy core providers agents mcp_server` passes.
- [ ] `pytest -q` passes.
- [ ] `python scripts/governor_dry_run.py` passes (when touching governor surfaces).
- [ ] `python scripts/ci/validate_boundary_contracts.py` passes (when touching agents).
- [ ] `pre-commit run --all-files` passes locally (gitleaks + checkov + the above).

## Tests

- [ ] New code is covered by tests.
- [ ] Bug fixes include a regression test.
- [ ] Agent eval gate (`pytest tests/agent_eval -q`) still passes when
      touching prompts, tools, or models.

## Security

- [ ] No API keys, credentials, or secrets in code, config, fixtures, or test data.
- [ ] All external inputs validated via `core/validation.py` (if applicable).
- [ ] No `eval()`, `exec()`, or `pickle.loads()` on untrusted data.
- [ ] Security checklist in [SECURITY.md](../SECURITY.md) reviewed for public-API changes.
- [ ] Boundary contract for any affected agent updated (`docs/governance/boundary_contracts/`).

## Governance & docs

- [ ] If this is an architectural change, an ADR exists or is being added under `docs/adr/`.
- [ ] [CHANGELOG.md](../CHANGELOG.md) updated under `## [Unreleased]` for user-visible changes.
- [ ] README / runbooks updated where reader-visible.
- [ ] Boundary review or maintainer roster changes (if any) reflected in [MAINTAINERS.md](../MAINTAINERS.md).

## Cost impact (Terraform / policies)

- [ ] N/A — no cost-affecting changes.
- [ ] `python scripts/validate_policies.py --strict` passes.
- [ ] `python scripts/drift_check.py` passes.
- [ ] Estimated monthly cost impact: <!-- e.g. +$0, -$500/mo, new $2k/mo -->
- [ ] Approval obtained for resources above policy thresholds.

## Reviewer notes

<!-- Anything reviewers should know that isn't obvious from the diff: trade-offs, intentional non-changes, follow-up issues. -->
