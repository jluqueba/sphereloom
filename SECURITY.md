# Security policy

## Supported versions

SphereLoom is pre-1.0. Until the first stable release, only the latest minor version receives security fixes.

| Version | Supported |
| ------- | --------- |
| Latest pre-1.0 minor | Yes |
| Older pre-1.0 minors | No |

## Reporting a vulnerability

Do not open public issues for vulnerabilities.

Report suspected vulnerabilities through a private GitHub Security Advisory:

<https://github.com/jluqueba/sphereloom/security/advisories/new>

Please include affected versions, configuration, impact, reproduction steps, and any relevant logs with secrets, SSIDs, tokens, and personal paths redacted.

## Response targets

The maintainer aims to meet these response targets:

- Acknowledge: within 3 business days
- Triage: within 7 business days
- Fix target: based on severity and available mitigations

Critical issues that allow unauthorized device control, path-jail escape, token disclosure, or arbitrary code execution are prioritized highest.

## Scope

Security reports are in scope when they affect SphereLoom code or documented deployment patterns, including:

- MCP server behavior
- HTTP transport authentication and loopback binding
- Workspace path-jail enforcement
- Destructive-operation confirmation tokens
- Log redaction for SSIDs, credentials, tokens, SDK paths, and personal file paths
- Sidecar process boundaries for vendor SDK integrations
- Job cancellation, concurrency limits, and file output handling

## Out of scope

These reports are out of scope for SphereLoom security advisories:

- Vulnerabilities in camera firmware; report those to the vendor.
- Vulnerabilities in Insta360 SDKs; report those to the vendor.
- The inherent lack of application-level authentication in the camera OSC API, which is a documented vendor design.
- Issues that require a user to intentionally install malicious third-party code outside SphereLoom.

## Threat-model note

An MCP server can grant an AI agent real-world device control. Treat SphereLoom tools as security-sensitive automation, not as simple local utilities. The default transport is stdio. If HTTP transport is enabled, it must stay loopback-bound and protected with a bearer token.
