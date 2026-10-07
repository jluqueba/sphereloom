# Pull request

## Summary

-

## Linked issue

Closes #

## Type of change

- [ ] Feature
- [ ] Bug fix
- [ ] Documentation
- [ ] Refactoring
- [ ] Tooling or CI
- [ ] Other:

## SDD artifacts

Link any specs, plans, tasks, or ADRs touched by this PR:

- Spec:
- Plan:
- Tasks:
- ADR:

## Checklist

- [ ] Ruff lint and format pass.
- [ ] Mypy strict passes.
- [ ] Pytest passes on Windows and on Linux (for example WSL Ubuntu).
- [ ] Every new regression test was mutation-checked: it fails when the defect is reintroduced.
- [ ] One topic, at most about 400 lines of production code (tests excluded).
- [ ] Closes exactly one issue (`Closes #N` above); the milestone is on that issue, not on this PR. Area and type labels set.
- [ ] Documentation is updated where needed.
- [ ] CHANGELOG entry added for user-visible changes.
- [ ] No GPL-licensed dependencies were added.
- [ ] No vendor SDK binaries, headers, samples, or archives were committed.
- [ ] All code, comments, and documentation are in English.
- [ ] PR title follows Conventional Commits and is suitable for squash merge.

## Invariants

For code changes, list each invariant this PR must hold and the test that proves it: bounds
on untrusted input, status or error to taxonomy mapping and retryability, behaviour after an
ambiguous outcome, and what is written to disk and how it is cleaned up. Write "None" for
documentation-only changes.

-

## Validation

List every command that was run and its result (for example `.\check.ps1` — passed).
Commands listed without a result do not count; state anything that was not run and why.

| Command | Result |
| ------- | ------ |
|         |        |

## Testing

Describe how this was tested:

- [ ] Fake OSC camera
- [ ] Real hardware, opt-in local test only
- [ ] Not run, reason:

Notes:
