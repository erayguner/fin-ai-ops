# Maintainers

This file lists the people who can merge changes, run releases, and
enforce the [Code of Conduct](./CODE_OF_CONDUCT.md). Roles and
promotion criteria are defined in [GOVERNANCE.md](./GOVERNANCE.md).

## Active maintainers

| Name | GitHub | Areas (CODEOWNERS) | Responsibilities |
|---|---|---|---|
| Eray Guner | [@erayguner](https://github.com/erayguner) | Project lead, all areas | Final tiebreaker, release manager, CoC enforcement |

## Reviewers

_None at present — vacant._ Apply by opening an issue on what you'd
like to maintain, after at least three substantial PRs landed in that
area.

## Emeritus

_None._ Maintainers who step down for six+ months are listed here with
the date they moved to emeritus.

## How to reach maintainers

- **Day-to-day** — open an issue or PR. That is the highest-priority
  channel and it leaves a public record.
- **Code of Conduct concerns** — file a private GitHub Security
  Advisory at
  <https://github.com/erayguner/fin-ai-ops/security/advisories/new>.
- **Security vulnerabilities** — same channel as above. See
  [SECURITY.md](./SECURITY.md) for the disclosure flow.

## Adding or removing a maintainer

Open a PR that edits this file plus the relevant CODEOWNERS lines.
Per [GOVERNANCE.md](./GOVERNANCE.md), promotions require explicit
consensus from every active maintainer.

## Succession

If the project lead becomes unavailable for an extended period:

1. The most senior maintainer (longest tenure) becomes acting project
   lead until a successor is named.
2. If no other maintainer is active, the project enters
   *maintenance-only* mode: dependency bumps are merged via Dependabot,
   security fixes are accepted, new feature work pauses. A README
   banner makes the status visible.
3. After six months in maintenance-only without a new lead, the
   project may be marked **archived**. The repository stays read-only
   so the code, runbooks, and ADRs remain available.
