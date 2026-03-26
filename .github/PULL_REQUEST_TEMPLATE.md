## Description

<!-- What does this PR do? Why is it needed? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring (no functional changes)
- [ ] Documentation
- [ ] CI / infrastructure

## Checklist

- [ ] Tests pass (`pytest`)
- [ ] Linter clean (`ruff check .` and `ruff format --check .`)
- [ ] Type-check clean (`mypy core providers agents mcp_server`)
- [ ] No API keys, credentials, or secrets in code or config
- [ ] New modules define `__all__` for explicit public API
- [ ] External inputs validated via `core/validation.py` (if applicable)
- [ ] Security checklist in [SECURITY.md](../SECURITY.md) reviewed (if touching public API)
