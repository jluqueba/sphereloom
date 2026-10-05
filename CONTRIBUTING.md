# Contributing to SphereLoom

## Project overview and SDD workflow

SphereLoom is an MCP server that lets AI agents control Insta360 cameras, starting with the X5, and process 360 media locally. The project follows Spec-Driven Development (SDD): read the relevant spec before coding.

SDD artifacts live under `docs/internal/` and are **encrypted at rest** with [git-age](https://github.com/prskr/git-age). Without the private key you will see ciphertext, which is expected and does not block contribution:

- Envisioning: `docs/internal/envisioning/`
- Features: `docs/internal/features/<feature>/spec.md`, `plan.md`, and `tasks.md`
- Architecture decisions: `docs/internal/adr/NNNN-*.md`

Everything you need to build, test and extend SphereLoom is public: the [developer guide](docs/DEVELOPER_GUIDE.md), this file, and the path-scoped instruction files under `.github/instructions/`. If a change needs design context you cannot read, open an issue and ask — the relevant reasoning will be summarised there or in the pull request.

Do not implement behavior that contradicts an accepted spec, plan, task list, or ADR.

Maintainers with the private key need no extra steps: the git filter decrypts on checkout and encrypts on commit. Never commit a decrypted copy of an internal document to a path outside `docs/internal/`.

## Environment setup with uv

SphereLoom supports Python 3.12 and 3.13 and uses `uv` for package and virtual-environment management.

Typical setup after the Milestone 0 project files land:

```powershell
uv sync
```

Use the project-managed environment for all local checks and examples.

## Contribution types

Contributions are welcome for:

- Features described by approved specs and tasks
- Bug fixes with tests
- Documentation improvements
- Test coverage and fake-camera behavior
- Tooling and CI improvements
- Example agent improvements

For substantial changes, open or update the SDD artifact first.

## Branch naming

Use one of these prefixes with a short slug:

- `feat/<short-slug>`
- `fix/<short-slug>`
- `docs/<short-slug>`
- `chore/<short-slug>`
- `refactor/<short-slug>`
- `test/<short-slug>`

## Conventional Commits

Use Conventional Commits for commits and PR titles, for example:

```text
feat: add camera status tool
fix: validate workspace paths before download
docs: clarify OSC capability limits
chore: update lint configuration
```

PRs are squash-merged using the PR title, so the PR title must also follow this format.

## Local checks

Milestone 0 introduces local parity commands:

```powershell
make check
.\check.ps1
```

These commands run the same core checks as CI: `ruff`, `mypy` in strict mode, and `pytest`.

## Pull requests and CI

Before opening a PR, make sure:

- The relevant SDD artifacts are linked.
- Ruff, mypy strict, and pytest pass locally when available.
- Documentation and `CHANGELOG.md` are updated for user-visible changes.
- No GPL-licensed dependency was added.
- No Insta360 SDK binaries were committed.
- The PR title follows Conventional Commits.
- A **milestone** is set, and at least one `area:` label plus a type label.
- The suite passes on **both** operating systems in the CI matrix.

### Run the suite on Linux before pushing

CI runs on Ubuntu and Windows. Developing on one of them alone has already sent a red build: a test asserted that a tab in a filename is refused, which is true on Windows and false on Linux, so it passed locally and failed in CI.

Run the suite under WSL Ubuntu, or any Linux with the project's Python, before pushing.

When a behaviour genuinely differs between platforms, do not encode one platform's answer in an assertion. Inject the failure instead of provoking it, so the test asserts the guarantee rather than the quirk: `test_a_refusal_from_the_operating_system_becomes_a_path_jail_error` patches `mkdir`, `mkstemp` and `replace` to raise, which tests all three translations on every platform. Gate a test with `@pytest.mark.skipif(sys.platform == ...)` only when injection is impossible, as with creating a real symlink on Windows.

The repository uses a single required status check named `ci-gate`. The CI design uses change detection so documentation-only PRs can skip heavy jobs while the required gate still reports success. PRs are squash-merged using the PR title.

### Milestones and labels

The milestone is what makes a release reconstructible. Release notes, the `CHANGELOG.md` section and the announcement are all assembled from the pull requests in a milestone, so a PR without one disappears from the record.

- Use the milestone titles from the roadmap; each one names the version it targets.
- Add one or more `area:` labels so the changelog can be grouped by subsystem.
- Add `release` to the PR that cuts a version.
- If a PR fits two milestones, it is doing two things and should be split.

Milestone **M2 is the first released version and the MVP**: reaching it produces a tag, a GitHub Release, a `CHANGELOG.md` entry and a public announcement together, rather than separately.

### Automated review must settle before merging

Copilot code review is configured as a branch ruleset with `review_on_push`, so **every push to an open PR triggers a new review**. That makes the merge condition easy to get wrong:

> A PR is ready to merge only when the **most recent** review cycle produced **no new findings**.

Resolving the findings from one review and merging as soon as CI turns green is not enough. The push that fixed those findings starts another review, and that review can surface new ones — including problems introduced by the fix itself.

The sequence to follow:

1. Push the fixes.
2. Wait for CI **and** for the new review to be posted.
3. Read the new review, **both** its inline comments and its body. If either reports findings, fix them and return to step 1.
4. Only when a full cycle comes back clean, resolve the threads and merge.

#### Findings appear in two places

Reading only the inline comments hides part of the review. This has happened in this repository: six medium findings survived several cycles because only the inline comments were being checked.

| Where | How to read it | What only it contains |
| --- | --- | --- |
| Inline threads | `gh api repos/jluqueba/sphereloom/pulls/<n>/comments` | Findings anchored to changed lines |
| Review body | `gh api repos/jluqueba/sphereloom/pulls/<n>/reviews`, field `body` of the latest review | Severity counts, and **"Previously missed"** — findings in code that has not changed since the last review |

Two traps worth knowing:

- **Do not filter findings by "created after my last push."** That filter cannot, by construction, show an older finding that is still outstanding — which is precisely what "previously missed" means. Use timestamps only to identify which cycle is the current one.
- **The reviewer has three different logins.** Verified against this repository: `copilot-pull-request-reviewer[bot]` in `/pulls/N/reviews`, `Copilot` in `/pulls/N/comments`, and `copilot-pull-request-reviewer` in GraphQL. A filter written for one silently returns nothing against another, so match the set exactly rather than with a wildcard. Thread resolution state (`isResolved`) is available only through GraphQL.

Rather than doing this by hand, check both places with `gh api`, matching the reviewer logins exactly and treating anything unreadable as not clean.

Note that automated review cannot read `docs/internal/**`, which is encrypted. Changes there are reviewed by a maintainer with the key.

## Code standards

Follow the repository instructions in `.github/instructions/`:

- Python: `.github/instructions/python.instructions.md`
- MCP tools: `.github/instructions/mcp-tools.instructions.md`
- Tests: `.github/instructions/testing.instructions.md`
- Documentation: `.github/instructions/docs.instructions.md`

Use Python 3.12+ syntax, full type annotations, pydantic models at boundaries, `httpx.AsyncClient` with explicit timeouts, structured logging with redaction, and the ports-and-adapters architecture.

## Testing without a camera

Most tests must run without hardware. Use the fake OSC server as the default backend for unit and component tests. Real-camera tests are opt-in, marked with `@pytest.mark.hardware`, enabled only by an environment variable, and never required in CI.

## Legal rules for contributors

By contributing, you agree that your contributions are provided under the MIT license.

Project-wide legal rules:

- Do not add GPL-licensed dependencies.
- Never commit Insta360 SDK binaries, headers, samples, archives, or redistributable packages.
- Never copy code from Insta360Develop repositories; repositories without a LICENSE file are not reusable.
- Keep vendor SDKs local to each user and referenced only through environment variables.
- Do not imply vendor endorsement, partnership, certification, or sponsorship.
- Use `Insta360` only for descriptive compatibility statements.

Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent, unaffiliated project and is neither endorsed by nor associated with Arashi Vision Inc.

## Code of Conduct

Please follow the [Code of Conduct](CODE_OF_CONDUCT.md) in all project spaces.
