"""Structured logging from a closed allow-list (ADR-0018).

Logging never receives untrusted text, so there is nothing in it to redact. Three rules
make that a property of this module rather than of every caller:

1. **Every record is an event.** `log_event` takes a `LogEvent` member, whose message and
   level are fixed in a table here. There is no free-form message argument, and a codebase
   rule forbids importing the standard `logging` module anywhere else, so no other part of
   SphereLoom can obtain a logger.
2. **Fields come from an allow-list, values from safe types.** A field name outside
   `FIELDS`, or a value that is not a bool, a finite float, a 64-bit integer, a `StrEnum`
   member or a `WorkspacePath`, is dropped and its name recorded in `rejected_fields`. An
   exception contributes only its type name and, for a SphereLoom error, its code.
3. **Third-party records carry no text.** The handler sits on the root logger and replaces
   whatever was there, including the handler the MCP SDK installs. From a record emitted by
   any other library, only its logger name, level and exception type are kept; never its
   message, arguments or traceback.

Pattern redaction (`redact`) still runs over the string values that remain, as defence in
depth. Its limits: it recognises only known shapes, such as a bearer token, a `key=value`
pair with a sensitive key, a query string or an absolute path under a personal directory;
it cannot recognise a secret that carries no marker, or one encoded or split to avoid the
patterns. It is never what keeps a secret out of the logs.

Logs go to stderr only: the stdio transport owns stdout for the MCP protocol itself.
"""

from __future__ import annotations

import json
import logging
import math
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from sphereloom.domain.errors import SphereLoomError

REDACTED = "[redacted]"

_ROOT_LOGGER = "sphereloom"
_PAYLOAD_ATTRIBUTE = "sphereloom_event"
_INT64_LIMIT = 2**63
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_UNRECOGNISED = "[unrecognised]"


class LogEvent(StrEnum):
    """Everything SphereLoom can log. A new event needs an entry in `_EVENTS`."""

    CONFIG_VALID = "config_valid"
    SERVER_STARTED = "server_started"
    SERVER_STOPPED = "server_stopped"
    SERVER_INTERRUPTED = "server_interrupted"
    SERVING_STDIO = "serving_stdio"
    SERVING_HTTP = "serving_http"
    HTTP_BEYOND_LOOPBACK = "http_beyond_loopback"


@dataclass(frozen=True, slots=True)
class _EventSpec:
    component: str
    level: int
    message: str


_EVENTS: dict[LogEvent, _EventSpec] = {
    LogEvent.CONFIG_VALID: _EventSpec("cli", logging.INFO, "configuration is valid"),
    LogEvent.SERVER_STARTED: _EventSpec("server", logging.INFO, "server started"),
    LogEvent.SERVER_STOPPED: _EventSpec("server", logging.INFO, "server stopped"),
    LogEvent.SERVER_INTERRUPTED: _EventSpec("cli", logging.INFO, "interrupted, shutting down"),
    LogEvent.SERVING_STDIO: _EventSpec("transport", logging.INFO, "serving over stdio"),
    LogEvent.SERVING_HTTP: _EventSpec("transport", logging.INFO, "serving over authenticated http"),
    LogEvent.HTTP_BEYOND_LOOPBACK: _EventSpec(
        "transport",
        logging.WARNING,
        "HTTP transport is bound beyond loopback; camera control is reachable from your "
        "network and is protected only by the bearer token",
    ),
}

#: Field names a record may carry. A codebase rule checks every `log_event` call against it.
FIELDS = frozenset({"camera_backend", "path", "port", "transport"})


class WorkspacePath(str):
    """A path relative to the workspace, safe to log.

    In SphereLoom's code only `workspace_relative` makes one, which a codebase rule
    enforces; a value that is not a relative path is rejected when logged all the same.
    """

    __slots__ = ()


type LogValue = bool | int | float | StrEnum | WorkspacePath


class _Rejected:
    """Marks a value that may not be logged."""


_REJECTED = _Rejected()


@dataclass(frozen=True, slots=True)
class _EventRecord:
    """What `log_event` attaches to a record. Only records carrying one are events."""

    event: str
    message: str
    fields: dict[str, object]


def log_event(event: LogEvent, *, error: BaseException | None = None, **fields: LogValue) -> None:
    """Log `event` with allow-listed fields and, optionally, the type and code of `error`."""
    spec = _EVENTS[event]
    logger = logging.getLogger(f"{_ROOT_LOGGER}.{spec.component}")
    if not logger.isEnabledFor(spec.level):
        return
    accepted: dict[str, object] = {}
    rejected: list[str] = []
    for name, value in fields.items():
        safe = _safe_value(value) if name in FIELDS else _REJECTED
        if isinstance(safe, _Rejected):
            rejected.append(_identifier(name))
        else:
            accepted[name] = safe
    if error is not None:
        accepted["exception_type"] = _identifier(type(error).__name__)
        if isinstance(error, SphereLoomError):
            accepted["error_code"] = error.code.value
    if rejected:
        accepted["rejected_fields"] = sorted(rejected)
    payload = _EventRecord(event.value, spec.message, accepted)
    logger.log(spec.level, spec.message, extra={_PAYLOAD_ATTRIBUTE: payload})


def _safe_value(value: object) -> object:
    # Order matters: `bool` is an `int`, and `StrEnum` and `WorkspacePath` are `str`.
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, StrEnum):
        # An enumeration proves nothing by itself: one can be built at run time from any
        # text. Only SphereLoom's own enumerations, with identifier-shaped values, pass.
        text = str(value.value)
        own = type(value).__module__.startswith(f"{_ROOT_LOGGER}.")
        return text if own and _IDENTIFIER.fullmatch(text) else _REJECTED
    if isinstance(value, WorkspacePath):
        return str(value) if _is_relative_path(value) else _REJECTED
    if type(value) is int and -_INT64_LIMIT <= value < _INT64_LIMIT:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    return _REJECTED


def _is_relative_path(text: str) -> bool:
    parts = text.replace("\\", "/").split("/")
    return bool(text) and not text.startswith(("/", "\\")) and ":" not in text and ".." not in parts


def _identifier(text: str) -> str:
    return text if _IDENTIFIER.fullmatch(text) else _UNRECOGNISED


class JsonFormatter(logging.Formatter):
    """Emits one JSON object per line, built only from allow-listed data."""

    def __init__(self, *, redaction: bool) -> None:
        super().__init__()
        self._redaction = redaction

    def format(self, record: logging.LogRecord) -> str:
        try:
            return self._format(record)
        except Exception:
            # A formatter that raises makes the handler echo the record, so any failure is
            # replaced by a fixed line.
            return json.dumps({"level": "ERROR", "event": "log_record_unformattable"})

    def _format(self, record: logging.LogRecord) -> str:
        output: dict[str, object] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": _identifier(record.levelname),
            "logger": _identifier(record.name),
        }
        payload = getattr(record, _PAYLOAD_ATTRIBUTE, None)
        if record.name.startswith(f"{_ROOT_LOGGER}.") and isinstance(payload, _EventRecord):
            output.update({"event": payload.event, "message": payload.message, **payload.fields})
        else:
            output["event"] = "third_party_record"
            if record.exc_info and record.exc_info[0] is not None:
                output["exception_type"] = _identifier(record.exc_info[0].__name__)
        if self._redaction:
            output = {
                key: redact(value) if isinstance(value, str) else value
                for key, value in output.items()
            }
        return json.dumps(output, allow_nan=False)


class _StderrHandler(logging.StreamHandler[TextIO]):
    """A stream handler that never echoes a record it failed to write."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - stdlib name
        # The default prints the record's message and arguments to stderr, which is exactly
        # the untrusted text this module exists to keep out of the output.
        return


class _ThirdPartyLevelFilter(logging.Filter):
    """Drops records below `WARNING` from loggers that are not SphereLoom's.

    The root logger's level is not enough: a library that sets its own logger to `DEBUG`
    emits below it, because propagation does not consult the levels of ancestors.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(f"{_ROOT_LOGGER}.") or record.levelno >= logging.WARNING


def configure_logging(*, level: str = "INFO", redaction: bool = True) -> None:
    """Install the stderr JSON handler on the root logger. Safe to call more than once.

    Any handler already on the root logger is removed, including the one the MCP SDK adds
    with `logging.basicConfig`; while one of ours is present, that call does nothing.
    Third-party records are emitted from `WARNING` up, SphereLoom's from `level` up.
    """
    handler = _StderrHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(redaction=redaction))
    handler.addFilter(_ThirdPartyLevelFilter())

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.WARNING)

    ours = logging.getLogger(_ROOT_LOGGER)
    ours.handlers.clear()
    ours.setLevel(level)
    ours.propagate = True


def workspace_relative(path: Path, workspace: Path) -> WorkspacePath | None:
    """Return `path` relative to `workspace`, or `None` if it lies outside it.

    Results and logs expose workspace-relative paths only, so absolute filesystem layout,
    which usually embeds a username, never leaves the process.
    """
    try:
        relative = path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return None
    return WorkspacePath(relative.as_posix())


#: Patterns removed from logged string values when redaction is enabled. Defence in depth
#: only; see the module docstring for what they cannot catch.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]{8,}=*"), r"\1" + REDACTED),
    (
        re.compile(r"(?i)\b(token|secret|password|api[_-]?key)(\"?\s*[:=]\s*\"?)[^\s,\"'}]+"),
        r"\1\2" + REDACTED,
    ),
    (re.compile(r"(?i)\b(ssid)(\"?\s*[:=]\s*\"?)[^\s,\"'}]+"), r"\1\2" + REDACTED),
    (re.compile(r"(\?)[^\s\"']{1,512}"), r"\1" + REDACTED),
    (re.compile(r"(?i)\b[a-z]:\\[^\s\"',]{1,512}"), REDACTED),
    (re.compile(r"(?<![\w.])/(?:home|Users|root|var|tmp)/[^\s\"',]{1,512}"), REDACTED),
)


def redact(text: str) -> str:
    """Remove credentials, network names, query strings and absolute paths from `text`."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text
