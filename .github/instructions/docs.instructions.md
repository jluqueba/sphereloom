---
applyTo: "**/*.md"
---

# Documentation instructions

- Write all documentation in English.
- Keep Markdown markdownlint-clean.
- Use sentence-case headings.
- Use ATX headings with `#` markers.
- Use fenced code blocks with language tags.
- Use repository-relative links for internal references.
- Do not use bare URLs when a descriptive link is clearer.
- Be honest about capability status.
- Capability matrices must distinguish `available now`, `planned milestone`, and `not supported by the vendor API`.
- Do not claim live preview, streaming, OSC exposure control, or OSC video stitching support.
- Do not claim trimming, cutting, merging, or timeline editing support; the vendor does not document it.
- Describe `sphereloom-assistant` as an optional, deferred layer after Milestone 2, never as part of the base server.
- State that the base server requires no model credentials and depends on no agent framework.
- State that Milestone 1 is OSC Wi-Fi only when describing initial functionality.
- State that media and stitching support is planned for a later milestone until implemented.
- When Insta360 is mentioned prominently, include this disclaimer: `Insta360 is a trademark of Arashi Vision Inc. SphereLoom is an independent, unaffiliated project and is neither endorsed by nor associated with Arashi Vision Inc.`
- Keep trademark usage descriptive and compatibility-focused.
- Do not imply endorsement, partnership, certification, or sponsorship.
- Pad table delimiter rows (`| --- | --- |`) so tables satisfy MD060.
- `MD029` is disabled on purpose: ADR decision lists are numbered continuously across sub-headings so individual decisions can be cited by number.
- Keep SDD envisioning artifacts in `docs/internal/envisioning/`.
- Keep feature artifacts in `docs/internal/features/<feature>/spec.md`, `plan.md`, and `tasks.md`.
- Keep ADRs in `docs/internal/adr/NNNN-*.md`.
- Write ADRs in MADR style with these sections: Status, Context, Decision, Consequences, and Alternatives considered.
- Changelog entries must follow Keep a Changelog conventions.
- Prefer concise examples over speculative roadmap promises.
