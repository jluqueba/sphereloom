# SphereLoom — product vision

- Status: approved
- Last updated: 2026-10-02
- Owner: @jluqueba

## One-line description

SphereLoom is an open-source MCP server that lets AI agents control Insta360 cameras and
process 360° media locally, through one coherent tool surface that is honest about what
each backend can actually do.

## Problem

Capturing and publishing 360° content is a chore made of disconnected steps: join the
camera's Wi-Fi hotspot, poke at a phone app, wait, copy files off the camera, stitch them
in a desktop application, export, then do it again for the next shot. The vendor exposes
real automation surfaces — a Wi-Fi Open Spherical Camera (OSC) HTTP API, a desktop USB
Camera SDK and a desktop Media SDK — but they are three different programming models with
three different capability sets, two of them gated behind an application form and a C++
toolchain.

Meanwhile, AI agents have become a credible shell for physical workflows: they can plan a
shoot, trigger a capture, name and file the results, and narrate the state of the device.
What they lack is a safe, typed, well-documented way to *reach* the hardware. Today an
agent that wants to take a 360° photo has to be handed either a browser or a pile of
bespoke shell scripts.

### Pain points we target

| # | Pain | Who feels it |
| --- | ------ | -------------- |
| P1 | No agent-reachable interface for 360° cameras; everything is phone-app-first | Developers building agentic workflows |
| P2 | Three vendor surfaces (OSC, Camera SDK, Media SDK) with different and partly overlapping capabilities | Integrators |
| P3 | Capability confusion: it is hard to find out that OSC has no live preview, no exposure control and no in-camera video stitching | Everyone, painfully and late |
| P4 | Long operations (downloads, stitching) do not fit a request/response tool call | Agent authors |
| P5 | Giving an LLM control of a real device with destructive operations (delete, format) is risky by default | Operators |
| P6 | Vendor SDKs cannot be redistributed, so naive projects either break the EULA or cannot be installed at all | Maintainers and users |
| P7 | Nothing can be tested without owning the hardware, so contribution is gated on an expensive camera | Contributors |

## Target users

1. **Agent builders** — people wiring an LLM agent to real-world capture, who want
   `camera_take_photo` to be a tool call and not a research project.
2. **360° creators and prosumers** who are comfortable with a terminal and want a scripted,
   repeatable capture-and-file pipeline.
3. **Researchers and surveyors** who need reproducible, timestamped, metadata-rich capture
   runs rather than hand-driven shoots.
4. **Contributors** who want a well-specified Python project with a real hardware story and
   a test suite that runs on a laptop with no camera attached.

## Product principles

1. **Honesty over surface area.** Every tool either works or returns a structured
   `unsupported` error naming the backend, the reason and a documentation link. We never
   paper over a vendor limitation, and we never ship a tool whose description implies a
   capability the backend does not have.
2. **One tool surface, many backends.** The agent-facing contract does not change when the
   transport changes from Wi-Fi/OSC to USB. A capability registry, not the tool names,
   absorbs the differences.
3. **Safe by default, powerful by opt-in.** Local stdio transport, a workspace path jail,
   confirmation tokens for destructive actions, bounded concurrency, redacted logs. HTTP
   access and real deletions are deliberate choices the operator makes.
4. **Clean licensing boundary.** SphereLoom is MIT and never redistributes vendor SDK
   binaries. Vendor C++ code runs in a separate sidecar process that our Python never links
   against.
5. **The core stays deterministic.** The server does the doing; the client does the
   thinking. No model credentials are needed to control a camera, and no non-deterministic
   decision sits in the path of a destructive operation.
6. **Testable without hardware.** A fake camera is a first-class component, not an
   afterthought. Hardware tests are opt-in.
7. **The spec is the product.** Features start as a spec with testable acceptance criteria,
   then a plan, then tasks, then code.

## Scope by milestone

| Milestone | Theme | Backends | Status |
| --- | --- | --- | --- |
| **M0** | Scaffolding: repository, tooling, CI gate, packaging, docs | — | Planned |
| **M1** | OSC camera control over Wi-Fi: connect, status, options, photo, video, list, download-as-job | OSC | Planned (next) |
| **M2** | Example agent (Microsoft Agent Framework) + capability/doc hardening + first PyPI release | OSC | Planned |
| **M2.5** | `sphereloom-assistant`: optional high-level agent entry point via `agent.as_mcp_server()`, installed as `sphereloom[assistant]` | OSC | **Deferred** — designed and documented ([ADR-0012](../adr/0012-optional-sphereloom-assistant-layer.md)), not scheduled, **not part of M1 or M2 delivery** |
| **M3** | USB desktop control via the Camera SDK sidecar: exposure, white balance, storage, live stream frames | Camera SDK | Planned, gated on SDK approval + legal review |
| **M4** | Media processing: video stitching, stabilisation, colour, export | Media SDK | Planned, gated on SDK approval + NVIDIA GPU |
| **M5** | Workflow tools: capture presets, batch pipelines, metadata extraction | All | Exploratory |

M2.5 is deliberately numbered between M2 and M3 rather than renumbering later milestones:
it is an **optional layer on top of the foundation**, not a step the foundation depends on.
Nothing in M3, M4 or M5 requires it, and removing it entirely would break nothing. The base
server never depends on an agent framework
([ADR-0010](../adr/0010-agent-framework-stays-out-of-the-server-core.md)).

### Milestone 1 definition of success

A user on Windows or Linux joins their X5's Wi-Fi hotspot, points an MCP client at
SphereLoom over stdio, and the agent can: confirm the camera is reachable and report
battery and storage, read and change supported capture options, take a photo, start and
stop a video recording, page through the gallery, and download selected files into a
workspace directory as a cancellable background job — with every unsupported request
(live preview, exposure control, video stitching) returning a structured, explained error
rather than a stack trace.

## Explicit non-goals

- **Not a replacement for the Insta360 mobile or desktop apps.** No GUI, no social sharing.
- **No live preview or live streaming in M1.** The vendor's OSC API does not provide it;
  a USB live-stream path is only possible from M3 onward via the Camera SDK.
- **No exposure parameter control over OSC.** The vendor does not expose it there.
- **No timeline editing.** Trimming, cutting, concatenating or merging clips is not
  documented in the Media SDK, so we do not promise it. If it turns out to be impossible,
  we say so in the capability matrix instead of shipping a half-working tool.
- **No DNG → JPG conversion.** Not supported by the Media SDK.
- **No macOS support for the USB/Media SDK milestones.** The vendor documents Windows and
  Linux only; M1 (OSC) is pure Python and therefore portable.
- **No LLM inside the server.** The MCP server is deterministic, credential-free and
  framework-agnostic; reasoning belongs to the client. An optional agent-backed entry point
  is a separate, deferred layer ([ADR-0010](../adr/0010-agent-framework-stays-out-of-the-server-core.md),
  [ADR-0012](../adr/0012-optional-sphereloom-assistant-layer.md)).
- **No automation of vendor desktop applications.** We will not drive Insta360 Studio
  through its graphical interface to obtain capabilities the documented APIs do not provide
  ([ADR-0013](../adr/0013-desktop-gui-automation-rejected.md)).
- **No redistribution of vendor SDK binaries, ever.** Users apply for and install the SDK
  themselves.
- **No cloud service.** SphereLoom runs on the user's machine and keeps media local.
- **No reverse engineering** of vendor protocols or binaries, and no copying of code from
  the vendor's public sample repositories (they carry no licence file).

## Key constraints that shape the design

- The camera is a **Wi-Fi access point** at a fixed `192.168.42.1`; the host joins the
  camera's hotspot, which means the host usually loses general internet access while
  connected. The camera cannot join a router and the subnet cannot be changed.
- The OSC API has **no application-level authentication** — possession of the Wi-Fi
  credentials is the only gate. SphereLoom must therefore be the component that adds
  guardrails, not assume the camera has any.
- The vendor asks that no new `/osc/commands` request be sent before the previous response
  arrives, and that `/osc/info` be polled at most once per second. Our client is serialised
  and rate-limited accordingly.
- The desktop SDKs are **approval-gated** (~3 business days) and carry a EULA with
  anti-GPL and trademark clauses, which is why GPL dependencies are banned project-wide and
  the product name contains no vendor trademark.
- The Media SDK requires a **discrete NVIDIA GPU** and does not run under WSL, so M4 will be
  an opt-in component with a hard capability check, never a required dependency.

## Risks

| ID | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| R1 | SDK application is rejected or delayed | M3/M4 slip | M1 and M2 are OSC-only and ship independently |
| R2 | Vendor withdraws SDK access or changes the EULA (they reserve the right) | M3/M4 blocked | Sidecar isolation keeps the blast radius to one process; core product stays MIT and useful |
| R3 | Legal ambiguity on MIT/EULA coexistence and descriptive trademark use | Distribution risk | Counsel review scheduled before M3/M5; M1 downloads no SDK and touches no EULA artefact |
| R4 | Users expect "export as 360° video" to work on day one | Trust damage | Capability matrix in README and tool descriptions; structured `unsupported` error names the milestone |
| R5 | An agent deletes or formats media unprompted | Data loss | Two-step confirmation tokens, destructive tools off by default, path jail |
| R6 | Firmware changes break OSC option names | Breakage | Fake-camera contract tests + a thin, data-driven option mapping layer |
| R7 | Only the maintainer owns a camera | Review bottleneck | Fake OSC camera covers the full suite; hardware tests are marker-gated and documented |

## How we will know it is working

- Time from "camera in hand" to first agent-triggered photo: under 10 minutes following the
  README.
- Share of tool calls that fail with an *unstructured* error: approaching zero; failures
  should be typed and explained.
- Contributors can run `make check` green on a machine with no camera attached.
- The capability matrix in the README matches reality at every release, verified by tests.

## Related artefacts

- Feature spec: [docs/features/osc-camera-control/spec.md](../features/osc-camera-control/spec.md)
- Technical plan: [docs/features/osc-camera-control/plan.md](../features/osc-camera-control/plan.md)
- Task breakdown: [docs/features/osc-camera-control/tasks.md](../features/osc-camera-control/tasks.md)
- Decision records: [docs/adr/README.md](../adr/README.md)
- Vendor capability matrix: [docs/vendor-capabilities.md](../vendor-capabilities.md)
- Repository settings proposal: [docs/repo-settings-proposal.md](../repo-settings-proposal.md)

## Trademark notice

Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent, unaffiliated
project and is neither endorsed by nor associated with Arashi Vision Inc. References to
camera models and vendor APIs are descriptive statements of compatibility only.
