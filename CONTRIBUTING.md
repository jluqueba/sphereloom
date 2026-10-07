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
- The PR closes exactly one issue with `Closes #N` in its description, and carries that issue's `area:` and type labels.
- The suite passes on **both** operating systems in the CI matrix.

The repository uses a single required status check named `ci-gate`. The CI design uses change detection so documentation-only PRs can skip heavy jobs while the required gate still reports success. The gate is an allow-list: it passes only when every job it depends on reported `success`, or `skipped` because the change did not touch that area. Any other result, including one GitHub adds in the future, fails it. PRs are squash-merged using the PR title.

### Keep pull requests small

A pull request covers one topic and at most about 400 lines of production code, plus its tests. Split it before opening it, not during review: large pull requests do not converge under automated review, because every fix adds new surface to review. A pull request must not change logging, the transport layer and the filesystem layer at once.

Before writing code, list the invariants the change must hold and the test that will prove each one. Decide these up front, because they are exactly where review finds defects:

- bounds on memory, time and size when handling untrusted input;
- the mapping from each status or error to a taxonomy code, and whether it is retryable;
- what happens after an ambiguous outcome — a capture the camera accepted is never retryable;
- what is written to disk, and how it is cleaned up on failure and cancellation.

A design decision that emerges during review lands in its own small documentation pull request, such as a new ADR or a spec amendment, rather than inside the feature pull request. Closing the feature pull request must never lose the decision.

### Run the suite on Linux before pushing

CI runs on Ubuntu and Windows. Developing on one of them alone has already sent a red build: a test asserted that a tab in a filename is refused, which is true on Windows and false on Linux, so it passed locally and failed in CI.

Run the suite under WSL Ubuntu, or any Linux with the project's Python, before pushing.

When a behaviour genuinely differs between platforms, do not encode one platform's answer in an assertion. Inject the failure instead of provoking it — for example, patch the filesystem call to raise — so the test asserts the guarantee rather than the quirk on every platform. Gate a test with `@pytest.mark.skipif(sys.platform == ...)` only when injection is impossible, as with creating a real symlink on Windows.

For every test written to catch a regression, reintroduce the defect locally and confirm the test fails before pushing. A test that cannot fail protects nothing.

### Issues, milestones and labels

Work is tracked in issues. Each planned pull request has an issue describing the behaviour to deliver and its acceptance criteria, and each pull request closes exactly one issue with `Closes #N`. If you want to work on something without an issue, open one first and say so in a comment, so nobody duplicates the work.

The **milestone goes on the issue, not on the pull request**. GitHub counts both issues and pull requests in a milestone, so setting it on both would count every task twice; the pull request is linked to its issue instead. A milestone's page therefore shows exactly how many tasks are done and how many remain, and release notes are assembled from the issues closed in it.

- Issues carry one or more `area:` labels so the changelog can be grouped by subsystem, a type label (`enhancement`, `bug`, `documentation`) and a size label (`size: S`, `size: M`, `size: L`). Pull requests carry the same area and type labels.
- Add `release` to the issue and the pull request that cut a version.
- If a task fits two milestones, it is doing two things and should be split.
- Issues are public: describe the behaviour, not file paths, and do not paste content from the encrypted internal documents.

Milestone **M2 is the first released version (0.1.0) and the MVP**: reaching it produces a tag, a GitHub Release, a `CHANGELOG.md` entry and a public announcement together, rather than separately.

### Automated review must settle before merging

Copilot code review is configured as a branch ruleset with `review_on_push`, so **every push to an open PR triggers a new review**. That makes the merge condition easy to get wrong:

> A PR is ready to merge when CI is green, the most recent **Copilot** review examined the current head commit, and that review contains **no unresolved correctness or security finding**.

Resolving the findings from one review and merging as soon as CI turns green is not enough. The push that fixed those findings starts another review, and that review can surface new ones — including problems introduced by the fix itself.

The sequence to follow:

1. Push the fixes.
2. Wait for CI **and** for the new review to be posted.
3. Read the new review, **both** its inline comments and its body, and triage every finding as described below. If any finding is a correctness or security issue, fix it and return to step 1.
4. When none remains, reply to and resolve any minor threads, then merge.

Do not widen the scope of a PR while it is under review. Allow at most **three review cycles**: if blocking findings remain after the third, stop, write down why the change is not converging, and split or narrow it instead of iterating further.

#### Triage findings before acting

Only two kinds of finding block a merge:

- **Correctness** — wrong behaviour on some input, a wrong or misleading error code, a test that cannot fail or exercises a different branch than it claims, or a CI or merge gate that can pass when it should not.
- **Security** — a leak of secrets or personal data, an escape from the path jail, unbounded work or memory on untrusted input, an off-origin request, or a missing authentication or confirmation check.

Everything else is **minor** and does not block: wording, docstring and comment typos, style, naming, consistency nits, and hardening against a condition no realistic input can trigger. Reply on the thread that it was judged minor under this rule and resolve it, without pushing a commit for it. A minor finding can be folded into a later change that touches the same code.

Reproduce a claimed defect before fixing it. A finding that does not reproduce is answered on its thread with the evidence, and resolved. When a defect is real, find every occurrence of its class before fixing it, and add an automated rule where one is possible.

This rule exists because chasing every finding on a large pull request does not converge. Each fix adds code, each new line is new review surface, and the reviewer revisits unchanged code in every cycle, so "zero findings" keeps moving. It relaxes only what blocks a merge. The checks before pushing stay mandatory for every change: the codebase rule tests, the suite on both Windows and Linux, an adversarial review of the diff, and a mutation check for any test written to catch a regression.

#### Findings appear in two places

Reading only the inline comments hides part of the review. This has happened in this repository: six medium findings survived several cycles because only the inline comments were being checked.

| Where | How to read it | What only it contains |
| --- | --- | --- |
| Inline threads | `gh api --paginate repos/jluqueba/sphereloom/pulls/<n>/comments`, filtered to the login `Copilot` | Findings anchored to changed lines |
| Review body | `gh pr view <n> --json reviews,headRefOid`, newest review whose author is `copilot-pull-request-reviewer`, field `body` | Severity counts, and **"Previously missed"** — findings in code that has not changed since the last review |

To read the latest Copilot review together with the commit it examined:

```powershell
gh pr view <n> --json reviews,headRefOid --jq '([.reviews[] | select(.author.login == "copilot-pull-request-reviewer")] | last) as $r | {head: .headRefOid, reviewed: $r.commit.oid, body: $r.body}'
```

If `reviewed` differs from `head`, the review for the latest push has not arrived yet.

Three traps worth knowing:

- **Do not take "the latest review" from an unfiltered, unpaginated list.** The REST endpoints return 30 items per page and include maintainer replies. On #5 the first page of reviews stopped at review 30 of 62, at a maintainer reply nine hours older than the latest Copilot review, so the procedure would have read a stale, irrelevant body. Filter by author and read every page, or use `gh pr view`, which fetches them all.
- **Do not filter findings by "created after my last push."** That filter cannot, by construction, show an older finding that is still outstanding — which is precisely what "previously missed" means. Use timestamps only to identify which cycle is the current one.
- **The reviewer has three different logins.** Verified against this repository: `copilot-pull-request-reviewer[bot]` in `/pulls/N/reviews`, `Copilot` in `/pulls/N/comments`, and `copilot-pull-request-reviewer` in GraphQL. A filter written for one silently returns nothing against another, so match the set exactly rather than with a wildcard. Thread resolution state (`isResolved`) is available only through GraphQL.

Treat anything unreadable as not clean: a check that cannot read its input must never report success.

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
