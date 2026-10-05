"""Structured logging with redaction.

Two hard rules, both of them load-bearing:

1. Logs go to stderr only. The stdio transport owns stdout for the MCP protocol itself, so
   a stray write there corrupts the session.
2. Sensitive material is removed before a record is emitted. Users point this server at
   their own cameras and their own footage; neither belongs in a log file or a bug report.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from sphereloom.domain.payloads import bounded_payload, bounded_text

REDACTED = "[redacted]"

#: Limits for a single log line. A log record carries device-derived values, so each part
#: needs a bound of its own: being serialisable is not the same as being a sensible size,
#: and an unreadable line helps nobody diagnose the failure it describes.
MAX_MESSAGE_LENGTH = 2000
MAX_TRACEBACK_LENGTH = 8000
MAX_CONTEXT_STRING = 500
MAX_CONTEXT_ITEMS = 50
MAX_CONTEXT_DEPTH = 6
MAX_CONTEXT_NODES = 500

#: Patterns stripped from every log record when redaction is enabled.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer tokens and confirmation tokens.
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]{8,}=*"), r"\1" + REDACTED),
    (
        re.compile(r"(?i)\b(token|secret|password|api[_-]?key)(\"?\s*[:=]\s*\"?)[^\s,\"'}]+"),
        r"\1\2" + REDACTED,
    ),
    # Wi-Fi network names, which identify a person's home or workplace.
    (re.compile(r"(?i)\b(ssid)(\"?\s*[:=]\s*\"?)[^\s,\"'}]+"), r"\1\2" + REDACTED),
    # Query strings can carry credentials and file identifiers.
    (re.compile(r"(\?)[^\s\"']{1,512}"), r"\1" + REDACTED),
    # Absolute paths leak usernames and library layout.
    (re.compile(r"(?i)\b[a-z]:\\[^\s\"',]{1,512}"), REDACTED),
    (re.compile(r"(?<![\w.])/(?:home|Users|root|var|tmp)/[^\s\"',]{1,512}"), REDACTED),
)


def redact(text: str) -> str:
    """Remove secrets, network names, query strings and absolute paths from a string."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactionFilter(logging.Filter):
    """Applies `redact` to the formatted message and to structured extras."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(str(record.msg))
        except Exception:  # `str()` runs arbitrary `__str__` code
            record.msg = "<message could not be rendered>"
            record.args = ()
        if record.args:
            record.args = tuple(
                redact(arg) if isinstance(arg, str) else arg
                for arg in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            record.context = {
                key: redact(value) if isinstance(value, str) else value
                for key, value in context.items()
            }
        return True


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per line, which is both greppable and machine-readable.

    Every line this formatter produces is valid JSON, without exception. A consumer that
    has to cope with occasional unparseable lines is not getting structured logs, and the
    lines most likely to be malformed are the ones describing a misbehaving device, which
    are exactly the ones worth reading.
    """

    def format(self, record: logging.LogRecord) -> str:
        try:
            message = record.getMessage()
        except Exception:  # a bad argument must not lose the record
            message = "<message could not be rendered>"

        base: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": bounded_text(message, MAX_MESSAGE_LENGTH),
        }
        payload: dict[str, Any] = dict(base)
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            # Bounded, not merely serialisable. `default=str` *succeeds* for an object whose
            # `__str__` returns megabytes, so the fallback below never runs and the line is
            # unbounded. Context values come from device responses, so the limit has to be
            # applied before `json.dumps` sees them.
            payload.update(
                bounded_payload(
                    context,
                    max_string=MAX_CONTEXT_STRING,
                    max_items=MAX_CONTEXT_ITEMS,
                    max_depth=MAX_CONTEXT_DEPTH,
                    max_nodes=MAX_CONTEXT_NODES,
                )
            )
        if record.exc_info:
            payload["exception"] = bounded_text(
                self.formatException(record.exc_info), MAX_TRACEBACK_LENGTH
            )

        # `allow_nan=False` because `json.dumps` otherwise emits bare NaN and Infinity,
        # which no JSON parser accepts: one malformed value from a device would make the
        # line unreadable by the tooling meant to consume it.
        try:
            return json.dumps(payload, default=str, allow_nan=False)
        except Exception:  # `default=str` runs arbitrary `__str__` code
            # The fallback carries only strings built above, so it cannot fail in turn.
            base["context_error"] = "context was not serialisable and has been dropped"
            return json.dumps(base, allow_nan=False)


def configure_logging(*, level: str = "INFO", redaction: bool = True) -> None:
    """Install the stderr JSON handler. Safe to call more than once."""
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())
    if redaction:
        handler.addFilter(RedactionFilter())

    root = logging.getLogger("sphereloom")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Without this, records would also reach the root logger's default stderr handler and
    # bypass the redaction filter installed above.
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger that inherits the configured handler."""
    return logging.getLogger(f"sphereloom.{name}")


def workspace_relative(path: Path, workspace: Path) -> str:
    """Render a path for user-facing output, relative to the workspace when possible.

    Results and logs expose workspace-relative paths so absolute filesystem layout, which
    usually embeds a username, never leaves the process.
    """
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.name
