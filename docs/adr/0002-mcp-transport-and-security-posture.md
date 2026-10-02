# ADR-0002: MCP transport and security posture

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: ADR-0001, ADR-0003, ADR-0004

## Context

SphereLoom gives an AI agent control of a physical device and write access to a local
filesystem. The threat picture is unusual for an MCP server:

- The camera's OSC API has **no application-level authentication**. The only gate is
  possession of the camera's Wi-Fi credentials, and the only protocol-level marker is a
  static `X-XSRF-Protected: 1` header. Anything that can reach `192.168.42.1` can command
  the camera.
- While connected, the host machine is joined to the camera's access point. Other devices
  on that hotspot are, by construction, inside the trust boundary.
- Some operations are irreversible: deleting media, and (from M3) formatting storage.
- LLM-driven callers can be prompt-injected via file names, camera metadata, or user
  content, so "the agent asked for it" is not an authorisation signal.
- Logs can easily leak Wi-Fi SSIDs, tokens and personal directory paths into pasted issue
  reports.

The MCP specification offers stdio and HTTP-based transports. Remote MCP servers raise the
exposure dramatically; local stdio servers inherit the trust of the process that spawned
them.

## Decision

### Transport

1. **stdio is the default and recommended transport.** The MCP client spawns SphereLoom as
   a child process; no network listener exists.
2. **HTTP (streamable) transport is opt-in**, enabled only by explicit configuration
   (`SPHERELOOM_TRANSPORT=http`), and when enabled it:
   - **binds to loopback by default** (`127.0.0.1`) and refuses to start on a non-loopback
     address unless an explicit "I understand the risk" flag is also set;
   - **requires a bearer token** (`SPHERELOOM_HTTP_TOKEN`); the server refuses to start
     without one, compares tokens in constant time, and never logs the value;
   - logs a prominent warning at startup describing the exposure.
3. No other transports are supported. WebSocket support is not planned.

### Filesystem containment

4. All writes go into a single configured **workspace directory**
   (`SPHERELOOM_WORKSPACE_DIR`). Every path that crosses the tool boundary is resolved,
   symlink-expanded and verified to remain inside the workspace (**path jail**). Tool
   results return **workspace-relative** paths, never absolute host paths.
5. Tools never accept arbitrary absolute destinations, never follow `..`, and reject
   reserved or non-portable filenames. Downloaded files are written to a temporary name and
   atomically renamed on completion.

### Destructive operations

6. Destructive operations (delete media now; format storage from M3) use a **two-step
   confirmation protocol**: the first call returns a short-lived, single-use
   `confirmation_token` together with an exact description of what would be affected; the
   operation executes only when the same call is repeated with that token. Tokens are
   bound to the exact target set, expire (default 120 s) and are consumed on use.
7. Destructive tools are additionally **disabled by default** and must be enabled by
   configuration.

### Resource bounds

8. Job concurrency is bounded (default 2) and queue depth is capped (ADR-0004); every
   outbound HTTP call has an explicit connect/read/total timeout; OSC command calls are
   serialised per the vendor's sequencing rule and `/osc/info` polling is rate-limited to
   at most one request per second.
9. Download sizes and job retention are bounded and configurable; the server never buffers
   a media file in memory in full and never returns binary payloads over MCP.

### Observability

10. Structured logging to **stderr** (stdout is reserved for the stdio transport), with a
    redaction filter applied to SSIDs, bearer tokens, query strings and absolute user paths.
    Redaction is on by default and can only be weakened explicitly.

## Consequences

### Positive

- The default configuration has no listening socket, no credentials to leak and no path
  outside the workspace.
- A prompt-injected agent cannot silently destroy media: it must surface a confirmation
  step that a human or policy layer can intercept.
- Bounded concurrency and serialised commands also keep us within the vendor's documented
  request-pacing guidance, improving reliability on real hardware.

### Negative / costs

- The two-step confirmation adds a round trip and some agent-prompting complexity; tool
  descriptions must explain it clearly or models will mis-handle it.
- Workspace-relative paths mean clients that want to open a file need an explicit
  "resolve path" affordance rather than reading an absolute path from the result.
- Refusing to start without a token in HTTP mode will frustrate some experimenters. That is
  the intent.

### Neutral

- These controls are independent of the backend, so the USB and Media SDK milestones
  inherit them unchanged.

## Alternatives considered

### HTTP transport by default

Rejected. It converts every installation into a network service with no authentication
story of its own, on a machine that is simultaneously joined to a shared camera hotspot.

### Relying on the MCP client's own approval UI

Clients such as the Microsoft Agent Framework do offer per-tool approval modes
(`approval_mode`, `allowed_tools`). Useful, and we document them — but they are a property
of the *caller*, not of the server. A server that is only safe when the client cooperates
is not safe. We implement confirmation tokens server-side and treat client approval as
defence in depth.

### Signed capability tokens / full authorisation framework

Rejected as over-engineering for a locally spawned process. Revisit if a multi-user or
hosted deployment ever becomes a goal.

### Sandboxing the process (containers, seccomp)

Out of scope for the project to impose, and in direct tension with USB device access in M3.
Documented as an operator-side option instead.

## Follow-ups

- Document recommended `approval_mode`/`allowed_tools` settings for the example agent
  (ADR-0008).
- Revisit the HTTP posture if and when the MCP specification standardises an authorisation
  story we can adopt wholesale.
