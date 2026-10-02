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

REDACTED = "[redacted]"

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
        record.msg = redact(str(record.msg))
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
    """Emits one JSON object per line, which is both greppable and machine-readable."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


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
