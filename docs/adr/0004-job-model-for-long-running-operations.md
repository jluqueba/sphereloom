# ADR-0004: Job model for long-running operations

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: [ADR-0002](0002-mcp-transport-and-security-posture.md),
  [ADR-0003](0003-ports-and-adapters-with-capability-registry.md),
  [ADR-0005](0005-sidecar-isolation-for-vendor-sdks.md),
  [ADR-0011](0011-job-manager-stays-in-house.md) — which records why an off-the-shelf
  workflow engine was not used to implement this model

## Context

Several SphereLoom operations take far longer than a comfortable tool-call round trip:

- Downloading media from the camera over Wi-Fi. A single 360° video file is routinely
  hundreds of megabytes to several gigabytes, and the camera's access point is not fast.
- From M4, stitching and exporting video through the Media SDK, which can run for minutes
  on a GPU.
- Batch operations over many files.

MCP tool calls are request/response. A multi-minute call will hit client timeouts, block an
agent's reasoning loop, and give the user no visibility or means of cancellation. Worse,
returning media *content* through MCP would blow up context windows and transport buffers:
a 2 GB video has no business being base64-encoded into a model's conversation.

There is also a sequencing constraint: the vendor asks that no new `/osc/commands` request
be issued before the previous response arrives, so concurrency has to be governed centrally
rather than left to whoever calls a tool.

## Decision

1. **Any operation that can exceed a few seconds is a job.** The initiating tool
   (`files_download`, later `media_stitch`, `media_export`) validates its input, enqueues
   work and returns immediately with a `job_id` and the initial job state. It never blocks
   to completion.
2. **A job is a first-class, observable entity** with an explicit state machine:

   `queued → running → (succeeded | failed | cancelled)`

   Transitions are one-way; terminal states are final.
3. **Three management tools** form the control surface, identical across backends:
   - `jobs_list` — paginated, filterable by state and kind;
   - `jobs_get` — full record for one job: state, kind, parameters, progress
     (`bytes_done`/`bytes_total` or `percent`), timestamps, result, error envelope;
   - `jobs_cancel` — requests cooperative cancellation; returns the resulting state.
4. **Results are references, never payloads.** A succeeded download job reports the
   workspace-relative path, size, checksum and content type of what it wrote. MCP responses
   never carry binary media (ADR-0002).
5. **Cooperative cancellation.** Workers check a cancellation signal at chunk boundaries,
   stop promptly, remove partial artefacts and report `cancelled`. Partial files are
   written to a temporary name and atomically renamed only on success, so a cancelled or
   crashed job never leaves a file that looks complete.
6. **Bounded resources.** A configurable concurrency limit (default 2) and a bounded queue
   govern all jobs. Camera-touching jobs additionally serialise through the same lock that
   enforces the vendor's command-sequencing rule, so a download cannot race a capture.
7. **In-process, in-memory registry for M1**, with retention limits (count and age) and a
   persistence seam (`JobStore` protocol) so a durable implementation can be added later
   without changing the tool contract. Jobs do not survive a server restart in M1, and the
   tool documentation says so plainly.
8. **Idempotency key** on job-creating tools: repeating a call with the same key returns
   the existing job instead of starting a duplicate, which protects against agent retries.
9. **Progress is pull-based** in M1 (the agent polls `jobs_get`). MCP progress
   notifications may be added later as an enhancement; the pull contract remains the
   supported baseline so that simple clients keep working.

## Consequences

### Positive

- Tool calls stay fast and predictable; agents keep reasoning while bytes move.
- The user (or the agent) can see progress and abort a long download that was started by
  mistake.
- Context windows stay small: paths and metadata instead of payloads.
- The same model serves M4's GPU-bound stitching without a second mechanism.
- Centralised concurrency control is the natural place to honour the vendor's pacing rules.

### Negative / costs

- Polling adds round trips and some prompt complexity; the agent must be taught the
  "start, then poll, then read the path" pattern. Tool descriptions carry that guidance.
- Non-durable jobs in M1 mean a server restart orphans in-flight work and leaves temporary
  files to be cleaned up at startup.
- Two tool calls minimum for an operation that "feels" like one.

### Neutral

- A job registry gives us a natural place to attach structured audit logging later.

## Alternatives considered

### Synchronous tools with long timeouts

Rejected: client-dependent, gives no progress and no cancellation, and ties up the server
for minutes at a time.

### Returning file content in the MCP response

Rejected outright for media. Token cost and transport limits make it unworkable, and it
would undermine the "keep media local, pass references" design.

### MCP progress notifications as the only mechanism

Rejected as a baseline because client support varies; adopted later as an optional
enhancement on top of the pull contract.

### Shelling out to an external downloader

Rejected: adds a dependency and a process boundary we do not need for an HTTP GET, and
complicates the path jail and cancellation story.

### Workflow or orchestration engines (including Microsoft Agent Framework Workflows)

Rejected for Milestone 1 in [ADR-0011](0011-job-manager-stays-in-house.md), which also
records the concrete conditions under which the decision is revisited at Milestone 4.

## Follow-ups

- Define the startup sweep that removes orphaned temporary files from the workspace.
- Evaluate a durable `JobStore` when M4 introduces jobs long enough to be worth resuming.
