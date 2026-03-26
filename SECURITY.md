# Security Policy — FinOps Automation Hub

## Security Model

The FinOps Automation Hub follows a **zero-credentials** architecture. No API keys, service account keys, or long-lived secrets are stored in code, configuration files, or environment variables.

### Authentication

| Provider | Mechanism | Details |
|----------|-----------|---------|
| **AWS** | IAM Roles | STS AssumeRole, EC2 Instance Profiles, GitHub OIDC |
| **GCP** | Workload Identity Federation | ADC, WIF pools, no service account key files |
| **CI/CD** | GitHub OIDC | Federated identity — no stored secrets |

### Defence in Depth

1. **Input validation** — All MCP tool inputs are validated and sanitised (`core/validation.py`) before reaching core logic. Size limits, format checks, and type coercion prevent injection and resource exhaustion.

2. **SSRF protection** — Webhook and notification dispatchers validate outbound URLs against:
   - Private/internal IP ranges (RFC 1918, link-local, loopback)
   - Cloud metadata endpoints (`169.254.169.254`, `metadata.google.internal`)
   - Scheme restrictions (HTTPS only by default)
   - Port restrictions (80, 443, 8080, 8443 only)

3. **Path traversal prevention** — `AuditLogger` and `PolicyEngine` reject paths containing `..` and optionally enforce base directory containment.

4. **Error sanitisation** — Public API error messages are scrubbed of file paths, line numbers, and internal class names to prevent information leakage.

5. **Audit trail** — Every action is recorded with chained SHA-256 checksums for tamper detection. Audit entries are append-only JSONL files.

6. **Least privilege** — Each cloud agent runs with a dedicated IAM role/service account scoped to only the permissions it needs.

### Public API Boundaries

The MCP server (`mcp_server/server.py`) is the only public API surface. All tool handlers validate inputs before passing them to core logic:

- **String fields**: Length limits, control character stripping
- **Enum fields**: Allowlisted values only (providers, severities, statuses)
- **Numeric fields**: Range validation (no negative costs, $1B cap)
- **Tags**: Maximum 50 tags, key/value length limits
- **Nested objects**: Maximum depth of 5 levels
- **Query limits**: Capped at 10,000 results

### Module Exports

All public modules define `__all__` to explicitly declare their public API surface. Internal implementation details are not exported.

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it responsibly:

1. **Do not** open a public GitHub issue for security vulnerabilities
2. **Email**: Report to the repository maintainer via GitHub Security Advisories
3. **GitHub**: Use the [Security Advisories](https://github.com/erayguner/fin-ai-ops/security/advisories) feature to create a private report
4. **Include**: A clear description of the vulnerability, steps to reproduce, and potential impact

We aim to acknowledge reports within 48 hours and provide a fix within 7 days for critical issues.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.3.x | Yes |
| < 0.3 | No |

## Security Checklist for Contributors

Before submitting a pull request, verify:

- [ ] No API keys, credentials, or secrets in code or config
- [ ] All external inputs validated via `core/validation.py`
- [ ] No `urllib.request.urlopen()` calls without SSRF validation
- [ ] No file path construction without traversal protection
- [ ] Error messages don't expose internal paths or stack traces
- [ ] New modules define `__all__` for explicit public API
- [ ] Tests cover both valid inputs and malicious inputs
- [ ] No `eval()`, `exec()`, or `pickle.loads()` on untrusted data
