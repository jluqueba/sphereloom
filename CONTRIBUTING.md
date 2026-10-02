# Contributing to SphereLoom

## Project overview and SDD workflow

SphereLoom is an MCP server that lets AI agents control Insta360 cameras, starting with the X5, and process 360 media locally. The project follows Spec-Driven Development (SDD): read the relevant spec before coding.

SDD artifacts live in these locations:

- Envisioning: `docs/envisioning/`
- Features: `docs/features/<feature>/spec.md`, `plan.md`, and `tasks.md`
- Architecture decisions: `docs/adr/NNNN-*.md`

Do not implement behavior that contradicts an accepted spec, plan, task list, or ADR.

Work in progress is tracked in [docs/features/osc-camera-control/tasks.md](docs/features/osc-camera-control/tasks.md), which lists the Milestone 0 and Milestone 1 tasks with their dependencies.

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
- No vendor SDK binaries were committed.
- The PR title follows Conventional Commits.

The repository uses a single required status check named `ci-gate`. The CI design uses change detection so documentation-only PRs can skip heavy jobs while the required gate still reports success. PRs are squash-merged using the PR title.

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
