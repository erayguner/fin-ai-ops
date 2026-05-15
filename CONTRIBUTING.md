# Contributing to FinOps Automation Hub

Thanks for your interest in contributing. This project follows the
recommendations in opensource.guide's
[Best Practices](https://opensource.guide/best-practices/), and the
intent is to make it easy for newcomers to land a useful change without
waiting on synchronous review.

If you're new here, start with:

1. [README.md](./README.md) — what the project does and why.
2. [GOVERNANCE.md](./GOVERNANCE.md) — how decisions are made.
3. [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) — our community standards.
4. [SECURITY.md](./SECURITY.md) — vulnerability reporting and the
   security model.

For documentation-only fixes you can skip straight to the
"Documentation" section below.

## How to contribute

The contribution flow is:

```
fork → branch → code + tests → quality gates → PR → review → merge
```

Issues are welcomed too — even if you don't intend to write the fix,
filing a clear issue with a reproducer is a useful contribution.

### What to work on

Three good starting points:

- Issues labelled
  [`good first issue`](https://github.com/erayguner/fin-ai-ops/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
- Issues labelled
  [`help wanted`](https://github.com/erayguner/fin-ai-ops/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22)
- The `### Unreleased` section of [CHANGELOG.md](./CHANGELOG.md) often
  hints at what's mid-flight.

Larger features should start with an issue or RFC-style discussion so
we can agree on scope before you write code. See "Architecture changes"
below.

## Development setup

```bash
git clone https://github.com/erayguner/fin-ai-ops.git
cd fin-ai-ops
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

You need Python 3.12 or 3.13, and Terraform 1.14+ if you intend to
touch the `providers/*/terraform/` directories.

The repository ships an `.editorconfig` and a `.python-version` so most
editors and `pyenv` will pick the right interpreter and indentation
automatically.

## Quality gates

Every PR must pass these checks. CI enforces them; run them locally
first to avoid the round-trip.

```bash
# Lint + format
ruff check .
ruff format --check .

# Type-check (strict ignore-missing-imports for vendored libs)
mypy core providers agents mcp_server

# Test suite (~15 s; 660+ tests)
pytest -q

# Governance dry-run + boundary-contract structural check
python scripts/governor_dry_run.py
python scripts/ci/validate_boundary_contracts.py

# Policy + Terraform alignment
python scripts/validate_policies.py --strict
python scripts/drift_check.py

# Pre-commit (gitleaks + checkov + the above)
pre-commit run --all-files
```

The agent regression eval harness runs offline by default:

```bash
pytest tests/agent_eval -q
```

If you change a prompt template, a tool, or an agent's foundation
model, the eval gate must still pass. Add a new case under
`tests/agent_eval/cases/` for any behaviour you care about not
regressing.

## Tests

We do not require 100 % coverage, but every new public surface needs
at least one test, and every bug fix needs a regression test.

- Use the existing tests as templates — `tests/test_governance_mcp_tools.py`
  is a good shape for MCP-tool tests, `tests/test_memory_audit.py` for
  pure-Python primitives.
- Tests must be deterministic. Anything dependent on time, network,
  randomness, or floating-point precision needs to be pinned, frozen,
  or seeded.
- Mark cloud-credential-requiring tests with the `@pytest.mark.integration`
  marker so they're skipped by default.

## Pull-request process

1. Fork and create a topic branch off `main`.
2. Implement the change. Keep the PR focused; one logical change per PR
   makes review faster.
3. Add or update tests, docs, and changelog. The PR template's
   checklist is the comprehensive list.
4. Push and open a PR. The auto-assigned reviewer comes from
   [CODEOWNERS](./.github/CODEOWNERS).
5. CI runs. Push fixes in response to red checks; force-push is fine
   on your own branch.
6. Address review feedback. We aim for code reviews within **48 hours**
   per opensource.guide's recommendation; pinging is fine after that.
7. A maintainer squash-merges when the PR is approved and CI is green.

## Commit and PR messages

We do not enforce Conventional Commits, but a clear shape helps the
changelog. A useful PR title fits the form *imperative + scope*:

```
add memory adapter for AgentCore Memory
fix audit chain reset across day rollover
refactor reconciliation to consult ApprovalStore
docs: clarify Bedrock provider version pin
```

PR bodies link the issue (`Closes #NNN`) and call out anything a
reviewer needs to know that isn't obvious from the diff: trade-offs,
intentional non-changes, follow-up issues you're creating.

## Documentation

- Most code-internal documentation lives in docstrings.
- User-facing docs live in `docs/`.
- ADRs live in `docs/adr/` — see the format defined in
  [GOVERNANCE.md](./GOVERNANCE.md).
- Runbooks live in `docs/runbooks/`.
- The opensource.guide gap analyses (`PLATFORM_GAP_ANALYSIS.md`,
  `OSS_TEMPLATE_AUDIT.md`) live in `docs/governance/`.

Doc-only PRs are reviewed in the same flow but with relaxed CI
expectations (lint + link-check only).

## Architecture changes

A change is "architectural" if it:

- Touches the agent governance plane (ADR-008 surfaces — governor,
  audit, approvals, supervisor, observer, memory).
- Modifies a public API or schema in a backwards-incompatible way.
- Adds a new cross-cutting dependency (a fourth cloud provider, a new
  agent framework, etc.).

For architectural changes, open an issue first describing the problem.
A maintainer will help you decide whether to:

- Write a draft ADR under `docs/adr/`, or
- Open a small spike PR to learn the shape, or
- Hold off until existing work lands.

This avoids the trap of building something the project will not accept.

## Security

If you find a security issue, do **not** open a public issue. Follow
the disclosure process in [SECURITY.md](./SECURITY.md) — typically a
private GitHub Security Advisory.

Before submitting, review the security checklist in SECURITY.md:

- No API keys, credentials, or secrets in code, config, fixtures, or
  test data.
- External inputs validated via `core/validation.py`.
- No `eval()`, `exec()`, or `pickle.loads()` on untrusted data.
- New public modules define `__all__`.

## License and DCO

By contributing, you agree your contributions are licensed under the
[MIT License](./LICENSE). We do not require a CLA. Sign-off on commits
is welcome but not required.

## Recognition

Substantial contributions move you into [MAINTAINERS.md](./MAINTAINERS.md)
as a Reviewer for the area you've contributed to (see
[GOVERNANCE.md](./GOVERNANCE.md) for the promotion criteria). Your
authorship is visible in `git log` and the GitHub contributor graph.

## Asking for help

If anything in this document is unclear or out of date, open an issue
with the `documentation` label or send a PR. We treat "the docs were
confusing" as a real bug.

For more general questions, see [SUPPORT.md](./SUPPORT.md).
