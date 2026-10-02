# ADR-0006: Licensing, trademark posture and non-redistribution of vendor SDKs

- Status: accepted
- Date: 2026-10-02
- Deciders: @jluqueba
- Related: [ADR-0005](0005-sidecar-isolation-for-vendor-sdks.md),
  [ADR-0009](0009-ci-gate-versioning-and-release-process.md),
  [ADR-0013](0013-desktop-gui-automation-rejected.md)

> **Not legal advice.** This ADR records how the maintainers read the publicly available
> terms and what engineering constraints we derived from that reading. A review by
> qualified counsel is scheduled before Milestone 3 (see Follow-ups).

## Context

SphereLoom is MIT-licensed and public. It interoperates with a vendor whose terms are
restrictive in ways that directly constrain architecture and packaging.

Facts we are working from (vendor EULA "End User License Agreement for Insta360 SDK", last
updated 21 September 2023, plus the vendor's public developer documentation):

- The SDK may be distributed **as part of** an application, but **standalone redistribution
  of the SDK is prohibited**.
- **No reverse engineering**, no modification, no derivative works of the SDK.
- The SDK **must not be combined with GPL-licensed programs** in a way that would subject
  the SDK to the GPL.
- The word "Insta360" **must not appear in the application's name** without written
  permission, and vendor trademarks must not be used for marketing or in a way that implies
  endorsement.
- The vendor **may withdraw access or features at any time**.
- Access to the Camera SDK and Media SDK requires an application and approval (~3 business
  days) and acceptance of a click-through EULA.
- The vendor's public sample repositories on GitHub **contain no licence file**. Publicly
  readable is not the same as open source: absent a licence, default copyright applies.
- The Milestone 1 scope (OSC over Wi-Fi) uses only the vendor's **public HTTP API and
  public documentation**; it downloads no SDK and requires no approval.

## Decision

### Project licensing

1. SphereLoom is and remains **MIT-licensed**. Contributions are accepted under the same
   licence (recorded in `CONTRIBUTING.md`).
2. **GPL-licensed dependencies are forbidden project-wide** — runtime, development and
   example code alike. The prohibition is deliberately broader than the EULA's anti-GPL
   clause strictly requires, because a project-wide rule is enforceable in review while a
   "only near the SDK" rule is not. Weak-copyleft licences (LGPL, MPL) require an explicit
   maintainer decision recorded in a new ADR before adoption.
3. Dependency licences are reviewed at addition time; the pull-request checklist carries an
   explicit "no GPL dependency added" item.

### Verified licences of notable dependencies

| Dependency | Licence | Role | Verdict |
| --- | --- | --- | --- |
| `agent-framework` 1.19.0 (Microsoft Agent Framework, Python 3.10–3.14) | **MIT** | Example agent (ADR-0008); later the optional `sphereloom[assistant]` extra (ADR-0012) | Compatible. Note that it is kept out of the server core for *architectural* reasons (ADR-0010), not licensing ones |
| `agent-framework-devui` | Beta release (`1.0.0b…`) | Optional local exploration aid | Allowed for manual use only; never a CI or required dependency |

Vendor SDKs are not dependencies in the packaging sense at all — see the next section.

### Vendor SDK handling

4. **SphereLoom never redistributes vendor SDK binaries, headers, sample code or
   documentation.** No vendor artefact is ever committed to this repository, published to
   PyPI, attached to a release, or baked into a container image we publish.
5. Users obtain the SDK themselves through the vendor's application process and point
   SphereLoom at their local copy via `SPHERELOOM_CAMERA_SDK_PATH` /
   `SPHERELOOM_MEDIA_SDK_PATH`. Absence of those paths is a normal, supported state that
   degrades to the structured `unsupported` capability error.
6. **No code is copied or adapted from the vendor's sample repositories.** Our integration
   is written from the published documentation. Where a sample is consulted for
   understanding, the resulting code must be an independent implementation and the pull
   request must say so.
7. **No reverse engineering** of vendor binaries, firmware or undocumented protocols.
   Undocumented OSC options that the vendor publishes (for example the underscore-prefixed
   extensions) may be used, because they are documented; sniffed or guessed behaviour may
   not be relied upon.
8. Vendor C++ code runs only in a **sidecar process** (ADR-0005), so SphereLoom's
   distributed artefacts never link against an EULA-bound library.
9. **No circumventing the vendor's sanctioned integration channels.** SphereLoom does not
   automate the vendor's desktop application through its graphical interface, or by any
   other means, to obtain capabilities the documented APIs do not provide. The full
   reasoning is in [ADR-0013](0013-desktop-gui-automation-rejected.md). The approval path
   for the SDKs is free and takes about three business days; routing around it is neither
   necessary nor defensible.

### Naming and trademarks

9. The product name is **SphereLoom** — it contains no vendor trademark, satisfying the
   EULA's application-name clause.
10. Vendor marks are used **descriptively only**, to state compatibility ("works with
    Insta360 X5 cameras"), never in logos, never as a badge of endorsement, never in the
    package name, and never in marketing phrasing that implies a relationship.
11. A **trademark disclaimer** appears in `README.md` and in the vision document:
    *"Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent,
    unaffiliated project and is neither endorsed by nor associated with Arashi Vision Inc."*
12. No vendor logo, product photograph or marketing asset is committed to the repository.

### Documentation honesty

13. Documentation never asserts a capability the vendor API does not provide. The
    capability matrix distinguishes *available now*, *planned for milestone N*, and *not
    supported by the vendor API*, and cites the vendor documentation for each "not
    supported" claim.

## Consequences

### Positive

- The MIT distribution stays clean: nothing we publish contains or links vendor-licensed
  material.
- A GPL-free dependency tree removes an entire class of future licence conflicts and keeps
  the eventual SDK-adjacent code compliant by construction.
- The naming and disclaimer posture reduces trademark risk to the ordinary descriptive-use
  case.
- If the vendor withdraws SDK access, nothing we have published becomes non-compliant.

### Negative / costs

- Users who want M3/M4 features must apply for the SDK and build a sidecar themselves. This
  is real friction and will cost us some adopters.
- We cannot offer a one-command install for the USB or media paths, nor publish a
  batteries-included container image.
- Some convenient GPL-licensed libraries are off the table and must be replaced with
  permissively licensed equivalents.
- Writing integrations from documentation rather than samples is slower.

### Neutral

- These constraints are invisible in Milestone 1, which touches no SDK at all.

## Alternatives considered

### Bundle the SDK for convenience

Rejected: that is precisely the standalone redistribution the EULA prohibits, and it would
also impose the SDK's narrow platform matrix on every user.

### Adopt a copyleft licence for SphereLoom

Rejected: it would collide with the EULA's anti-GPL clause in exactly the area where our
code meets the SDK, and it would deter the integration-oriented adoption the project wants.

### Vendor the sample code "as a starting point"

Rejected: the samples carry no licence, so copying them is a copyright problem independent
of the EULA.

### Use the vendor's name in the project name for discoverability

Rejected: explicitly prohibited without written permission. Discoverability is handled with
descriptive wording in the README and repository description instead.

## Follow-ups

- **Legal counsel review before Milestone 3 (and again before any Milestone 5 commercial
  framing)**, covering: MIT/EULA coexistence in a distributed artefact; the scope of the
  redistribution prohibition relative to our sidecar design; whether the EULA governs
  OSC-only usage at all, given OSC uses public documentation and no SDK download; and the
  limits of descriptive trademark use.
- Add an automated dependency-licence check to CI before the first PyPI release.
- Keep an `ATTRIBUTIONS`/third-party notices file once runtime dependencies are pinned.
