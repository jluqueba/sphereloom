# ADR-0013: Desktop GUI automation (Microsoft UI Automation) evaluated and rejected

- Status: accepted (rejecting the approach)
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: [ADR-0002](0002-mcp-transport-and-security-posture.md),
  [ADR-0005](0005-sidecar-isolation-for-vendor-sdks.md),
  [ADR-0006](0006-licensing-and-trademark-posture.md),
  [ADR-0007](0007-testing-strategy-without-hardware.md)

## Context

The vendor's Media SDK documents stitching, stabilisation, colour grading and export, but
does **not** document trimming, cutting, concatenating or merging clips, nor any timeline or
multi-clip editing. The project has committed not to promise those capabilities
(see [vendor capabilities](../vendor-capabilities.md)).

The vendor's desktop application, Insta360 Studio, does expose some of that functionality
through its graphical interface. Windows provides a supported accessibility framework,
**Microsoft UI Automation**, for programmatically discovering and driving UI elements of
another application — reading its control tree, invoking buttons, setting values and
injecting synthetic input.
Source: <https://learn.microsoft.com/windows/win32/winauto/entry-uiauto-win32>

So the question was asked honestly: could SphereLoom drive Insta360 Studio through UI
Automation to deliver editing capabilities the SDK does not offer? This ADR records the
evaluation and the answer, so the idea does not have to be re-litigated every time someone
wants a trim feature.

## Decision

**Microsoft UI Automation of vendor desktop applications is rejected** as a mechanism for
delivering SphereLoom capabilities. It will not be implemented, and no adapter driving a
third-party GUI will exist alongside `OscCameraAdapter` and `UsbCameraAdapter`.

### Reasons

1. **It would smuggle back exactly the promises we committed not to make.** The project's
   first principle is honesty about capabilities. Delivering trimming and editing by
   puppeteering someone else's GUI would mean advertising features whose real
   implementation is "we click buttons in another program and hope". That is the opposite of
   the structured `unsupported` error we chose precisely so users are never misled.
2. **No stable contract.** A GUI control tree is not an API. Element identifiers, layout,
   control types and dialog flow change with every application update, including automatic
   ones. Every release of a program we do not control becomes a potential silent breakage
   with no deprecation notice, no versioning and no changelog we can track.
3. **It cannot run headless, as a service, or in CI.** UI Automation requires an
   interactive, unlocked Windows desktop session. It is Windows-only, cannot run in
   Session 0, and would require an interactive Windows CI runner with proprietary
   third-party software installed and signed in. That directly breaks the
   "testable without hardware" principle (ADR-0007): a capability nobody can test in CI is
   a capability nobody can safely change.
4. **It is uncontainable and incompatible with secure-by-default.** Synthetic input
   injection is not scoped to one window. Granting an AI agent the ability to send
   keystrokes and clicks into a live desktop session hands it the whole machine — every
   other open application, every dialog, every password prompt. Nothing in our security
   model (path jail, confirmation tokens, bounded concurrency) can contain that, because
   the mechanism operates below the layer where those controls live.
5. **It worsens the legal position.** It would layer the consumer terms of the vendor's
   desktop application on top of the SDK EULA already analysed in ADR-0006, and it would do
   so **in order to circumvent a legitimate, free, roughly three-business-day SDK approval
   path**. Routing around a vendor's sanctioned integration channel to obtain capabilities
   they chose not to expose is a bad posture for a project whose licensing story is one of
   its selling points.

### The narrow conditional door

If a future need can **only** be met this way, it is not forbidden outright — but it does
not belong in the MCP server. Such a thing would have to be:

- a **separate, opt-in tool outside the MCP server process**, never a peer adapter
  alongside OSC and USB;
- **Windows-only** and explicitly labelled **experimental**, with no compatibility promise;
- disabled by default, requiring deliberate activation by the user who also accepts the
  vendor application's own terms;
- documented as unsupported for automation, with its breakage mode stated up front;
- excluded from `ci-gate`, from the capability registry's supported set, and from any
  claim in the README capability matrix.

Reaching for that door requires a new ADR superseding this section, with evidence that no
documented API can serve the need.

## Consequences

### Positive

- The capability matrix stays truthful: if the vendor does not document it, we do not ship
  it, and the user gets an explained `unsupported` error naming the reason.
- The security model remains coherent. No component of SphereLoom can inject input into the
  user's desktop.
- CI keeps running on ordinary headless runners with no proprietary software, so
  contributors without a camera — or without Windows — can still work on everything.
- No dependency on an undocumented, unversioned, silently changing surface.
- The legal posture stays clean and defensible.

### Negative / costs

- Trimming, merging and timeline editing remain unavailable in SphereLoom for the
  foreseeable future, and some users will want them. The honest answer is that the vendor
  does not document an API for them.
- Users who need those operations must use the vendor's own application manually, which the
  documentation will say plainly rather than papering over.

### Neutral

- UI Automation remains a perfectly legitimate technology for accessibility and for testing
  your *own* application. The rejection is about driving a third party's GUI as a product
  feature, not about the framework.

## Alternatives considered

### Image-based screen automation (template matching, OCR, synthetic clicks)

Strictly worse than UI Automation on every axis evaluated above: less reliable, equally
uncontainable, equally untestable, and additionally resolution- and theme-dependent.
Rejected.

### Driving a vendor command-line tool, if one existed

Would be acceptable in principle — a documented CLI is a contract, can run headless, and can
be wrapped in a sidecar like any other vendor component (ADR-0005). No such documented tool
is known for the capabilities in question. If one appears, it gets its own ADR.

### Implementing trimming and merging ourselves with a media library

Not evaluated here beyond noting the constraint: any such library must be permissively
licensed (no GPL, ADR-0006), which rules out the most obvious candidates in their usual
distributions. A genuine proposal needs its own ADR covering licensing, quality of
360°-aware handling, and whether re-encoding is acceptable.

### Waiting for the vendor to document the capability

The default position. The capability registry already distinguishes "not supported by the
vendor API" from "planned for milestone N", so adding it later is a data change plus tests.

## Follow-ups

- Record in the troubleshooting documentation that trimming and merging are not available
  and why, pointing users at the vendor's own application.
- Re-check the Media SDK documentation at each milestone boundary; if editing operations
  become documented, open a new ADR to add them properly.
