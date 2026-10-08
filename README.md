# 🔮 SphereLoom

> Automating a 360° shoot today means a phone app, a desktop stitcher and a folder of files
> copied by hand. **SphereLoom is built to let an AI agent drive an Insta360 camera directly, safely
> and honestly.**

SphereLoom is an [MCP](https://modelcontextprotocol.io) server, designed so that any MCP
client — an editor, a desktop assistant or your own agent — can check a camera, change its
settings, capture, browse the gallery and bring media to your machine, through one set of
typed tools. It is in early development: the [capability matrix](docs/CAPABILITIES.md) shows
what works today.

## What it is designed to do

- **Speaks one tool surface** for connecting, checking status, reading and changing
  settings, taking photos, recording video, browsing the gallery and downloading media.
- **Is honest about every capability.** A request the camera cannot serve gets a
  structured, explained answer — what is not possible, why, and where to read more —
  instead of a timeout or a stack trace.
- **Runs long work in the background.** Downloads are jobs you can follow and cancel, and
  responses carry file paths and metadata, never media bytes.
- **Is safe by default.** It runs locally over stdio with no network listener, writes only
  inside one workspace folder, and asks for an explicit confirmation before deleting
  anything.
- **Keeps your media local.** Nothing is uploaded anywhere, and no model credentials are
  needed: the server is deterministic, and the reasoning stays in your client.
- **Works without a camera for development.** A built-in fake camera runs the whole test
  suite and doubles as a demo backend.

## How it is designed to work, in plain terms

1. You join your camera's Wi-Fi access point from your computer.
2. Your MCP client starts SphereLoom as a local process.
3. The agent calls tools such as "check status" or "take a photo"; SphereLoom checks that
   the camera can do it, talks to the camera, and returns a typed result.
4. Downloads run as background jobs and land in your workspace folder.

SphereLoom targets **Insta360 cameras**, and development and testing target the
**Insta360 X5**. The Wi-Fi layer follows the open Open Spherical Camera API, so other
cameras may happen to work, but none is tested or supported.

## Documentation

- [Developer guide](docs/DEVELOPER_GUIDE.md): architecture and diagrams, requirements,
  getting started, project layout, testing, configuration and the security model.
- [Capabilities](docs/CAPABILITIES.md): what SphereLoom can do today, what is not available
  yet, and what the camera's API does not offer.
- [Contributing](CONTRIBUTING.md): how to propose changes and the workflow to follow.
- [Security policy](SECURITY.md): how to report a vulnerability.
- [Code of Conduct](CODE_OF_CONDUCT.md): the standards expected in this community.
- [Changelog](CHANGELOG.md): what changed, and when.

## For developers

Want to run SphereLoom, try it without a camera, or understand how it is built? Head to the
[developer guide](docs/DEVELOPER_GUIDE.md). You do not need a camera to contribute.

## Status

Early development. The MCP server runs over stdio and enforces its configuration and
security guard rails, but it does not control a camera yet. The
[capability matrix](docs/CAPABILITIES.md) shows the state of every capability, and the
[changelog](CHANGELOG.md) announces each one as it ships.

## Licensing and trademarks

SphereLoom is released under the [MIT licence](LICENSE). It needs no agent framework and no
model client, and GPL-licensed dependencies are not used.

SphereLoom **does not redistribute any Insta360 SDK**. Wi-Fi control uses only Insta360's
publicly documented HTTP API. Features that need a vendor SDK require you to apply for it
yourself, accept its licence, and point SphereLoom at your local copy.

> Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent, unaffiliated
> project and is neither endorsed by, sponsored by, nor associated with Arashi Vision Inc.
> The name "Insta360" is used here only descriptively, to state which cameras this software
> works with.
