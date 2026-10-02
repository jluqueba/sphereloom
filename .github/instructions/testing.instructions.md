---
applyTo: "tests/**/*.py"
---

# Testing instructions

- Use pytest for all tests.
- Put fast unit tests in `tests/unit/`.
- Put component tests in `tests/component/`.
- Put optional real-hardware tests in `tests/integration/`.
- Use the fake OSC camera fixture as the default backend.
- Unit and component tests must not require a physical camera.
- Unit and component tests must not access the external network.
- Use local fakes, stubs, and in-process HTTP test servers for OSC behavior.
- Mark real-hardware tests with `@pytest.mark.hardware`.
- Enable hardware tests only when an explicit environment variable is set.
- Skip hardware tests otherwise.
- Never run hardware tests in CI by default.
- Inject clocks for deterministic time behavior.
- Inject ID factories for deterministic job IDs, confirmation tokens, cursors, and request IDs.
- Prefer Arrange-Act-Assert structure in test bodies.
- Use descriptive test names that state the behavior under test.
- Cover success, capability-unsupported, timeout, cancellation, path-jail, and redaction paths as features land.
- Every public error-taxonomy code must have at least one test.
- Keep tests independent and order-insensitive.
- Avoid sleeping in tests; use fake clocks or explicit synchronization.
- Do not paste real SSIDs, tokens, personal paths, or camera credentials into fixtures.
- Treat real-camera tests as opt-in diagnostics, not as required validation.
