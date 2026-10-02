# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Public developer guide at `docs/DEVELOPER_GUIDE.md` covering architecture, getting
  started, project layout, testing tiers, configuration and the security model, so the
  repository is fully contributable from public documentation alone.
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
- Architecture decision records ADR-0001 to ADR-0013, including the agent-framework
  boundary, the rejection of workflow engines for the Milestone 1 job manager, the deferred
  optional `sphereloom-assistant` layer, and the evaluation and rejection of desktop GUI
  automation.
- Verified vendor capability matrix with sources.
- Repository settings proposal (documented only, not applied).
- Repository scaffolding: Copilot and per-path instruction files, issue and pull-request templates, `CODEOWNERS`, Dependabot configuration, contribution guide, security policy, code of conduct, markdownlint configuration and `.env.example`.

[Unreleased]: https://github.com/jluqueba/sphereloom/commits/main
