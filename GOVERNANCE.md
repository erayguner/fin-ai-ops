# Project Governance

This document describes how decisions are made, who can make them, and
how new contributors can grow into project responsibilities. It exists
because opensource.guide's
[Best Practices](https://opensource.guide/best-practices/) and
[Building Community](https://opensource.guide/building-community/)
guides recommend that every public project codify its governance so
that disagreements have a transparent resolution path.

## Project values

The project optimises for four properties, in order:

1. **Security** — agents touch cloud spend, IAM, and audit trails;
   weakening any control is never an acceptable trade-off for
   convenience.
2. **Traceability** — every consequential action carries a structured
   record. Closed-source agent behaviour is a bug.
3. **Maintainability** — clear scope, tested code, documented
   decisions. We prefer fewer features done well over more features
   done quickly.
4. **Welcomingness** — newcomers must be able to read [README.md](./README.md),
   [CONTRIBUTING.md](./CONTRIBUTING.md), and a first issue in under 30
   minutes and submit a useful PR.

Decisions that trade off these properties cite the trade explicitly in
the PR description.

## Scope

The project is in scope for:

- Cost governance, tagging governance, and tool-call governance on AWS
  and GCP.
- A FinOps agent built on the providers' native frameworks (Bedrock
  Agents / AgentCore on AWS, Google ADK on GCP).
- The cross-cutting governance plane defined in
  [docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md](./docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md)
  and ADR-008.

The project is **out of scope** for:

- A third cloud provider until there is a maintainer who owns it.
- Generic agent-framework replacements (LangChain wrappers, vendor-
  agnostic LLM routers) — provider-native is the architectural bet.
- Cost optimisation features that lack accountability (we always
  surface WHO/WHAT/WHY).

## Roles

| Role | What they do | How they become one |
|---|---|---|
| **Contributor** | Files issues, opens PRs, reviews PRs. Anyone. | Open a PR. |
| **Reviewer** | Has review rights on a specific subdirectory (see [CODEOWNERS](./.github/CODEOWNERS)). | Sustained contribution to that area + nominated by a Maintainer. |
| **Maintainer** | Merges PRs, sets roadmap, casts the tiebreaker vote, manages releases, runs the CoC enforcement process. | Sustained contribution across the codebase + invited by an existing Maintainer + acceptance by the other Maintainers. |
| **Project lead** | Final tiebreaker; owns the namespace and the project's public surfaces. | Currently the founder ([@erayguner](https://github.com/erayguner)). Succession plan in [MAINTAINERS.md](./MAINTAINERS.md). |

Reviewer and Maintainer status is recorded in
[MAINTAINERS.md](./MAINTAINERS.md). Promotion is a PR against that file
that the other Maintainers approve.

## Decision-making

Most decisions are made by **lazy consensus**: a maintainer proposes,
waits a clearly stated review window (usually 3 working days for code,
7 for design / governance), and merges if there is no sustained
objection. Lazy consensus optimises for momentum.

A decision moves to **explicit consensus** (a 👍 from every active
Maintainer) when any of the following is true:

- The change touches [GOVERNANCE.md](./GOVERNANCE.md), [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md),
  [LICENSE](./LICENSE), or [MAINTAINERS.md](./MAINTAINERS.md).
- The change adds or removes a Maintainer or Reviewer.
- The change widens the project scope (above).
- The change weakens a security control documented in
  [docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md](./docs/governance/AGENT_GOVERNANCE_FRAMEWORK.md).
- The change introduces a backward-incompatible API or schema
  modification.

When consensus cannot be reached, the **project lead** decides. Their
decision is recorded with a one-paragraph rationale in the PR that
prompted it. Where the project lead is the proposer, the most senior
Maintainer (longest tenure) decides instead.

## Architecture decisions

Substantial architectural changes are captured as ADRs under
[docs/adr/](./docs/adr/). The ADR format we use is:

1. **Status:** Proposed / Accepted / Superseded.
2. **Context:** What problem is being solved, what constraints apply.
3. **Decision:** The chosen approach.
4. **Consequences:** Trade-offs we accept.
5. **Implementation status:** A living section listing what's built
   vs pending; updated each time the ADR lands code.

A change that does not have ADR coverage will be asked to add one
before being merged when its blast radius warrants it.

## Release cadence

See [CHANGELOG.md](./CHANGELOG.md) for the release timeline. Versioning
follows [Semantic Versioning 2.0.0](https://semver.org/):

- **MAJOR** — backwards-incompatible API or schema changes.
- **MINOR** — additive features; existing consumers keep working.
- **PATCH** — bug fixes, dependency bumps, documentation.

A release is a tagged commit on `main` plus a CHANGELOG entry. Tag
format: `v<MAJOR>.<MINOR>.<PATCH>`. We aim for **monthly** patch / minor
releases when there are user-visible changes, and major releases when
the consequences justify a coordinated migration.

## Conflicts of interest

If a Maintainer has a personal, employment, or financial interest in a
decision (e.g. their employer benefits from the choice), they record
the conflict in the PR description and recuse from approving the
change. Other Maintainers approve as normal.

## Suspension and removal

A Maintainer who has not been active for **six consecutive months** is
moved to *emeritus* status: keeps the name in MAINTAINERS, loses
merge rights, can be reinstated by opening a PR. Removal for CoC
violations follows the [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)
enforcement guidelines.

## Forking

If the project's direction stops serving you, forking is a legitimate
option and we will link the fork from the README if it is actively
maintained. opensource.guide notes that forks are a healthy outcome,
not a hostile one.

## Amending this document

Open a PR that edits this file. Per the "explicit consensus" rule
above, every active Maintainer must approve.
