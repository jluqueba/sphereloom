# ADR-0009: CI gate, versioning and release process

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: ADR-0001, ADR-0006, ADR-0007

## Context

SphereLoom is a public repository that wants outside contributions and publishes a package
to PyPI. It therefore needs:

- a branch-protection story that is simple to configure and does not break on
  documentation-only changes;
- deterministic, reproducible checks that a contributor can run locally and get the same
  answer as CI;
- a dependency-pinning policy that keeps CI stable without freezing users out of patch
  updates;
- a versioning and changelog discipline, because the moment a package is on PyPI its users
  need to know what changed and whether it breaks them.

A prior repository by the same maintainer validated a pattern worth reusing: a single
required status check fed by a change-detection job, plus local/CI command parity. Its gaps
— no changelog, no releases, no issue/PR templates, no `CODEOWNERS` — are deliberately not
reproduced here, since SphereLoom ships to PyPI and invites contributors.

## Decision

### CI gate

1. **One required status check named `ci-gate`.** Branch protection requires exactly this
   check, so the required-check configuration never has to change when jobs are added,
   renamed or split.
2. A **change-detection job** using `dorny/paths-filter` classifies each pull request
   (Python sources, tests, docs, workflows, dependencies). Heavy jobs are conditioned on
   those filters, so a documentation-only pull request skips the test matrix — but
   `ci-gate` still runs and still reports success, so the branch protection is never
   left waiting on a skipped job.
3. **Jobs:** lint and format check (`ruff`), type check (`mypy --strict`), tests
   (`pytest` on Python 3.12 and 3.13, Linux and Windows), markdown lint, and a
   link/consistency check for documentation. From Milestone 2, the `scripted`-mode example
   agent run against the fake camera is added (ADR-0008).
4. **Hardware tests never run in CI** (ADR-0007). The `hardware` marker is deselected by
   default.
5. **Workflow hygiene:** workflow-level `permissions: {}` with the minimum widened per job;
   `concurrency` groups with `cancel-in-progress` on pull-request workflows;
   `timeout-minutes` on every job; actions pinned by major version at minimum, by commit SHA
   for third-party actions.

### Local/CI parity

6. A `Makefile` (Unix/macOS) and `check.ps1` (Windows) run **the exact same commands** CI
   runs, in the same order: `ruff format --check`, `ruff check`, `mypy --strict`,
   `pytest`. "It passed locally" must mean the same thing as "it passed in CI".
7. All commands go through `uv` so the environment is identical everywhere, and the
   lockfile is committed.

### Dependency pinning policy

8. **Development tools are pinned exactly (`==`)** — `ruff`, `mypy`, `pytest` and friends —
   because a silent minor bump in a linter turns an unrelated pull request red and wastes
   contributor time. Upgrades are deliberate, reviewed commits.
9. **Runtime dependencies are range-pinned** (compatible-release ranges) so that users
   installing SphereLoom from PyPI can receive patch and minor fixes without waiting for a
   release. The rationale is written as a comment next to the pins so nobody "tidies" it
   away.
10. Dependabot runs weekly for `pip` and `github-actions`, with grouped minor/patch updates
    and Conventional Commit prefixes.
11. **No GPL-licensed dependency may be added** (ADR-0006); the pull-request checklist and,
    before the first release, an automated licence check enforce this.

### Commits, branches and merges

12. **Conventional Commits** for commit messages and, crucially, for **pull-request
    titles** — because the merge strategy is **squash merge using the pull-request title**,
    so the PR title is what lands in history and what drives the changelog.
13. Branch names: `feat/`, `fix/`, `docs/`, `chore/`, `refactor/`, `test/` plus a short
    slug.
14. Linear history on `main`: no merge commits, no force-pushes, no branch deletion.

### Versioning and releases

15. **Semantic versioning.** Until 1.0.0, breaking changes may land in minor releases, and
    that is stated explicitly in the README and `SECURITY.md`.
16. **`CHANGELOG.md` in Keep a Changelog format is updated in the same pull request as the
    change.** A user-visible change without a changelog entry is an incomplete pull request.
17. A release is: finalise the `Unreleased` section into a version heading, tag `vX.Y.Z`,
    build with `uv`, publish to PyPI via a trusted-publishing workflow, and create a GitHub
    release whose notes are the changelog section. Release notes record which tier of tests
    ran, including whether the hardware tier was exercised and on which firmware.
18. The MCP **tool contract is part of the public API** for versioning purposes: renaming a
    tool, removing a field or tightening validation is a breaking change.

## Consequences

### Positive

- Branch protection is configured once and never touched again.
- Documentation contributors get a green check in under a minute instead of waiting on a
  full matrix.
- Contributors can reproduce CI exactly, which removes the most common source of
  review friction.
- Users get a trustworthy changelog and predictable upgrades, which an MCP server
  controlling hardware badly needs.

### Negative / costs

- The change-detection indirection is one more moving part to understand, and a misconfigured
  filter can skip a job that should have run. Filters are reviewed like code.
- Exactly pinned dev tools require periodic deliberate upgrade commits.
- Changelog discipline adds friction to every pull request. Accepted.

### Neutral

- Squash merging loses intermediate commit granularity; acceptable given Conventional Commit
  PR titles carry the meaning.

## Alternatives considered

### Requiring every individual job as a status check

Rejected: the required-check list must then be edited whenever the matrix changes, and
conditionally skipped jobs leave pull requests permanently pending.

### Running the full matrix on every pull request regardless of paths

Simple and wasteful; it makes documentation contributions slow and burns runner minutes for
no signal.

### Fully pinning runtime dependencies for users

Rejected: it prevents downstream users from taking security patches and causes resolver
conflicts in their environments. The lockfile already gives developers and CI
reproducibility.

### Calendar versioning or no versioning

Rejected: a published package that controls hardware needs explicit compatibility signals.

### Automated release-please style changelog generation

Attractive, and compatible with Conventional Commits. Deferred rather than rejected:
hand-written entries are better prose while the project is small. Revisit after 1.0.0.

## Follow-ups

- Implement the workflows, `Makefile` and `check.ps1` in Milestone 0.
- Configure PyPI trusted publishing before the Milestone 2 release.
- Apply the branch-protection settings described in `docs/repo-settings-proposal.md` once
  the maintainer approves them.
