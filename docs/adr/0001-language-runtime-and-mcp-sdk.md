# ADR-0001: Language, runtime and MCP SDK

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: ADR-0005, ADR-0007

## Context

SphereLoom must expose a Model Context Protocol (MCP) server that AI agents call to drive
an Insta360 camera. The immediate backend (Milestone 1) is the vendor's Open Spherical
Camera (OSC) HTTP API: plain JSON over HTTP against a fixed address, with no
authentication, modest throughput and strict sequencing rules (no overlapping
`/osc/commands` requests; `/osc/info` polled at most once per second).

Later milestones add two vendor C++ SDKs (desktop USB Camera SDK and desktop Media SDK).
Both are approval-gated, platform-restricted (Windows and Linux only, no macOS), and the
Media SDK additionally requires a discrete NVIDIA GPU. ADR-0005 isolates them into sidecar
processes, so the language of the MCP server does **not** need to be C++-friendly in the
linking sense — only in the "spawn a process and speak a protocol to it" sense.

Constraints influencing the choice:

- MCP client/server tooling maturity matters more than raw performance; the workload is
  I/O-bound against a device that is itself the bottleneck.
- The project wants contributors. Python has by far the largest pool of contributors in the
  agent/MCP ecosystem.
- The example agent (ADR-0008) uses the Microsoft Agent Framework's Python package
  `agent_framework`, so a Python server keeps the whole repository in one toolchain.
- The licence is MIT and GPL dependencies are banned project-wide (ADR-0006), which rules
  out some otherwise convenient libraries.

## Decision

- **Language and runtime:** Python, supporting **3.12 and 3.13**. CI tests both; the
  lower bound is 3.12 so we can rely on modern typing syntax without `typing_extensions`
  gymnastics.
- **Environment and packaging:** [`uv`](https://docs.astral.sh/uv/) for dependency
  resolution, virtual environments, locking and running tasks. `pyproject.toml` is the
  single source of project metadata; the lockfile is committed.
- **MCP implementation:** the official **MCP Python SDK**, using its **`MCPServer`** API
  (`from mcp.server.mcpserver import MCPServer`) for tool registration, schema generation
  and transport handling. This class was named `FastMCP` before SDK 2.0, and much of the
  material published online still uses the old name.
- **HTTP client:** **`httpx`** (`AsyncClient`), chosen for first-class async support,
  explicit timeout objects and a permissive BSD licence.
- **Data modelling and validation:** **`pydantic` v2** for every MCP tool input and output
  model, for configuration, and for parsing vendor responses.
- **Developer tooling:** **`ruff`** as the single linter and formatter, **`mypy` in strict
  mode** for the whole package, **`pytest`** (plus `pytest-asyncio`) for tests.
- **Async-first:** the server, the OSC adapter and the job engine are written against
  `asyncio`. Blocking work (file writes, future sidecar I/O) goes through explicit
  thread-offload helpers rather than being allowed to stall the event loop.

## Consequences

### Positive

- One language across the server, the fake camera, the tests and the example agent.
- The SDK generates tool schemas from pydantic models, so the agent-facing contract and the
  runtime validation cannot drift apart.
- `uv` gives fast, reproducible environments and a single command surface for local and CI
  parity (ADR-0009).
- `mypy --strict` plus pydantic catches most adapter/port contract drift at build time,
  which matters because the real hardware is rarely in the loop (ADR-0007).

### Negative / costs

- Python is a poor fit for CPU-bound 360° media processing. We accept this: such work
  happens inside the vendor's C++ Media SDK in a sidecar (ADR-0005), never in our process.
- Packaging a Python CLI for non-Python users adds friction; mitigated by `uvx`/`pipx`
  installation instructions and, later, by documented MCP client configuration snippets.
- `mypy --strict` over async code with third-party stubs occasionally requires explicit
  annotations that feel redundant.

### Neutral

- Supporting two Python versions doubles part of the test matrix; the change-detection CI
  gate (ADR-0009) keeps that cheap for documentation-only changes.

## Alternatives considered

### TypeScript / Node.js

Equally mature MCP SDK and a large ecosystem. Rejected because the example agent framework
we chose is used from Python here, because the scientific/imaging ecosystem we will want in
M4/M5 is Python-centric, and because mixing two runtimes in one repository raises the
contribution barrier without buying anything.

### C# / .NET

Attractive for a native, statically-linked path to the vendor C++ SDKs and for strong
typing. Rejected because the sidecar decision (ADR-0005) removes the linking advantage, the
MCP ecosystem momentum is smaller, and the contributor pool for a hobbyist-facing 360°
camera tool is thinner.

### C++ end to end

Would allow direct SDK integration with no IPC. Rejected outright: it would put vendor SDK
headers on our compile path, entangling an MIT codebase with an EULA-bound dependency
(ADR-0006), and it would make the project nearly unapproachable for contributors.

### Hand-rolled MCP implementation

Rejected. The protocol is evolving; tracking it by hand is wasted effort and a correctness
risk.

## Follow-ups

- Pin exact dev-tool versions and range-pin runtime dependencies in `pyproject.toml`
  (ADR-0009).
- Re-evaluate the Python lower bound when 3.14 reaches general availability.
