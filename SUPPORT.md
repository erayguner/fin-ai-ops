# Getting Help

Thanks for using this project. Use the table below to choose the right
channel for your question — the right channel speeds up the response.

| What you need | Where to go |
|---|---|
| **A bug or unexpected behaviour** | [Open an issue](https://github.com/erayguner/fin-ai-ops/issues/new?template=bug_report.yml) using the Bug Report template. Include Python version, cloud provider, and the smallest reproducer you can produce. |
| **A new feature or change** | [Open a feature request](https://github.com/erayguner/fin-ai-ops/issues/new?template=feature_request.yml). Describe the user problem, not just the proposed code. |
| **A documentation fix or improvement** | [Open a docs issue](https://github.com/erayguner/fin-ai-ops/issues/new?template=documentation.yml) or — better — send a PR directly. |
| **A how-do-I question / discussion** | [GitHub Discussions](https://github.com/erayguner/fin-ai-ops/discussions) once enabled. While Discussions are off, use a feature-request issue with the `question` label and we will respond there. |
| **A security vulnerability** | **Do not open a public issue.** Use [GitHub Security Advisories](https://github.com/erayguner/fin-ai-ops/security/advisories/new) — see [SECURITY.md](./SECURITY.md). |
| **A code of conduct concern** | See [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md) — never raise these in a public issue. |

## What to include in a question

A useful question carries enough context that someone reproducing the
problem doesn't have to ask follow-ups:

- **What you ran** — the exact command or API call.
- **What you expected** — and where that expectation came from
  (README link, ADR, docstring).
- **What happened** — error message, log output, screenshot.
- **Environment** — Python version, OS, cloud provider, whether you
  use AWS Bedrock or Google ADK (or both).
- **What you've already tried** — saves both of us time.

Issues without a reproducer can still be filed — we may close them
asking for one, but that is **not** a rejection. Re-open once you have
the missing context.

## Response-time expectations

This is a community project run by volunteers. Reasonable targets:

| Severity | First response | Resolution attempt |
|---|---|---|
| Security (private advisory) | 48 hours | 7 days |
| Bug — critical (data loss, audit-chain break) | 2 working days | Best effort |
| Bug — non-critical | 1 week | Best effort |
| Feature request | 2 weeks | Triaged into a milestone or closed |
| Discussion / question | 2 weeks | Best effort |

The targets above are aspirational. Pinging is fine after the target
elapses; please do it once and add new information rather than
"bumping".

## Triage labels

Issues are labelled to make filtering easy:

- `good first issue` — Welcoming for newcomers; small, well-scoped,
  reviewer waiting.
- `help wanted` — Maintainers won't get to this soon; PRs welcome.
- `needs-info` — Waiting on the reporter for a reproducer or detail.
- `governance` — Touches the agent-governance plane (ADR-008). Adds a
  security-review reviewer automatically.
- `terraform` — Cloud infrastructure change. Includes the drift check
  in CI.

## Asking better questions over time

If you're new to open source, [How to ask good questions](https://stackoverflow.com/help/how-to-ask)
is the classic primer. It applies here too: the more specific the
question, the faster the answer.
