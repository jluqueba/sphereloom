"""Logging carries only allow-listed, structured data (ADR-0018, AC-9.5).

Each test feeds the logging path something that must not come out, such as a device value,
an absolute path, an exception message or a third-party log line, and checks the emitted
JSON for it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from enum import StrEnum
from pathlib import Path

import pytest

from sphereloom.config import CameraBackend, Settings, Transport
from sphereloom.domain.errors import StorageFullError
from sphereloom.logging import (
    FIELDS,
    REDACTED,
    JsonFormatter,
    LogEvent,
    WorkspacePath,
    configure_logging,
    log_event,
    redact,
    workspace_relative,
)
from sphereloom.server.app import build_server

SECRET = "s3cret-alice-holiday"


class _Colour(StrEnum):
    RED = "red"


class _NotAPath(str):
    __slots__ = ()


@pytest.fixture(autouse=True)
def _restore_logging() -> Iterator[None]:
    """Leave the root and SphereLoom loggers as they were, so tests stay independent."""
    root = logging.getLogger()
    ours = logging.getLogger("sphereloom")
    saved = (list(root.handlers), root.level, list(ours.handlers), ours.level, ours.propagate)
    yield
    root.handlers[:], root.level = saved[0], saved[1]
    ours.handlers[:], ours.level, ours.propagate = saved[2], saved[3], saved[4]


# Tests of the allow-list run with pattern redaction off, so a leak cannot pass by being
# redacted: redaction is defence in depth, not the guarantee under test.


def _records(capsys: pytest.CaptureFixture[str]) -> list[dict[str, object]]:
    captured = capsys.readouterr()
    assert captured.out == ""
    return [json.loads(line) for line in captured.err.splitlines()]


def _only_record(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    records = _records(capsys)
    assert len(records) == 1
    return records[0]


def test_an_event_is_logged_with_its_fixed_message_and_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(redaction=False)

    log_event(LogEvent.SERVER_STARTED, transport=Transport.STDIO, camera_backend=CameraBackend.OSC)

    record = _only_record(capsys)
    assert record["event"] == "server_started"
    assert record["message"] == "server started"
    assert record["logger"] == "sphereloom.server"
    assert record["level"] == "INFO"
    assert record["transport"] == "stdio"
    assert record["camera_backend"] == "osc"


def test_the_level_comes_from_the_event(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="WARNING")

    log_event(LogEvent.SERVING_HTTP, port=8765)
    log_event(LogEvent.HTTP_BEYOND_LOOPBACK, port=8765)

    record = _only_record(capsys)
    assert record["event"] == "http_beyond_loopback"
    assert record["level"] == "WARNING"


def test_a_field_outside_the_allow_list_is_dropped(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(redaction=False)

    log_event(LogEvent.SERVING_HTTP, port=1, serial=_Colour.RED)

    record = _only_record(capsys)
    assert "serial" not in record
    assert record["rejected_fields"] == ["serial"]
    assert record["port"] == 1


@pytest.mark.parametrize(
    "value",
    [
        SECRET,
        _NotAPath(SECRET),
        SECRET.encode(),
        float("nan"),
        float("inf"),
        2**63,
        -(2**63) - 1,
        object(),
        Path("/home/alice") / SECRET,
        ValueError(SECRET),
    ],
    ids=[
        "plain-str",
        "foreign-str-subclass",
        "bytes",
        "nan",
        "infinity",
        "above-int64",
        "below-int64",
        "object",
        "path",
        "exception",
    ],
)
def test_an_unsafe_value_is_dropped(capsys: pytest.CaptureFixture[str], value: object) -> None:
    configure_logging(redaction=False)

    log_event(LogEvent.SERVING_HTTP, port=value)  # type: ignore[arg-type]  # an unsafe value is the point

    record = _only_record(capsys)
    assert "port" not in record
    assert record["rejected_fields"] == ["port"]
    assert SECRET not in json.dumps(record)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (8765, 8765),
        (-(2**63), -(2**63)),
        (2**63 - 1, 2**63 - 1),
        (1.5, 1.5),
        (_Colour.RED, "red"),
        (WorkspacePath("downloads/clip.insv"), "downloads/clip.insv"),
    ],
    ids=["bool", "int", "int64-min", "int64-max", "float", "str-enum", "workspace-path"],
)
def test_a_safe_value_is_kept(
    capsys: pytest.CaptureFixture[str], value: object, expected: object
) -> None:
    configure_logging(redaction=False)

    log_event(LogEvent.SERVING_HTTP, port=value)  # type: ignore[arg-type]  # every safe type goes through one field

    assert _only_record(capsys)["port"] == expected


def test_only_the_type_and_code_of_an_error_are_logged(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(redaction=False)
    error = StorageFullError(f"card full at /home/alice/{SECRET}")
    error.__cause__ = OSError(SECRET)

    log_event(LogEvent.SERVER_STOPPED, error=error)

    record = _only_record(capsys)
    assert record["exception_type"] == "StorageFullError"
    assert record["error_code"] == "storage_full"
    assert SECRET not in json.dumps(record)
    assert "alice" not in json.dumps(record)


def test_a_third_party_record_carries_no_text(capsys: pytest.CaptureFixture[str]) -> None:
    """The leak found on `main`: httpx and the MCP SDK logged URLs and tracebacks verbatim."""
    configure_logging(redaction=False)

    logging.getLogger("httpx").warning(
        'HTTP Request: GET http://192.168.42.1/DCIM/%s.jpg?token=abc "HTTP/1.1 200 OK"', SECRET
    )
    try:
        raise ValueError(rf"camera said C:\Users\alice\{SECRET}")
    except ValueError:
        logging.getLogger("mcp.server.mcpserver.server").exception("Tool %r failed", SECRET)

    first, second = _records(capsys)
    assert first == {
        "ts": first["ts"],
        "level": "WARNING",
        "logger": "httpx",
        "event": "third_party_record",
    }
    assert second["exception_type"] == "ValueError"
    assert second["event"] == "third_party_record"
    assert SECRET not in json.dumps([first, second])
    assert "alice" not in json.dumps([first, second])


def test_third_party_records_below_warning_are_not_emitted(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging(level="DEBUG")

    logging.getLogger("httpx").info("HTTP Request: GET http://192.168.42.1/")

    assert _records(capsys) == []


def test_an_unrecognisable_logger_name_is_replaced(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(redaction=False)

    logging.getLogger(f"plugin {SECRET} " + "x" * 100).warning("anything")

    assert _only_record(capsys)["logger"] == "[unrecognised]"


@pytest.mark.parametrize("order", ["configure-first", "server-first"])
def test_the_mcp_sdk_cannot_add_a_second_root_handler(
    order: str, settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    """`MCPServer()` calls `logging.basicConfig`; in either order only our handler remains."""
    if order == "configure-first":
        configure_logging(redaction=False)
        build_server(settings)
    else:
        logging.getLogger().handlers.clear()
        build_server(settings)
        configure_logging(redaction=False)

    handlers = logging.getLogger().handlers
    assert len(handlers) == 1
    assert isinstance(handlers[0].formatter, JsonFormatter)

    logging.getLogger("mcp").warning("leak %s", SECRET)
    assert SECRET not in capsys.readouterr().err


def test_a_failing_formatter_emits_a_fixed_line(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    configure_logging(redaction=False)

    def explode(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError(SECRET)

    monkeypatch.setattr(JsonFormatter, "_format", explode)
    logging.getLogger("httpx").warning("leak %s", SECRET)

    err = capsys.readouterr().err
    assert json.loads(err) == {"level": "ERROR", "event": "log_record_unformattable"}
    assert SECRET not in err


def test_a_failed_write_does_not_echo_the_record(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default handler prints the message and arguments of a record it failed to write."""
    configure_logging(redaction=False)
    handler = logging.getLogger().handlers[0]

    def broken_emit(_record: logging.LogRecord) -> None:
        try:
            raise OSError(SECRET)
        except OSError:
            handler.handleError(_record)

    monkeypatch.setattr(handler, "emit", broken_emit)
    logging.getLogger("httpx").warning("leak %s", SECRET)

    assert SECRET not in capsys.readouterr().err


def test_redaction_applies_to_the_string_values_that_remain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Defence in depth: a workspace path can still contain a query-like name."""
    configure_logging(redaction=True)

    log_event(LogEvent.SERVING_HTTP, path=WorkspacePath("downloads/a.jpg?token=abc"))

    assert _only_record(capsys)["path"] == "downloads/a.jpg?" + REDACTED


def test_redaction_can_be_turned_off(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(redaction=False)

    log_event(LogEvent.SERVING_HTTP, path=WorkspacePath("downloads/a.jpg?token=abc"))

    assert _only_record(capsys)["path"] == "downloads/a.jpg?token=abc"


def test_configure_logging_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(redaction=False)
    configure_logging(redaction=False)

    log_event(LogEvent.CONFIG_VALID)

    assert len(_records(capsys)) == 1


def test_every_event_has_a_specification() -> None:
    for event in LogEvent:
        log_event(event)


def test_a_workspace_path_is_relative_or_absent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "downloads").mkdir(parents=True)

    inside = workspace_relative(workspace / "downloads" / "clip.insv", workspace)
    outside = workspace_relative(tmp_path / "elsewhere" / "clip.insv", workspace)

    assert inside == "downloads/clip.insv"
    assert isinstance(inside, WorkspacePath)
    assert outside is None


def test_the_allow_list_holds_only_identifiers() -> None:
    assert all(name.isidentifier() for name in FIELDS)


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abcdef1234567890abcdef",
        'token="s3cret-value-that-is-long"',
        "api_key=abcdef1234567890",
        'password: "hunter2hunter2"',
        'connected to ssid="X5.ABC123.OSC"',
        "GET http://192.168.42.1/files?token=abc&name=holiday",
        r"saved to C:\Users\alice\Videos\holiday.mp4",
        "saved to /home/alice/videos/holiday.mp4",
    ],
)
def test_pattern_redaction_removes_known_shapes(raw: str) -> None:
    cleaned = redact(raw)

    assert REDACTED in cleaned
    for secret in ("abcdef1234567890", "s3cret-value", "hunter2", "ABC123", "alice", "holiday"):
        assert secret not in cleaned


def test_pattern_redaction_keeps_relative_workspace_paths() -> None:
    assert redact("saved to downloads/clip.insv") == "saved to downloads/clip.insv"
