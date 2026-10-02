# ADR-0012: Optional `sphereloom-assistant` layer via `agent.as_mcp_server()`

- Status: accepted (deferred milestone — documented now, implemented later)
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: [ADR-0002](0002-mcp-transport-and-security-posture.md),
  [ADR-0008](0008-microsoft-agent-framework-for-example-agent.md),
  [ADR-0010](0010-agent-framework-stays-out-of-the-server-core.md)

> **Not part of Milestone 1 or Milestone 2 delivery.** This ADR records an approved design
> for a deferred milestone scheduled after M2. Nothing described here is implemented, and
> the base server never depends on it.

## Context

ADR-0010 keeps the LLM on the client side of the MCP boundary: the server exposes granular,
deterministic tools and nothing reasons inside it. That is the right default, but it leaves
a real user segment unserved.

Some users do not want to orchestrate `camera_connect` → `options_set` →
`camera_start_recording` → wait → `camera_stop_recording` → `files_list` →
`files_download` → `jobs_get`. They want to say **"record thirty seconds and download it"**
and get a path back. Today that orchestration has to be written by whoever integrates
SphereLoom, and a weaker model will frequently get the job-polling pattern wrong.

The Microsoft Agent Framework offers a direct answer: `agent.as_mcp_server()` publishes an
LLM-backed agent as an MCP server — the agent-as-a-tool pattern. On slim installs it
additionally requires the optional `mcp` package. The framework is MIT licensed
(`agent-framework` 1.19.0, Python 3.10–3.14), so there is no licensing obstacle.

The risk is obvious: done carelessly, this would reintroduce everything ADR-0010 rejected —
credentials required to start, token cost on trivial operations, and a non-deterministic
decision-maker in the path of destructive operations.

## Decision

Build **`sphereloom-assistant`** as a deferred milestone after M2: a **second, optional
entry point** that wraps the SphereLoom MCP server in a SphereLoom-aware agent and exposes
it through `agent.as_mcp_server()` as a small set of high-level, intent-shaped tools.

Non-negotiable constraints:

1. **Separate entry point, never the base server.** `sphereloom` (the deterministic server)
   and `sphereloom-assistant` are different commands. The assistant is a *client* of the
   base server, connecting over stdio exactly like any other MCP client — it does not
   import the server's internals and does not bypass any of its guard rails.
2. **Separate installation extra.** Shipped as `pip install sphereloom[assistant]`. The
   base install pulls in no agent framework and no model client (ADR-0010 §1).
3. **User-supplied credentials.** The assistant requires the user's own model credentials,
   configured through environment variables, provider-agnostic. SphereLoom ships no keys,
   no default provider and no hosted endpoint. Without credentials the assistant refuses to
   start with a clear message; the base server is unaffected.
4. **Never required.** No feature of Milestones 1–4 depends on the assistant. Removing it
   entirely would not break anything.
5. **All server-side guard rails still apply.** The assistant runs behind the same path
   jail, confirmation tokens, concurrency caps and redaction (ADR-0002), because it is
   simply another caller. It cannot grant itself permissions the server does not give.
6. **Destructive operations are not delegated to the model.** The assistant's tool
   allow-list excludes destructive operations by default; where they are enabled, the
   confirmation step is surfaced to the human rather than auto-answered by the agent.
   The assistant must never auto-supply a confirmation token.
7. **Honest documentation.** The assistant is documented as an **optional convenience layer
   on top of the foundation**, with its trade-offs stated plainly: non-deterministic,
   costs tokens, requires credentials, harder to audit. Users who need determinism are
   pointed at the base server.
8. **No MCP sampling.** Consistent with ADR-0010 §6.

### Candidate high-level tools (indicative, not a contract)

`capture_session` ("record N seconds and download"), `fetch_recent` ("download today's
shots"), `describe_camera_state` (a narrated status summary). The final surface is designed
in the milestone's own feature spec.

## Consequences

### Positive

- Serves the "just do the thing" user without compromising the deterministic core.
- The orchestration knowledge we have — especially the start-job/poll/read-path pattern —
  lives in one maintained place instead of being re-derived badly by every integrator.
- Clean architectural story: the base server is the foundation, the assistant is a layer,
  and the boundary between them is an ordinary MCP connection that can be inspected.
- Demonstrates MAF's agent-as-a-tool pattern on a genuinely useful example.

### Negative / costs

- A second entry point, a second installation story, a second set of documentation and a
  second thing to keep working as the framework evolves.
- Non-deterministic behaviour becomes part of the project's public face, and some users will
  judge SphereLoom by the assistant's worst answer rather than the server's correctness.
- Debugging spans two processes and a model.
- Token cost and credential requirements for users who opt in.

### Neutral

- Being deferred, the design can absorb what Milestones 1 and 2 teach us about how agents
  actually misuse the granular tools; that evidence should shape the high-level surface.

## Alternatives considered

### Put the high-level tools in the base server

Rejected by ADR-0010: it would require credentials to start a camera server and put a model
in the path of destructive operations.

### Ship only prompt recipes and documentation

Cheap, and we will do this regardless. But recipes are untested text that rots, whereas an
assistant entry point can be exercised in CI (in a scripted, model-free mode for the
mechanical parts).

### Implement it now, alongside Milestone 1

Rejected. The foundation must be solid and the granular surface proven before a convenience
layer is built on it; building both at once risks designing the high-level tools around
assumptions the real tools do not satisfy.

### A separate repository

Considered; keeping it in-repo under an extra keeps the version contract between the
assistant and the tool surface obvious. Revisit if the assistant grows its own release
cadence.

## Follow-ups

- Write the feature spec for `sphereloom-assistant` when the milestone is scheduled, after
  Milestone 2 ships.
- Decide how the assistant surfaces confirmation prompts to a human before any destructive
  capability is enabled.
- Add an import-boundary test asserting the base package never imports the assistant extra.
