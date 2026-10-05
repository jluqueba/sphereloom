# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- The OSC client validates that a file URL supplied by the camera stays on the camera's own
  origin before following it. Download URLs arrive in device responses, and a malformed or
  hostile payload could otherwise make SphereLoom fetch an arbitrary host with the client's
  headers attached.
- Log redaction now covers tracebacks and strings nested anywhere in a record's structured
  context. Previously only the message and top-level context values were redacted, so a
  traceback could record absolute file paths containing the user's home directory.
- Log records are bounded before they are redacted or formatted, and every cut ends on a
  word boundary so that a truncated value never leaves a fragment of a secret too short
  for redaction to recognise.
- A destination path that resolves to the workspace directory itself is refused. Writing
  to it previously placed the temporary download file in the workspace's parent directory,
  outside the path jail.

### Added

- OSC HTTP client (`sphereloom.adapters.osc.client`) enforcing the three protocol
  constraints Insta360 documents: the required `X-XSRF-Protected` header on every request,
  one command in flight at a time, and `/osc/info` polled no more than once per second with
  the age of cached readings reported to callers. Retries distinguish three kinds of
  failure: setup failures the camera never saw are repeated; 500-class responses are
  repeated only for read-only commands on an allowlist; and ambiguous post-send failures,
  where the camera may already be acting, are never repeated and are reported as
  non-retryable when the command changes state, so a caller is not invited to duplicate a
  capture or a delete.
- Command runner (`sphereloom.adapters.osc.commands`) that waits for asynchronous commands
  to actually finish. A capture acknowledgement is not a result, and returning early would
  hand back a photo that does not exist yet. The deadline bounds the poll itself and is
  rechecked after each response, so it is a limit rather than a suggestion. On timeout the
  vendor command identifier is reported, since the camera may still be writing the file.
- Streaming downloads map every transport failure to the SphereLoom taxonomy, including
  failures raised while the caller reads the body. A dropped transfer is the normal case on
  a weak access point, and a consumer should not have to catch third-party exception types
  to handle it.
- Vendor error mapping (`sphereloom.adapters.osc.errors`) translating Insta360 error codes
  into the SphereLoom taxonomy, preserving the original payload for diagnosis and keeping
  actionable vendor wording. Unrecognised codes become `internal` rather than a guess.
- `storage_unavailable` error code, for a camera with no card or a card it cannot read.
  The vendor reports this as `cardNotFound`, which has no space to free, so it is kept
  apart from `storage_full` to point the user at the right remedy.
- Fake camera backend (`sphereloom.adapters.fake`) serving the OSC protocol over real HTTP
  on loopback. It runs the whole test suite without hardware and doubles as a demo backend,
  so SphereLoom can be tried with no camera at all. Includes failure injection for a busy
  camera, full storage, malformed JSON, server errors, an unactivated camera, latency,
  flakiness that recovers, and interrupted downloads.
- Capture tool (`scripts/capture_osc_fixtures.py`) that records real camera responses as
  redacted protocol fixtures, so the fake camera can be checked against hardware rather
  than against the vendor's older published examples. Read-only by default; serial numbers,
  thumbnails and filename dates are removed or normalised before anything is written.
- Public developer guide at `docs/DEVELOPER_GUIDE.md` covering architecture, getting
  started, project layout, testing tiers, configuration and the security model, so the
  repository is fully contributable from public documentation alone.
- Documented pull request completion rule in `CONTRIBUTING.md`: automated review runs on
  every push, so a pull request is ready only when the most recent review cycle produced no
  new findings.
- Python project scaffolding: `pyproject.toml` with the `sphereloom` console entry point, a
  committed dependency policy (range-pinned runtime, exactly pinned development tools),
  `ruff`, `mypy --strict` and `pytest` configuration, and a Python-oriented `.gitignore`
  that keeps personal media and vendor SDKs out of the repository.
- Runnable MCP server over stdio, built on the MCP Python SDK `MCPServer` API, exposing a
  `server_health` tool and advertising usage instructions to clients.
- Configuration model with fail-fast validation that names the offending environment
  variable, including transport guard rails: the HTTP transport refuses to start without a
  bearer token, and binding beyond loopback requires an explicit acknowledgement.
- Workspace path jail confining every write to a configured directory, rejecting parent
  traversal, absolute paths and symlinks that escape the root, with an atomic-write helper
  so an interrupted transfer never publishes a partial file.
- Structured stderr logging with a redaction filter for credentials, network names, query
  strings and absolute paths, keeping stdout free for the MCP protocol.
- Error taxonomy with one exception class per code and a single envelope renderer, plus an
  exhaustiveness test so a new code cannot be added without a matching class.
- Domain models, capability vocabulary, and the `CameraPort` and `MediaPort` protocols.
- Test harness with a hardware-free default: outbound network access is blocked, time and
  identifiers are injectable, and the real-camera tier is marker-gated.
- Dependency boundary check enforcing ADR-0010: the server core may not import an agent or
  LLM library, and none may appear in the runtime dependency set.
- Continuous integration with change detection and a single required `ci-gate` status
  check, running lint, type checking and tests across Python 3.12 and 3.13 on Linux and
  Windows.
- Local and CI parity runners, `Makefile` and `check.ps1`, executing identical commands.
- Release communication process describing how a shipped capability becomes an
  announcement, with a mandatory pre-publication checklist for trademark use, accuracy and
  privacy.
- Architecture decision record ADR-0014 recording that internal design documentation is
  encrypted at rest with `git-age`, why public documentation must stay self-sufficient, and
  the costs that choice carries.
- Product vision document describing the problem, target users, product principles, milestones M0–M5, non-goals and risks.
- Milestone 1 feature specification for OSC camera control over Wi-Fi, with testable acceptance criteria.
- Milestone 1 technical plan covering architecture, module layout, error taxonomy, security model, pagination contract, configuration schema and testing strategy.
- Task breakdown for Milestone 0 (scaffolding) and Milestone 1 (OSC), with dependencies, sizing and suggested pull-request slicing.
- Architecture decision records ADR-0001 to ADR-0014, including the agent-framework
  boundary, the rejection of workflow engines for the Milestone 1 job manager, the deferred
  optional `sphereloom-assistant` layer, the evaluation and rejection of desktop GUI
  automation, and the encryption of internal design documentation at rest.
- Verified vendor capability matrix with sources.
- Repository settings proposal (documented only, not applied).
- Repository scaffolding: Copilot and per-path instruction files, issue and pull-request templates, `CODEOWNERS`, Dependabot configuration, contribution guide, security policy, code of conduct, markdownlint configuration and `.env.example`.

### Changed

- Documentation now states plainly that SphereLoom targets **Insta360** cameras rather than
  360° cameras generally. The Wi-Fi layer follows the open OSC standard, so other OSC
  cameras may happen to work, but none is tested or supported.
- The architecture sketch in `README.md` is a Mermaid diagram, which renders inline on
  GitHub and diffs as text.
- Internal design documents moved under `docs/internal/` and are encrypted at rest.

### Fixed

- The fake camera accepted option values whose JSON type was wrong. Because `False == 0`
  and `1 == True` in Python, `exposureDelay: false` matched the accepted value `0` and was
  stored unchanged, so `getOptions` returned a boolean where a number belongs and a
  malformed request passed. Matching now compares type as well as value.
- The fake camera did not require the `X-XSRF-Protected` header the protocol mandates, so
  an adapter omitting it would have passed every test and failed against real hardware.
- `camera.setOptions` on the fake reported success for options it never applied and for
  values outside its own advertised support lists, and could apply a batch partially.
- Deleting the same file twice in one call raised after the first removal, returning an
  error that had nevertheless already changed state.
- The capture tool reused its output directory, so a read-only run after an earlier
  `--capture-photo` run left a stale capture response in place while writing a fresh
  manifest, attributing an old response to the current firmware and date.
- The capture tool recorded only the `inProgress` acknowledgement of a photo capture,
  missing the terminal response that carries the file URL and group fields.
- Hardware-marked tests could contact a real camera during an ordinary test run. They are
  now skipped at collection unless `SPHERELOOM_ENABLE_HARDWARE_TESTS` is set.
- `ruff` tried to read the encrypted internal documents, which fails in any checkout
  without the decryption key, including every CI runner.

[Unreleased]: https://github.com/jluqueba/sphereloom/commits/main
