"""Running the fake camera on loopback.

Tests talk to the fake over a real socket rather than through an in-process transport
shim. That costs a few milliseconds per test and buys coverage of the things that actually
break against hardware: timeouts, streaming, connection reuse and dropped transfers.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager

import uvicorn

from sphereloom.adapters.fake.camera_server import FakeCamera


def _free_port() -> int:
    """Reserve an ephemeral port, then release it for the server to bind.

    There is a small race between release and bind. It is tolerable here and avoids the
    much worse alternative of a fixed port, which makes tests fail when run in parallel.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def run_fake_camera(camera: FakeCamera, *, startup_timeout: float = 10.0) -> Iterator[str]:
    """Serve a fake camera on loopback, yielding its base URL."""
    port = _free_port()
    config = uvicorn.Config(
        camera.build_app(),
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
        lifespan="off",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        _wait_until_ready(server, port, timeout=startup_timeout)
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=startup_timeout)


def _wait_until_ready(server: uvicorn.Server, port: int, *, timeout: float) -> None:
    deadline = threading.Event()
    timer = threading.Timer(timeout, deadline.set)
    timer.start()
    try:
        while not deadline.is_set():
            if server.started:
                return
            deadline.wait(0.02)
    finally:
        timer.cancel()

    message = f"The fake camera did not start listening on port {port} within {timeout}s."
    raise TimeoutError(message)
