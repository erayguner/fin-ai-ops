## Description

<!-- What does this PR do? Why is it needed? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactoring (no functional changes)
- [ ] Documentation
- [ ] CI / infrastructure

## Cost Impact

<!-- If this PR changes policies, Terraform, or cloud resource definitions, complete this section -->

- [ ] N/A — no cost-affecting changes
- [ ] Policy validation passes (`python scripts/validate_policies.py`)
- [ ] Terraform-policy drift check passes (`python scripts/drift_check.py`)
- [ ] Estimated monthly cost impact: <!-- e.g. +$0, -$500/mo, new $2k/mo resource -->
- [ ] Approval obtained for resources above policy thresholds

## Checklist

- [ ] Tests pass (`pytest`)
- [ ] Linter clean (`ruff check .` and `ruff format --check .`)
- [ ] Type-check clean (`mypy core providers agents mcp_server`)
- [ ] No API keys, credentials, or secrets in code or config
- [ ] New modules define `__all__` for explicit public API
- [ ] External inputs validated via `core/validation.py` (if applicable)
- [ ] Security checklist in [SECURITY.md](../SECURITY.md) reviewed (if touching public API)
- [ ] Policy changes validated with `/finops:policy-check` (if touching `policies/`)
