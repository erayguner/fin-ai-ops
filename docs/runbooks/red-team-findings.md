# Red-team findings log

Framework §12.7 requires a maintained log of red-team findings.
Operator roles need quarterly red-teaming; autonomous roles need
monthly. Each novel finding produces a regression case before
close-out.

## Coverage matrix (per quarter)

| Category | Required coverage | Latest exercise |
|---|---|---|
| Direct prompt injection | Override system prompt, reveal instructions, repurpose | _pending_ |
| Indirect prompt injection (tool output) | Payload via search result / API response | _pending_ |
| Indirect prompt injection (memory) | Payload planted in Memory Bank / Bedrock memory | _pending_ |
| Tool-chain escalation | Allowed-tool combinations → forbidden outcome | _pending_ |
| Credential exfiltration | Get agent to print secrets | _pending_ |
| PII exfiltration | Get agent to emit regulated data | _pending_ |
| Sandbox escape (code interp) | N/A — no code-execution tools yet | n/a |

## Findings

| Date | Finding | Severity | Owner | Status | Regression case |
|---|---|---|---|---|---|
| _none yet_ | | | | | |

## DR exercises

| Date | Exercise | Verdict |
|---|---|---|
| _none yet_ | | |

## How to log a finding

1. Open a SecOps ticket with the prompt sequence + observed agent behaviour.
2. Hash the offending prompt for the `forbidden_phrases` list of a new eval case.
3. Add the case to `tests/agent_eval/cases/` so the harness gates future PRs.
4. Update this log with the finding + the case id.
