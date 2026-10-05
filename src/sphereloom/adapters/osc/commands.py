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
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sphereloom.adapters.osc.client import OscHttpClient
from sphereloom.adapters.osc.errors import BACKEND, map_vendor_error
from sphereloom.domain.clock import Clock, SystemClock
from sphereloom.domain.errors import OperationTimeoutError
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
        clock: Clock | None = None,
        default_deadline: float = 30.0,
    ) -> None:
        self._client = client
        self._clock = clock or SystemClock()
        self._default_deadline = default_deadline

    async def run(
        self,
        name: str,
        parameters: Mapping[str, Any] | None = None,
        *,
        retryable: bool = False,
        deadline_seconds: float | None = None,
    ) -> CommandResult:
        """Execute a command and return only once it has actually finished.

        Raises:
            SphereLoomError: for any vendor error, mapped to the taxonomy.
            OperationTimeoutError: if the command does not reach a terminal state in time.
        """
        payload = await self._client.execute(name, parameters, retryable=retryable)
        state = payload.get("state")

        if state == STATE_ERROR or "error" in payload:
            raise map_vendor_error(payload, command=name)

        if state == STATE_IN_PROGRESS:
            command_id = payload.get("id")
            if not command_id:
                # Without an identifier there is nothing to poll. Reporting this is better
                # than returning an acknowledgement as though it were a result.
                raise map_vendor_error(payload, command=name)
            return await self._await_completion(
                name,
                str(command_id),
                deadline_seconds=deadline_seconds or self._default_deadline,
            )

        # `done`, or a command that simply has no asynchronous phase.
        return CommandResult(name=name, results=_results(payload))

    async def _await_completion(
        self, name: str, command_id: str, *, deadline_seconds: float
    ) -> CommandResult:
        started = self._now()
        interval = POLL_INITIAL_SECONDS

        while True:
            elapsed = self._now() - started
            if elapsed >= deadline_seconds:
                raise OperationTimeoutError(
                    f"{name} did not report completion within {deadline_seconds:.0f} seconds. "
                    f"The camera may still be writing the file. Its command id is "
                    f"{command_id}, which you can poll directly if needed.",
                    backend=BACKEND,
                    details={"command": name, "command_id": command_id},
                )

            await asyncio.sleep(min(interval, max(deadline_seconds - elapsed, 0.0)))
            interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_SECONDS)

            payload = await self._client.command_status(command_id)
            state = payload.get("state")

            if state == STATE_ERROR or "error" in payload:
                raise map_vendor_error(payload, command=name)

            if state == STATE_DONE:
                return CommandResult(name=name, results=_results(payload), command_id=command_id)

            logger.debug(
                "command still running",
                extra={
                    "context": {
                        "command": name,
                        "job_progress": _completion(payload),
                    }
                },
            )

    def _now(self) -> float:
        return self._clock.now().timestamp()


def _results(payload: Mapping[str, Any]) -> dict[str, Any]:
    results = payload.get("results")
    return dict(results) if isinstance(results, dict) else {}


def _completion(payload: Mapping[str, Any]) -> float | None:
    progress = payload.get("progress")
    if isinstance(progress, Mapping):
        completion = progress.get("completion")
        if isinstance(completion, int | float) and not isinstance(completion, bool):
            return float(completion)
    return None
