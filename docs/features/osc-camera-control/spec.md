# Feature spec — OSC camera control over Wi-Fi (Milestone 1)

- Feature ID: `osc-camera-control`
- Milestone: **M1**
- Status: approved, not implemented
- Owner: @jluqueba
- Last updated: 2026-10-02
- Related: [vision](../../envisioning/vision.md) · [plan](plan.md) · [tasks](tasks.md) ·
  ADR-[0001](../../adr/0001-language-runtime-and-mcp-sdk.md),
  [0002](../../adr/0002-mcp-transport-and-security-posture.md),
  [0003](../../adr/0003-ports-and-adapters-with-capability-registry.md),
  [0004](../../adr/0004-job-model-for-long-running-operations.md),
  [0007](../../adr/0007-testing-strategy-without-hardware.md),
  [0010](../../adr/0010-agent-framework-stays-out-of-the-server-core.md),
  [0011](../../adr/0011-job-manager-stays-in-house.md)

## Summary

Expose a complete, safe MCP tool surface for controlling an Insta360 camera over its Wi-Fi
Open Spherical Camera (OSC) HTTP API: check connectivity and status, read and change
supported capture options, take photos, start and stop video recording, browse the gallery
with pagination, and download media into a local workspace as a cancellable background job.
Every operation the OSC backend cannot perform returns a structured, explained
`unsupported` error instead of failing opaquely.

## User stories

| ID | As a… | I want to… | So that… |
| ---- | ------- | ----------- | ---------- |
| US-1 | agent builder | ask whether the camera is reachable and get battery and storage | I can decide whether a shoot is viable before planning it |
| US-2 | agent builder | discover which capabilities this backend supports | my planner does not attempt impossible actions |
| US-3 | creator | read and change capture settings (capture mode, photo/video resolution, exposure-delay timer, ISO-free options the API exposes) | shots come out the way I intended |
| US-4 | creator | take a 360° photo with one tool call | capture is scriptable |
| US-5 | creator | start and stop a video recording | I can bracket a take under agent control |
| US-6 | creator | page through everything on the card with stable ordering | large galleries are navigable within an agent's context budget |
| US-7 | creator | download selected files to a local folder and watch progress | multi-gigabyte transfers do not block or time out my session |
| US-8 | operator | be protected from accidental deletion | an agent cannot destroy footage on a misread instruction |
| US-9 | contributor | run everything against a fake camera | I can develop and test without hardware |

## Scope

### In scope

- `OscCameraAdapter` implementing `CameraPort` against the documented OSC endpoints.
- MCP tools: `camera_connect`, `camera_status`, `camera_info`, `capabilities_list`,
  `options_get`, `options_set`, `camera_take_photo`, `camera_start_recording`,
  `camera_stop_recording`, `files_list`, `files_get`, `files_download`, `files_delete`,
  `jobs_list`, `jobs_get`, `jobs_cancel`.
- Capability registry with OSC entries and the `unsupported` error contract.
- Job engine for downloads.
- Path-jailed workspace, confirmation tokens, redacted structured logging.
- Fake OSC camera backend usable as `SPHERELOOM_CAMERA_BACKEND=fake`.
- stdio transport; HTTP transport behind explicit opt-in.

### Out of scope for M1

| Excluded | Reason |
| --- | --- |
| Live preview / live streaming | Not provided by the vendor's OSC API |
| Exposure parameter control | Not provided by the vendor's OSC API |
| Video stitching / export to 360° video | Requires the Media SDK (M4). In-camera stitching covers photos only |
| DNG → JPG conversion | Not supported by the Media SDK |
| Trimming, merging or timeline editing | Not documented by the vendor; treated as unavailable |
| USB control, storage formatting | Camera SDK (M3) |
| Joining the camera to a router, changing its IP | The camera is AP-only at a fixed address; the vendor does not allow it |
| Automatic Wi-Fi network switching by SphereLoom | Out of scope; host OS responsibility, documented instead |
| Firmware upload/update | Camera SDK (M3), high blast radius |
| Durable jobs across restarts | Deferred (ADR-0004); workflow engines rejected for M1 (ADR-0011) |
| Agent-backed high-level tools ("record 30 seconds and download it") | No LLM runs in the server (ADR-0010); the optional `sphereloom-assistant` layer is deferred to after M2 (ADR-0012) |
| Automating the vendor's desktop application to add editing features | Evaluated and rejected (ADR-0013) |

## Assumptions and preconditions

- A1. The host machine has already joined the camera's Wi-Fi access point. SphereLoom does
  not manage network connections; when the camera is unreachable it says so with
  actionable guidance.
- A2. The camera is reachable at `http://192.168.42.1` (overridable by configuration for
  testing and for the fake backend).
- A3. The OSC API requires no credentials; the static `X-XSRF-Protected: 1` header is sent
  on every request.
- A4. Target camera: Insta360 X5. Other OSC-capable models in the vendor's supported list
  are expected to work; only the X5 is verified in M1.
- A5. While connected to the camera's access point, the host typically has no internet
  access. No tool may require internet connectivity.

## Vendor protocol constraints the implementation must honour

- C1. Never issue a new `/osc/commands/execute` request before the previous command's
  response has been received. Commands are serialised.
- C2. Poll `/osc/info` at most once per second.
- C3. `camera.takePicture` and `camera.startCapture` are asynchronous: the execute response
  may return `inProgress` with an `id`, and completion is determined by polling
  `/osc/commands/status`.
- C4. Options are read and written through `camera.getOptions` / `camera.setOptions`, which
  require naming the options to read. Vendor-specific options are underscore-prefixed
  (for example `_sensorModuleType`, `_videoType`, `_timelapseResolution`,
  `_topBottomCorrection`, `_MuteEnable`, `_batteryCapacity`, `_sysTimestamp`,
  `_fileGroup` / `_localFileGroup`) and must be treated as optional: absence is not an
  error.
- C5. File listing is `camera.listFiles` with vendor-defined entry counts and continuation;
  deletion is `camera.delete`.
- C6. Media is downloaded over plain HTTP from URLs the camera returns.

## Functional requirements and acceptance criteria

Acceptance criteria are written to be executable against the fake OSC camera
(ADR-0007). Identifiers are referenced by the task breakdown.

### FR-1 — Connection and status

**FR-1.1 `camera_connect`** establishes and validates a session with the camera.

- AC-1.1.1 **Given** a reachable camera, **when** `camera_connect` is called, **then** the
  result reports `connected: true`, the model name, firmware version, serial number, the
  API level and the resolved base URL, within the configured timeout.
- AC-1.1.2 **Given** no camera on the network, **when** `camera_connect` is called,
  **then** the tool returns `code: "not_connected"` within the connect timeout (default
  5 s), with a message that names the expected address and tells the user to join the
  camera's Wi-Fi access point. No stack trace or raw exception text is exposed.
- AC-1.1.3 **Given** a host connected to a *different* network where `192.168.42.1`
  answers with non-OSC content, **when** `camera_connect` is called, **then** the result is
  `not_connected` with a reason distinguishing "unexpected response" from "no response".
- AC-1.1.4 Connection state is cached; a successful `camera_connect` is not required before
  every other tool, but any tool used while disconnected returns `not_connected` rather
  than hanging.

**FR-1.2 `camera_status`** reports live device state.

- AC-1.2.1 **Given** a connected camera, **when** `camera_status` is called, **then** the
  result includes battery level, charging state, storage free and total bytes, remaining
  recordable time or photo count where the camera supplies it, current capture mode, and
  whether a capture is in progress.
- AC-1.2.2 **Given** a field the camera does not report, **when** the status is built,
  **then** the field is `null` and the result is still valid — a missing optional vendor
  field never fails the call.
- AC-1.2.3 **Given** two `camera_status` calls less than one second apart, **when** the
  second is served, **then** the underlying `/osc/info` request is not repeated within the
  one-second window (cached), honouring C2, and the result indicates its age.
- AC-1.2.4 **Given** the camera is recording, **when** `camera_status` is called,
  **then** `capture_in_progress` is `true` and the active capture's elapsed time is
  reported if the camera supplies it.

**FR-1.3 `camera_info`** returns static device metadata (model, serial, firmware,
supported API levels, declared endpoints) sourced from `/osc/info`.

- AC-1.3.1 The result is stable across calls for an unchanged device and is served from
  cache after the first successful retrieval.

### FR-2 — Capabilities and the unsupported contract

**FR-2.1 `capabilities_list`** returns the capability matrix for the active backend.

- AC-2.1.1 **Given** the OSC backend, **when** `capabilities_list` is called, **then**
  every capability in the enumerated set appears exactly once with
  `supported: true|false`, a `reason` when false, a `docs_url`, and `available_in` naming a
  milestone when the limitation is ours rather than the vendor's.
- AC-2.1.2 `exposure.control` and `preview.live` are reported as **not supported** with
  `available_in: null`, because the vendor's OSC API does not offer them.
- AC-2.1.3 `media.stitch.video` and `media.export` are reported as **not supported** with
  `available_in: "M4"`.
- AC-2.1.4 The capability matrix published in `README.md` is generated from the same
  registry, and a test fails if the two disagree.

**FR-2.2 Unsupported operations fail uniformly.**

- AC-2.2.1 **Given** the OSC backend, **when** a tool requiring an unsupported capability
  is called, **then** the response is the canonical envelope with `code: "unsupported"` and
  fields `backend`, `capability`, `reason`, `docs_url`, `available_in`.
- AC-2.2.2 The capability check happens **before** any network request: an unsupported call
  must not touch the camera.
- AC-2.2.3 Tools whose capability is unsupported are still **declared** in the MCP tool
  list, and their descriptions state the requirement in plain language.
- AC-2.2.4 Every error-taxonomy code (`unsupported`, `not_connected`, `camera_busy`,
  `invalid_argument`, `not_found`, `confirmation_required`, `confirmation_invalid`,
  `permission_denied`, `path_outside_workspace`, `timeout`, `rate_limited`, `storage_full`,
  `job_failed`, `internal`) is reachable and covered by at least one test.

### FR-3 — Options (available settings)

**FR-3.1 `options_get`** reads current settings.

- AC-3.1.1 **Given** a connected camera, **when** `options_get` is called with no argument,
  **then** the server requests the documented default option set and returns each option
  with its current value, its type, and — when the camera reports them — its supported
  values.
- AC-3.1.2 **Given** an explicit list of option names, **when** `options_get` is called,
  **then** only those options are requested from the camera.
- AC-3.1.3 **Given** an option the camera does not report, **when** the response is mapped,
  **then** the option is omitted from the result and listed under `unavailable`, with no
  error.
- AC-3.1.4 Vendor underscore-prefixed options are returned under their documented names and
  flagged `vendor_extension: true`.

**FR-3.2 `options_set`** changes settings.

- AC-3.2.1 **Given** a valid option and value, **when** `options_set` is called, **then**
  the change is applied and the tool returns the resulting values re-read from the camera,
  not merely echoed from the request.
- AC-3.2.2 **Given** a value outside the camera's declared supported values, **when**
  `options_set` is called, **then** the tool returns `invalid_argument` listing the
  supported values, **without** sending the request to the camera.
- AC-3.2.3 **Given** an option name that is not recognised, **when** `options_set` is
  called, **then** the tool returns `invalid_argument` and suggests the closest known names.
- AC-3.2.4 **Given** the camera rejects a `camera.setOptions` call, **when** the vendor
  error is received, **then** it is mapped to a taxonomy code and the vendor's own code and
  message are preserved in the error `details`.
- AC-3.2.5 **Given** an attempt to set an exposure parameter, **when** `options_set` is
  called, **then** the result is `unsupported` for `exposure.control`, per AC-2.2.1.
- AC-3.2.6 Setting multiple options in one call is atomic from the caller's perspective:
  either all requested options are applied, or the tool reports which ones were applied and
  which failed, explicitly.

### FR-4 — Photo capture

**FR-4.1 `camera_take_photo`**

- AC-4.1.1 **Given** a connected, idle camera, **when** `camera_take_photo` is called,
  **then** the tool issues `camera.takePicture`, polls `/osc/commands/status` until
  completion, and returns the resulting file's camera-side URI, file name, size, capture
  timestamp and declared dimensions.
- AC-4.1.2 **Given** the camera is in video mode, **when** `camera_take_photo` is called,
  **then** the tool switches to photo capture mode first (when the camera permits it) and
  reports in the result that the mode was changed; if the mode cannot be changed, it
  returns `camera_busy` or `invalid_argument` with an explanation.
- AC-4.1.3 **Given** a capture is already in progress, **when** `camera_take_photo` is
  called, **then** the tool returns `camera_busy` and does not queue a second command
  (honouring C1).
- AC-4.1.4 **Given** the command does not complete within the configured capture timeout
  (default 30 s), **when** the deadline passes, **then** the tool returns `timeout`
  including the vendor command `id` so the user can reconcile state, and it does not leave a
  poll loop running.
- AC-4.1.5 **Given** `download: true` is requested, **when** the photo completes, **then**
  the tool additionally starts a download job and returns its `job_id` alongside the capture
  result; it does not block on the transfer.
- AC-4.1.6 **Given** storage is full, **when** capture is attempted, **then** the tool
  returns `storage_full` with free-space figures.
- AC-4.1.7 Photo capture never returns image bytes in the MCP response.

### FR-5 — Video recording

**FR-5.1 `camera_start_recording`**

- AC-5.1.1 **Given** a connected, idle camera, **when** `camera_start_recording` is called,
  **then** `camera.startCapture` is issued, the tool returns `recording: true` with the
  start timestamp, and `camera_status` subsequently reports a capture in progress.
- AC-5.1.2 **Given** the camera is already recording, **when** `camera_start_recording` is
  called, **then** the tool returns `camera_busy` and the ongoing recording is not disturbed.
- AC-5.1.3 **Given** the camera is in photo mode, **when** recording is requested, **then**
  the mode is switched first where permitted and the change is reported.

**FR-5.2 `camera_stop_recording`**

- AC-5.2.1 **Given** an active recording, **when** `camera_stop_recording` is called,
  **then** `camera.stopCapture` is issued and the tool returns the recorded file list
  (URIs, names, sizes) and the duration.
- AC-5.2.2 **Given** no active recording, **when** `camera_stop_recording` is called,
  **then** the tool returns `invalid_argument` with a message saying no recording was in
  progress. It is not an internal error.
- AC-5.2.3 **Given** the camera produced a multi-file recording (file grouping), **when**
  the result is built, **then** **all** files in the group are reported, not only the first.
- AC-5.2.4 The result states explicitly that the video is **unstitched** and that stitching
  requires the Media SDK (M4), so no caller assumes a finished 360° video.

### FR-6 — Gallery listing and pagination

**FR-6.1 `files_list`**

- AC-6.1.1 **Given** a camera with media, **when** `files_list` is called with no cursor,
  **then** the tool returns at most `limit` entries (default 25, maximum 100) ordered by
  capture time descending, each with file URI, name, type (`photo`/`video`), size,
  timestamp, duration for videos, and group identity when the camera reports it.
- AC-6.1.2 **Given** more entries exist, **when** a page is returned, **then**
  `next_cursor` is a non-empty opaque string; **when** the last page is returned, **then**
  `next_cursor` is `null`.
- AC-6.1.3 **Given** a `next_cursor` from a previous page, **when** `files_list` is called
  with it, **then** the following page is returned with no duplicated and no skipped entries
  for an unchanged gallery.
- AC-6.1.4 **Given** a malformed, foreign or expired cursor, **when** it is supplied,
  **then** the tool returns `invalid_argument`; cursors are opaque, validated, and never a
  vehicle for injecting arbitrary request parameters.
- AC-6.1.5 **Given** filters (`type`, `since`, `until`), **when** supplied, **then** only
  matching entries are returned, and filters are stable across pages (encoded in the cursor).
- AC-6.1.6 **Given** an empty gallery, **when** `files_list` is called, **then** the result
  is an empty list with `next_cursor: null` and no error.
- AC-6.1.7 A single response never exceeds the configured maximum entry count, so an agent's
  context cannot be flooded by a large card.

**FR-6.2 `files_get`** returns metadata for one file by URI.

- AC-6.2.1 **Given** a known URI, the full metadata record is returned, including whether a
  local copy already exists in the workspace and, if so, its workspace-relative path.
- AC-6.2.2 **Given** an unknown URI, the tool returns `not_found`.

### FR-7 — Download as a job

**FR-7.1 `files_download`**

- AC-7.1.1 **Given** one or more file URIs, **when** `files_download` is called, **then**
  the tool validates input, enqueues a job and returns `job_id` with state `queued` or
  `running` **within 2 seconds**, regardless of file size.
- AC-7.1.2 **Given** a running download, **when** `jobs_get` is called, **then** it reports
  `bytes_done`, `bytes_total` when the server supplies a length, per-file progress, and
  elapsed time.
- AC-7.1.3 **Given** a completed download, **when** `jobs_get` is called, **then** state is
  `succeeded` and the result lists, per file, the **workspace-relative** path, byte size and
  checksum. No absolute host path and no file content appear in any response.
- AC-7.1.4 **Given** a destination that resolves outside the configured workspace
  (absolute path, `..` traversal, or a symlink escaping it), **when** `files_download` is
  called, **then** the tool returns `path_outside_workspace` and nothing is written.
- AC-7.1.5 **Given** a download is interrupted (connection lost, camera powered off),
  **when** the failure is detected, **then** the job ends `failed` with a typed error, and
  **no partial file remains** under the final name — downloads write to a temporary name and
  are atomically renamed only on success.
- AC-7.1.6 **Given** `jobs_cancel` is called on a running download, **when** the worker
  reaches its next chunk boundary, **then** the job transitions to `cancelled` within
  2 seconds, partial data is removed, and the response reports what was discarded.
- AC-7.1.7 **Given** the concurrency limit is reached, **when** another download is
  requested, **then** it is accepted in state `queued` and started when a slot frees; the
  limit is never exceeded.
- AC-7.1.8 **Given** a target file already exists in the workspace, **when** a download is
  requested, **then** behaviour follows an explicit `on_conflict` argument
  (`skip` | `overwrite` | `rename`, default `skip`), and the chosen behaviour is reported.
- AC-7.1.9 **Given** the same `idempotency_key` as an in-flight or recent job, **when**
  `files_download` is called again, **then** the existing `job_id` is returned and no
  duplicate transfer starts.
- AC-7.1.10 **Given** insufficient local disk space, **when** the job starts, **then** it
  fails fast with `storage_full` naming required and available bytes.
- AC-7.1.11 Downloads stream to disk; peak process memory does not scale with file size.

**FR-7.2 Job management** — the shared control surface for background work.

- AC-7.2.1 `jobs_list` returns jobs newest-first with the same pagination contract as
  `files_list`, filterable by state and kind.
- AC-7.2.2 `jobs_get` on an unknown or evicted `job_id` returns `not_found` with a note
  about the retention policy.
- AC-7.2.3 `jobs_cancel` on an already-terminal job returns the current state and is a
  no-op, not an error.
- AC-7.2.4 Job records are retained for a bounded count and age; eviction is documented and
  configurable.
- AC-7.2.5 Jobs do not survive a server restart in M1, which the tool descriptions state
  explicitly; on startup, orphaned temporary files in the workspace are swept.

### FR-8 — Deletion (destructive, guarded)

**FR-8.1 `files_delete`**

- AC-8.1.1 Destructive tools are **disabled by default**; when disabled, `files_delete`
  returns `permission_denied` explaining the configuration flag that enables it.
- AC-8.1.2 **Given** deletion is enabled and no confirmation token is supplied, **when**
  `files_delete` is called, **then** nothing is deleted and the tool returns
  `confirmation_required` with a single-use token, the token's expiry, and an exact list of
  the files that would be deleted.
- AC-8.1.3 **Given** the matching token within its lifetime and the identical target set,
  **when** the call is repeated, **then** the files are deleted and the result reports what
  was removed.
- AC-8.1.4 **Given** an expired, reused, unknown, or target-mismatched token, **when**
  supplied, **then** the tool returns `confirmation_invalid` and nothing is deleted.
- AC-8.1.5 Tokens are single-use, time-limited (default 120 s), and bound to the exact
  target set; changing the target set invalidates the token.

### FR-9 — Transport, configuration and logging

- AC-9.1 **Given** default configuration, **when** the server starts, **then** it uses
  stdio transport and opens no network listener.
- AC-9.2 **Given** `SPHERELOOM_TRANSPORT=http` without a token, **when** the server starts,
  **then** it refuses to start with a clear configuration error.
- AC-9.3 **Given** HTTP transport with a token, **when** the server starts, **then** it
  binds `127.0.0.1` by default, rejects requests with a missing or wrong token using
  constant-time comparison, and logs a startup warning about the exposure.
- AC-9.4 **Given** a non-loopback bind address without the explicit risk-acknowledgement
  flag, **when** the server starts, **then** it refuses.
- AC-9.5 **Given** any log output, **when** it is emitted, **then** Wi-Fi SSIDs, bearer
  tokens, confirmation tokens, query strings and absolute user paths are redacted, and logs
  go to stderr only — stdout carries protocol traffic exclusively.
- AC-9.6 **Given** an invalid configuration (missing workspace, unwritable directory,
  nonsensical limits), **when** the server starts, **then** it fails fast with a message
  naming the offending variable and the expected form.
- AC-9.7 **Given** `SPHERELOOM_CAMERA_BACKEND=fake`, **when** the server starts, **then**
  the fake camera backend serves every tool in this spec, enabling a complete demo with no
  hardware.

### FR-10 — Resilience

- AC-10.1 Every outbound HTTP call has explicit connect, read and total timeouts; no call
  can hang indefinitely.
- AC-10.2 Transient failures (connection reset, 5xx, timeouts) on **idempotent** requests
  are retried with bounded exponential backoff and jitter; capture and option-setting
  commands are **never** blindly retried, to avoid duplicate shots or conflicting state.
- AC-10.3 Commands are serialised per C1; a test asserts that two concurrent capture calls
  never produce overlapping `/osc/commands/execute` requests.
- AC-10.4 `/osc/info` requests are rate-limited to at most one per second per C2, verified
  by a test counting requests against the fake camera.
- AC-10.5 Malformed or truncated JSON from the camera produces a typed `internal` error
  containing a redacted excerpt, never an unhandled exception that terminates the server.
- AC-10.6 The camera disappearing mid-session (hotspot drop) causes subsequent calls to
  return `not_connected` with recovery guidance; the server stays alive and recovers when
  the camera returns.

## Non-functional requirements

| ID | Requirement |
| ---- | ------------- |
| NFR-1 | Non-job tool calls return within 3 s against a responsive camera (excluding capture completion, which is bounded by the capture timeout) |
| NFR-2 | `files_download` returns a `job_id` within 2 s regardless of file size |
| NFR-3 | Memory usage does not scale with downloaded file size (streamed to disk) |
| NFR-4 | Server cold start to ready under 2 s on a typical laptop |
| NFR-5 | Full unit + component suite runs in under 60 s with no hardware |
| NFR-6 | Supported platforms for M1: Windows 10+, Linux, macOS (pure Python; no vendor SDK involved) |
| NFR-7 | Python 3.12 and 3.13 |
| NFR-8 | Every tool description is written for an LLM consumer and states preconditions, the capability it needs, and whether it is destructive |
| NFR-9 | All code, comments, documentation and error messages are in English |

## Definition of done

- [ ] All FR acceptance criteria pass against the fake OSC camera in CI.
- [ ] `mypy --strict` and `ruff` clean; coverage ≥ 85%.
- [ ] Every error-taxonomy code has at least one test.
- [ ] README capability matrix generated from the registry and verified by test.
- [ ] Hardware tier executed once by the maintainer on a real X5; firmware version recorded
      in the release notes; any divergence reproduced in the fake camera first.
- [ ] `CHANGELOG.md` updated; configuration documented in `.env.example`.
- [ ] Documentation states plainly that 360° video stitching is not available until M4.

## Open questions

| ID | Question | Owner | Blocking? |
| ---- | ---------- | ------- | ----------- |
| OQ-1 | Which option set should `options_get` request by default, balancing usefulness against request size? | @jluqueba | No — resolve during implementation against a real X5 |
| OQ-2 | Does the X5's `camera.listFiles` continuation behave well enough to back an opaque cursor directly, or must we maintain an offset? | @jluqueba | No — fake camera implements both; hardware tier decides |
| OQ-3 | Should `camera_take_photo` auto-switch capture mode by default, or require an explicit argument? | @jluqueba | No — default to auto-switch with the change reported (AC-4.1.2) |
| OQ-4 | Minimum viable default for job retention count/age | @jluqueba | No |
