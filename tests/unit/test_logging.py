"""Logs must never carry a user's secrets, network names or filesystem layout."""

from __future__ import annotations

import json
import logging

import pytest

from sphereloom.domain.payloads import MALFORMED
from sphereloom.logging import REDACTED, RedactionFilter, configure_logging, redact


@pytest.mark.parametrize(
    "raw",
    [
        "Authorization: Bearer abcdef1234567890abcdef",
        'token="s3cret-value-that-is-long"',
        "api_key=abcdef1234567890",
        'password: "hunter2hunter2"',
    ],
)
def test_credentials_are_removed(raw: str) -> None:
    cleaned = redact(raw)

    assert REDACTED in cleaned
    for secret in ("abcdef1234567890abcdef", "s3cret-value-that-is-long", "hunter2hunter2"):
        assert secret not in cleaned


def test_wifi_network_names_are_removed() -> None:
    """An SSID identifies a person's home or workplace."""
    cleaned = redact('connected to ssid="X5 ABC123.OSC"')

    assert "X5 ABC123.OSC" not in cleaned
    assert REDACTED in cleaned


def test_query_strings_are_removed() -> None:
    cleaned = redact("GET http://192.168.42.1/files?token=abc&name=holiday")

    assert "token=abc" not in cleaned
    assert "holiday" not in cleaned


@pytest.mark.parametrize(
    "raw",
    [
        r"saved to C:\Users\alice\Videos\holiday.mp4",
        "saved to /home/alice/videos/holiday.mp4",
        "saved to /Users/alice/Movies/holiday.mp4",
    ],
)
def test_absolute_paths_are_removed(raw: str) -> None:
    """Absolute paths embed a username, and filenames describe someone's private life."""
    cleaned = redact(raw)

    assert "alice" not in cleaned
    assert "holiday" not in cleaned


def test_relative_workspace_paths_survive() -> None:
    """Redaction must not destroy the information users actually need."""
    cleaned = redact("saved to downloads/clip.insv")

    assert cleaned == "saved to downloads/clip.insv"


def test_the_filter_cleans_structured_context() -> None:
    record = logging.LogRecord(
        name="sphereloom.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="download finished",
        args=(),
        exc_info=None,
    )
    record.context = {"destination": "/home/alice/clip.mp4", "bytes": 1024}

    RedactionFilter().filter(record)

    cleaned = record.context  # type: ignore[attr-defined]
    assert cleaned["destination"] == REDACTED
    assert cleaned["bytes"] == 1024


def test_logging_goes_to_stderr_not_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """stdout belongs to the MCP protocol; a stray write there corrupts the session."""
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("hello")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "hello" in captured.err


def test_configure_logging_is_idempotent(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", redaction=True)
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("once")

    assert capsys.readouterr().err.count("once") == 1


def test_every_log_line_is_parseable_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("structured", extra={"context": {"bytes": 1024}})

    line = capsys.readouterr().err.strip()
    assert json.loads(line)["bytes"] == 1024


def test_a_non_finite_value_does_not_produce_invalid_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`json.dumps` renders NaN bare by default, which no JSON parser accepts.

    Bounding the context replaces it rather than dropping the whole context, so the rest
    of the record still says what was happening when the bad value arrived.
    """
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "progress", extra={"context": {"completion": float("nan"), "command": "takePicture"}}
    )

    line = capsys.readouterr().err.strip()
    assert "NaN" not in line
    payload = json.loads(line)
    assert payload["message"] == "progress"
    assert payload["completion"] == MALFORMED
    assert payload["command"] == "takePicture"


def test_an_unserialisable_value_does_not_lose_the_log_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A log record must never fail; the message survives even when a value cannot."""

    class Unserialisable:
        def __repr__(self) -> str:
            raise RuntimeError("repr blows up")

    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info(
        "kept", extra={"context": {"value": Unserialisable(), "command": "listFiles"}}
    )

    payload = json.loads(capsys.readouterr().err.strip())
    assert payload["message"] == "kept"
    assert payload["value"] == MALFORMED
    assert payload["command"] == "listFiles"


def test_an_enormous_context_value_does_not_produce_an_unbounded_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`default=str` succeeds for an object whose `__str__` returns megabytes.

    Succeeding is the problem: the fallback never runs, so without a bound applied before
    serialisation the line is as large as the device cares to make it.
    """

    class Enormous:
        def __str__(self) -> str:
            return "x" * 5_000_000

    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("big", extra={"context": {"blob": Enormous()}})

    line = capsys.readouterr().err.strip()

    assert len(line) < 10_000
    assert json.loads(line)["message"] == "big"


def test_an_enormous_message_is_bounded(capsys: pytest.CaptureFixture[str]) -> None:
    """Arguments interpolated into a message are as untrusted as any other value."""
    configure_logging(level="INFO", redaction=True)
    logging.getLogger("sphereloom.test").info("camera said %s", "y" * 5_000_000)

    line = capsys.readouterr().err.strip()

    assert len(line) < 10_000
    assert json.loads(line)["message"].startswith("camera said")
