"""The fixture capture script decodes camera responses strictly and within bounds.

It talks to a real camera, so it receives exactly the untrusted input the strict decoder
exists for. These tests drive its request helper through an in-memory transport.
"""

from __future__ import annotations

import gzip
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from sphereloom.domain.errors import InternalError
from sphereloom.domain.payloads import MAX_PAYLOAD_BYTES

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "capture_osc_fixtures.py"
_CHUNK = 64 * 1024


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_osc_fixtures_decoding", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load_script()


# Bodies are given as iterators: a response built from `bytes` counts as already read, and
# the script reads raw streams, as every response from a real connection is.
def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(base_url="http://camera.invalid", transport=handler)


def test_a_response_is_decoded_strictly() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=iter([b'{"x": NaN}'])))

    with _client(transport) as client, pytest.raises(InternalError) as caught:
        script._request_json(client, "GET", "/osc/info")

    assert caught.value.details == {"decode_error": "non_finite_number"}


def test_a_well_formed_response_is_returned() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, content=iter([b'{"model": "X5"}']))
    )

    with _client(transport) as client:
        assert script._request_json(client, "GET", "/osc/info") == {"model": "X5"}


def test_a_compressed_response_is_not_decompressed_into_the_buffer() -> None:
    """A compressed chunk could expand past the limit before the buffer sees it."""
    compressed = gzip.compress(b'{"model": "X5"}')
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, content=iter([compressed]), headers={"Content-Encoding": "gzip"}
        )
    )

    with _client(transport) as client, pytest.raises(InternalError) as caught:
        script._request_json(client, "GET", "/osc/info")

    assert caught.value.details == {"decode_error": "not_utf8"}


_ACCEPTED = b'{"state": "inProgress", "id": "1"}'


@pytest.mark.parametrize(
    ("execute_body", "status_body", "expected"),
    [
        (_ACCEPTED, b'{"state": "done", "x": NaN}', {"decode_error": "non_finite_number"}),
        (_ACCEPTED, b"[]", {"unexpected_shape": "/osc/commands/status"}),
        (_ACCEPTED, b"null", {"unexpected_shape": "/osc/commands/status"}),
        (b'"accepted"', b"{}", {"unexpected_shape": "/osc/commands/execute"}),
    ],
    ids=["undecodable-status", "array-status", "null-status", "string-acknowledgement"],
)
def test_an_unusable_response_fails_the_capture(
    monkeypatch: pytest.MonkeyPatch,
    execute_body: bytes,
    status_body: bytes,
    expected: dict[str, str],
) -> None:
    """Only a capture that did not finish is tolerated; an unusable response is not.

    Tolerating it, or reading a malformed status as one that is merely unfinished, would
    write partial fixtures and exit successfully.
    """

    def camera(request: httpx.Request) -> httpx.Response:
        bodies = {"/osc/commands/status": status_body, "/osc/commands/execute": execute_body}
        return httpx.Response(200, content=iter([bodies.get(request.url.path, b"{}")]))

    real_client = httpx.Client

    def client_factory(**kwargs: object) -> httpx.Client:
        return real_client(transport=httpx.MockTransport(camera), **kwargs)  # type: ignore[arg-type]  # forwarding the script's own keyword arguments

    ticks = iter(range(1_000))
    monkeypatch.setattr(script.httpx, "Client", client_factory)
    monkeypatch.setattr(
        script, "time", SimpleNamespace(monotonic=lambda: float(next(ticks)), sleep=lambda _: None)
    )

    with pytest.raises(InternalError) as caught:
        script.capture("http://camera.invalid", take_photo=True, timeout=1.0)

    assert caught.value.details == expected


def test_an_oversized_response_is_refused_without_reading_it_all() -> None:
    pulled = 0
    available = 4 * MAX_PAYLOAD_BYTES // _CHUNK

    def body() -> Iterator[bytes]:
        nonlocal pulled
        for _ in range(available):
            pulled += 1
            yield b" " * _CHUNK

    transport = httpx.MockTransport(lambda _: httpx.Response(200, content=body()))

    with _client(transport) as client, pytest.raises(InternalError) as caught:
        script._request_json(client, "GET", "/osc/info")

    assert caught.value.details["decode_error"] == "too_large"
    assert pulled <= MAX_PAYLOAD_BYTES // _CHUNK + 1
