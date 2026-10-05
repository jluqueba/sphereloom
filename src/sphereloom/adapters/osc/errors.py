"""Vendor error payloads mapped onto the SphereLoom taxonomy.

The camera reports failures with its own codes and prose. Translating them here, once,
means the tool layer only ever sees taxonomy errors, and an agent can branch on a stable
`code` instead of pattern-matching English that changes between firmware revisions.

The original payload is preserved under `details.vendor` so a maintainer debugging a real
camera is never left guessing what the device actually said.
"""

from __future__ import annotations

from typing import Any

from sphereloom.domain.errors import (
    CameraBusyError,
    InternalError,
    InvalidArgumentError,
    NotFoundError,
    SphereLoomError,
    StorageFullError,
)

BACKEND = "osc"

#: Documented vendor error codes, mapped to the taxonomy class that fits their meaning.
#: Anything absent here is deliberately *not* guessed at: it becomes `internal`, which is
#: honest about the fact that SphereLoom did not recognise the condition.
_VENDOR_CODES: dict[str, type[SphereLoomError]] = {
    "disabledCommand": CameraBusyError,
    "invalidParameterName": InvalidArgumentError,
    "invalidParameterValue": InvalidArgumentError,
    "missingParameter": InvalidArgumentError,
    "noFreeSpace": StorageFullError,
    "cardNotFound": StorageFullError,
    "fileNotFound": NotFoundError,
    "unexpected": InternalError,
    "powerOffSequenceRunning": CameraBusyError,
    "serviceUnavailable": CameraBusyError,
}

#: Vendor codes that mean "the owner must do something in the vendor's phone app". Nothing
#: SphereLoom can do recovers from these, so the message has to reach the user intact.
_OWNER_ACTION_CODES = {"unactivated", "cameraNotActivated"}

#: How much of an unrecognised payload to preserve. Generous enough to identify a problem,
#: bounded so an untrusted camera response cannot flood a log or an error envelope.
_EXCERPT_LIMIT = 200
_MAX_ITEMS = 20
_MAX_DEPTH = 4
#: Total nodes the excerpt may contain. Per-level limits alone are not a bound: twenty
#: items at four levels deep is a hundred and sixty thousand nodes, which defeats the point.
_MAX_NODES = 200

#: Vendor code and message are attacker-adjacent input: they come from a device we do not
#: control, and they are rendered into the public error. Both are bounded before use.
_CODE_LIMIT = 64
_MESSAGE_LIMIT = 300


def _bounded(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


def map_vendor_error(
    payload: Any,
    *,
    command: str | None = None,
) -> SphereLoomError:
    """Translate a vendor error response into a taxonomy error.

    Accepts the whole response body, since the vendor nests the error differently depending
    on whether a command was accepted and then failed, or rejected outright.
    """
    error = _extract_error(payload)
    if error is None:
        return InternalError(
            "The camera returned a response SphereLoom could not interpret. This usually "
            "means an unexpected firmware revision; please report it with the excerpt below.",
            backend=BACKEND,
            details=_details(payload, command),
        )

    code = _bounded(str(error.get("code", "")), _CODE_LIMIT)
    message = _bounded(str(error.get("message", "")), _MESSAGE_LIMIT)

    if code in _OWNER_ACTION_CODES:
        return InvalidArgumentError(
            "The camera reports that it is not activated. Activate it in the vendor's "
            "official mobile app once; SphereLoom cannot do this for you.",
            backend=BACKEND,
            reason=message or None,
            details=_details(payload, command),
        )

    error_class = _VENDOR_CODES.get(code)
    if error_class is None:
        return InternalError(
            f"The camera rejected the request with an unrecognised error code {code!r}. "
            "The original response is preserved for diagnosis.",
            backend=BACKEND,
            reason=message or None,
            details=_details(payload, command),
        )

    return error_class(
        _human_message(code, message, command),
        backend=BACKEND,
        reason=message or None,
        details=_details(payload, command),
    )


def _extract_error(payload: Any) -> dict[str, Any] | None:
    """Find the error object, wherever the vendor put it this time."""
    if not isinstance(payload, dict):
        return None

    error = payload.get("error")
    if isinstance(error, dict):
        return error

    # Some responses report the failure through `state` with the detail alongside.
    if payload.get("state") == "error":
        nested = payload.get("results")
        if isinstance(nested, dict) and isinstance(nested.get("error"), dict):
            return dict(nested["error"])

    return None


def _human_message(code: str, vendor_message: str, command: str | None) -> str:
    """Write the message a user and a model both need: what failed, and what to do."""
    what = f"The camera rejected {command}" if command else "The camera rejected the request"

    guidance = {
        "disabledCommand": (
            "This usually means the camera is busy, or is in the wrong capture mode. "
            "Check the status, switch mode if needed, and try again."
        ),
        "noFreeSpace": "Free space on the camera's card, then retry.",
        "cardNotFound": "Check that a memory card is inserted and readable.",
        "fileNotFound": "The file no longer exists on the camera. List the gallery again.",
        "invalidParameterValue": "One of the supplied values is not accepted by this camera.",
        "invalidParameterName": "One of the supplied option names is not known to this camera.",
        "missingParameter": "The command needs a parameter that was not supplied.",
        "powerOffSequenceRunning": "The camera is shutting down. Power it on and retry.",
        "serviceUnavailable": "The camera is temporarily unable to serve requests. Retry shortly.",
    }.get(code, "")

    detail = f" The camera said: {vendor_message}" if vendor_message else ""
    suffix = f" {guidance}" if guidance else ""
    return f"{what} with {code!r}.{detail}{suffix}"


def _details(payload: Any, command: str | None) -> dict[str, Any]:
    details: dict[str, Any] = {"vendor": _excerpt(payload)}
    if command:
        details["command"] = command
    return details


class _Budget:
    """Counts nodes consumed while building an excerpt."""

    def __init__(self) -> None:
        self.remaining = _MAX_NODES

    def take(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


def _excerpt(payload: Any) -> Any:
    """Preserve the vendor payload within an overall budget.

    Strings, collection sizes, nesting depth and total node count are all bounded.
    Per-level limits alone are not a bound: twenty items at four levels deep is a hundred
    and sixty thousand nodes, which would flood exactly what this is meant to protect.
    """
    return _excerpt_bounded(payload, depth=0, budget=_Budget())


def _excerpt_bounded(payload: Any, *, depth: int, budget: _Budget) -> Any:
    # Budget is consumed before the depth check, not after. Short-circuiting on depth first
    # lets every node beyond the depth limit render for free, so a broad tree produces
    # thousands of placeholders while the budget sits untouched.
    if not budget.take() or depth >= _MAX_DEPTH:
        return "…"

    if isinstance(payload, dict):
        excerpt: dict[str, Any] = {}
        for index, (key, value) in enumerate(payload.items()):
            if index >= _MAX_ITEMS or budget.remaining <= 0:
                excerpt["…"] = f"{len(payload) - index} more keys"
                break
            excerpt[_bounded(str(key), _EXCERPT_LIMIT)] = _excerpt_bounded(
                value, depth=depth + 1, budget=budget
            )
        return excerpt

    if isinstance(payload, list):
        items: list[Any] = []
        for index, value in enumerate(payload):
            if index >= _MAX_ITEMS or budget.remaining <= 0:
                items.append("…")
                break
            items.append(_excerpt_bounded(value, depth=depth + 1, budget=budget))
        return items

    if isinstance(payload, str):
        return _bounded(payload, _EXCERPT_LIMIT)

    return payload
