# Architecture decision records

This directory holds the architecture decision records (ADRs) for SphereLoom, in
[MADR](https://adr.github.io/madr/)-flavoured markdown.

## Conventions

- One decision per file, named `NNNN-kebab-case-title.md`, numbered sequentially and never
  renumbered.
- Status is one of `proposed`, `accepted`, `deprecated`, or `superseded by ADR-NNNN`.
- ADRs are immutable once accepted. To change a decision, write a new ADR and mark the old
  one as superseded; do not rewrite history.
- Every ADR states its consequences, including the bad ones, and the alternatives that were
  considered and rejected.
- Use [0000-template.md](0000-template.md) as the starting point.

## Index

| ADR | Title | Status |
| ----- | ------- | -------- |
| [0001](0001-language-runtime-and-mcp-sdk.md) | Language, runtime and MCP SDK | Accepted |
| [0002](0002-mcp-transport-and-security-posture.md) | MCP transport and security posture | Accepted |
| [0003](0003-ports-and-adapters-with-capability-registry.md) | Ports and adapters with a capability registry | Accepted |
| [0004](0004-job-model-for-long-running-operations.md) | Job model for long-running operations | Accepted |
| [0005](0005-sidecar-isolation-for-vendor-sdks.md) | Sidecar process isolation for vendor C++ SDKs | Accepted |
| [0006](0006-licensing-and-trademark-posture.md) | Licensing, trademark posture and non-redistribution of vendor SDKs | Accepted |
| [0007](0007-testing-strategy-without-hardware.md) | Testing strategy without hardware | Accepted |
| [0008](0008-microsoft-agent-framework-for-example-agent.md) | Microsoft Agent Framework for the example agent | Accepted |
| [0009](0009-ci-gate-versioning-and-release-process.md) | CI gate, versioning and release process | Accepted |
| [0010](0010-agent-framework-stays-out-of-the-server-core.md) | The agent framework stays out of the MCP server core | Accepted |
| [0011](0011-job-manager-stays-in-house.md) | The job manager stays in-house; MAF Workflows rejected for Milestone 1 | Accepted |
| [0012](0012-optional-sphereloom-assistant-layer.md) | Optional `sphereloom-assistant` layer via `agent.as_mcp_server()` | Accepted (deferred milestone) |
| [0013](0013-desktop-gui-automation-rejected.md) | Desktop GUI automation (Microsoft UI Automation) evaluated and rejected | Accepted (rejecting the approach) |
