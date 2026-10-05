"""Running commands that do not finish when the request returns.

Captures are asynchronous. `camera.takePicture` returns an acknowledgement with a command
identifier, and the photograph exists only once a later poll of `/osc/commands/status`
reports `done`. Getting this wrong is the difference between "the agent downloaded the
photo" and "the agent downloaded the previous photo".

This module owns that loop so no caller has to reimplement it, and so the polling cadence,
the deadline and the error mapping are decided once.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sphereloom.adapters.osc.client import RETRY_SAFE_COMMANDS, OscHttpClient
from sphereloom.adapters.osc.errors import BACKEND, map_vendor_error
from sphereloom.domain.clock import Monotonic, SystemMonotonic
from sphereloom.domain.errors import (
    InternalError,
    InvalidArgumentError,
    OperationTimeoutError,
    SphereLoomError,
)
from sphereloom.logging import get_logger

logger = get_logger("adapters.osc.commands")

#: Poll quickly at first, since many commands finish almost immediately, then back off so a
#: slow capture does not generate hundreds of requests over a weak access point.
POLL_INITIAL_SECONDS = 0.25
POLL_MAX_SECONDS = 1.0
POLL_BACKOFF_FACTOR = 1.5

STATE_DONE = "done"
STATE_ERROR = "error"
STATE_IN_PROGRESS = "inProgress"

#: Vendor identifiers are short strings such as "001996". A bound keeps an untrusted value
#: from being carried into every poll request, message and error detail unchecked.
COMMAND_ID_MAX_LENGTH = 128


@dataclass(frozen=True, slots=True)
class CommandResult:
    """The terminal outcome of a command."""

    name: str
    results: dict[str, Any]
    #: Present when the command completed asynchronously. Surfaced so a timeout message can
    #: name the identifier a maintainer would need to ask the camera about it.
    command_id: str | None = None


class CommandRunner:
    """Executes OSC commands and waits for the ones that finish later."""

    def __init__(
        self,
        client: OscHttpClient,
        *,
        monotonic: Monotonic | None = None,
        default_deadline: float = 30.0,
    ) -> None:
        self._client = client
        self._monotonic = monotonic or SystemMonotonic()
        self._default_deadline = _validated_deadline(default_deadline)

    async def run(
        self,
        name: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        deadline_seconds: float | None = None,
    ) -> CommandResult:
        """Execute a command and return only once it has actually finished.

        Whether the command is retried is decided by the client, from the command name, so
        a caller cannot accidentally make a capture repeatable.

        Raises:
            SphereLoomError: for any vendor error, mapped to the taxonomy.
            OperationTimeoutError: if the command does not reach a terminal state in time.
        """
        # `is None` rather than a falsy check: a caller passing 0 means "do not wait", and
        # silently turning that into the default deadline would change what they asked for.
        deadline = (
            self._default_deadline
            if deadline_seconds is None
            else _validated_deadline(deadline_seconds)
        )

        payload = await self._client.execute(name, parameters)
        state = payload.get("state")

        if state == STATE_ERROR or "error" in payload:
            raise map_vendor_error(payload, command=name)

        if state == STATE_IN_PROGRESS:
            command_id = _validated_command_id(payload.get("id"))
            if command_id is None:
                # Without a usable identifier there is nothing to poll. Reporting this is
                # better than returning an acknowledgement as though it were a result.
                raise map_vendor_error(payload, command=name)
            return await self._await_completion(name, command_id, deadline_seconds=deadline)

        if state == STATE_DONE:
            return CommandResult(name=name, results=_results(payload, command=name))

        # Anything else is a response we do not understand. Treating an unrecognised state
        # as success would turn a malformed reply into an empty successful result, which is
        # the most misleading outcome available.
        raise map_vendor_error(payload, command=name)

    async def _await_completion(
        self, name: str, command_id: str, *, deadline_seconds: float
    ) -> CommandResult:
        started = self._monotonic.elapsed()
        interval = POLL_INITIAL_SECONDS

        def remaining() -> float:
            return deadline_seconds - (self._monotonic.elapsed() - started)

        def expired() -> OperationTimeoutError:
            budget = _format_seconds(deadline_seconds)
            return OperationTimeoutError(
                f"{name} did not report completion within {budget} seconds. "
                f"The camera may still be writing the file. Its command id is "
                f"{command_id}, which you can poll directly if needed.",
                backend=BACKEND,
                details={"command": name, "command_id": command_id},
                # The camera accepted this command and may still be running it. Advertising
                # it as retryable would invite a caller to issue a second capture or delete
                # on top of one already in progress. The command id is the way forward, not
                # a repeat of the command.
                retryable=False,
            )

        while True:
            if remaining() <= 0:
                raise expired()

            await asyncio.sleep(min(interval, max(remaining(), 0.0)))
            interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_SECONDS)

            budget = remaining()
            if budget <= 0:
                raise expired()

            try:
                # Bounded by what is left of the deadline. Without this the poll could
                # consume its own full HTTP timeout on top of an already exhausted budget.
                payload = await asyncio.wait_for(
                    self._client.command_status(command_id), timeout=budget
                )
            except TimeoutError as exc:
                raise expired() from exc
            except SphereLoomError as exc:
                # A polling failure carries no command name, so the client judges it by the
                # status endpoint, which is harmless to repeat. But the command being polled
                # was already accepted and may still be running, so letting that verdict
                # through would invite a duplicate capture or delete. The outer operation's
                # safety is what matters here, not the poll's.
                if not _is_safe_to_repeat(name):
                    exc.retryable = False
                raise

            # Rechecked after the response: a poll that answered `done` just past the
            # deadline would otherwise be accepted, making the deadline advisory.
            if remaining() <= 0:
                raise expired()

            state = payload.get("state")

            if state == STATE_ERROR or "error" in payload:
                raise map_vendor_error(payload, command=name)

            if state == STATE_DONE:
                return CommandResult(
                    name=name, results=_results(payload, command=name), command_id=command_id
                )

            if state != STATE_IN_PROGRESS:
                # An unrecognised state during polling is as untrustworthy as one in the
                # initial response, and looping on it forever would be worse still.
                raise map_vendor_error(payload, command=name)

            logger.debug(
                "command still running",
                extra={
                    "context": {
                        "command": name,
                        "job_progress": _completion(payload),
                    }
                },
            )


def _is_safe_to_repeat(command: str) -> bool:
    """Whether repeating this command is free of side effects."""
    return command in RETRY_SAFE_COMMANDS


def _validated_command_id(value: Any) -> str | None:
    """Accept a command identifier only if it is a usable, bounded string.

    The identifier comes from a device response and is copied into poll requests, timeout
    messages and error details. A list or object would be stringified into its repr, and an
    arbitrarily long string would be carried into every message unbounded.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > COMMAND_ID_MAX_LENGTH:
        return None
    return candidate


def _format_seconds(value: float) -> str:
    """Render a duration without rounding a fractional budget away.

    `:.0f` turns a 0.5-second deadline into "0 seconds", which reads as a bug report rather
    than an explanation of what was configured.
    """
    if value == int(value):
        return str(int(value))
    return f"{value:g}"


def _validated_deadline(seconds: float) -> float:
    """Reject a deadline that cannot mean what the caller intended.

    Non-finite values are refused explicitly: NaN defeats every elapsed-time comparison and
    would reach `asyncio.sleep`, while infinity silently removes the deadline this class
    exists to enforce.
    """
    if not math.isfinite(seconds):
        message = f"A command deadline must be a finite number of seconds, got {seconds!r}."
        raise InvalidArgumentError(message, backend=BACKEND)
    if seconds < 0:
        message = f"A command deadline cannot be negative, got {seconds!r}."
        raise InvalidArgumentError(message, backend=BACKEND)
    return seconds


def _results(payload: Mapping[str, Any], *, command: str) -> dict[str, Any]:
    """Extract the result object from a terminal response.

    A missing `results` is legitimate: several commands report success with no payload. A
    `results` that is present but is not an object is not, and silently turning it into an
    empty dictionary would report success while discarding whatever the camera actually
    said -- the same failure mode as treating an unrecognised state as done.
    """
    results = payload.get("results")
    if results is None:
        return {}
    if not isinstance(results, Mapping):
        message = (
            f"{command} returned a 'results' field of type "
            f"{type(results).__name__} instead of an object."
        )
        raise InternalError(message, backend=BACKEND)
    return dict(results)


def _completion(payload: Mapping[str, Any]) -> float | None:
    progress = payload.get("progress")
    if not isinstance(progress, Mapping):
        return None

    completion = progress.get("completion")
    if not isinstance(completion, int | float) or isinstance(completion, bool):
        return None

    # The range check comes first and does the work of a finiteness check without the
    # hazard: `math.isfinite` converts its argument to a float, which raises OverflowError
    # for a large but perfectly valid JSON integer. Comparing against the documented 0..1
    # range short-circuits that, and rejects NaN too, since every comparison with NaN is
    # false. Dropping the value matters because it reaches a log record, and `json.dumps`
    # writes a non-finite float bare, which no JSON parser accepts.
    if not 0.0 <= completion <= 1.0:
        return None
    return float(completion)
