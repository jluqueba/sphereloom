# SphereLoom repository instructions

- SphereLoom is an MCP server for controlling Insta360 cameras and processing 360 media locally.
- The first supported camera target is the Insta360 X5.
- This repository is public, MIT licensed, and maintained by `@jluqueba`.
- All code, comments, commit messages, issue text, PR text, and documentation must be written in English.
- Follow Spec-Driven Development (SDD) for all non-trivial changes.
- Read the relevant specification before coding.
- Keep envisioning material in `docs/internal/envisioning/`.
- Keep feature artifacts in `docs/internal/features/<feature>/spec.md`, `plan.md`, and `tasks.md`.
- Keep architecture decisions in `docs/internal/adr/NNNN-*.md`.
- ADRs use a MADR-style structure: Status, Context, Decision, Consequences, Alternatives considered.
- Do not create implementation that contradicts accepted specs, plans, tasks, or ADRs.
- If an instruction and an approved SDD artifact conflict, stop and align the artifacts before implementation.
- Use Conventional Commits for commit messages and PR titles.
- PRs are squash-merged using the PR title.
- Keep changes small, reviewable, and traceable to a spec, task, issue, or ADR.
- Update the CHANGELOG for user-visible changes.
- Follow semantic versioning for releases.
- Use Keep a Changelog formatting in `CHANGELOG.md`.

## Approved stack

- Use Python 3.12 and 3.13 as supported runtimes.
- Use `uv` for package and virtual-environment management.
- Use the MCP Python SDK for the server surface. The server class is `MCPServer` (`from mcp.server.mcpserver import MCPServer`); it was called `FastMCP` before SDK 2.0.
- Use `httpx` for OSC HTTP communication.
- Use `pydantic` for configuration, MCP tool inputs, and MCP tool outputs.
- Use `ruff` as the only formatter and linter.
- Use `mypy` in strict mode.
- Use `pytest` for tests.
- Pin development tools exactly with `==`.
- Range-pin runtime dependencies where compatible with the public support policy.
- Keep the exact-pin versus range-pin rationale visible in dependency metadata comments.
- Maintain local and CI parity through `Makefile` and `check.ps1` when those files exist.
- Keep `.env.example` as living configuration documentation.
- Leave secret values blank in examples.

## Architecture

- Keep a ports-and-adapters architecture.
- Domain and application code define behavior through protocols and models.
- Backend-specific details belong in adapter packages only.
- The camera abstraction is `CameraPort`.
- The OSC backend is `OscCameraAdapter`.
- The USB backend is `UsbCameraAdapter`.
- The media abstraction is `MediaPort`.
- The Media SDK backend is `MediaSdkAdapter`.
- Milestone 1 is OSC Wi-Fi only.
- USB Camera SDK support is planned for a later milestone.
- Media SDK support is planned for a later milestone.
- The optional `sphereloom-assistant` entry point is a deferred milestone after M2 and is not part of M1 or M2 delivery.
- C++ vendor SDKs must run in sidecar processes.
- Never load C++ vendor SDKs in-process with the MCP server.
- The MCP tool surface must remain coherent across backends.
- Do not create one unrelated tool namespace per backend.
- Use a capability registry to describe backend and camera-model support.
- Check capabilities before dispatching to an adapter.
- Unsupported operations must return a structured error instead of failing opaquely.
- The unsupported error shape is `{code: "unsupported", backend, capability, status, reason, docs_url}`, where `status` is `not_yet_available` or `unsupported_by_vendor`. It never names a milestone (ADR-0016).
- Do not claim a capability unless the active backend can actually provide it.
- Do not fake a capability to make a demo look complete.
- Keep backend selection explicit and observable in diagnostics.

## Agent framework boundary

- Do not add an agent framework or LLM orchestration library to the MCP server core.
- No module under `src/sphereloom/` may import `agent_framework` or any other agent or LLM library.
- The server must start without model credentials and must not make inference calls while serving a tool.
- Keep the tool surface deterministic, auditable and framework-agnostic.
- `agent-framework` (MIT, 1.19.0, Python 3.10-3.14) is an optional or dev dependency only.
- It is used by `examples/agent/` from Milestone 2, and by the deferred `sphereloom[assistant]` extra if that milestone is ever scheduled.
- `agent-framework-devui` is a beta package: local manual use only, never a CI dependency.
- Never use MCP server-initiated sampling or `sampling_callback`; it is deprecated.
- Client-side `approval_mode` and `allowed_tools` are defense in depth and never replace the server-side confirmation token.
- Never auto-answer a confirmation token on behalf of a human.
- Do not implement the job manager on a workflow engine; the in-house engine is the decision for Milestone 1.
- Do not implement `sphereloom-assistant` yet; it is a deferred milestone after M2, documented in ADR-0012.

## Approaches that are rejected

- Do not automate the Insta360 Studio desktop GUI, through Microsoft UI Automation or any other means.
- Do not use screen scraping, template matching or synthetic input injection to obtain camera or media capabilities.
- Do not add trimming, cutting, merging or timeline editing features; the vendor does not document them.
- Do not circumvent the vendor's SDK approval path; it is free and takes about three business days.
- See `docs/internal/adr/0013-desktop-gui-automation-rejected.md` before proposing any desktop automation idea.

## Camera facts

- The OSC camera is a Wi-Fi access point at fixed IP `192.168.42.1`.
- The host must join the camera hotspot before OSC tools can work.
- OSC has no application-level authentication.
- OSC requires the static `X-XSRF-Protected: 1` header.
- OSC supports `info`, `state`, `takePicture`, `startCapture`, `stopCapture`, `listFiles`, `delete`, `getOptions`, and `setOptions`.
- OSC does not support live preview or streaming.
- OSC does not support exposure parameter adjustment.
- In-camera stitching covers photos only.
- Video stitching requires the Media SDK.
- Media and stitching tools may be declared before their milestone only if they return the structured unsupported error.

## MCP tools and jobs

- MCP tools must be named in `snake_case`.
- Prefer `<noun>_<verb>` grouping such as `camera_status`, `camera_take_photo`, `files_list`, `files_download`, and `jobs_get`.
- Every MCP tool must define pydantic input and output models.
- Every MCP tool description must be written for an LLM consumer.
- MCP responses must not carry large binary payloads.
- Return workspace-relative paths and metadata for files.
- Long operations such as downloads and exports are stateful jobs.
- Tools that start long operations return a `job_id`.
- Use `jobs_list`, `jobs_get`, and `jobs_cancel` to manage jobs.
- Bound job concurrency through configuration.
- Promote work that can exceed a few seconds into a job or enforce an explicit timeout.
- Jobs must report stable states, progress where possible, and structured errors.
- Cancellation must be best-effort and explicit.

## Security defaults

- Default transport is local stdio.
- HTTP transport is opt-in only.
- HTTP transport must bind to loopback by default.
- HTTP transport must require a bearer token.
- Never expose HTTP transport on a non-loopback interface by default.
- Confine all output paths to the configured workspace directory.
- Enforce a path jail before reading or writing files.
- Never trust client-provided paths without normalization and jail checks.
- Destructive operations such as delete and format require a two-step confirmation token.
- Confirmation tokens must expire.
- Logs must redact SSIDs, credentials, tokens, and personal file paths.
- Do not log raw bearer tokens or SDK paths.
- Prefer structured logging over free-form strings.
- Do not use `print` for server logging.
- Treat MCP tools as real-world device control, not harmless function calls.

## Legal and licensing

- The project license is MIT.
- Contributions are accepted under the MIT license unless explicitly documented otherwise.
- GPL-licensed dependencies are forbidden project-wide.
- Do not add GPL, LGPL-with-unacceptable-obligations, or license-unclear dependencies without maintainer approval.
- SphereLoom must never redistribute Insta360 SDK binaries.
- Users must apply for vendor SDKs themselves.
- Users point SphereLoom to local SDK copies through environment variables.
- Never commit vendor SDK binaries, headers, samples, archives, or generated redistributable packages.
- Never copy code from Insta360Develop repositories.
- Treat Insta360Develop repositories with no LICENSE file as not reusable.
- Do not paste vendor SDK source into issues, tests, docs, or examples.
- Keep compatibility statements descriptive.
- `Insta360` is a trademark of Arashi Vision Inc.
- SphereLoom is independent, unaffiliated, and unendorsed.
- Do not imply vendor endorsement, partnership, certification, or sponsorship.

## Testing

- Unit and component tests must run without real hardware.
- Use a fake OSC camera HTTP stub as the default backend in tests.
- Real-camera tests must be optional and marker-gated.
- Real-camera tests require an explicit environment variable.
- Real-camera tests must not run in CI by default.
- Unit and component tests must not access the external network.
- Inject clocks and ID factories for deterministic tests.
- Every error taxonomy code must have at least one test.
- Include tests for capability checks and unsupported responses.
- Include tests for path jail enforcement and redaction behavior when implementing those features.
- Inject time, sleeping and failures through seams the production code already defines; never synchronise a test on wall-clock time.
- Never assert a platform's own behaviour. CI runs Ubuntu and Windows, and a filename a tab makes illegal on one is legal on the other. Inject the failure instead of provoking it, so the test asserts the guarantee rather than the quirk; gate with `skipif` only when injection is impossible.
- Run the suite on Linux as well as Windows before pushing; CI runs both.
- Do not prove that work is bounded with a stopwatch. On a shared CI runner no threshold reliably separates the fixed cost from the regressed one, so the test either flakes or passes the regression. Observe the work instead, by counting what was read or done through an injected seam.
- For every test written to catch a regression, reintroduce the defect and confirm the test fails (a mutation check) before pushing.

## Documentation

- Documentation must be honest about available capabilities.
- Public documentation and tool output describe the current state only, in three states: available, not yet available, and not supported by the vendor API. Never mention milestones, dates or a roadmap there (ADR-0016); milestones are internal planning, and new capabilities are announced in the CHANGELOG and GitHub Releases.
- Do not promise live preview, streaming, OSC exposure control, or OSC video stitching.
- Use relative links inside the repository.
- Keep markdown markdownlint-clean.
- Use sentence-case headings.
- Use fenced code blocks with language tags.
- Include the trademark disclaimer when Insta360 is mentioned prominently.

## GitHub and CI conventions

- Create or change GitHub Actions workflows only when a task explicitly calls for it, such as task M0-05 in `docs/internal/features/osc-camera-control/tasks.md`.
- CI reports everything it runs through one status check, `ci-gate`. The repository requires it and a second check, `copilot-review-gate`, which passes only once Copilot has reviewed the head commit (ADR-0019).
- The gate is fed by change detection using `dorny/paths-filter`.
- Documentation-only PRs may skip heavy jobs while still reporting `ci-gate` success.
- Workflow permissions should default to `{}` and be narrowed per job.
- Workflows should use concurrency with `cancel-in-progress`.
- Workflows should set `timeout-minutes`.
- Keep Dependabot updates grouped and conventional-commit friendly.

## Pull request scope

- Keep each pull request to one topic and at most about 400 lines of production code, plus its tests. Split before opening, not during review.
- A pull request must not change logging, the transport layer and the filesystem layer at the same time.
- Before writing code, list the invariants the change must hold and the test that proves each one: bounds on memory, time and size against untrusted input; the mapping from each status or error to a taxonomy code and whether it is retryable; what happens after an ambiguous outcome (an accepted capture is never retryable); and what is written to disk and how it is cleaned up.
- A design decision that emerges during review lands in its own small documentation pull request (an ADR, a spec or plan amendment), not inside the feature pull request. Closing the feature pull request must never lose the decision.

## Work tracking: issues, milestones and the project board

Issues are the unit of work. Milestones group issues into phases and releases. A private GitHub Project (`jluqueba` project 2, "SphereLoom") holds status and priority. Each piece of information lives in exactly one place, so the milestone page and the board always agree.

- **One issue per pull request.** Every planned pull request has an issue, and every pull request closes exactly one issue with a closing keyword (`Closes #N`) in its description. Work that has no issue gets one before it starts, including work discovered during another task.
- **The milestone goes on the issue, never on the pull request.** GitHub counts issues and pull requests in a milestone, so a milestone on both would count every task twice. The pull request is linked to the issue instead.
- **Labels**: issues carry one or more `area: *` labels, a type label (`enhancement`, `bug`, `documentation`) and a size label (`size: S`, `size: M`, `size: L`). Pull requests carry the same area and type labels. Add `release` to the issue and the pull request that cut a version.
- **Board status**: `Todo` → `In Progress` when work starts (assign the issue at the same time) → `In review` when the pull request opens → `Done` when the pull request merges and closes the issue. Set each transition explicitly and verify it after merging, even where a project workflow is expected to do it.
- **Priority**: the `Priority` field (`P1`, `P2`, `P3`) is set only to override the planned order. An empty priority means "in plan order", which is the issue number order within a milestone. Pick the next task as the highest-priority `Todo` issue whose dependencies are closed, in the milestone in progress.
- **Progress report**: after every merge, report the milestone's open and closed counts.
- **Rolling-wave detail**: before work on a milestone starts, detail it into pull-request-sized issues, each with acceptance criteria taken from its specification, a size and its dependencies, and add them to the board. A milestone whose specification does not exist yet keeps a few high-level issues, the first of which is to write that specification.
- **Issues are public.** Write them in English, describe behaviour rather than file paths, and summarise design context instead of copying encrypted internal documents (ADR-0014). Describe what a capability does, not when it will ship.
- A task that spans two milestones is too large; split it.

Milestones double as release groupings. M2 is the first released version (0.1.0) and is the MVP: it is the point at which a tag, a GitHub Release, a CHANGELOG entry and a LinkedIn post are produced together, following `docs/internal/process/release-communication.md`. Release notes are assembled from the issues closed in the milestone, each linked to the pull request that delivered it.

## Pull request completion

- Copilot code review runs on every push to an open pull request (`review_on_push`).
- Read every review in full, then **triage each finding** before acting on it. Only correctness and security findings block a merge; see "Triage findings before acting" below.
- A pull request is ready to merge when CI is green, the most recent **Copilot** review examined the current head commit, and that review, including its "Previously missed" section, contains **no unresolved correctness or security finding**.
- The required `copilot-review-gate` check enforces that the review of the head commit exists, not what it found. Still read its body and threads, and triage every finding. If Copilot never reviews a push, request a review again, and rerun the check if the submitted review does not start it.
- A fixing push starts a new review, which may surface new problems, including ones the fix introduced. Wait for it and triage it the same way before merging.
- Do not widen the scope of a pull request while it is under review.
- Allow at most three review cycles per pull request. If blocking findings remain after the third, stop, explain why the change is not converging, and propose splitting or narrowing it instead of iterating further.

### Triage findings before acting

Fix a finding before merging only if it is one of these:

- **Correctness**: wrong behaviour on some input, a wrong or misleading error code, a test that cannot fail or exercises a different branch than it claims, a CI or merge gate that can pass when it should not.
- **Security**: a leak of secrets or personal data, an escape from the path jail, unbounded work or memory on untrusted input, an off-origin request, a missing authentication or confirmation check.

Everything else is **minor** and does not block: wording, docstring and comment typos, style, naming, consistency nits, and hardening against a condition no realistic input can trigger. Reply on the thread that it was judged minor under this rule, resolve it, and do not push a commit for it. A minor finding may be folded into a later change that touches the same code.

Verify before fixing. Reproduce a claimed defect first; a finding that does not reproduce is answered on its thread with the evidence and resolved, not fixed on faith. When a defect is real, find every occurrence of its class before fixing, and add an automated rule where one is possible.

This rule relaxes what blocks a merge, not what is done before pushing. The local safeguards stay mandatory for every change: the codebase rule tests, the suite on both Windows and Linux, an adversarial review of the diff, and mutation checks for any new test written to catch a regression.

### Read the review body, not only the inline comments

Findings are reported in two different places, and reading only one of them hides the rest. This has happened: six medium findings survived several cycles because only inline comments were being checked.

- **Inline threads** carry the findings attached to changed lines: `gh api --paginate repos/<owner>/<repo>/pulls/<n>/comments`, filtered to the login `Copilot`.
- **The review body** carries the overview and two sections that appear nowhere else: the **severity counts** (`Findings: 1 High`), and **"Previously missed (n)"**, which lists findings in code that has not changed since the last review. Read the newest review *by Copilot*, together with the commit it examined: `gh pr view <n> --json reviews,headRefOid --jq '([.reviews[] | select(.author.login == "copilot-pull-request-reviewer")] | last) as $r | {head: .headRefOid, reviewed: $r.commit.oid, body: $r.body}'`. `gh pr view` fetches every review, whereas the REST endpoints return only the first 30 items unless paginated.
- Never take "the last review" or "the last comment" without filtering by author and reading every page. The reviews endpoint also returns maintainer replies, and on a long pull request the first page ends well before the newest review: on #5 it stopped at review 30 of 62, at a maintainer reply nine hours older than the latest Copilot review.
- Never filter findings by `created_at > last push`. That filter cannot by construction show an older finding that is still outstanding, which is exactly what "previously missed" means.
- The reviewer appears under three logins: `copilot-pull-request-reviewer[bot]` in `/pulls/N/reviews`, `Copilot` in `/pulls/N/comments`, and `copilot-pull-request-reviewer` in GraphQL. Match the set exactly; a filter written for one returns nothing against another.
- Thread resolution state is only available through GraphQL (`reviewThreads { isResolved }`), not REST.
- Treat an unreadable answer as not clean: a check that cannot read its input must never report success.
- Compare review and comment timestamps against the last push to prove a cycle ran after the change, but use timestamps to establish *which cycle is current*, never to decide which findings still need work.
- Resolve review threads only after the findings are actually addressed, never to unblock a merge. Under the triage rule, a minor finding is addressed by a reply explaining that it was judged minor.
- Automated review cannot read `docs/internal/**` because it is encrypted; those changes need a maintainer with the key.

## Authoritative artifacts

Read these before proposing design changes; they override any summary in this file.

- Product vision: `docs/internal/envisioning/vision.md`
- Milestone 1 spec: `docs/internal/features/osc-camera-control/spec.md`
- Milestone 1 technical plan, including the module layout, error taxonomy and configuration schema: `docs/internal/features/osc-camera-control/plan.md`
- Task breakdown for M0 and M1: `docs/internal/features/osc-camera-control/tasks.md`
- Decision records: `docs/internal/adr/README.md`
- Verified vendor capability matrix with sources: `docs/internal/vendor-capabilities.md`
- Proposed, unapplied repository settings: `docs/internal/repo-settings-proposal.md`
