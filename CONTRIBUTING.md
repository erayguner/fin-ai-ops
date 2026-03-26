# Contributing to FinOps Automation Hub

Thank you for your interest in contributing! This guide covers the development setup and quality gates for pull requests.

## Development Setup

```bash
# Clone and install
git clone https://github.com/erayguner/fin-ai-ops.git
cd fin-ai-ops
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality Gates

All PRs must pass these checks (enforced by CI):

```bash
# Lint
ruff check .
ruff format --check .

# Type check
mypy core providers agents mcp_server

# Tests (367 tests, < 1 second)
pytest
```

## Pull Request Process

1. Fork the repository and create a branch from `main`
2. Make your changes
3. Ensure all quality gates pass locally
4. Open a PR against `main` — fill in the PR template checklist
5. Address review feedback

## Security

Before submitting, review the security checklist in [SECURITY.md](SECURITY.md):

- No API keys, credentials, or secrets in code or config
- All external inputs validated via `core/validation.py`
- New modules define `__all__` for explicit public API
- No `eval()`, `exec()`, or `pickle.loads()` on untrusted data

Report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/erayguner/fin-ai-ops/security/advisories) — never as public issues.

## Code Style

- **Line length**: 100 characters
- **Target**: Python 3.12+
- **Linter rules**: See `[tool.ruff.lint]` in `pyproject.toml`
- **Formatting**: Enforced by `ruff format`

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
