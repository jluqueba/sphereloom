---
applyTo: "**/*.py"
---

# Python instructions

- Use Python 3.12+ syntax and standard-library features.
- Keep code compatible with Python 3.12 and 3.13.
- Put importable package code under `src/sphereloom/`.
- Use full type annotations for functions, methods, attributes, and public constants.
- Keep `mypy` strict clean.
- Do not use `Any` as an escape hatch without a local written justification.
- Prefer precise protocols, generics, `TypedDict`, `Literal`, and pydantic models over `Any`.
- Do not use bare `# type: ignore`; include the exact error code and a short reason.
- Use `ruff` as the single formatter and linter.
- Do not introduce Black, isort, Flake8, pylint, or formatter-specific workarounds.
- Use `pydantic` models for all MCP tool inputs, MCP tool outputs, and configuration objects.
- Validate environment-derived configuration at the boundary.
- Keep pydantic models explicit about defaults, examples, and constrained values.
- Use `httpx.AsyncClient` for HTTP I/O.
- Always configure explicit `httpx` timeouts.
- Use async-first APIs for camera, media, server, and job execution code.
- Do not block the event loop with synchronous file, network, or subprocess work.
- Wrap unavoidable blocking work in an explicit executor or sidecar boundary.
- Use structured logging with redaction.
- Do not use `print` for server, adapter, or tool logging.
- Redact SSIDs, bearer tokens, credentials, SDK paths, and personal file paths before logging.
- Define a custom exception hierarchy for domain and adapter failures.
- Map exceptions onto the public error taxonomy at MCP boundaries.
- Include the structured `unsupported` error for unsupported capabilities.
- Write docstrings for public modules, classes, protocols, functions, and methods.
- Keep docstrings concise and factual.
- Keep backend-specific imports inside backend adapter packages.
- No module outside an adapter package may import backend-specific details.
- Domain code must depend on ports, protocols, and value objects, not concrete adapters.
- Sidecar process integration must be isolated behind adapter boundaries.
- Never load vendor C++ SDKs in-process.
- Do not write Python code that redistributes or embeds vendor SDK material.
- No module under `src/sphereloom/` may import `agent_framework` or any other agent or LLM orchestration library.
- Agent-framework imports belong in `examples/` and, later, in the optional `sphereloom[assistant]` extra.
- Do not introduce model clients, prompts, or inference calls into the server, tools, adapters, or job engine.
- Implement background work on the in-house job engine; do not build it on a workflow engine.
- Do not write code that drives another application's GUI, injects synthetic input, or scrapes the screen.

## Treat device and vendor responses as untrusted input

A camera response is not a trusted value. It arrives over a network from firmware we do not
control, and a malformed or hostile payload must never become a crash, an unbounded log, or
a request to somewhere we did not intend.

- Validate any URL taken from a device response against the expected origin before following it.
- Bound anything copied from a response into a message, a log or an error envelope using `sphereloom.domain.payloads`. Do not write a second bounding implementation: that logic existed twice here and only one copy was correct.
- Treat an unrecognised state, code or shape as a failure, never as success. Accepting an unknown state as "done" turns a malformed reply into an empty successful result, which is the most misleading outcome available.
- Reject non-2xx HTTP responses explicitly when redirects are not followed; a 3xx body is not content.
- Bound the size of anything read into memory from a remote source, while reading rather than after.
- Map every third-party transport exception onto the taxonomy, including ones raised while a caller iterates a stream.

## A validator must not raise an unmapped exception

Converting bad input into a structured refusal is the whole job of a validator. If it
crashes instead, it has failed at that job and the caller gets a stack trace where a clear
explanation was promised.

- Assume every operation on untrusted input can raise, including ones that look total. `int()` raises on non-finite floats; `str.encode()` raises on a lone surrogate; `json.loads` raises `RecursionError`, not `ValueError`, on deep nesting, and accepts `NaN` and `Infinity` by default.
- Wrap those operations and re-raise as the taxonomy error the function's contract promises.
- Test the hostile input, not only the merely wrong input.

## Anything placed in an error envelope must be JSON-serialisable by construction

An envelope that cannot be serialised turns a reported failure into an unreported crash,
which is the one thing the error path must never do.

- Put only JSON scalars, lists and objects into `details`. Replace anything else with a marker rather than hoping it renders.
- Never rely on `str()` of an untrusted value to make it safe: it materialises the whole structure before any bound applies.

## Make guarantees enforceable, not advisory

If a docstring states an invariant, ask immediately what makes it impossible to violate. If
the answer is "every caller remembers to pass the right argument", it is not a guarantee.

- Put safety decisions inside the component that owns the guarantee, not in a caller-supplied flag.
- Derive behaviour from data the component already has, such as a command name checked against an allowlist, so a new case cannot inherit unsafe behaviour by omission.
- When a mechanism and a signal both exist, fix both. Stopping an automatic retry while still reporting `retryable: true` simply moves the duplicate side effect one layer out.
- Keep module contracts, the CHANGELOG and the code describing the same policy. Prose that overstates what the code does is a defect, not documentation.

## Check edge values deliberately

These are not hypothetical; each has been a real defect in this repository.

- `or` for defaulting silently replaces a meaningful `0`, `""` or `False`. Use `is None`.
- `False == 0` and `True == 1`, so a membership test accepts a boolean where a number belongs. Compare type as well as value.
- Reject non-finite floats where a duration or a bound is expected: `NaN` defeats every comparison and infinity removes the limit.
- Non-finite values arrive as well-formed literals too. `parse_constant` catches the bare `NaN` and `Infinity` tokens, but `1e400` is valid JSON that every parser accepts and Python renders as `inf`; bound `parse_float` as well.
- `str()` on an integer is neither bounded nor total: CPython raises above 4300 digits rather than render it.
- Prefer a monotonic source for elapsed time and deadlines; wall clocks move backwards.

## Apply a rule at the chokepoint, not at the sites that were flagged

Every finding names one location. The rule it implies almost never applies to only that location.

- Before fixing, find the point through which the whole class flows: a shared helper, a parser, a formatter. Fix it there, then check the call sites.
- Fixing the consumers and leaving the shared helper untouched is backwards, and it is the single most common way a rule in this file has been written and then immediately violated. A `NaN` guard was once added to two callers of a bounding helper while the helper itself kept emitting `NaN`.
- A bound applied after an unbounded step protects nothing. Check the ordering: redacting, copying or decoding before bounding has already spent the memory the bound existed to save.
- When a limit reserves space for something added later, validate against the reduced limit. A name that fits on its own may not fit once a temporary suffix is appended, and the failure then arrives from the kernel mid-operation instead of from the validator.

## Write tests that can fail

- For each test, ask what would have to break for it to fail. If that is not obvious, the test is decorative.
- **A test's name is a claim. Make it exercise that claim.** A test called "retried even for an unsafe command" that uses a safe command passes without covering anything, and its green tick actively misleads.
- Do not synchronise on a fixed `sleep` when asserting on concurrency; wait on an explicit signal, or the test passes under favourable scheduling even when the behaviour is wrong.
- When testing a branch that waits, keep the conditions inside the window that triggers the wait, or the branch never runs.
- Prefer injecting at a seam the production code already defines, such as a transport or a clock, over patching internals. A test coupled to implementation detail breaks on refactoring and tests the wrong thing meanwhile.
- Fix the class of defect, not the single instance the reviewer named. When a fix applies to one call site, search for the others before committing.
- A test must not be a hazard itself. Building a structure to prove a bound works can allocate more than the bound ever would: share one child object per level instead of materialising a distinct subtree, so traversal is still exponential while construction is not.
- **Never assert a platform's own behaviour.** CI runs Ubuntu and Windows, and a filename a tab makes illegal on one is perfectly legal on the other. Inject the failure rather than provoking it, so the test asserts the guarantee instead of the quirk, and run the suite on Linux as well as Windows before pushing.
- Choose a timing threshold by measuring both the fixed and the regressed cost, and record both in the docstring. A threshold the regression would still pass under proves nothing, and one too close to the fixed cost fails on a loaded runner.
- Review the diff adversarially before pushing, not after. A finding that arrives from the pull request costs a full round trip; the same finding found locally costs minutes.
