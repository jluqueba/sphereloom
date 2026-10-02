# SphereLoom

**An MCP server that lets AI agents control 360° cameras and process 360° media locally.**

SphereLoom exposes one coherent set of [Model Context Protocol](https://modelcontextprotocol.io)
tools — connect, check status, read and change settings, take photos, record video, browse
the gallery, download media as background jobs — so an AI agent can drive a real camera
safely, and so every unsupported request gets an explained, structured answer instead of a
stack trace.

> **Status: Milestone 0 complete, Milestone 1 in progress.** The MCP server runs, serves
> tools over stdio, and enforces its configuration and security guard rails, but it does not
> talk to a camera yet. The repository also contains the product vision, the Milestone 1
> specification, the technical plan, the task breakdown and the architecture decision
> records.

## Why

Automating 360° capture today means juggling a phone app, a desktop stitcher and a folder
of manually copied files. The camera vendor publishes real automation surfaces — a Wi-Fi
HTTP API and two desktop C++ SDKs — but they have different capabilities, different
platforms and different access requirements. SphereLoom turns them into a single, typed,
agent-friendly tool surface that is **honest about what each backend can actually do**.

## Capability matrix

This matrix is the project's core promise. Once the capability registry exists it will be
generated from the code, and a test will fail if this table and the code disagree.

| Capability | Status | Notes |
| --- | --- | --- |
| Connect, status, battery, storage | 🟡 Milestone 1 | Over the camera's Wi-Fi access point |
| Read and change capture settings | 🟡 Milestone 1 | Limited to what the vendor's OSC API exposes |
| Take a 360° photo | 🟡 Milestone 1 | Stitched in camera |
| Start / stop video recording | 🟡 Milestone 1 | Output is **unstitched** |
| Browse the gallery with pagination | 🟡 Milestone 1 | |
| Download media as a cancellable job | 🟡 Milestone 1 | Files land in a workspace directory |
| Delete media | 🟡 Milestone 1 | Disabled by default; two-step confirmation |
| Example agent (Microsoft Agent Framework) | 🟡 Milestone 2 | Includes an offline scripted mode |
| `sphereloom-assistant` — one high-level tool like "record 30 seconds and download it" | 🟡 Deferred (after Milestone 2) | Optional extra: `sphereloom[assistant]`. Requires your own model credentials. **Never required by the base server** |
| Exposure / white balance control | 🟡 Milestone 3 | USB only — **not possible over Wi-Fi** |
| Live preview / live stream | 🟡 Milestone 3 | USB only — **not possible over Wi-Fi** |
| Format storage | 🟡 Milestone 3 | USB only; guarded |
| **Export as a stitched 360° video** | 🟡 Milestone 4 | **Does not work before Milestone 4.** Video stitching requires the vendor's Media SDK, a discrete NVIDIA GPU, and a user-supplied SDK copy |
| Stabilisation, colour grading | 🟡 Milestone 4 | Same requirements as above |
| Trim, cut, merge or edit clips | 🔴 Not planned | Not documented by the vendor, so we do not promise it — and we will not fake it by automating the vendor's desktop GUI |
| DNG → JPG conversion | 🔴 Not supported | Not supported by the vendor's Media SDK |

Legend: 🟢 available · 🟡 planned for the stated milestone · 🔴 not available.

Every entry above is traced to vendor documentation in the project's internal capability
matrix, and a test fails if this table and the capability registry disagree.

## How it will work

```text
AI agent ──MCP (stdio)──▶ SphereLoom ──HTTP──▶ camera Wi-Fi access point   (M1)
                              │
                              ├──IPC──▶ camera sidecar  ──USB──▶ camera     (M3)
                              └──IPC──▶ media sidecar   ──CUDA─▶ local files (M4)
```

- **Ports and adapters.** The agent-facing tools never change when the backend does.
- **Capability registry.** Unsupported operations return
  `{"code": "unsupported", "backend": …, "reason": …, "docs_url": …}` — never a timeout or
  a lie.
- **Jobs, not blocking calls.** Downloads and exports return a `job_id` you poll and can
  cancel. MCP responses carry paths and metadata, never media bytes.
- **Sidecars for vendor C++ SDKs.** They run in their own process, so a native crash cannot
  kill your agent session and our MIT code never links EULA-bound binaries.
- **No LLM inside the server.** The server is deterministic, needs no model credentials to
  start, and costs no tokens to poll. The reasoning lives in your client — which also means
  any MCP client works, not just ours. An optional agent-backed entry point is a separate,
  deferred layer you can ignore entirely.
- **Safe by default.** stdio transport, no network listener, a workspace path jail,
  confirmation tokens for destructive actions, bounded concurrency, redacted logs. HTTP
  access is opt-in and requires a loopback bind plus a token.
- **Testable without hardware.** A protocol-faithful fake camera backend runs the full test
  suite — and doubles as a demo mode, so you can try SphereLoom with no camera at all.

## Planned requirements

- Python 3.12 or 3.13, installed with [`uv`](https://docs.astral.sh/uv/)
- Milestone 1 is pure Python and runs on Windows, Linux and macOS
- A camera supporting the vendor's Open Spherical Camera API — development targets the
  **Insta360 X5**
- Milestones 3 and 4 additionally require a vendor SDK that **you** obtain and install
  yourself (see [Licensing](#licensing-and-trademarks)); Milestone 4 also requires a
  discrete NVIDIA GPU, and neither SDK supports macOS

## Quick start for contributors

No camera is required.

```bash
git clone https://github.com/jluqueba/sphereloom.git
cd sphereloom
uv venv --python 3.13
uv pip install -e ".[dev]"
```

Run everything CI runs, with one command:

```bash
make check        # Linux and macOS
.\check.ps1       # Windows
```

Confirm the server starts and validate your configuration without launching it:

```bash
uv run --no-sync sphereloom --version
uv run --no-sync sphereloom --check-config
```

To register it with an MCP client, run `sphereloom` over stdio. Configuration is read from
`SPHERELOOM_*` environment variables, every one of which is documented in
[`.env.example`](.env.example).

## Documentation

| Document | What it covers |
| --- | --- |
| [Developer guide](docs/DEVELOPER_GUIDE.md) | Architecture, getting started, project layout, testing, configuration, security model |
| [Contributing](CONTRIBUTING.md) | Branch naming, commit conventions, pull request checklist |
| [Security policy](SECURITY.md) | How to report a vulnerability |
| [Changelog](CHANGELOG.md) | What changed, and when |

Design records — architecture decision records, feature specifications, technical plans and
the product vision — live under `docs/internal/` and are encrypted at rest. You do not need
them to build, test or extend SphereLoom; the developer guide and the instruction files
under `.github/instructions/` carry everything a contributor needs. If a change needs design
context you cannot read, open an issue and ask.

## Contributing

Contributions are welcome, and **you do not need a camera**: the fake backend drives the
entire test suite. Start with [CONTRIBUTING.md](CONTRIBUTING.md) and read the relevant spec
before writing code — this project is spec-driven by design.

Three rules worth knowing up front:

1. Everything in the repository is in **English**.
2. **No GPL-licensed dependencies**, anywhere.
3. **Never commit vendor SDK binaries**, and never copy code from the vendor's sample
   repositories — they carry no licence file.

## Licensing and trademarks

SphereLoom is released under the [MIT licence](LICENSE).

The base install (`pip install sphereloom`) pulls in no agent framework and no model
client: the server is deterministic and framework-agnostic by design
(a deliberate, recorded decision). The example
agent and the deferred assistant layer use the Microsoft Agent Framework
(`agent-framework`, MIT licensed), which is an optional extra. GPL-licensed dependencies
are forbidden project-wide.

SphereLoom **does not redistribute any Insta360 SDK**. The Milestone 1 Wi-Fi functionality
uses only the vendor's publicly documented HTTP API and needs no SDK at all. For the USB and
media milestones you apply for the SDK yourself at the vendor's developer portal, accept
their EULA, and point SphereLoom at your local copy with an environment variable.

> Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent, unaffiliated
> project and is neither endorsed by nor associated with Arashi Vision Inc. References to
> camera models and vendor APIs are descriptive statements of compatibility only.
