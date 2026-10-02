# Task breakdown — Milestone 0 (scaffolding) and Milestone 1 (OSC camera control)

- Feature ID: `osc-camera-control`
- Status: ready for implementation
- Owner: @jluqueba
- Last updated: 2026-10-02
- Implements: [spec.md](spec.md) via [plan.md](plan.md)

## How to read this

- **Size**: `S` ≈ half a day, `M` ≈ 1–2 days, `L` ≈ 3–5 days, for one developer.
- **Depends on**: tasks that must be merged first. Tasks with no dependency in the same
  wave can proceed in parallel.
- **AC**: acceptance criteria from [spec.md](spec.md) that the task must satisfy.
- Definition of done for every task: `ruff` clean, `mypy --strict` clean, tests added at the
  appropriate tier, documentation touched if user-visible, `CHANGELOG.md` entry if
  user-visible, Conventional Commit pull-request title, `ci-gate` green.
- No task is complete because the code exists; it is complete when its acceptance criteria
  are demonstrated by tests against the fake camera.

---

## Milestone 0 — Scaffolding

Goal: a repository where a contributor can clone, run one command, and get a green check —
with no camera and no vendor SDK.

| ID | Task | Size | Depends on | Output |
| ---- | ------ | ------ | ----------- | -------- |
| **M0-01** | Python project skeleton: `pyproject.toml` (name, MIT, Python 3.12/3.13, console entry point `sphereloom`), `src/sphereloom/` package with `__main__.py`. **No committed `uv` lockfile**: SphereLoom is a distributable package rather than a deployed application, so reproducibility lives in the dependency policy of M0-02 | S | — | Installable package stub |
| **M0-02** | Dependency policy applied: runtime deps range-pinned (`mcp`, `httpx`, `pydantic`, `pydantic-settings`), dev tools exact-pinned (`ruff`, `mypy`, `pytest`, `pytest-asyncio`, `pytest-cov`), with the rationale as a comment (ADR-0009). **No agent framework in the runtime dependency set**; declare the `assistant` extra as a placeholder only if it costs nothing (ADR-0010, ADR-0012) | S | M0-01 | Reproducible environment |
| **M0-03** | Tooling configuration: `ruff` lint + format rules, `mypy` strict for the whole package, `pytest` configuration with markers (`hardware`), coverage settings | S | M0-02 | Enforced standards |
| **M0-04** | Local/CI parity runners: `Makefile` and `check.ps1` executing the identical sequence `ruff format --check`, `ruff check`, `mypy --strict`, `pytest` through `uv` | S | M0-03 | One command, same answer everywhere |
| **M0-05** | CI workflow with change detection: `dorny/paths-filter` job plus the single required `ci-gate` aggregator; lint, typecheck, test matrix (3.12/3.13 × Linux/Windows), markdown lint; workflow-level `permissions: {}`, per-job narrowing, `concurrency` with cancel-in-progress, `timeout-minutes`; `hardware` marker deselected | M | M0-04 | ADR-0009 gate |
| **M0-06** | Logging foundation: structured stderr logging, redaction filter (SSIDs, tokens, query strings, absolute paths), no stdout writes | S | M0-01 | AC-9.5 |
| **M0-07** | Configuration model: `pydantic-settings` schema per plan §11, fail-fast validation naming the offending variable, parity test against `.env.example` | M | M0-01 | AC-9.6 |
| **M0-08** | Error taxonomy: exception hierarchy, one class per code, envelope renderer, exhaustiveness test over the code set | M | M0-01 | Plan §7, AC-2.2.4 |
| **M0-09** | Domain models and ports: `domain/models.py`, `Clock`, `IdFactory`, `CameraPort`, `MediaPort` protocols | M | M0-08 | Plan §3–4 |
| **M0-10** | MCP server bootstrap: app construction, lifespan, dependency wiring, `sphereloom --version`, stdio transport serving a trivial health tool | M | M0-06, M0-07, M0-09 | Runnable server |
| **M0-11** | Transport guard rails: HTTP opt-in, mandatory token, loopback default, non-loopback acknowledgement flag, constant-time comparison, startup warning | M | M0-10 | AC-9.1 – AC-9.4 |
| **M0-12** | Workspace path jail: resolution with symlink expansion, containment checks, atomic write helper, workspace-relative rendering | M | M0-07 | AC-7.1.4 |
| **M0-13** | README rewrite: positioning, honest capability matrix placeholder, quick start with the fake backend, security notes, trademark disclaimer | S | M0-01 | Public face |
| **M0-14** | Test harness: `tests/{unit,component,integration}` layout, no-network fixture, deterministic clock/ID fixtures, coverage reporting | S | M0-03, M0-09 | ADR-0007 |
| **M0-15** | Import-boundary check wired into `ci-gate`: fail the build if any module under `src/sphereloom/` imports an agent or LLM orchestration library, or if one appears in the runtime dependency set | S | M0-05, M0-14 | ADR-0010 §8 |

**M0 exit criteria:** `make check` / `.\check.ps1` green locally and in CI on both Python
versions and both operating systems; `sphereloom` starts over stdio and responds to a tool
listing; HTTP transport refuses to start without a token; the import-boundary check is
enforced by `ci-gate`.

---

## Milestone 1 — OSC camera control

### Wave 1 — Backend foundations (parallelisable after M0)

| ID | Task | Size | Depends on | AC |
| ---- | ------ | ------ | ----------- | ----- |
| **M1-01** | OSC HTTP client: shared `AsyncClient`, static headers, explicit timeouts, redirects disabled, bounded retry for idempotent requests only, `/osc/info` 1-per-second cache | M | M0-10 | AC-1.2.3, AC-10.1, AC-10.2, AC-10.4 |
| **M1-02** | Command runner: serialising lock over `/osc/commands/execute`, asynchronous completion polling via `/osc/commands/status` with backoff and deadline, command-id surfacing on timeout | M | M1-01 | C1/C3, AC-4.1.4, AC-10.3 |
| **M1-03** | Vendor error mapping: vendor code → taxonomy, original preserved in `details.vendor`, malformed-JSON handling | S | M0-08, M1-01 | AC-3.2.4, AC-10.5 |
| **M1-04** | Fake OSC camera server: protocol-faithful loopback HTTP implementation of `/osc/info`, `/osc/state`, `/osc/commands/execute`, `/osc/commands/status`, file serving; asynchronous command completion; one-in-flight-command enforcement; tiny real media files | L | M0-14 | AC-9.7, ADR-0007 |
| **M1-05** | Fake camera failure injection: latency, busy, storage-full, malformed JSON, 5xx, truncated and mid-transfer-dropped downloads | M | M1-04 | AC-10.5, AC-7.1.5, AC-4.1.6 |
| **M1-06** | Capability registry: enum, static data table with citations and `docs_url`, runtime refinement merge, `@requires` decorator performing the check before I/O | M | M0-08, M0-09 | AC-2.1.1 – AC-2.2.3 |

### Wave 2 — Adapter and first tools

| ID | Task | Size | Depends on | AC |
| ---- | ------ | ------ | ----------- | ----- |
| **M1-07** | `OscCameraAdapter` skeleton implementing `CameraPort`, plus connect/info/status with payload → domain mapping and optional-field tolerance | M | M1-01, M1-02, M1-03 | AC-1.1.1 – AC-1.3.1 |
| **M1-08** | Tools `camera_connect`, `camera_status`, `camera_info` with pydantic input/output models and LLM-oriented descriptions | S | M1-07, M1-06 | FR-1 |
| **M1-09** | Tool `capabilities_list` + README capability-matrix generator and drift test | S | M1-06 | AC-2.1.1 – AC-2.1.4 |
| **M1-10** | Option catalogue: name mapping, types, constraints, vendor-extension flagging, local validation with suggestions | M | M1-07 | AC-3.1.1 – AC-3.1.4, AC-3.2.2, AC-3.2.3 |
| **M1-11** | Tools `options_get` / `options_set`, including re-read-after-write, partial-failure reporting, and `unsupported` for exposure | M | M1-10, M1-06 | FR-3 |

### Wave 3 — Capture

| ID | Task | Size | Depends on | AC |
| ---- | ------ | ------ | ----------- | ----- |
| **M1-12** | Adapter photo capture: `camera.takePicture`, completion polling, mode switching, busy and storage-full detection | M | M1-07, M1-02 | AC-4.1.1 – AC-4.1.6 |
| **M1-13** | Tool `camera_take_photo`, including the optional `download: true` job hand-off | S | M1-12, M1-20 | FR-4 |
| **M1-14** | Adapter recording: `camera.startCapture` / `camera.stopCapture`, in-progress state, duration, multi-file group results | M | M1-07, M1-02 | AC-5.1.1 – AC-5.2.3 |
| **M1-15** | Tools `camera_start_recording` / `camera_stop_recording`, with the explicit "unstitched video, stitching requires M4" statement in the result and description | S | M1-14 | FR-5, AC-5.2.4 |

### Wave 4 — Gallery and pagination

| ID | Task | Size | Depends on | AC |
| ---- | ------ | ------ | ----------- | ----- |
| **M1-16** | Opaque cursor codec: encode/decode/validate, filter digest binding, rejection of foreign cursors | S | M0-08 | AC-6.1.4, AC-6.1.5 |
| **M1-17** | Adapter file listing: `camera.listFiles`, deterministic ordering, both continuation and offset strategies behind one interface | M | M1-07, M1-16 | AC-6.1.1 – AC-6.1.3 |
| **M1-18** | Tools `files_list` / `files_get`, page-size cap, filters, local-copy detection | M | M1-17, M0-12 | FR-6 |

### Wave 5 — Jobs and downloads

| ID | Task | Size | Depends on | AC |
| ---- | ------ | ------ | ----------- | ----- |
| **M1-19** | Job engine: state machine, bounded worker pool and queue, in-memory `JobStore` behind a protocol, retention and eviction, cooperative cancellation. Built in-house, not on a workflow engine (ADR-0011) | L | M0-09 | FR-7.2, ADR-0004 |
| **M1-20** | Download worker: streaming to `.part-<job_id>`, throttled progress, length verification, checksum, atomic rename, partial cleanup, disk-space pre-check, `on_conflict` handling | L | M1-19, M0-12, M1-07 | AC-7.1.1 – AC-7.1.11 |
| **M1-21** | Tool `files_download` with idempotency keys and fast `job_id` return | M | M1-20 | AC-7.1.1, AC-7.1.9 |
| **M1-22** | Tools `jobs_list` / `jobs_get` / `jobs_cancel`, shared pagination, terminal-state no-op cancel, retention messaging | M | M1-19, M1-16 | AC-7.2.1 – AC-7.2.4 |
| **M1-23** | Startup sweep for orphaned temporary files | S | M1-20 | AC-7.2.5 |

### Wave 6 — Destructive operations and hardening

| ID | Task | Size | Depends on | AC |
| ---- | ------ | ------ | ----------- | ----- |
| **M1-24** | Confirmation-token service: single-use, target-bound, TTL, sweeping, constant-time lookup | M | M0-08 | AC-8.1.2 – AC-8.1.5 |
| **M1-25** | Tool `files_delete`: disabled by default, two-step confirmation, exact target preview, adapter `camera.delete` | M | M1-24, M1-07 | FR-8 |
| **M1-26** | Resilience test suite against the injection scenarios: disconnect mid-session, malformed JSON, busy, timeouts, rate limiting, recovery | M | M1-05, M1-08, M1-21 | FR-10 |
| **M1-27** | Error-taxonomy coverage test: every code reachable through the real tool layer | S | all tool tasks | AC-2.2.4 |
| **M1-28** | Contract fixtures: recorded redacted X5 responses, schema-equivalence test against the fake camera, documented redaction and recording procedure | M | M1-04 | ADR-0007 §7–8 |

### Wave 7 — Documentation and release readiness

| ID | Task | Size | Depends on | AC |
| ---- | ------ | ------ | ----------- | ----- |
| **M1-29** | Generated tool reference from pydantic models, wired into docs | S | all tool tasks | Plan §14 |
| **M1-30** | `docs/configuration.md`; `docs/troubleshooting.md` (camera not found, hotspot/internet trade-off, timeouts); refresh `docs/vendor-capabilities.md` against anything learned on hardware | M | M1-09 | Plan §14 |
| **M1-31** | README final pass: generated capability matrix, quick start with the fake backend, real-camera walkthrough, MCP client configuration snippets, explicit "no 360° video stitching until M4" statement | S | M1-29, M1-30 | Vision, AC-2.1.4 |
| **M1-32** | Maintainer hardware run on a real X5: execute the `hardware` tier, record firmware version, file divergences, reproduce each in the fake camera before fixing | M | M1-26, M1-28 | Spec DoD |
| **M1-33** | Milestone review: confirm every acceptance criterion maps to a passing test; update `CHANGELOG.md`; raise ADRs for anything that changed | S | all | Spec DoD |

---

## Dependency graph (condensed)

```text
M0-01 ──┬─ M0-02 ─ M0-03 ─ M0-04 ─ M0-05
        ├─ M0-06 ─┐
        ├─ M0-07 ─┼─ M0-10 ─ M0-11
        ├─ M0-08 ─ M0-09 ─┘        └─ M0-12
        └─ M0-13                       │
                                       │
M0-10 ─ M1-01 ─ M1-02 ─┬─ M1-07 ─┬─ M1-08
                       │         ├─ M1-10 ─ M1-11
        M1-03 ─────────┘         ├─ M1-12 ─ M1-13
                                 ├─ M1-14 ─ M1-15
        M1-06 ─ M1-09            ├─ M1-17 ─ M1-18
                                 └─ M1-20
M0-14 ─ M1-04 ─ M1-05 ─ M1-26        │
        M1-04 ─ M1-28            M1-19 ─┴─ M1-21 ─ M1-23
        M1-16 ─ M1-17/M1-22      M1-19 ─── M1-22
        M1-24 ─ M1-25
        (all tools) ─ M1-27 ─ M1-29 ─ M1-30 ─ M1-31 ─ M1-32 ─ M1-33
```

## Critical path

`M0-01 → M0-07/M0-09 → M0-10 → M1-01 → M1-02 → M1-07 → M1-20 → M1-21 → M1-32 → M1-33`

The fake camera (M1-04) is on nobody's critical path but gates *all testing*, so it is
scheduled in Wave 1 and must not slip: building the adapter before the fake exists would
mean writing untested code and is explicitly not the plan.

## Sizing summary

| Milestone | Tasks | S | M | L | Rough effort |
| --- | --- | --- | --- | --- | --- |
| M0 | 15 | 8 | 7 | 0 | ~2 weeks |
| M1 | 33 | 11 | 18 | 4 | ~5–6 weeks |

Estimates assume one developer, the fake-camera-first approach, and one hardware session
near the end. They exclude the legal review (deferred to before M3) and the example agent
(M2).

## Suggested pull-request slicing

1. `chore: project skeleton and tooling` (M0-01 … M0-04)
2. `ci: change-detected ci-gate workflow` (M0-05, M0-15)
3. `feat: configuration, logging and error taxonomy` (M0-06 … M0-08)
4. `feat: domain models, ports and server bootstrap` (M0-09 … M0-12)
5. `test: fake OSC camera` (M1-04, M1-05)
6. `feat: OSC client and command runner` (M1-01 … M1-03)
7. `feat: capability registry and capabilities_list` (M1-06, M1-09)
8. `feat: connection and status tools` (M1-07, M1-08)
9. `feat: options tools` (M1-10, M1-11)
10. `feat: photo and video capture tools` (M1-12 … M1-15)
11. `feat: gallery listing with pagination` (M1-16 … M1-18)
12. `feat: job engine and downloads` (M1-19 … M1-23)
13. `feat: guarded deletion` (M1-24, M1-25)
14. `test: resilience and taxonomy coverage` (M1-26 … M1-28)
15. `docs: tool reference, capability matrix and README` (M1-29 … M1-31)
16. `chore: milestone 1 review and release prep` (M1-32, M1-33)

Each slice is independently reviewable and leaves `main` in a working state.
