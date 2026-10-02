# ADR-0011: The job manager stays in-house; MAF Workflows rejected for Milestone 1

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: [ADR-0004](0004-job-model-for-long-running-operations.md),
  [ADR-0005](0005-sidecar-isolation-for-vendor-sdks.md),
  [ADR-0010](0010-agent-framework-stays-out-of-the-server-core.md)

## Context

ADR-0004 established that long operations are stateful jobs with a `job_id`, progress,
cancellation and bounded concurrency, backed by an in-process engine with an in-memory
store and a `JobStore` seam for a future durable implementation.

Since the Microsoft Agent Framework is already in the repository for the example agent
(ADR-0008), it is reasonable to ask whether its **Workflows** feature should back the job
manager instead of a bespoke engine, particularly because of its checkpointing story.

Verified facts about MAF Workflows checkpointing:

- Checkpoints are written **at the end of each superstep** and capture executor state,
  pending messages, pending requests/responses and shared state.
- Storage providers: `InMemoryCheckpointStorage`, `FileCheckpointStorage` (local disk,
  single machine) and `CosmosCheckpointStorage` (via `agent-framework-azure-cosmos`).
- Since Python package version 1.13.0, an **entry checkpoint** is also written before the
  first superstep, which makes runs replayable from the beginning.
- Source: <https://learn.microsoft.com/agent-framework/workflows/checkpoints>

Against that, the shape of SphereLoom's actual work matters. A SphereLoom job is a **linear
pipeline** — download, and later download → stitch → export. It is not a graph, there is no
branching, no fan-out/fan-in, no agent deciding the next node. The hard parts of the job
manager are not orchestration at all; they are:

- streaming multi-gigabyte HTTP responses to disk without buffering;
- reporting byte-level progress from in-flight I/O and, later, from a sidecar subprocess
  (ADR-0005);
- cancelling in-flight I/O promptly and cleanly, leaving no file under a final name;
- enforcing concurrency caps and the vendor's command-sequencing rule in the same place;
- keeping every write inside the workspace path jail.

None of those are problems a workflow engine solves, and a superstep boundary is the wrong
granularity for progress on a single long byte stream.

## Decision

1. **Milestone 1 ships the in-house job engine described in ADR-0004.** MAF Workflows are
   **not** used for the job manager.
2. The job engine remains free of any agent-framework dependency, consistent with
   ADR-0010: the deterministic core stays framework-agnostic.
3. The `JobStore` protocol remains the designated seam for durability, so adding
   persistence later does not change the tool contract.
4. **Revisit trigger — explicit and recorded.** Re-evaluate MAF Workflows with
   `FileCheckpointStorage` **if, at Milestone 4, the export pipeline becomes genuinely
   multi-stage *and* there is a requirement to resume after a crash.** Concretely, all of
   the following must hold before the re-evaluation is worth doing:
   - the pipeline has three or more distinct stages with real intermediate artefacts
     (for example stitch → stabilise → encode), and
   - a stage is expensive enough that losing it to a crash or a restart is unacceptable
     (minutes of GPU time, not seconds), and
   - resumption from a stage boundary is actually useful, meaning intermediate artefacts
     are durable and re-usable.
   If those conditions are met, the evaluation compares MAF Workflows + `FileCheckpointStorage`
   against simply implementing a durable `JobStore`, and the outcome is recorded in a new ADR.
5. Until that trigger fires, the documented behaviour stands: jobs do not survive a server
   restart, tool descriptions say so, and the startup sweep removes orphaned temporary
   files.

## Consequences

### Positive

- The deterministic core keeps a minimal dependency surface and no agent-framework coupling.
- Progress and cancellation are modelled at the granularity that matters — byte chunks and
  subprocess messages — rather than at superstep boundaries.
- The engine is small, directly testable with an injected clock, and fully exercised by the
  hardware-free suite (ADR-0007).
- The decision is reversible by design: the revisit trigger is written down with concrete
  conditions, so this is a deliberate deferral rather than a dismissal.

### Negative / costs

- We write and maintain a queue, worker pool, state machine, cancellation and retention
  logic that an off-the-shelf engine would have supplied. This is accepted: the code is
  modest and the requirements are unusual.
- We forgo checkpointing in Milestone 1, so an interrupted download restarts from the
  beginning rather than resuming. For a single HTTP transfer this is a minor loss; HTTP
  range resumption can be added inside the download worker without any workflow engine.
- If the Milestone 4 trigger does fire, part of the engine may be revisited. The `JobStore`
  seam limits the cost.

### Neutral

- Nothing here criticises MAF Workflows; they solve graph orchestration with agent
  participation, which is simply not the problem in front of us.

## Alternatives considered

### MAF Workflows with `FileCheckpointStorage` now

Rejected for Milestone 1. It would couple the deterministic core to an agent framework
(ADR-0010), introduce checkpoint semantics at the wrong granularity for byte-stream
progress, and pay for graph orchestration we do not need for a linear pipeline. Kept alive
through an explicit revisit trigger.

### A general-purpose task queue (Celery, RQ, Dramatiq, APScheduler)

Rejected: all of them assume a broker or an extra process, which is absurd for a locally
spawned stdio server that is supposed to install with one command and open no sockets.

### `asyncio` tasks with no job abstraction at all

Rejected in ADR-0004 already: no progress, no cancellation, no identity, no retention.

### Durable `JobStore` (SQLite) from day one

Not rejected, merely deferred. It is the cheaper answer to "resume after restart" than a
workflow engine, and it is the baseline the Milestone 4 re-evaluation must beat.

## Follow-ups

- Record the revisit trigger in the Milestone 4 planning artefacts so it is actually checked
  rather than forgotten.
- Consider HTTP range resumption in the download worker as a cheap, independent improvement.
