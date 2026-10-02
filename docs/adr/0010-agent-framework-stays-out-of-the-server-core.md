# ADR-0010: The agent framework stays out of the MCP server core

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: [ADR-0002](0002-mcp-transport-and-security-posture.md),
  [ADR-0008](0008-microsoft-agent-framework-for-example-agent.md),
  [ADR-0011](0011-job-manager-stays-in-house.md),
  [ADR-0012](0012-optional-sphereloom-assistant-layer.md)

## Context

SphereLoom uses the Microsoft Agent Framework (MAF) for its example agent (ADR-0008). MAF
is capable enough that it is tempting to pull it deeper into the product: it can host
agents, orchestrate workflows, and even expose an agent **as** an MCP server via
`agent.as_mcp_server()`. The question this ADR settles is where the boundary sits between
"the thing that controls the camera" and "the thing that decides what to do".

Verified facts about the framework:

- PyPI package `agent-framework`, version 1.19.0, **MIT licensed**, supporting Python
  3.10–3.14. MIT satisfies the project's no-GPL constraint (ADR-0006), so licensing is
  **not** the reason for this decision.
- Exposes `Agent`, `MCPStdioTool`, `MCPStreamableHTTPTool`, `MCPWebsocketTool`.
- Documents per-MCP-tool `approval_mode` and `allowed_tools`.
  Source: <https://learn.microsoft.com/agent-framework/agents/tools/local-mcp-tools>
- `agent.as_mcp_server()` can publish an LLM-backed agent as an MCP server; on slim
  installs it additionally requires the optional `mcp` package.
- `agent-framework-devui` is currently a **beta** release (`1.0.0b…`).
- MCP server-initiated sampling (`sampling_callback`) is **deprecated** as of the MCP
  specification dated 2026-07-28.

The relevant property of the SphereLoom server is that it is the component holding the
power to move a physical device and delete irreplaceable footage. Everything in ADR-0002 —
path jail, confirmation tokens, bounded concurrency, redaction — assumes the server behaves
the same way every time it is asked the same thing.

## Decision

1. **The Microsoft Agent Framework is not a dependency of the SphereLoom MCP server
   core.** Neither `agent-framework` nor any other agent/LLM orchestration library appears
   in the server's runtime dependency graph. `pip install sphereloom` must not pull one in.
2. **No LLM runs inside the server.** The server contains no model client, needs no model
   credentials to start, and makes no token-consuming call in the course of serving a tool.
3. **The server's tool surface stays deterministic and auditable.** Given the same inputs,
   the same camera state and the same configuration, a tool produces the same outcome.
   Decisions about *what* to do are the client's; decisions about *whether it is allowed*
   are the server's, and both are rule-based.
4. **MAF is confined to `examples/agent/`** as an optional/dev dependency (ADR-0008), and —
   if and when it ships — to the separately installed `sphereloom[assistant]` extra
   (ADR-0012). Neither is importable from, or required by, `sphereloom.server`,
   `sphereloom.tools`, `sphereloom.adapters` or `sphereloom.jobs`.
5. **Client-side approvals are defence in depth, not the control.** The example agent
   configures `approval_mode` and `allowed_tools` so destructive operations require explicit
   client-side approval. This **complements and never replaces** the server-side two-step
   confirmation token (ADR-0002 §6): a server that is only safe when the client cooperates
   is not safe.
6. **SphereLoom never uses MCP server-initiated sampling.** It is deprecated in the current
   specification, and asking the client for model completions would invert the trust
   direction in a process that controls hardware.
7. **`agent-framework-devui` is a manual exploration tool only.** Being a beta release, it
   may be used locally to poke at the example agent; it must never appear in CI, in the
   `ci-gate` path, or in any required dependency set.
8. An automated check in CI asserts that no module under `src/sphereloom/` imports an agent
   framework, so this boundary cannot erode by accident.

## Consequences

### Positive

- **Installability.** The server has a small, boring dependency tree and starts with no
  credentials. A user with a camera and Python can run it; a user without a model API key
  can still run it.
- **Auditability.** "Why did SphereLoom delete that file?" has a deterministic answer:
  a tool was called with a valid confirmation token. There is no second, non-deterministic
  decision-maker hidden inside the server.
- **Cost and latency.** `camera_status` is an HTTP call, not an inference. Polling a job
  does not burn tokens.
- **Framework agnosticism.** Any MCP client works — an editor, a desktop app, a custom
  script, or a different agent framework entirely. We do not conscript users into our
  framework choice.
- **Testability.** The hardware-free test suite (ADR-0007) stays free of model mocks, API
  keys and non-deterministic assertions.
- **Blast radius.** Churn in a fast-moving agent framework cannot break the server.

### Negative / costs

- Capabilities that genuinely benefit from reasoning — "record thirty seconds and download
  it", "find the shots from this morning" — are not available from the base server. That is
  the gap ADR-0012 addresses as an explicitly optional layer.
- Agent authors must implement the multi-step patterns themselves (start job, poll, read
  path). We mitigate this with LLM-oriented tool descriptions and the worked example.
- Two installation stories (base server versus assistant extra) is more documentation than
  one would be.

### Neutral

- The decision is about architecture, not licensing: `agent-framework` is MIT and would be
  perfectly acceptable as a dependency on licence grounds alone.

## Alternatives considered

### Build the server on top of MAF so tools can reason internally

Rejected. It would make model credentials a prerequisite for starting a camera server, put
a non-deterministic decision in the path of destructive operations, add token cost to
trivial status calls, and couple a hardware control plane to a fast-moving framework. The
LLM belongs on the client side of the MCP boundary; that boundary is the product's main
safety asset.

### Make MAF an optional runtime dependency of the server, activated by configuration

Softer, but it still means the server has two behavioural modes, one of which is
non-deterministic, and that every security review has to consider both. If an agent-backed
surface is wanted, a separate entry point (ADR-0012) is cleaner than a mode switch.

### Use MCP sampling so the server can ask the client's model for help

Rejected: deprecated in the current MCP specification, and it inverts the trust direction.

### Rely solely on client-side `approval_mode` for destructive operations

Rejected as a control, adopted as defence in depth. Client approval is a property of the
caller and can be disabled, misconfigured or simply not implemented by another client.

## Follow-ups

- Add the import-boundary check to the CI gate (Milestone 0 tooling, enforced from
  Milestone 2 when `agent-framework` first appears in the repository).
- Document the base-versus-assistant installation split in the README once ADR-0012 is
  scheduled.
