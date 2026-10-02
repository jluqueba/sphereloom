# SphereLoom documentation

SphereLoom follows spec-driven development: every feature starts as a specification with
testable acceptance criteria, becomes a technical plan, is decomposed into tasks, and only
then is implemented. Decisions that outlive a single feature become architecture decision
records.

## Map

| Path | Contents |
| --- | --- |
| [`envisioning/`](envisioning/) | Product vision: problem, users, principles, milestones, non-goals, risks |
| [`features/<feature>/spec.md`](features/) | What a feature must do, with acceptance criteria |
| [`features/<feature>/plan.md`](features/) | How it will be built: architecture, modules, conventions |
| [`features/<feature>/tasks.md`](features/) | Ordered, sized tasks with dependencies |
| [`adr/`](adr/README.md) | Architecture decision records |
| [`vendor-capabilities.md`](vendor-capabilities.md) | What each vendor surface supports, with sources |
| [`repo-settings-proposal.md`](repo-settings-proposal.md) | Proposed (not applied) repository configuration |

## Current state

| Artefact | Status |
| --- | --- |
| Product vision | Approved |
| Milestone 1 spec — OSC camera control | Approved, not implemented |
| Milestone 1 technical plan | Approved |
| M0 + M1 task breakdown | Ready for implementation |
| ADR-0001 … ADR-0013 | Accepted |
| Implementation | Not started |

## Working agreements

- All documentation is written in **English**.
- A claim about vendor behaviour needs a source in
  [`vendor-capabilities.md`](vendor-capabilities.md).
- A capability matrix must distinguish *available now*, *planned for milestone N*, and
  *not supported by the vendor API*.
- Changing an accepted decision means writing a new ADR, not editing the old one.
- Documentation changes ship in the same pull request as the behaviour they describe.
