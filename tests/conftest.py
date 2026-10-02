"""Shared test fixtures.

Two defaults make the whole suite trustworthy:

1. Outbound network access is blocked, so a test that accidentally reaches a real camera
   fails loudly instead of passing on someone's desk and failing in CI.
2. Time and identifiers are injectable, so nothing sleeps and nothing is random.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from sphereloom.config import Settings
from sphereloom.domain.clock import FakeClock
from sphereloom.domain.ids import SequentialIdFactory
from sphereloom.security.workspace import Workspace

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@pytest.fixture(autouse=True)
def _block_outbound_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail any test that opens a socket to something other than loopback.

    The fake camera runs on loopback, so legitimate tests are unaffected. A test that
    reached a real camera on someone's desk would otherwise pass locally and fail in CI.

    Tests marked `hardware` are exempt: reaching a real camera is the entire point of that
    tier, and it is opt-in and deselected in CI.
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
