# ADR-0003: Ports and adapters with a capability registry

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: ADR-0004, ADR-0005, ADR-0007

## Context

SphereLoom will eventually speak to the same camera through three very different vendor
surfaces, each with a different capability set:

| Capability | OSC (Wi-Fi) | Camera SDK (USB) | Media SDK |
| --- | --- | --- | --- |
| Take photo | Yes | Yes (HDR/AEB/RAW variants) | — |
| Start/stop video | Yes | Yes | — |
| List / download / delete files | Yes | Yes | — |
| Read options | Yes | Partial | — |
| Exposure / white balance control | **No** | Yes | — |
| Live preview / streaming | **No** | Yes (raw dual-fisheye over USB) | — |
| Storage state / format | Partial | Yes | — |
| Video stitching | **No** (photos stitch in camera) | — | Yes |
| Stabilisation, colour grading | — | — | Yes |
| Trim / merge / timeline editing | **No** | **No** | **Not documented → treat as unavailable** |

Capabilities also vary by **camera model** within a single backend, and the vendor
explicitly reserves the right to withdraw SDK access or features at any time.

Two failure modes must be avoided:

1. **Tool-surface churn** — renaming or duplicating tools per backend
   (`osc_take_photo`, `usb_take_photo`) would force every agent prompt and every piece of
   documentation to be rewritten per transport.
2. **Opaque failure** — an agent calling a tool that cannot work on the current backend and
   receiving a timeout, an HTTP 400, or an invented success.

## Decision

### Hexagonal structure

Adopt **ports and adapters**:

- **Ports** (`sphereloom.ports`) are `typing.Protocol` definitions describing what the
  domain needs: `CameraPort` (connectivity, status, options, capture, file listing, file
  retrieval, deletion) and `MediaPort` (stitching and export). Ports speak in domain models
  only — never in vendor payloads, HTTP responses or SDK handles.
- **Adapters** (`sphereloom.adapters.*`) implement the ports:
  `OscCameraAdapter` (M1, `httpx`), `FakeCameraAdapter` (test/demo),
  `UsbCameraAdapter` (M3, sidecar), `MediaSdkAdapter` (M4, sidecar).
- **Tools** (`sphereloom.tools`) are thin: validate input, check capability, call the port,
  map the domain result to an output model. No vendor-specific branching lives in a tool.
- Dependency direction is strictly inward: adapters depend on ports, never the reverse. No
  module outside `sphereloom.adapters.<backend>` may import that backend's vendor details.

### Capability registry

A single declarative registry (`sphereloom.capabilities`) answers one question:
*"can capability X be performed by backend B on camera model M, and if not, why not?"*

- Capabilities are an enumerated, documented set (for example `photo.capture`,
  `video.record`, `files.list`, `files.download`, `files.delete`, `options.read`,
  `options.write`, `exposure.control`, `preview.live`, `media.stitch.video`,
  `media.export`).
- Entries are **data**, not code: backend × capability → `supported | unsupported |
  requires_milestone`, each with a human-readable `reason` and a `docs_url`.
- Adapters additionally report **runtime** capability refinements discovered from the
  device (model, firmware, available options), which are merged over the static table.
- `capabilities_list` is itself an MCP tool, so an agent can plan before acting.

### Unified tool surface and the `unsupported` contract

- The MCP tool surface is **identical regardless of backend**. Tools that a given backend
  cannot serve are still **declared**, and return a structured error rather than
  disappearing — a disappearing tool is invisible to an agent's planner and produces worse
  behaviour than an explained refusal.
- Every tool consults the capability registry **before** dispatching to the port. When the
  capability is absent, the tool returns the canonical envelope:

  ```json
  {
    "error": {
      "code": "unsupported",
      "backend": "osc",
      "capability": "exposure.control",
      "reason": "The vendor's Open Spherical Camera API does not expose exposure parameter adjustment.",
      "docs_url": "https://onlinemanual.insta360.com/developer/en-us/resource/integration",
      "available_in": null
    }
  }
  ```

  `available_in` names the milestone when the capability is merely *not yet* implemented
  (for example `"M4"` for `media.stitch.video`) and is `null` when the vendor API simply
  does not offer it.
- Tool descriptions state the capability requirement in plain language so that a model can
  avoid the call in the first place.

### Error taxonomy

All tool failures use the same envelope with a closed set of `code` values:
`unsupported`, `not_connected`, `camera_busy`, `invalid_argument`, `not_found`,
`confirmation_required`, `confirmation_invalid`, `permission_denied`, `path_outside_workspace`,
`timeout`, `rate_limited`, `storage_full`, `job_failed`, `internal`.
Each code is documented once, mapped from exactly one exception class, and covered by at
least one test (ADR-0007).

## Consequences

### Positive

- Adding the USB backend in M3 changes adapters and registry data, not the agent-facing
  contract, the prompts or the documentation.
- The fake adapter is just another adapter, which is what makes hardware-free testing
  cheap (ADR-0007).
- "Why did that not work?" has a machine-readable, link-carrying answer, which directly
  serves the project's honesty principle.
- Capability data is reviewable: a pull request that claims a new capability is a visible
  one-line diff with a citation.

### Negative / costs

- Extra indirection for operations that are currently served by exactly one backend; in M1
  the registry looks like bureaucracy. We accept the cost now to avoid a rewrite at M3.
- Static capability data can drift from firmware reality; mitigated by runtime refinement
  and contract tests against recorded vendor responses.
- Declaring tools that cannot run risks an agent "wasting" a call. Tool descriptions and
  `capabilities_list` mitigate this, and an explained refusal is cheap.

### Neutral

- The registry becomes the natural home for per-model differences as more cameras are
  supported.

## Alternatives considered

### Backend-specific tool names

Rejected: forces prompt and documentation churn, and leaks our internal transport choice
into the agent's mental model.

### Dynamically hiding unsupported tools

Rejected as the primary mechanism: an absent tool gives the model no explanation and no
path forward, and it makes the tool list non-deterministic across sessions, which is
hostile to caching and to reproducible evaluations. (A future opt-in "hide unsupported
tools" flag is acceptable for token-constrained clients, but the default stays "declare and
explain".)

### Lowest-common-denominator surface

Exposing only what every backend supports would permanently forfeit exposure control and
live streaming. Rejected.

### Capability discovery purely at runtime

Rejected as the sole mechanism: some limitations (no live preview over OSC) are documented
vendor facts that should be answerable without a camera present, including in tests and
documentation generation.

## Follow-ups

- Generate the README capability matrix from the registry so documentation cannot drift.
- Record representative vendor responses as fixtures for contract tests.
