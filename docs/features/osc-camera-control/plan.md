# Technical plan — OSC camera control over Wi-Fi (Milestone 1)

- Feature ID: `osc-camera-control`
- Status: approved, not implemented
- Owner: @jluqueba
- Last updated: 2026-10-02
- Implements: [spec.md](spec.md) · Decomposed in: [tasks.md](tasks.md)
- Governing decisions: ADR-[0001](../../adr/0001-language-runtime-and-mcp-sdk.md) ·
  [0002](../../adr/0002-mcp-transport-and-security-posture.md) ·
  [0003](../../adr/0003-ports-and-adapters-with-capability-registry.md) ·
  [0004](../../adr/0004-job-model-for-long-running-operations.md) ·
  [0005](../../adr/0005-sidecar-isolation-for-vendor-sdks.md) ·
  [0007](../../adr/0007-testing-strategy-without-hardware.md) ·
  [0009](../../adr/0009-ci-gate-versioning-and-release-process.md) ·
  [0010](../../adr/0010-agent-framework-stays-out-of-the-server-core.md) ·
  [0011](../../adr/0011-job-manager-stays-in-house.md)

## 1. Architecture overview

```text
            ┌────────────────────────────────────────────────────────┐
            │  MCP client (editor, desktop app, example agent)        │
            └───────────────┬────────────────────────────────────────┘
                            │ MCP over stdio (default) / HTTP (opt-in)
            ┌───────────────▼────────────────────────────────────────┐
            │  server/        MCP app, transport, lifespan           │
            ├────────────────────────────────────────────────────────┤
            │  tools/         thin MCP tools: validate → guard →      │
            │                 call port → map result                  │
            ├──────────────┬──────────────┬──────────────────────────┤
            │ capabilities │ security     │ jobs/                    │
            │ registry     │ path jail,   │ queue, workers,          │
            │              │ confirmations│ state machine            │
            ├──────────────┴──────────────┴──────────────────────────┤
            │  ports/         CameraPort, MediaPort  (Protocols)      │
            ├────────────────────────────────────────────────────────┤
            │  domain/        models, errors, capability enums        │
            ├────────────────────────────────────────────────────────┤
            │  adapters/                                              │
            │    osc/   ← M1  httpx client, command runner, mapping   │
            │    fake/  ← M1  in-process fake backend                 │
            │    usb/   ← M3  sidecar client (not in this milestone)  │
            └────────────────────────────────────────────────────────┘
                            │ HTTP (no auth, X-XSRF-Protected: 1)
            ┌───────────────▼────────────────────────────────────────┐
            │  Camera access point — http://192.168.42.1              │
            └────────────────────────────────────────────────────────┘
```

Dependency rule: `tools → ports → domain`, and `adapters → ports → domain`. Nothing outside
`adapters/osc/` may import `httpx` or know an OSC command name. `domain/` imports nothing
from the project except itself.

### Dependency boundary: no agent framework in the core

Per [ADR-0010](../../adr/0010-agent-framework-stays-out-of-the-server-core.md), no module
under `src/sphereloom/` may import an agent or LLM orchestration library, and none appears
in the package's runtime dependencies. `agent-framework` (MIT, version 1.19.0, Python
3.10–3.14) is an **optional/dev** dependency used only by `examples/agent/` from Milestone 2
(ADR-0008) and, if it is ever scheduled, by the separately installed `sphereloom[assistant]`
extra (ADR-0012). `agent-framework-devui` is a beta package and is never a CI dependency.

Consequences for this milestone: the server starts with no model credentials, every tool is
deterministic given the same inputs and camera state, and the job engine is written in-house
rather than on a workflow engine
([ADR-0011](../../adr/0011-job-manager-stays-in-house.md)). An import-boundary check in CI
enforces the rule.

## 2. Module layout

```text
src/sphereloom/
  __init__.py
  __main__.py                  # python -m sphereloom
  cli.py                       # entry point, argument parsing, transport selection
  config.py                    # pydantic-settings model, validation, fail-fast
  logging.py                   # structured logging to stderr + redaction filter
  domain/
    models.py                  # CameraInfo, CameraStatus, OptionValue, MediaFile, Page[T]
    capabilities.py            # Capability enum + CapabilityStatus
    errors.py                  # exception hierarchy ↔ error taxonomy
    clock.py                   # Clock protocol + system/fake implementations
    ids.py                     # IdFactory protocol (job ids, tokens)
  ports/
    camera.py                  # CameraPort protocol
    media.py                   # MediaPort protocol (declared, unimplemented in M1)
  capabilities/
    registry.py                # static table + runtime refinement + resolution
    data.py                    # backend × capability declarations (data, with citations)
  adapters/
    osc/
      client.py                # httpx wrapper: headers, timeouts, retries, rate limit
      commands.py              # execute + status polling, command serialisation
      mapping.py               # vendor payload ↔ domain models
      options.py               # option catalogue, validation, vendor-extension handling
      adapter.py               # OscCameraAdapter(CameraPort)
      errors.py                # vendor error code → taxonomy mapping
    fake/
      camera_server.py         # protocol-faithful fake OSC HTTP server
      scenarios.py             # failure injection profiles
      adapter.py               # FakeCameraAdapter wiring (uses the HTTP server)
  jobs/
    models.py                  # Job, JobState, JobKind, JobResult
    store.py                   # JobStore protocol + in-memory implementation
    engine.py                  # queue, bounded workers, cancellation, retention
    download.py                # download worker (streaming, atomic rename, checksum)
  security/
    workspace.py               # path jail: resolve, verify, atomic write helpers
    confirmations.py           # single-use, target-bound, expiring tokens
    http_auth.py               # bearer token check for the opt-in HTTP transport
  server/
    app.py                     # MCPServer construction, lifespan, DI container
    transport.py               # stdio / http selection and guard rails
  tools/
    camera.py                  # camera_connect, camera_status, camera_info
    options.py                 # options_get, options_set
    capture.py                 # camera_take_photo, camera_start_recording, camera_stop_recording
    files.py                   # files_list, files_get, files_download, files_delete
    jobs.py                    # jobs_list, jobs_get, jobs_cancel
    capabilities.py            # capabilities_list
    _envelope.py               # shared result/error envelope helpers
    _pagination.py             # opaque cursor encode/decode/validate
tests/
  unit/  component/  integration/  fixtures/
examples/
  agent/                       # M2 (ADR-0008)
docs/                          # specs, plans, ADRs
```

## 3. Domain model

| Model | Key fields |
| --- | --- |
| `CameraInfo` | `model`, `serial_number`, `firmware_version`, `api_levels`, `base_url`, `vendor_raw` (redacted) |
| `CameraStatus` | `battery_percent`, `charging`, `storage_free_bytes`, `storage_total_bytes`, `remaining_video_seconds?`, `remaining_photos?`, `capture_mode`, `capture_in_progress`, `capture_elapsed_seconds?`, `observed_at`, `age_seconds` |
| `OptionDescriptor` | `name`, `value`, `value_type`, `supported_values?`, `writable`, `vendor_extension` |
| `MediaFile` | `uri`, `name`, `kind` (`photo`\|`video`), `size_bytes`, `captured_at`, `duration_seconds?`, `width?`, `height?`, `group_id?`, `stitched: bool`, `local_path?` |
| `Page[T]` | `items`, `next_cursor`, `total_estimate?` |
| `Job` | `id`, `kind`, `state`, `created_at`, `started_at?`, `finished_at?`, `progress`, `params_digest`, `idempotency_key?`, `result?`, `error?` |

All optional vendor fields are genuinely optional: a missing field yields `None`, never an
exception (AC-1.2.2, AC-3.1.3).

## 4. Ports

`CameraPort` (Protocol, async) — the only contract the tool layer knows:

```text
connect() -> CameraInfo
info() -> CameraInfo
status() -> CameraStatus
get_options(names: Sequence[str] | None) -> OptionsResult
set_options(values: Mapping[str, object]) -> OptionsResult
take_photo(*, switch_mode: bool) -> CaptureResult
start_recording(*, switch_mode: bool) -> RecordingHandle
stop_recording() -> RecordingResult
list_files(query: FileQuery) -> Page[MediaFile]
get_file(uri: str) -> MediaFile
open_file_stream(uri: str) -> AsyncIterator[bytes]        # used by the download worker
delete_files(uris: Sequence[str]) -> DeleteResult
runtime_capabilities() -> Mapping[Capability, CapabilityStatus]
```

`MediaPort` is declared in M1 with no implementation; its capabilities resolve to
`unsupported / available_in: "M4"`.

## 5. OSC adapter design

### 5.1 HTTP client

- One shared `httpx.AsyncClient` per adapter instance, created in the server lifespan and
  closed on shutdown; connection reuse matters on a weak access point.
- Default headers: `X-XSRF-Protected: 1`, `Content-Type: application/json`, a SphereLoom
  user agent.
- Explicit `httpx.Timeout(connect=5, read=15, write=15, pool=5)` by default, overridable;
  download streams use a longer read timeout and no total deadline (jobs own cancellation).
- No proxies, no environment-trust (the camera is a link-local device), redirects disabled.

### 5.2 Command sequencing and rate limiting

- A single `asyncio.Lock` serialises every `/osc/commands/execute` call (C1, AC-10.3). The
  lock is held for the execute call and released for the status-polling loop, while the
  "a capture is in progress" state prevents a second capture command.
- `/osc/info` is wrapped in a 1-second minimum-interval cache (C2, AC-1.2.3, AC-10.4); the
  cached result carries `age_seconds`.
- Asynchronous command completion (C3): `execute` → if `inProgress`, poll
  `/osc/commands/status` with a bounded schedule (250 ms, backing off to 1 s) until `done`
  or `error`, capped by the capture timeout. On timeout the vendor command `id` is surfaced
  (AC-4.1.4).

### 5.3 Retry policy

- Retry only **idempotent** requests (`/osc/info`, `/osc/state`, `camera.getOptions`,
  `camera.listFiles`, status polls, and download range requests) on connection errors,
  resets and 5xx, with bounded exponential backoff plus jitter, maximum 3 attempts.
- **Never** retry `camera.takePicture`, `camera.startCapture`, `camera.stopCapture`,
  `camera.setOptions`, `camera.delete` (AC-10.2) — a duplicated shot or a repeated delete is
  worse than a clear error.

### 5.4 Options handling

- A declarative option catalogue maps domain-friendly names to OSC option names, types and
  constraints, and marks underscore-prefixed vendor extensions. Validation happens locally
  first, so bad values never reach the camera (AC-3.2.2).
- Unknown names produce `invalid_argument` with "did you mean" suggestions from the
  catalogue (AC-3.2.3).
- `set_options` always re-reads the affected options and returns observed values
  (AC-3.2.1).
- Exposure-related names are deliberately present in the catalogue as **unsupported
  capability** entries, so the user gets `unsupported` rather than "unknown option"
  (AC-3.2.5).

### 5.5 Error mapping

Vendor error payloads carry their own codes and messages. `adapters/osc/errors.py` maps
them to the taxonomy, preserving the original in `details.vendor` (AC-3.2.4). Unknown vendor
codes map to `internal` with the redacted payload excerpt (AC-10.5), never an unhandled
exception.

## 6. Capability registry

- `capabilities/data.py` is a table of `(backend, capability) → status`, where each
  `unsupported` entry carries a `reason` and a `docs_url` citing vendor documentation, plus
  `available_in` for self-imposed gaps (`media.stitch.video` → `"M4"`).
- `registry.resolve(capability)` merges static data with the adapter's
  `runtime_capabilities()` (model and firmware refinements), with runtime narrowing but
  never widening a statically unsupported entry.
- A `@requires(Capability.X)` decorator on tool handlers performs the check **before** any
  I/O (AC-2.2.2) and raises `UnsupportedCapabilityError`, which the envelope layer renders.
- `capabilities_list` serialises the resolved matrix. A test renders the README matrix from
  the same source and fails on divergence (AC-2.1.4).

Capability identifiers for M1: `camera.connect`, `camera.status`, `options.read`,
`options.write`, `photo.capture`, `video.record`, `files.list`, `files.download`,
`files.delete`, `exposure.control` (unsupported), `preview.live` (unsupported),
`storage.format` (M3), `media.stitch.video` (M4), `media.export` (M4).

## 7. Error taxonomy

One exception class per code; one rendering path; no other way to fail a tool.

| Code | Exception | Meaning | Typical cause |
| --- | --- | --- | --- |
| `unsupported` | `UnsupportedCapabilityError` | Backend cannot do this | OSC exposure control, live preview |
| `not_connected` | `NotConnectedError` | Camera unreachable or not an OSC device | Host not on the camera's AP |
| `camera_busy` | `CameraBusyError` | A capture or command is already in progress | Concurrent capture |
| `invalid_argument` | `InvalidArgumentError` | Input failed validation | Bad option value, bad cursor, stop without start |
| `not_found` | `NotFoundError` | Unknown file URI or job id | Evicted job, deleted file |
| `confirmation_required` | `ConfirmationRequiredError` | Destructive call needs a token | First `files_delete` call |
| `confirmation_invalid` | `ConfirmationInvalidError` | Token expired, reused or mismatched | Late retry |
| `permission_denied` | `PermissionDeniedError` | Operation disabled by configuration | Deletion not enabled |
| `path_outside_workspace` | `PathJailError` | Destination escapes the workspace | `..`, absolute path, symlink |
| `timeout` | `OperationTimeoutError` | Deadline exceeded | Slow capture completion |
| `rate_limited` | `RateLimitedError` | Caller exceeded an internal limit | Status polling storm |
| `storage_full` | `StorageFullError` | No space on camera or host | Full card or disk |
| `job_failed` | `JobFailedError` | Background work failed | Interrupted download |
| `internal` | `InternalError` | Unexpected condition | Malformed vendor JSON |

Envelope rendered by `tools/_envelope.py`:

```json
{
  "error": {
    "code": "…",
    "message": "human-readable, actionable, English",
    "backend": "osc",
    "capability": "optional",
    "reason": "optional",
    "docs_url": "optional",
    "available_in": "optional milestone",
    "details": {},
    "retryable": false
  }
}
```

Messages are written for both a human and a model: they say what failed, why, and what to
do next. They never contain tokens, SSIDs or absolute paths.

## 8. Job engine

Implements ADR-0004. Built in-house rather than on an off-the-shelf workflow engine; the
reasoning and the Milestone 4 revisit trigger are recorded in
[ADR-0011](../../adr/0011-job-manager-stays-in-house.md).

- `JobState`: `queued → running → succeeded | failed | cancelled`; transitions validated in
  one place and unit-tested exhaustively.
- `engine.py` runs an `asyncio` worker pool with `SPHERELOOM_MAX_CONCURRENT_JOBS`
  (default 2) and a bounded queue; camera-touching jobs also acquire the adapter's command
  lock so a download cannot interleave with a capture command.
- `download.py`: stream the camera URL with `httpx` in chunks, write to
  `<target>.part-<job_id>`, update progress at a throttled cadence, verify length when the
  camera supplies one, compute a checksum, then atomically rename (AC-7.1.5). On cancel or
  failure, remove the partial file.
- Cancellation is cooperative at chunk boundaries with a ≤ 2 s target (AC-7.1.6).
- Idempotency: `(tool, params_digest, idempotency_key)` lookup returns the existing job
  (AC-7.1.9).
- Retention: bounded count and age, oldest terminal jobs evicted first; `jobs_get` on an
  evicted id returns `not_found` mentioning retention (AC-7.2.2).
- Startup sweep removes orphaned `*.part-*` files from the workspace (AC-7.2.5).

## 9. Security model

Implements ADR-0002.

- **Transport.** stdio default, no listener. HTTP requires a token and loopback; a
  non-loopback bind demands an explicit acknowledgement flag; token comparison is
  constant-time; startup logs a warning (AC-9.1 – AC-9.4).
- **Path jail.** `security/workspace.py` resolves candidate paths with symlink expansion
  and asserts containment in the workspace root; all writes go through its atomic-write
  helper; results expose only workspace-relative paths (AC-7.1.3, AC-7.1.4).
- **Confirmations.** `security/confirmations.py` issues single-use tokens bound to a digest
  of the exact target set, with a configurable TTL (default 120 s) and a swept store
  (AC-8.1.2 – AC-8.1.5). Destructive tools additionally require an enable flag (AC-8.1.1).
- **Input validation.** Every tool input is a pydantic model with strict types and bounds;
  cursors are opaque, signed-and-validated payloads that cannot smuggle request parameters
  (AC-6.1.4).
- **Resource bounds.** Concurrency limit, queue cap, page-size cap, explicit timeouts,
  rate-limited `/osc/info`.
- **Redaction.** A logging filter removes SSIDs, bearer and confirmation tokens, query
  strings and absolute user paths; logging goes to stderr only (AC-9.5).
- **Threat note.** The camera's API has no authentication; anything on its access point can
  command it. SphereLoom's guard rails protect the *operator's* workflow and filesystem,
  not the camera itself, and the documentation says so.

## 10. Pagination contract

- Request: `cursor: str | None`, `limit: int = 25` (1–100), plus filters.
- Response: `items`, `next_cursor: str | None`.
- The cursor is an opaque base64url payload containing the position marker **and a digest of
  the filter set**, so filters cannot change mid-pagination (AC-6.1.5). It is validated on
  decode; anything unexpected yields `invalid_argument`.
- Ordering is capture time descending, with file URI as a deterministic tiebreaker, so pages
  are stable (AC-6.1.1, AC-6.1.3).
- Two cursor strategies are implemented behind one interface: vendor continuation token, and
  offset-based fallback. The fake camera exercises both; the hardware tier picks the default
  (OQ-2).

## 11. Configuration schema

Single pydantic settings model, `SPHERELOOM_` prefix, validated at startup with fail-fast
messages naming the variable (AC-9.6). Mirrored in `.env.example`.

| Variable | Type | Default | Purpose |
| --- | --- | --- | --- |
| `SPHERELOOM_WORKSPACE_DIR` | path | `./sphereloom-workspace` | Path-jail root for all writes |
| `SPHERELOOM_LOG_LEVEL` | enum | `INFO` | Logging verbosity |
| `SPHERELOOM_LOG_REDACT` | bool | `true` | Redaction filter |
| `SPHERELOOM_TRANSPORT` | enum | `stdio` | `stdio` \| `http` |
| `SPHERELOOM_HTTP_HOST` | str | `127.0.0.1` | HTTP bind address |
| `SPHERELOOM_HTTP_PORT` | int | `8765` | HTTP port |
| `SPHERELOOM_HTTP_TOKEN` | secret | — | Required when transport is `http` |
| `SPHERELOOM_HTTP_ALLOW_NON_LOOPBACK` | bool | `false` | Explicit risk acknowledgement |
| `SPHERELOOM_CAMERA_BACKEND` | enum | `osc` | `osc` \| `fake` (`usb` from M3) |
| `SPHERELOOM_OSC_BASE_URL` | url | `http://192.168.42.1` | Camera base URL |
| `SPHERELOOM_OSC_TIMEOUT_SECONDS` | float | `15` | Default read timeout |
| `SPHERELOOM_OSC_CONNECT_TIMEOUT_SECONDS` | float | `5` | Connect timeout |
| `SPHERELOOM_CAPTURE_TIMEOUT_SECONDS` | float | `30` | Capture completion deadline |
| `SPHERELOOM_MAX_CONCURRENT_JOBS` | int | `2` | Worker pool size |
| `SPHERELOOM_MAX_QUEUED_JOBS` | int | `32` | Queue cap |
| `SPHERELOOM_JOB_RETENTION_COUNT` | int | `100` | Terminal jobs kept |
| `SPHERELOOM_JOB_RETENTION_HOURS` | int | `24` | Terminal job age cap |
| `SPHERELOOM_ENABLE_DESTRUCTIVE_TOOLS` | bool | `false` | Enables `files_delete` |
| `SPHERELOOM_CONFIRMATION_TTL_SECONDS` | int | `120` | Confirmation token lifetime |
| `SPHERELOOM_MAX_PAGE_SIZE` | int | `100` | Pagination cap |
| `SPHERELOOM_ENABLE_HARDWARE_TESTS` | bool | `false` | Enables the hardware test tier |
| `SPHERELOOM_CAMERA_SDK_PATH` | path | — | M3; user-supplied SDK, never redistributed |
| `SPHERELOOM_MEDIA_SDK_PATH` | path | — | M4; user-supplied SDK, never redistributed |

## 12. Testing strategy

Implements ADR-0007.

- **Unit** — cursor codec, capability resolution, option validation, error mapping, path
  jail, confirmation tokens, job state machine. Injected `Clock` and `IdFactory` keep them
  deterministic.
- **Component** — real MCP server + real tools + real `OscCameraAdapter` + real HTTP
  against the fake OSC camera on loopback. This tier owns the acceptance criteria.
- **Failure injection** — scenarios for latency, busy, storage-full, malformed JSON,
  truncated downloads, mid-transfer disconnect, and 5xx, driving AC-10.x and AC-7.1.5.
- **Contract fixtures** — redacted recorded X5 responses; a test asserts the fake camera's
  responses satisfy the same schemas.
- **Integration (`@pytest.mark.hardware`)** — real X5, opt-in via
  `SPHERELOOM_ENABLE_HARDWARE_TESTS=1`, never in CI.
- **Traceability** — each acceptance criterion maps to at least one test; test names carry
  the AC identifier so coverage of the spec is greppable.

## 13. Observability

- Structured JSON logs to stderr with `tool`, `capability`, `backend`, `job_id`,
  `duration_ms`, `outcome`, and an error `code` on failure — all redacted.
- Every tool call logs start and end at `INFO`; vendor payload excerpts only at `DEBUG` and
  still redacted.
- Job progress logged at a throttled cadence, never per chunk.

## 14. Documentation deliverables

- `README.md`: what it is, the honest capability matrix (generated), quick start with the
  fake backend, real-camera setup, MCP client configuration, security notes, trademark
  disclaimer.
- `docs/vendor-capabilities.md`: the vendor-surface matrix with citations.
- `docs/configuration.md`: the table in §11, expanded.
- `docs/troubleshooting.md`: "camera not found", hotspot/internet trade-off, timeouts.
- Tool reference generated from the pydantic models so it cannot drift.

## 15. Risks specific to this milestone

| Risk | Mitigation |
| --- | --- |
| X5 firmware returns option names or shapes we did not anticipate | Catalogue is data-driven; unknown options degrade to `unavailable`, not errors; fixtures updated from hardware runs |
| Vendor continuation semantics unsuitable for an opaque cursor (OQ-2) | Offset fallback implemented behind the same interface |
| Multi-file recordings (file grouping) mishandled | Explicit AC-5.2.3 and a fake-camera scenario |
| Wi-Fi throughput makes downloads look hung | Progress reporting and throttled logging; documented expectations |
| Agents mishandle the job polling pattern | Tool descriptions spell out the start-poll-read sequence; the M2 example demonstrates it |
| Host loses internet while on the camera AP, breaking a cloud-backed agent | Documented prominently; fake backend allows development without the trade-off |

## 16. Milestone exit criteria

The definition of done in [spec.md](spec.md), plus: ADRs unchanged or amended by new ADRs,
`ci-gate` green on Linux and Windows for Python 3.12 and 3.13, and one maintainer hardware
run recorded.
