# Release communication

How a shipped capability becomes an announcement. The point of writing this down is that a
ritual nobody records is a ritual nobody follows.

## When this applies

From the first usable release onwards, every **user-visible capability** that lands on
`main` goes through this process. Internal refactoring, dependency bumps and documentation
fixes do not.

## The chain

The trigger is deliberately tied to artifacts that exist, not to a feeling of progress:

1. A pull request delivering user-visible capability is squash-merged to `main`.
2. `CHANGELOG.md` gains an entry under the next version.
3. A tag and a GitHub Release are published with notes derived from that entry.
4. The maintainer is notified, so humans and agents stay aligned on what shipped.
5. A LinkedIn draft is prepared for the maintainer to edit and publish.

If a capability never reaches `main`, there is no release and no post. That is the point.

## Draft structure

Each draft is written in English and contains:

- A hook: the concrete situation the capability addresses.
- The problem, in one or two sentences.
- What the capability actually does, described in terms a non-specialist can follow.
- **One honest limitation.** Every post names something the project cannot do yet.
- Links to the repository and to the release.
- A call to action: what kind of feedback or contribution is wanted.

Drafts are raw material, not finished copy. The maintainer's voice belongs to the
maintainer; the draft supplies the structure and the technical accuracy.

Drafts live outside this repository. They are communication material, not project
documentation, and versioning them alongside the code would serve no one.

## Pre-publication checklist

Every draft must pass all of these before it is handed over.

### Trademark and attribution

The SDK end user licence agreement prohibits using the vendor's trademarks to market
software built on it without written permission, and prohibits implying that the software
comes from the vendor or is endorsed by them. A public post is marketing in a fairly literal
sense, so:

- [ ] No vendor logos, product photography or brand imagery.
- [ ] No wording that implies official status, partnership, endorsement or collaboration.
- [ ] Vendor names appear only descriptively, to state compatibility.
- [ ] The post or its first comment notes that this is an independent project, not
      affiliated with or endorsed by the camera manufacturer.

### Accuracy

- [ ] Every claim matches a capability that is implemented and released, checked against
      `docs/vendor-capabilities.md` and the capability registry.
- [ ] No capability is implied by omission. In particular, nothing suggests clip trimming,
      joining clips or timeline editing, which no vendor documentation describes.
- [ ] Stated limitations are current, not copied from an earlier post.

### Privacy and secrets

- [ ] No personal photos or video in screenshots or demo clips.
- [ ] No network names, tokens, serial numbers or absolute file paths visible in any
      terminal capture.
- [ ] Demo material uses the fake camera backend or footage the maintainer is content to
      publish.

## Related documents

- [`CHANGELOG.md`](../../CHANGELOG.md) — the source of release notes.
- [`docs/vendor-capabilities.md`](../vendor-capabilities.md) — what is actually supported.
- [ADR-0006](../adr/0006-licensing-and-trademark-posture.md) — the licensing and trademark
  reasoning behind the checklist above.
