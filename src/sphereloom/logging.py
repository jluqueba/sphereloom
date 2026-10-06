"""Structured logging with redaction.

Two hard rules, both of them load-bearing:

1. Logs go to stderr only. The stdio transport owns stdout for the MCP protocol itself, so
   a stray write there corrupts the session.
2. Sensitive material is removed before a record is emitted. Users point this server at
   their own cameras and their own footage; neither belongs in a log file or a bug report.
"""

from __future__ import annotations

import builtins
import collections
import itertools
import json
import logging
import re
import reprlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from sphereloom.domain.payloads import (
    ELLIPSIS,
    MALFORMED,
    MAX_INT_BITS,
    bounded_payload,
    cut_text,
)

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

#: Whitespace as it appears in a log line: real whitespace, or any escape `repr` writes in
#: its place. A container argument is rendered with `repr` before redaction runs, so a
#: separator between `Bearer` and its token arrives as `\n`, `\x0b`, `\u2028` and so on; a
#: pattern that required real whitespace let the token through. `repr` never escapes
#: printable ASCII, so treating every escape as a separator only covers what it hid.
_SEP = r"(?:\s|\\[ntr]|\\x[0-9a-fA-F]{2}|\\u[0-9a-fA-F]{4}|\\U[0-9a-fA-F]{8})"

#: Structured keys whose values are secrets whatever they look like. The text patterns
#: below find a secret by its label, and in a structured record the label is the key, not
#: part of the value, so `{"token": "abc"}` would otherwise be logged as it is.
_SENSITIVE_KEY = re.compile(
    r"(?i)(token|secret|passw(or)?d|api[_-]?key|ssid|authori[sz]ation|bearer|credential)"
)

#: Patterns stripped from every log record when redaction is enabled.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Bearer tokens and confirmation tokens.
    (re.compile(rf"(?i)\b(bearer{_SEP}+)[A-Za-z0-9._~+/-]{{8,}}=*"), r"\1" + REDACTED),
    (
        re.compile(
            rf"(?i)\b(token|secret|password|api[_-]?key)(\"?{_SEP}*[:=]{_SEP}*\"?)[^\s,\"'}}]+"
        ),
        r"\1\2" + REDACTED,
    ),
    # Wi-Fi network names, which identify a person's home or workplace.
    (re.compile(rf"(?i)\b(ssid)(\"?{_SEP}*[:=]{_SEP}*\"?)[^\s,\"'}}]+"), r"\1\2" + REDACTED),
    # Query strings can carry credentials and file identifiers. Each run is consumed whole:
    # a cap on its length left everything after the cap in the log. Every record is
    # bounded before it is redacted, and a single character class cannot backtrack, so
    # matching a whole run costs no more than reading it.
    (re.compile(r"(\?)[^\s\"']+"), r"\1" + REDACTED),
    # Absolute paths leak usernames and library layout.
    (re.compile(r"(?i)\b[a-z]:\\[^\s\"',]+"), REDACTED),
    (re.compile(r"(?<![\w.])/(?:home|Users|root|var|tmp)/[^\s\"',]+"), REDACTED),
)


def redact(text: str) -> str:
    """Remove secrets, network names, query strings and absolute paths from a string."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class _BoundedRepr(reprlib.Repr):
    """Renders a container argument with limits on breadth, depth and string length.

    Three departures from `reprlib`'s defaults, each closing a way a bound could be
    defeated or a secret could survive redaction:

    * Dispatch is by `isinstance`, not by type name, so a `str` subclass is still cut as a
      string and an `int` subclass is still checked before rendering.
    * Strings and anything rendered through `repr_instance` are cut from the end with
      `cut_text`. `reprlib` keeps the head and the tail and drops the middle, and the kept
      tail can begin part-way through a token, where no pattern recognises it.
    * Dictionaries and sets are read in their own order, a bounded number of items at a
      time. `reprlib` sorts the whole container first, which costs the full size of the
      input before any limit applies, and reorders what is logged.

    Bytes are summarised by length rather than rendered: no device payload is binary, and
    there is no boundary to cut a byte string on safely. Per-level limits multiply, so they
    are kept small: three levels of ten items is at most about a thousand nodes.
    """

    def __init__(self) -> None:
        super().__init__()
        self.maxlevel = 3
        self.maxlist = self.maxtuple = self.maxdict = 10
        self.maxset = self.maxfrozenset = self.maxdeque = 10
        self.maxstring = MAX_MESSAGE_LENGTH
        self.maxother = MAX_MESSAGE_LENGTH

    def repr1(self, x: Any, level: int) -> str:
        # Every container and scalar `_bound_argument` accepts is routed here by
        # `isinstance`, so a subclass -- a namedtuple, a `list` subclass, a `str` subclass --
        # is bounded like its base. Left to `reprlib`'s dispatch by type name, a subclass
        # falls through to `repr_instance`, which renders the whole object before cutting.
        if isinstance(x, str):
            return self.repr_str(x, level)
        if isinstance(x, bytes | bytearray):
            return f"<{len(x)} bytes>"
        if isinstance(x, int) and not isinstance(x, bool):
            return self.repr_int(x, level)
        if isinstance(x, dict):
            return self.repr_dict(x, level)
        if isinstance(x, set | frozenset):
            return self._repr_unordered(x, level)
        if isinstance(x, list):
            return self.repr_list(x, level)
        if isinstance(x, tuple):
            return self.repr_tuple(x, level)
        if isinstance(x, collections.deque):
            return self.repr_deque(x, level)
        return super().repr1(x, level)

    def repr_str(self, x: str, level: int) -> str:
        return builtins.repr(cut_text(x, self.maxstring))

    def repr_int(self, x: int, level: int) -> str:
        return builtins.repr(int(x)) if x.bit_length() <= MAX_INT_BITS else MALFORMED

    def repr_instance(self, x: Any, level: int) -> str:
        try:
            return cut_text(builtins.repr(x), self.maxother)
        except Exception:  # an object's own `__repr__` is arbitrary code
            return f"<{type(x).__name__} instance>"

    def repr_dict(self, x: Any, level: int) -> str:
        if not x:
            return "{}"
        if level <= 0:
            return "{" + self.fillvalue + "}"
        pieces = [
            f"{self.repr1(key, level - 1)}: {self.repr1(value, level - 1)}"
            for key, value in itertools.islice(x.items(), self.maxdict)
        ]
        if len(x) > self.maxdict:
            pieces.append(self.fillvalue)
        return "{" + ", ".join(pieces) + "}"

    def _repr_unordered(self, x: set[Any] | frozenset[Any], level: int) -> str:
        if not x:
            return f"{type(x).__name__}()"
        if level <= 0:
            body = "{" + self.fillvalue + "}"
        else:
            pieces = [self.repr1(item, level - 1) for item in itertools.islice(x, self.maxset)]
            if len(x) > self.maxset:
                pieces.append(self.fillvalue)
            body = "{" + ", ".join(pieces) + "}"
        return body if isinstance(x, set) else f"frozenset({body})"


class _Rendered:
    """A pre-rendered container that `%s` and `%r` both print as it was rendered.

    For lists, tuples and dictionaries `str()` and `repr()` produce the same text, so
    returning that text from both keeps either conversion looking as it always has.
    """

    __slots__ = ("_text",)

    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text

    __repr__ = __str__


_BOUNDED_REPR = _BoundedRepr()
_TRACEBACK_FORMATTER = logging.Formatter()


def _bound_argument(value: Any) -> Any:
    """Bound one logging argument so that rendering it costs no more than the bound.

    Strings are cut, which costs only the bound and never runs the value's own `__str__`.
    Containers are rendered once with explicit limits, so a `Path` inside a list still
    prints as a `Path`. Numbers are left alone so `%d` and `%.2f` still format, and other
    objects are left for `%s` to render as they always have: they come from this codebase,
    not from a device.
    """
    if isinstance(value, str):
        return cut_text(value, MAX_MESSAGE_LENGTH)
    if isinstance(value, list | tuple | dict | set | frozenset):
        try:
            return _Rendered(cut_text(_BOUNDED_REPR.repr(value), MAX_MESSAGE_LENGTH))
        except Exception:  # a member's own `__repr__` is arbitrary code
            return MALFORMED
    return value


def _bound_mapping(args: Mapping[Any, Any]) -> dict[Any, Any]:
    """Bound a mapping passed as the logging arguments, reading no more than the bound.

    A single dictionary argument becomes `record.args` itself, which is how `%(name)s`
    works. It is therefore bounded as a mapping, not as a container: only the first
    `MAX_CONTEXT_ITEMS` entries are read, and keys are cut as well as values, since an
    enormous key is as costly to render as an enormous value. A key cut short no longer
    matches its `%(name)s` placeholder, and the message falls back to the placeholder text.
    """
    entries = list(itertools.islice(args.items(), MAX_CONTEXT_ITEMS + 1))
    bounded = {
        (cut_text(key, MAX_CONTEXT_STRING) if isinstance(key, str) else key): _bound_argument(value)
        for key, value in entries[:MAX_CONTEXT_ITEMS]
    }
    if len(entries) > MAX_CONTEXT_ITEMS:
        bounded[ELLIPSIS] = ELLIPSIS
    return bounded


class BoundingFilter(logging.Filter):
    """Bounds every part of a record before anything else reads it.

    It must run first. `record.getMessage()` interpolates the full arguments, and the
    redaction filter applies every pattern to every string it is given, so a bound applied
    afterwards limits the output but not the work spent producing it. Here the arguments
    are bounded, the message is rendered once, and the result replaces the original, so
    every later stage sees a bounded record. Every cut goes through `cut_text`, so bounding
    first never leaves redaction a fragment it cannot recognise.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, Mapping):
            record.args = _bound_mapping(args)
        elif args:
            record.args = tuple(_bound_argument(value) for value in args)

        try:
            message = record.getMessage()
        except Exception:  # a hostile argument must not lose the record
            message = "<message could not be rendered>"
        record.msg = cut_text(message, MAX_MESSAGE_LENGTH)
        record.args = ()

        context = getattr(record, "context", None)
        if isinstance(context, dict):
            record.context = bounded_payload(
                context,
                max_string=MAX_CONTEXT_STRING,
                max_items=MAX_CONTEXT_ITEMS,
                max_depth=MAX_CONTEXT_DEPTH,
                max_nodes=MAX_CONTEXT_NODES,
            )

        # The traceback is rendered here, once, so redaction can see it. Frames name source
        # files by absolute path, which embeds the user's home directory.
        if record.exc_info and not record.exc_text:
            try:
                rendered = _TRACEBACK_FORMATTER.formatException(record.exc_info)
            except Exception:  # an exception's own `__str__` is arbitrary code
                rendered = "<traceback could not be rendered>"
            record.exc_text = cut_text(rendered, MAX_TRACEBACK_LENGTH)
        return True


def _redact_value(value: Any) -> Any:
    """Redact every string in an already-bounded structure, however deeply it is nested.

    Only top-level strings used to be redacted, so a path or a token one level down in a
    log record's context was written out as it was. The structure has been bounded by
    `BoundingFilter`, so walking all of it costs no more than the bound.
    """
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            (redact(key) if isinstance(key, str) else key): (
                REDACTED if isinstance(key, str) and _is_sensitive_key(key) else _redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _is_sensitive_key(key: str) -> bool:
    """Whether a structured key names a secret, or was cut short before it could be read.

    Bounding runs before redaction, so a long key is truncated first. A key that ended in
    `token` would lose the very word that marks it, and its value would be written out. A
    truncated key therefore counts as sensitive: its value is withheld rather than guessed.
    """
    return _SENSITIVE_KEY.search(key) is not None or key.endswith(ELLIPSIS)


class RedactionFilter(logging.Filter):
    """Applies `redact` to the message, to structured extras and to the traceback."""

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
            record.context = _redact_value(context)
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
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
            "message": cut_text(message, MAX_MESSAGE_LENGTH),
        }
        payload: dict[str, Any] = dict(base)
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            # `BoundingFilter` has normally bounded this already, before redaction ran. It is
            # bounded again here so the formatter is safe on its own: `default=str` succeeds
            # for an object whose `__str__` returns megabytes, so without a bound the
            # fallback below never runs and the line is unbounded. On an already-bounded
            # context this costs no more than the bound itself.
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
            # `exc_text` is the traceback `BoundingFilter` rendered and redaction cleaned;
            # it is formatted here only when the formatter is used without the filters.
            traceback_text = (
                record.exc_text
                if record.exc_text is not None
                else self.formatException(record.exc_info)
            )
            payload["exception"] = cut_text(traceback_text, MAX_TRACEBACK_LENGTH)

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
    # Order matters: filters run in the order they are added, and redaction must only ever
    # see a record that has already been bounded.
    handler.addFilter(BoundingFilter())
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
