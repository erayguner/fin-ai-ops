# Open-Source Template Audit

**Date:** 2026-05-15
**Baseline:** [opensource.guide](https://opensource.guide/) — Starting
a project, Best Practices, Building Community, Maintaining Balance.
**Scope:** Every file and convention a public, secure, future-ready
GitHub repository template is expected to ship.

This document records what was already in place, what was added in
this round, and what is intentionally out of scope.

## 1. Audit summary

| Area | Recommended by opensource.guide | Status before | Status after |
|---|---|---|---|
| README — purpose / install / usage / help | Yes | Substantial, but missed status / community / roadmap | Expanded with status, TOC, quick-start, quality gates, community, roadmap |
| LICENSE | Yes | MIT, present | unchanged |
| CONTRIBUTING.md | Yes | Basic 63-line file | Rewritten end-to-end (testing, eval, governance, release flow) |
| CODE_OF_CONDUCT.md | Yes (Contributor Covenant) | **Missing** | Added — Contributor Covenant 2.1 by reference |
| SECURITY.md | Yes | Substantial, present | unchanged |
| SUPPORT.md | Yes | **Missing** | Added — channel-routing table |
| GOVERNANCE.md | Yes | **Missing** | Added — roles, decision-making, scope, release cadence |
| MAINTAINERS.md | Implied | **Missing** | Added — roster + succession plan |
| CHANGELOG.md | Yes (Keep a Changelog) | **Missing** | Added — Keep a Changelog 1.1.0 format |
| Issue templates | Yes | bug + feature | Added documentation template; chooser links Discussions + CoC channel |
| PR template | Yes | Cost-impact only | Rewritten — quality gates, security, governance, docs, cost |
| CODEOWNERS | Yes | `* @erayguner` | Granular per directory |
| FUNDING.yml | Yes (sustainability) | **Missing** | Added — GitHub Sponsors slot |
| .github/release.yml | Yes (release notes) | **Missing** | Added — Keep-a-Changelog-aligned categories |
| .editorconfig | Yes (cross-editor consistency) | **Missing** | Added |
| .python-version | Tooling consistency | **Missing** | Added — `3.12` |
| Dependabot | Yes | Present, weekly | unchanged |
| OSSF Scorecard | Best practice (supply chain) | **Missing** | Added — `.github/workflows/scorecard.yml` |
| Secret scanning workflow | Yes | pre-commit gitleaks only | Added — `.github/workflows/secret-scan.yml` |
| SBOM (CycloneDX + SPDX) | Best practice (supply chain) | **Missing** | Added — `.github/workflows/sbom.yml` |
| Stale-issue automation | Yes (queue hygiene) | **Missing** | Added — `.github/workflows/stale.yml` |
| Workflow lint (zizmor) | Best practice | **Missing** | Added — `.github/workflows/zizmor.yml` |
| All Actions SHA-pinned | Yes (supply chain) | Yes | Verified — every `uses:` line pinned to a 40-char commit hash |
| Tooling versions | Latest stable | pytest 9 / ruff 0.15 / mypy 1.20 / gitleaks 8.30 / harden-runner 2.19.3 | All current |
| Pre-commit | Yes | Present | unchanged |

## 2. What was added (file-by-file)

| File | Purpose |
|---|---|
| `CODE_OF_CONDUCT.md` | Adopt Contributor Covenant 2.1 by reference (canonical text linked, not duplicated). Local additions: report-handling SLA, maintainer commitments, recusal rules. |
| `GOVERNANCE.md` | Roles (Contributor / Reviewer / Maintainer / Project lead), decision-making (lazy consensus + explicit consensus list), scope, ADR format, release cadence, conflict-of-interest rule, suspension policy, succession. |
| `SUPPORT.md` | Channel routing table (bug / feature / docs / question / security / CoC), what to include in a question, response-time targets, triage labels. |
| `CHANGELOG.md` | Keep a Changelog 1.1.0 format. Existing 0.3.0 release captured; `[Unreleased]` section seeded. |
| `MAINTAINERS.md` | Maintainer roster, reviewer roster, emeritus, contact methods, succession plan. |
| `CONTRIBUTING.md` | Rewrite — 5 onboarding sections, full quality gates, test guidance, PR process, commit conventions, architecture-change flow, security checklist. |
| `.github/PULL_REQUEST_TEMPLATE.md` | Rewrite — 6 checklists (scope, quality, tests, security, governance, cost). |
| `.github/CODEOWNERS` | Per-directory ownership instead of catch-all. |
| `.github/ISSUE_TEMPLATE/documentation.yml` | New template for docs issues. |
| `.github/ISSUE_TEMPLATE/config.yml` | Added Discussions and CoC channels. |
| `.github/FUNDING.yml` | Sponsorship surface (GitHub Sponsors slot enabled). |
| `.github/release.yml` | Auto-changelog generation for GitHub releases (Keep-a-Changelog-aligned categories). |
| `.editorconfig` | Cross-editor whitespace + indent consistency. |
| `.python-version` | `3.12` for `pyenv` users. |
| `.github/workflows/scorecard.yml` | OSSF Scorecard, weekly + on push. Publishes results to the Security tab. |
| `.github/workflows/secret-scan.yml` | gitleaks on every push and PR. Complements pre-commit hook (CI catches when contributors skip the hook). |
| `.github/workflows/sbom.yml` | CycloneDX + SPDX SBOMs via Syft on every main push. |
| `.github/workflows/stale.yml` | actions/stale — 60-day issue / 30-day PR dormancy with conservative exempt-label set. |
| `.github/workflows/zizmor.yml` | Workflow lint — fails on unpinned actions, missing `permissions:`, injection risks. |

## 3. Pinning audit

Every workflow `uses:` reference is pinned to a 40-character commit
SHA, with a `# vX.Y.Z` comment for human readability. Verified by:

```bash
grep -EHn '^\s*uses:.*@(v[0-9]+|main|master)\s*$' .github/workflows/*.yml
# → no matches
```

Distinct SHAs in use after this round:

| SHA | Action | Version |
|---|---|---|
| `de0fac2…` | actions/checkout | v6.0.2 |
| `a309ff8…` | actions/setup-python | v6.2.0 |
| `043fb46…` | actions/upload-artifact | v7.0.1 |
| `b5d41d4…` | actions/stale | v10.2.0 |
| `a1d282b…` | actions/dependency-review-action | v5.0.0 |
| `ab7a940…` | step-security/harden-runner | v2.19.3 |
| `68bde55…` | github/codeql-action | v4.35.4 |
| `5e8dbf3…` | hashicorp/setup-terraform | v4.0.0 |
| `4eaacf0…` | ossf/scorecard-action | v2.4.3 |
| `ff98106…` | gitleaks/gitleaks-action | v2.3.9 |
| `e22c389…` | anchore/sbom-action | v0.24.0 |
| `b572f7b…` | zizmorcore/zizmor-action | v0.5.4 |

Pre-commit hooks remain pinned by tag (`rev:`), which is the conventional
contract for `pre-commit-autoupdate`. SHA-pinning hooks would break
that automation; we accept the trade.

## 4. Tooling currency

Snapshot of every tool's pin and the latest available stable:

| Tool | Pin | Latest stable | Action |
|---|---|---|---|
| Python | `3.12` (`requires-python = ">=3.12"`) | 3.13.x supported | Already on Python 3.12 + 3.13 in CI matrix |
| Pydantic | `>=2.7,<3` | 2.x | unchanged |
| pytest | `>=9.0.3,<10` | 9.x | unchanged |
| ruff | `>=0.15.10,<1` | 0.15.x | unchanged |
| mypy | `>=1.20.1,<3` | 1.x — `<3` keeps the door open for the eventual 2.x | unchanged |
| pre-commit | `>=4.5.1,<5` | 4.x | unchanged |
| pre-commit-hooks | v6.0.0 | 6.x | unchanged |
| gitleaks | v8.30.0 | 8.x | unchanged |
| antonbabenko/pre-commit-terraform | v1.105.0 | 1.x | unchanged |
| checkov | 3.2.521 | 3.x | unchanged |

No tooling is end-of-life. Dependabot continues to PR upgrades weekly.

## 5. Accessibility considerations

This is a backend / IaC project; there is no UI to apply WCAG to. The
documentation accessibility recommendations from opensource.guide are
covered:

- Headings follow logical structure (single H1 per file, sequential H2/H3).
- Tables use header rows so screen readers announce columns.
- Link text describes the destination (no "click here").
- Code blocks declare a language for syntax-highlight contrast.
- Diagrams are accompanied by prose descriptions.
- All new docs use plain language and define jargon on first use.

## 6. Out of scope

We deliberately did **not** add the following, with reasons:

| Item | Why not |
|---|---|
| Issue / PR auto-labelling action | Existing CI is enough; auto-labelling without a clear taxonomy creates noise. Add when triage volume warrants. |
| Conventional Commits enforcement | Friction for newcomers. PR titles use a clear convention; CHANGELOG categorisation works without commit-format gating. |
| `release-please` / `semantic-release` | Manual changelog + tag works at our cadence and is easier for newcomers to understand. Revisit when we ship monthly. |
| All-Contributors bot | The contributor graph is sufficient signal for a small project. |
| Multi-language `README` | English-only for now; offer translations when contributors propose them. |
| GitHub Discussions content | The chooser links to Discussions; enabling them is a repo-settings action, not a file change. Toggle when ready. |
| Custom CodeQL config | Default config catches what we need; revisit if the SARIF backlog grows. |

## 7. Validation

Run after merging this audit:

```bash
pytest -q                                                # core tests
python scripts/governor_dry_run.py                       # governor invariants
python scripts/ci/validate_boundary_contracts.py         # per-agent contracts
python scripts/validate_policies.py --strict             # cost policies
ruff check . && ruff format --check .                    # lint
mypy core providers agents mcp_server                    # types
grep -EHn '^\s*uses:.*@(v[0-9]+|main|master)\s*$' .github/workflows/*.yml
# → must produce no output (every action is SHA-pinned)
```

Expect 662+ tests pass, 0 ruff errors, mypy clean, no unpinned
actions, and the governor + contract scripts to print "all checks
passed".
