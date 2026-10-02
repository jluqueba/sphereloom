# ADR-0007: Testing strategy without hardware

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: ADR-0003, ADR-0004, ADR-0009

## Context

SphereLoom's subject is a physical device that exactly one person on the project owns. CI
runners have no camera, no Wi-Fi hotspot to join, no NVIDIA GPU and no vendor SDK. If tests
require hardware, there are effectively no tests — and no outside contributions either,
because nobody will buy a camera to fix a typo in a pagination cursor.

The system under test is nevertheless mostly *not* the camera. It is: input validation,
capability resolution, error mapping, pagination, the job state machine, cancellation,
path-jail enforcement, confirmation tokens, retry and rate-limit behaviour, and the
translation between vendor payloads and domain models. All of that is testable against a
stand-in, provided the stand-in is faithful at the protocol level rather than at the
Python-object level.

The vendor backend has quirks worth reproducing deliberately: `camera.takePicture` and
`camera.startCapture` are asynchronous commands whose completion must be polled via
`/osc/commands/status`; overlapping command requests are disallowed; `/osc/info` must not
be polled more than once a second; errors arrive as JSON bodies with vendor-specific codes;
and files are served over plain HTTP from the camera's own host.

## Decision

### Test tiers

1. **Unit tests** (`tests/unit`) — pure logic with no I/O: models, capability registry
   resolution, cursor encoding/decoding, path-jail rules, confirmation-token lifecycle,
   error mapping, job state machine transitions. Fast, no sockets, no sleeping on real
   time.
2. **Component tests** (`tests/component`) — the real MCP server, the real tool layer, the
   real `OscCameraAdapter` and real `httpx` traffic, pointed at a **fake OSC camera**: a
   locally bound HTTP server that implements the vendor's documented endpoints
   (`/osc/info`, `/osc/state`, `/osc/commands/execute`, `/osc/commands/status`, file
   serving) over loopback. This is the default and largest tier, and it is what runs in CI.
3. **Integration tests** (`tests/integration`) — against a real camera, marked
   `@pytest.mark.hardware`, **skipped unless** `SPHERELOOM_ENABLE_HARDWARE_TESTS=1` is set.
   They never run in CI. They are the maintainer's pre-release checklist, and their results
   are recorded in the release notes.

### The fake camera is a product-quality component

4. The fake OSC camera lives in the package (not only in `tests/`) and is reachable through
   `SPHERELOOM_CAMERA_BACKEND=fake`, so it also serves as a **demo and development mode**:
   anyone can run SphereLoom end to end, including the example agent, with no camera.
5. It is **protocol-faithful by construction**: it speaks HTTP, mirrors the documented
   request and response shapes, implements asynchronous command completion via
   `/osc/commands/status`, enforces the "one in-flight command" rule by returning the
   vendor's busy error, and serves real (tiny) media files for download tests.
6. It is **programmable** for failure injection: configurable latency, truncated or slow
   responses, disconnection mid-download, storage-full, busy, malformed JSON, and
   HTTP errors. Resilience code without a failure-injection test does not count as tested.

### Fidelity control

7. **Contract fixtures.** Recorded, redacted real-camera responses are committed as
   fixtures. A contract test asserts that the fake camera's responses satisfy the same
   schemas as the recorded ones, so the fake cannot quietly drift into fiction.
8. Fixtures are refreshed by the maintainer when running the hardware tier, and any
   divergence found on real hardware must be reproduced in the fake camera **before** the
   fix is merged.

### Determinism

9. Time and identifiers are injected (a `Clock` and an ID factory), never read from
   `datetime.now()` or `uuid4()` inside logic under test, so job timing, token expiry and
   rate limiting are deterministic.
10. Unit and component tests must not reach the network beyond loopback; a fixture
    fails the test if an outbound connection is attempted.
11. Async tests use `pytest-asyncio`; no test sleeps on wall-clock time to wait for a job.

### Coverage expectations

12. Every error-taxonomy code (ADR-0003) has at least one test that produces it through the
    real tool layer.
13. Every MCP tool has at least: a success test, an invalid-input test, and an
    `unsupported`-capability test where applicable.
14. Line coverage target 85% overall with no hard failure below it in M0/M1; branch
    coverage is reported. Coverage is a signal, not a gate to game.

### What we deliberately do not test

15. Vendor correctness. If the camera violates its own documentation, our job is to record
    the behaviour in a fixture and handle it, not to assert the vendor is right.
16. The vendor SDKs themselves (M3/M4). The sidecar IPC contract is tested with a fake
    sidecar; the SDK behind it is out of scope.

## Consequences

### Positive

- The full suite runs on any laptop and on CI runners in seconds, with no hardware.
- Contributors can be productive without owning a camera, which is the difference between
  having a community and not having one.
- The fake backend doubles as a demo mode, which makes the README's "try it now" path real
  and keeps the example agent runnable in CI (ADR-0008).
- Failure injection makes the resilience requirements in the spec actually verifiable.

### Negative / costs

- The fake camera is code that must be written and maintained, and it is not the product.
- Protocol fidelity is a standing risk: a fake that drifts gives false confidence. The
  contract-fixture rule mitigates but does not eliminate this.
- Some classes of bug — firmware timing, Wi-Fi flakiness, thermal throttling, real file
  sizes — are only reachable on hardware, so the hardware tier remains a genuine
  pre-release gate rather than a formality.

### Neutral

- Recording fixtures requires care to redact serial numbers, SSIDs and personal paths; the
  recording helper does this automatically and the result is reviewed.

## Alternatives considered

### Mocking `httpx` at the client level

Faster to write, but it tests our mocks rather than our HTTP usage: it would not catch
header, timeout, redirect, streaming or connection-reuse mistakes, which are exactly the
kinds of bug this backend produces. Rejected as the primary tier; acceptable in unit tests
for narrow cases.

### Record/replay cassettes (VCR-style) as the main tier

Attractive for fidelity, but brittle under refactoring, awkward for failure injection and
for the asynchronous poll-until-done flows, and it would make writing a test require having
had a camera at some point. Adopted only in the reduced form of contract fixtures.

### Hardware-in-the-loop CI with a self-hosted runner and a real camera

Rejected for now: a camera that must stay charged, connected and on its own access point is
an unreliable CI dependency, and exposing a self-hosted runner to a public repository's
pull requests is a security problem. Revisit if the project grows maintainers.

### No fake at all; rely on manual testing

Rejected. It would make every change a gamble and close the project to contributors.

## Follow-ups

- Build the fake OSC camera in Milestone 1 alongside the adapter, not after it.
- Define the redaction rules for fixture recording before the first hardware session.
- Add a fake sidecar harness when the M3 IPC protocol is specified.
