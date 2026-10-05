"""Shared test fixtures.

Two defaults make the whole suite trustworthy:

1. Outbound network access is blocked, so a test that accidentally reaches a real camera
   fails loudly instead of passing on someone's desk and failing in CI.
2. Time and identifiers are injectable, so nothing sleeps and nothing is random.
"""

from __future__ import annotations

import os
import socket
from collections.abc import Iterator
from pathlib import Path
from typing import NoReturn

import pytest

from sphereloom.config import Settings
from sphereloom.domain.clock import FakeClock
from sphereloom.domain.ids import SequentialIdFactory
from sphereloom.security.workspace import Workspace

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

HARDWARE_OPT_IN = "SPHERELOOM_ENABLE_HARDWARE_TESTS"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip the hardware tier unless it was explicitly asked for.

    The marker alone is not enough. A plain `pytest -m hardware` would otherwise reach for
    whatever camera happens to be on the network, which is both surprising and, since these
    tests can capture and download, not harmless. The opt-in is checked here rather than in
    a fixture because `_isolate_settings_env` clears every SPHERELOOM_ variable before
    fixtures run, so by then the flag would always look unset.
    """
    if os.environ.get(HARDWARE_OPT_IN, "").strip() in {"1", "true", "True"}:
        return

    skip = pytest.mark.skip(
        reason=f"Hardware tests need a real camera and {HARDWARE_OPT_IN}=1.",
    )
    for item in items:
        if item.get_closest_marker("hardware") is not None:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _block_outbound_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail any test that opens a socket to something other than loopback.

    The fake camera runs on loopback, so legitimate tests are unaffected. A test that
    reached a real camera on someone's desk would otherwise pass locally and fail in CI.

    Tests marked `hardware` are exempt: reaching a real camera is the entire point of that
    tier. They are additionally gated by `pytest_collection_modifyitems` above, which skips
    them altogether unless the opt-in variable is set, so exempting them here cannot by
    itself let a test reach the network.
    """
    if request.node.get_closest_marker("hardware") is not None:
        return

    real_connect = socket.socket.connect

    def guarded_connect(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in _LOOPBACK_HOSTS:
            message = (
                f"Outbound network access to {host!r} is blocked in tests. Use the fake "
                "camera on loopback, or mark the test with @pytest.mark.hardware."
            )
            raise RuntimeError(message)
        real_connect(self, address)  # type: ignore[arg-type]

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def _isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove the developer's own SPHERELOOM_* variables from the test environment."""
    for key in [name for name in os.environ if name.startswith("SPHERELOOM_")]:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def clock() -> FakeClock:
    """A manually advanced clock."""
    return FakeClock()


@pytest.fixture
def id_factory() -> SequentialIdFactory:
    """Predictable identifiers and tokens."""
    return SequentialIdFactory()


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    """A path jail rooted in a temporary directory."""
    root = tmp_path / "workspace"
    root.mkdir()
    return Workspace(root)


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Default settings pointed at an isolated workspace."""
    monkeypatch.setenv("SPHERELOOM_WORKSPACE_DIR", str(tmp_path / "workspace"))
    return Settings()


class InstrumentedStr(str):
    """A string that records how much of itself was read and refuses whole-string work.

    Proving that a bound limits the *work*, not just the result, cannot be done with a
    stopwatch: on a shared CI runner no threshold reliably separates the fixed cost from the
    regressed one, so such a test either flakes or passes a regression. This makes the work
    observable instead. Iteration is counted, and the operations that touch every character
    at once -- `strip`, `split`, `encode`, `__str__` -- raise, so an implementation that
    reaches for them on oversized input fails deterministically.

    Slicing and `len` are left alone because they are what a bounded implementation uses.
    """

    consumed: int

    def __new__(cls, value: str) -> InstrumentedStr:
        instance = super().__new__(cls, value)
        instance.consumed = 0
        return instance

    def __iter__(self) -> Iterator[str]:
        for character in str.__iter__(self):
            self.consumed += 1
            yield character

    def _refuse(self, *args: object, **kwargs: object) -> NoReturn:
        message = "a whole-string operation ran on input that should have been bounded first"
        raise AssertionError(message)

    strip = _refuse
    split = _refuse
    encode = _refuse
    __str__ = _refuse


@pytest.fixture
def instrumented_str() -> type[InstrumentedStr]:
    """The `InstrumentedStr` class, for tests that need to observe how much input was read."""
    return InstrumentedStr
