# Developer guide

Everything you need to work on SphereLoom. This is the public companion to the
[README](../README.md): the README says what the project is, this says how to build it.

## What SphereLoom is, architecturally

SphereLoom is an MCP server that exposes one coherent tool surface for controlling 360
cameras and processing 360 media, regardless of how the camera is connected.

```text
MCP client ──stdio──▶ SphereLoom server
                          │
                          ├─ tools/         MCP tool surface
                          ├─ capabilities/  what this backend can actually do
                          ├─ jobs/          long operations, with state
                          ├─ security/      path jail, confirmations, transport auth
                          └─ adapters/
                               ├─ osc/      Wi-Fi HTTP  (Milestone 1)
                               ├─ fake/     loopback test double
                               └─ usb/      sidecar process (Milestone 3)
```

Four ideas carry most of the design:

1. **Ports and adapters.** The tool layer depends on the `CameraPort` and `MediaPort`
   protocols and nothing else. Swapping Wi-Fi for USB changes no tool.
2. **Capabilities are data.** A backend declares what it supports. An unsupported operation
   returns a structured error that explains why and links to the vendor documentation,
   rather than failing opaquely or timing out.
3. **Long operations are jobs.** Downloads and exports return a job identifier immediately.
   Responses carry paths and metadata, never media bytes.
4. **The core is deterministic.** No language model runs inside the server. It needs no
   model credentials to start and costs nothing to poll.

## Getting started

Requirements: Python 3.12 or 3.13 and [`uv`](https://docs.astral.sh/uv/). No camera needed.

```bash
git clone https://github.com/jluqueba/sphereloom.git
cd sphereloom
uv venv --python 3.13
uv pip install -e ".[dev]"
```

Run every check CI runs:

```bash
make check        # Linux and macOS
.\check.ps1       # Windows
```

Individual steps, should you want them:

```bash
make lint         # ruff format --check, then ruff check
make typecheck    # mypy --strict
make test         # pytest, excluding the hardware tier
make coverage     # the same with a coverage report
make docs-lint    # markdownlint, needs Node
```

Confirm the server runs:

```bash
uv run --no-sync sphereloom --version
uv run --no-sync sphereloom --check-config
```

## Project layout

```text
src/sphereloom/
  cli.py          entry point and argument parsing
  config.py       settings model, validated once at startup
  logging.py      structured stderr logging with redaction
  domain/         models, capabilities, errors, clock, identifiers
  ports/          CameraPort and MediaPort protocols
  security/       path jail, transport authentication
  server/         application assembly and transport selection
tests/
  unit/           fast, isolated, no I/O
  component/      the real server and real tools against a fake camera
  integration/    real hardware, opt-in only
docs/
  DEVELOPER_GUIDE.md   this file
  internal/            design records, encrypted at rest
```

## Testing

The suite runs with no camera attached, and that is a deliberate constraint rather than a
convenience. Two defaults enforce it:

- Outbound network access is blocked in tests. A test that reaches a real camera fails
  loudly instead of passing on one machine and failing elsewhere.
- Time and identifiers are injected, so nothing sleeps and nothing is random.

Tests are organised in three tiers:

| Tier | What it covers | Runs in CI |
| --- | --- | --- |
| `tests/unit` | Pure logic: path jail, error taxonomy, configuration, redaction | Yes |
| `tests/component` | The real MCP server and real tools against a fake camera on loopback | Yes |
| `tests/integration` | A real camera, marked `@pytest.mark.hardware` | No, never |

The hardware tier is opt-in and requires both the marker and
`SPHERELOOM_ENABLE_HARDWARE_TESTS=1`. It is deselected in CI, so it cannot run by accident.

```bash
uv run --no-sync pytest -m "not hardware"   # the default
uv run --no-sync pytest -m hardware         # needs a real camera
```

## Configuration

Every setting is an environment variable prefixed `SPHERELOOM_`, documented in
[`.env.example`](../.env.example). Configuration is validated once at startup and failures
name the offending variable.

The defaults are the cautious ones: stdio transport with no network listener, destructive
tools disabled, log redaction on.

## Security model

SphereLoom runs on your machine, against your camera, over a network you control. The guard
rails protect your workflow and your filesystem:

- **Transport.** stdio by default, which opens no socket. HTTP is opt-in, requires a bearer
  token, and binding beyond loopback requires an explicit acknowledgement flag.
- **Path jail.** Every write is confined to a configured workspace. Parent traversal,
  absolute paths and escaping symlinks are rejected after full resolution.
- **Confirmations.** Destructive operations are disabled by default and require a
  single-use, target-bound token.
- **Redaction.** Credentials, network names, query strings and absolute paths are stripped
  from logs. Logging goes to stderr only.

One thing to be clear about: the camera's own API has no authentication. Anything on its
access point can command it. SphereLoom cannot fix that, and does not pretend to.

## Internal documentation

Design records — architecture decision records, feature specifications, technical plans,
task breakdowns and the product vision — live under `docs/internal/` and are **encrypted at
rest** with [git-age](https://github.com/prskr/git-age). Contributors without the key see
ciphertext.

This does not affect contributing. Everything needed to build, test and extend SphereLoom
is in this guide, in [CONTRIBUTING.md](../CONTRIBUTING.md), and in the path-scoped
instruction files under `.github/instructions/`. If a change needs design context you
cannot read, open an issue and ask; the relevant reasoning will be summarised in the issue
or the pull request.

Maintainers with the private key need nothing special: the git filter decrypts on checkout
and encrypts on commit automatically.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for branch naming, commit conventions and the
pull request checklist. Three rules worth repeating:

1. Everything in the repository is in **English**.
2. **No GPL-licensed dependencies**, anywhere.
3. **Never commit vendor SDK binaries**, and never copy code from vendor sample
   repositories, which carry no licence file.
