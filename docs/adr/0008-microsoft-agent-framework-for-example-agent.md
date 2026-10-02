# ADR-0008: Microsoft Agent Framework for the example agent

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: [ADR-0001](0001-language-runtime-and-mcp-sdk.md),
  [ADR-0002](0002-mcp-transport-and-security-posture.md),
  [ADR-0007](0007-testing-strategy-without-hardware.md),
  [ADR-0010](0010-agent-framework-stays-out-of-the-server-core.md),
  [ADR-0011](0011-job-manager-stays-in-house.md),
  [ADR-0012](0012-optional-sphereloom-assistant-layer.md)

## Context

An MCP server is only half a demonstration. Users need to see an agent actually planning a
shoot, calling the tools in order, polling a job and reporting where the file landed.
SphereLoom therefore ships an **example agent** in-repository.

Requirements for that example:

- Must speak MCP as a client, over **stdio** (the default and recommended transport,
  ADR-0002).
- Must support **per-tool approval and allow-listing**, because the demo's whole point
  includes showing how to keep a model away from destructive operations.
- Must be **provider-agnostic**: configured through environment variables, not hard-wired
  to one model vendor.
- Must be **runnable in CI with no API keys**, otherwise it rots.
- Must be permissively licensed (no GPL, per ADR-0006) and in Python (ADR-0001).

Verified capabilities of the chosen framework: the PyPI package `agent-framework`, version
1.19.0, is **MIT licensed** and supports Python 3.10–3.14 — so it satisfies the project's
no-GPL constraint (ADR-0006). It exposes `Agent`, `MCPStdioTool`, `MCPStreamableHTTPTool`
and `MCPWebsocketTool`; it supports `approval_mode` and `allowed_tools` per MCP tool; and
human-in-the-loop function approvals are documented
(<https://learn.microsoft.com/agent-framework/agents/tools/local-mcp-tools>). It also offers
`agent.as_mcp_server()`, which publishes an LLM-backed agent as an MCP server; that pattern
is deliberately **not** used here and is instead scoped to the deferred optional layer in
ADR-0012. Separately, MCP server-initiated *sampling* (`sampling_callback`) is deprecated as
of the MCP specification dated 2026-07-28.

## Decision

1. The example agent is built with the **Microsoft Agent Framework** (distribution
   `agent-framework`, import `agent_framework`), using `MCPStdioTool` to launch and talk to
   the SphereLoom server over stdio.
2. It demonstrates the safety posture explicitly: `allowed_tools` restricted to the
   read-and-capture set, and `approval_mode` requiring human approval for anything
   destructive. The configuration is the teaching material, not an afterthought. These
   client-side approvals are **defence in depth**: they complement, and never replace, the
   server-side two-step confirmation token (ADR-0002, ADR-0010 §5).
3. The agent is **provider-agnostic**, configured entirely by environment variables
   (`SPHERELOOM_AGENT_PROVIDER`, `SPHERELOOM_AGENT_MODEL`, `SPHERELOOM_AGENT_ENDPOINT`,
   `SPHERELOOM_AGENT_API_KEY`). No provider is hard-coded and no key is ever committed.
4. The agent has two modes, selected by `SPHERELOOM_AGENT_MODE`:
   - `llm` — a real model drives the tool calls;
   - `scripted` — a deterministic, offline script issues the same MCP calls in the same
     order with no model and no API key.
   **`scripted` mode runs in CI** against the fake camera backend (ADR-0007), which means
   the example is continuously verified end to end: server startup, tool discovery, capture,
   listing, download-as-job, polling and result reporting.
5. **SphereLoom does not implement or rely on MCP server-initiated sampling.** It is
   deprecated in the current specification; the server never asks the client for model
   completions.
6. The example lives under `examples/agent/` and is **not** a runtime dependency of the
   server package. `agent-framework` is an optional/dev dependency only, so installing
   SphereLoom never pulls an agent framework in. The boundary itself is a decision in its
   own right: see ADR-0010.
7. The example is documentation-grade: a short README, a single entry point, and inline
   explanation of why each safety setting is set the way it is.
8. **`agent-framework-devui` is a local exploration aid only.** It is currently a beta
   release (`1.0.0b…`), so it may be used manually while developing the example but must
   never become a CI dependency or part of the `ci-gate` path.

## Consequences

### Positive

- A first-class, maintained demonstration of the intended usage pattern, including the
  job-polling flow that agents otherwise get wrong.
- `approval_mode` and `allowed_tools` give us a concrete, runnable illustration of
  defence in depth layered on top of the server-side confirmation tokens (ADR-0002).
- The `scripted` mode makes the example a genuine integration test of the MCP surface, at
  zero cost and with no secrets in CI.
- Staying provider-agnostic avoids implying an endorsement of any model vendor and keeps
  the example useful to everyone.

### Negative / costs

- A dependency on a framework that is evolving; API churn will occasionally require example
  updates. Contained by keeping the example small and out of the server's dependency graph.
- Two modes mean two code paths to keep aligned; mitigated by sharing the call sequence
  between them so `scripted` exercises the same steps the `llm` prompt describes.
- Choosing one framework may read as a recommendation. The README states plainly that any
  MCP client works and documents raw client configuration alongside the example.

### Neutral

- If the framework's MCP surface changes, the server is unaffected: the coupling is entirely
  on the client side.

## Alternatives considered

### A hand-rolled MCP client script

Maximum control, minimum dependencies — but it would demonstrate nothing about approval
workflows, which is a core part of what we want to show, and it would duplicate protocol
work that the MCP SDK already does.

### LangChain / LlamaIndex adapters

Large dependency surfaces, faster-moving APIs, and a weaker story for per-tool human
approval than the framework we chose. Rejected for the canonical example; contributed
examples are welcome.

### Only documenting configuration for existing MCP clients (editors, desktop apps)

We do this *as well*, but it cannot be tested in CI and it does not demonstrate the
agent-side approval configuration. Rejected as the sole approach.

### Using MCP sampling to let the server ask the client for reasoning

Rejected: deprecated in the current MCP specification, and it would invert the trust
direction in a server that controls hardware.

## Follow-ups

- Add the `scripted`-mode example run to the CI gate as part of Milestone 2.
- Document recommended `allowed_tools` / `approval_mode` values alongside the destructive
  tool documentation.
