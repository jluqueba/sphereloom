# Repository settings proposal

- Status: **proposal — not applied**
- Date: 2026-10-02
- Owner: @jluqueba
- Related: [ADR-0009](adr/0009-ci-gate-versioning-and-release-process.md),
  [SECURITY.md](../SECURITY.md)

> **Nothing in this document has been applied.** No remote repository setting, ruleset,
> branch protection rule, security feature or topic has been changed. This is a written
> proposal awaiting the maintainer's explicit approval. Each item below is actionable by
> hand in the GitHub UI or by `gh` once approved.

## Why these settings

SphereLoom is public, invites contributions, and publishes a package that controls physical
hardware. The settings below aim at three things: a stable required-check configuration
that never needs editing (ADR-0009), a history on `main` that can be trusted, and the
baseline security features GitHub offers for free on public repositories.

## 1. Rulesets on the default branch

Target: `~DEFAULT_BRANCH` (`main`).

| Setting | Proposed value | Rationale |
| --- | --- | --- |
| Restrict deletions | Enabled | `main` should not be deletable |
| Block force pushes (non-fast-forward) | Enabled | History on `main` is append-only |
| Require a pull request before merging | Enabled | No direct pushes, including by the maintainer |
| Required approving reviews | **0** | Single-maintainer project; the gate is CI and self-review, not a rubber stamp. Raise to 1 when a second maintainer joins |
| Dismiss stale reviews on push | Enabled | An approval must refer to the code that merges |
| Require status checks to pass | Enabled, **strict** (branch must be up to date) | Prevents semantic merge breakage |
| Required status check | **`ci-gate`** only | One stable name; jobs can change freely behind it (ADR-0009) |
| Require conversation resolution | Enabled | Review comments are not silently dropped |
| Require linear history | Enabled | Matches the squash-merge policy |
| Code scanning results gate | Alerts at **high or higher** block merge | Keeps CodeQL actionable rather than decorative |

## 2. Copilot code review ruleset

A separate ruleset so it can be toggled without touching branch protection.

| Setting | Proposed value |
| --- | --- |
| Automatic Copilot review on push | Enabled |
| Draft pull requests | Excluded |

Rationale: useful first-pass review on a single-maintainer project; excluding drafts avoids
reviewing work in progress.

## 3. Merge strategy

| Setting | Proposed value | Rationale |
| --- | --- | --- |
| Allow squash merging | **Enabled**, default | One commit per pull request |
| Squash commit message | **Pull-request title and description** | Conventional Commit titles land in history and drive the changelog (ADR-0009) |
| Allow merge commits | Disabled | Linear history |
| Allow rebase merging | Disabled | One path, no ambiguity |
| Automatically delete head branches | Enabled | Housekeeping |
| Allow auto-merge | Enabled | Lets a green pull request land without babysitting |

## 4. Security features

| Feature | Proposed state | Rationale |
| --- | --- | --- |
| Private vulnerability reporting | **Enabled** | Required by [SECURITY.md](../SECURITY.md); gives reporters a non-public channel |
| CodeQL (default setup) | Enabled for Python | Free on public repositories; feeds the code-scanning merge gate |
| Dependabot alerts | Enabled | |
| Dependabot security updates | Enabled | Automatic patches for known vulnerabilities |
| Dependabot version updates | Enabled via the committed `.github/dependabot.yml` | Weekly, grouped, Conventional Commit prefixes |
| Secret scanning | Enabled | |
| Secret scanning push protection | **Enabled** | The project handles bearer tokens and model-provider API keys; this stops the common accident |

## 5. Actions permissions

| Setting | Proposed value | Rationale |
| --- | --- | --- |
| Workflow permissions | Read-only by default | Workflows widen explicitly per job (ADR-0009) |
| Allow Actions to create and approve pull requests | Disabled | Not needed |
| Allowed actions | GitHub-owned plus explicitly listed third parties (`dorny/paths-filter`) | Smallest viable supply-chain surface; third-party actions pinned by commit SHA |
| Fork pull-request workflows | Require approval for first-time contributors | Standard protection against drive-by workflow abuse |

## 6. Repository metadata

| Item | Proposed value |
| --- | --- |
| Description | "MCP server for controlling 360° cameras and processing 360° media locally. Independent project; not affiliated with or endorsed by Arashi Vision Inc." |
| Topics | `mcp`, `model-context-protocol`, `360-camera`, `insta360`, `python`, `agent-tools`, `osc`, `open-spherical-camera` |
| Features | Issues ✅, Discussions ✅ (Q&A for support), Projects ✅, Wiki ❌ (documentation lives in `docs/`) |
| Social preview | None until a logo exists; **no vendor logo or product photography** (ADR-0006) |

Note on topics: `insta360` is a descriptive discoverability tag, consistent with the
descriptive-use posture in ADR-0006. If the maintainer prefers maximum caution, drop it —
the remaining topics still cover search.

## 7. Releases and publishing

| Item | Proposed value |
| --- | --- |
| PyPI publishing | Trusted publishing (OIDC), no long-lived token stored in the repository |
| Environment `pypi` | Protected, required reviewer: the maintainer |
| Tags | `v*` release tags protected against deletion |

## 8. Deliberately not proposed

- **Required approving reviews > 0** while there is one maintainer — it would simply block
  all work.
- **Self-hosted runners** — a camera attached to a public repository's CI is both unreliable
  and a security problem (ADR-0007).
- **Signed-commit enforcement** — valuable, but it raises the contribution barrier more than
  it buys at this stage. Revisit after 1.0.0.
- **Any change to the licence or the repository name.**

## Approval checklist

- [ ] Section 1 — default-branch ruleset
- [ ] Section 2 — Copilot code review ruleset
- [ ] Section 3 — merge strategy
- [ ] Section 4 — security features
- [ ] Section 5 — Actions permissions
- [ ] Section 6 — metadata and topics
- [ ] Section 7 — releases and trusted publishing

Apply only the sections that are ticked, and record the date applied here once done.
