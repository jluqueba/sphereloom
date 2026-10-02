# SphereLoom repository instructions

- SphereLoom is an MCP server for controlling Insta360 cameras and processing 360 media locally.
- The first supported camera target is the Insta360 X5.
- This repository is public, MIT licensed, and maintained by `@jluqueba`.
- All code, comments, commit messages, issue text, PR text, and documentation must be written in English.
- Follow Spec-Driven Development (SDD) for all non-trivial changes.
- Read the relevant specification before coding.
- Keep envisioning material in `docs/envisioning/`.
- Keep feature artifacts in `docs/features/<feature>/spec.md`, `plan.md`, and `tasks.md`.
- Keep architecture decisions in `docs/adr/NNNN-*.md`.
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
- The unsupported error shape is `{code: "unsupported", backend, reason, docs_url}`.
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
- See `docs/adr/0013-desktop-gui-automation-rejected.md` before proposing any desktop automation idea.

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

## Documentation

- Documentation must be honest about available capabilities.
- Distinguish available now, planned milestone, and not supported by the vendor API.
- Do not promise live preview, streaming, OSC exposure control, or OSC video stitching.
- Use relative links inside the repository.
- Keep markdown markdownlint-clean.
- Use sentence-case headings.
- Use fenced code blocks with language tags.
- Include the trademark disclaimer when Insta360 is mentioned prominently.

## GitHub and CI conventions

- Create or change GitHub Actions workflows only when a task explicitly calls for it, such as task M0-05 in `docs/features/osc-camera-control/tasks.md`.
- Planned CI uses a single required status check named `ci-gate`.
- The gate is fed by change detection using `dorny/paths-filter`.
- Documentation-only PRs may skip heavy jobs while still reporting `ci-gate` success.
- Workflow permissions should default to `{}` and be narrowed per job.
- Workflows should use concurrency with `cancel-in-progress`.
- Workflows should set `timeout-minutes`.
- Keep Dependabot updates grouped and conventional-commit friendly.

## Authoritative artifacts

Read these before proposing design changes; they override any summary in this file.

- Product vision: `docs/envisioning/vision.md`
- Milestone 1 spec: `docs/features/osc-camera-control/spec.md`
- Milestone 1 technical plan, including the module layout, error taxonomy and configuration schema: `docs/features/osc-camera-control/plan.md`
- Task breakdown for M0 and M1: `docs/features/osc-camera-control/tasks.md`
- Decision records: `docs/adr/README.md`
- Verified vendor capability matrix with sources: `docs/vendor-capabilities.md`
- Proposed, unapplied repository settings: `docs/repo-settings-proposal.md`
