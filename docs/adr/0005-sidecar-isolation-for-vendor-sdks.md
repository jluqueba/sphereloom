# ADR-0005: Sidecar process isolation for vendor C++ SDKs

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: ADR-0001, ADR-0003, ADR-0006

## Context

Milestones 3 and 4 depend on two vendor C++ SDKs:

- **Desktop Camera SDK** (USB only on desktop; Windows 7+, Ubuntu 22.04 x86_64, Linux
  ARM64; no macOS; requires administrator/root privileges; emulators unsupported).
- **Desktop Media SDK** (Windows 10+ x64 with VS2019 + CUDA 10.2, or Ubuntu 22.04 x64 with
  GCC 11+ and CUDA 11.7; Conan 2; **requires a discrete NVIDIA GPU**; WSL unsupported).

Both are obtained only by application and approval, under a click-through EULA whose terms
matter to us: redistribution of the standalone SDK is prohibited, modification and reverse
engineering are prohibited, and the SDK must not be combined with GPL-licensed programs in
a way that would subject the SDK to the GPL. The vendor also reserves the right to withdraw
access or features at any time.

Technically, a native crash, an unhandled signal, a GPU driver fault or a licence check
inside a loaded C++ library would take down whatever process hosts it. If that process is
the MCP server, the agent's entire session dies — mid-capture, possibly with the camera in
recording state.

The platform matrices also differ from the server's: our Python server runs anywhere,
the Camera SDK does not run on macOS, and the Media SDK additionally needs CUDA hardware.
Making the server's installability depend on the strictest of those matrices would be
absurd.

## Decision

1. **Vendor C++ SDKs always run in a separate sidecar process.** The SphereLoom MCP server
   process never loads, links against, or imports a vendor SDK — not via `ctypes`, not via
   `cffi`, not via a compiled extension module.
2. **One sidecar per SDK**: a camera sidecar (Camera SDK) and a media sidecar (Media SDK),
   each spawned on demand by its adapter and supervised by it.
3. **IPC contract:** a small, versioned, line-delimited JSON request/response protocol over
   the sidecar's stdin/stdout, with stderr reserved for the sidecar's structured logs.
   Large data is exchanged through the workspace filesystem (paths), never through the pipe.
   The contract is owned by SphereLoom and documented in this repository, so a sidecar can
   be reimplemented by anyone who has their own SDK licence.
4. **The sidecar source is not vendored.** SphereLoom publishes the protocol and a build
   recipe; the user supplies their own approved SDK copy, located through
   `SPHERELOOM_CAMERA_SDK_PATH` / `SPHERELOOM_MEDIA_SDK_PATH`. SphereLoom ships **no**
   vendor binaries and **no** code copied from vendor sample repositories (ADR-0006).
5. **Supervision and failure containment.** The adapter treats sidecar death as an ordinary
   error: it reports a typed error envelope (`internal` or `not_connected` as appropriate),
   marks affected jobs `failed`, and may restart the sidecar with backoff. The MCP server
   stays alive and keeps serving other backends.
6. **Capability gating.** If the SDK path is unset, the platform is unsupported, or the
   required GPU is absent, the adapter is simply not available and every affected
   capability resolves to the structured `unsupported` error through the capability registry
   (ADR-0003). Installation of SphereLoom never requires CUDA, a GPU, or administrator
   rights.
7. **Privilege boundary.** Only the camera sidecar needs elevated privileges for USB
   access. Documentation will describe the minimum viable elevation per platform rather
   than telling users to run the whole server as administrator or root.
8. **Timeouts and resource bounds** apply to every sidecar call, and sidecar work is
   expressed as jobs (ADR-0004) when it can be slow.

## Consequences

### Positive

- **Licensing hygiene:** our MIT Python code never links EULA-bound binaries, keeping the
  boundary between the two licence regimes a process boundary rather than an argument.
- **Crash isolation:** a native fault or GPU driver reset cannot kill the agent session.
- **Portability:** the server installs and runs everywhere; only the optional sidecars
  inherit the vendor's narrow platform matrices.
- **Vendor risk containment:** if access is withdrawn or the EULA changes, the affected
  component is a replaceable process, not the heart of the product.
- **Testability:** the IPC contract can be exercised with a fake sidecar, so M3/M4 logic is
  testable without the SDK or a GPU (ADR-0007).

### Negative / costs

- IPC serialisation, process supervision and a versioned protocol are real engineering work
  that a direct binding would not need.
- Extra latency per call. Acceptable: the operations concerned are device- or GPU-bound and
  measured in seconds, not microseconds.
- Live-stream frames (M3) cannot flow efficiently through a JSON pipe; that path will need
  a dedicated side channel (shared memory, a local socket, or frames written to the
  workspace). This is explicitly deferred to the M3 design, not promised here.
- Users must build or obtain a sidecar binary themselves, which raises the barrier for
  M3/M4. We consider this unavoidable given the redistribution prohibition.

### Neutral

- The sidecar protocol is a public artefact of the project, which may be useful to others
  independently of SphereLoom.

## Alternatives considered

### In-process bindings (pybind11, ctypes, a compiled extension)

Lowest latency and least code. Rejected on two independent grounds: a native crash would
kill the MCP server mid-session, and it would place EULA-bound SDK headers and binaries
directly on the build and link path of an MIT-licensed distribution.

### Shipping prebuilt wheels that bundle the SDK

Would make installation trivial. Rejected: it is exactly the standalone redistribution the
EULA prohibits, and it would also drag the platform matrix of the strictest SDK onto the
whole project.

### A separate microservice over HTTP

Functionally similar to a sidecar. Rejected for the default case because it adds a network
listener, a port, and an authentication problem (ADR-0002) to solve something a pipe
already solves. May be reconsidered for a remote GPU workstation scenario in M4.

### Dropping USB/Media support entirely

Rejected: in-camera stitching covers photos only, so meaningful video output genuinely
requires the Media SDK. The honest answer is to isolate it, not to pretend it does not
exist.

## Follow-ups

- Specify and version the sidecar IPC protocol before M3 implementation begins.
- Design the live-stream side channel as part of the M3 feature spec.
- Confirm with legal counsel, before M3, that the sidecar boundary satisfies the EULA's
  distribution and anti-GPL clauses as we read them (ADR-0006).
