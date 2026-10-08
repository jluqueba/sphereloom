# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Public documentation site configuration for GitHub Pages (`_config.yml`): the README is
  the home page, and only public documentation is published. A test fails when a new
  top-level path is neither published on purpose nor excluded, when internal documents
  would be published, or when published Markdown contains text Jekyll would evaluate as a
  template.
- Public capability page, `docs/CAPABILITIES.md`, listing every capability in one of three
  states: available, not yet available, or not supported by the vendor API. Tests check
  that every capability appears in one of those states and that public documents name no
  milestones.
- The architecture diagram in the developer guide is a committed SVG image rendered from a
  Mermaid source by `scripts/render_diagrams.py`; a test fails if the source changes
  without the image being regenerated.
- Architecture decision record ADR-0016: public documentation and tool output describe what
  SphereLoom can do now, in three states (available, not yet available, not supported by
  the vendor API), with no milestones or roadmap. New capabilities are announced in this
  changelog and in GitHub Releases. The capability contract reports a three-state `status`
  instead of a milestone.
- Architecture decision record ADR-0015 for a public documentation site: the README and the
  public guides will be published with GitHub Pages straight from `main`, with no workflow,
  no new dependency and nothing from the encrypted internal documents. Diagrams move from
  inline Mermaid to committed SVG files so they render on the site as they do on GitHub.
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

- The README is a short general summary; architecture, requirements and getting started
  moved to the developer guide, and the capability matrix moved to its own page. Public
  documentation describes the current state and names no milestones.
- Work is tracked in issues: every pull request closes one issue, and the milestone is set
  on the issue only, so a milestone's open and closed counts show tasks rather than
  counting each task twice. Documented in `CONTRIBUTING.md` and the pull-request template.
  The feature request form asks for the area concerned instead of a target milestone.
- Pull requests merge when CI is green and the latest automated review, read in full
  including its "Previously missed" section, holds no unresolved correctness or security
  finding. Other findings are triaged as minor and answered without a commit, and a pull
  request stops after three review cycles to be split or narrowed. Documented in
  `CONTRIBUTING.md` together with the size limit per pull request, milestone and label
  conventions, the requirement to run the suite on Linux as well as Windows, and an
  extended pull-request template that records invariants and validation results.
- Documentation now states plainly that SphereLoom targets **Insta360** cameras rather than
  360° cameras generally. The Wi-Fi layer follows the open OSC standard, so other OSC
  cameras may happen to work, but none is tested or supported.
- The architecture sketch in `README.md` is a Mermaid diagram, which renders inline on
  GitHub and diffs as text.
- Internal design documents moved under `docs/internal/` and are encrypted at rest.

### Fixed

- CI ran the test matrix only for paths on an allow-list, so a pull request changing only a
  file the tests read but nobody listed (maintainer scripts, `.env.example`, the public
  documentation, the site configuration) or adding a new directory skipped the tests that
  guard it, while `ci-gate` still passed. The matrix now runs for every change except an
  explicit list of paths no test reads, and a test fails if that list ever covers a file the
  suite reads. The documentation filter's exclusion of internal documents also took effect
  for the first time.
- The `ci-gate` status check searched the job results for `failure` or `cancelled` and
  passed otherwise, so it failed open: an empty or malformed result set, a result outside
  that pair, or a run in which change detection never ran all reported success. The gate
  now passes only when change detection succeeded and every job reported `success` or
  `skipped`.
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
