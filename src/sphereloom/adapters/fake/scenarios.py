"""Failure injection for the fake camera.

Real cameras fail in specific, recurring ways: the battery dies mid-recording, the card
fills up, the user walks out of Wi-Fi range, firmware returns something that is not quite
JSON. Those paths are the ones that matter to an agent, and they are exactly the ones that
never happen while you are sitting at a desk testing the happy path.

Each scenario here reproduces one of them deterministically, so the behaviour can be
asserted rather than hoped for.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Scenario:
    """How the fake camera should misbehave.

    The default is a camera that works. Every field turns on one specific failure, so a test
    can state precisely which condition it is exercising.
    """

    #: Seconds of artificial delay before each response. Exercises timeout handling.
    latency_seconds: float = 0.0

    #: Reject commands with the vendor's "another capture is running" error.
    busy: bool = False

    #: Report no space left on the card.
    storage_full: bool = False

    #: Return a body that is not valid JSON, as some firmware revisions do under load.
    malformed_json: bool = False

    #: Return HTTP 500 for command execution.
    server_error: bool = False

    #: Report the camera as not activated, which the vendor requires the owner to fix in
    #: their phone app. Nothing SphereLoom does can recover from it, so the message matters.
    unactivated: bool = False

    #: Answer protocol requests with a body larger than any documented command produces.
    #: The camera is unauthenticated, so a hostile or broken responder on its network must
    #: not be able to exhaust the client's memory.
    oversized_responses: bool = False

    #: Answer protocol requests with a redirect. Clients do not follow redirects, so the
    #: body is not a result; a caller that accepted it would treat a redirect page as one.
    redirect_responses: bool = False

    #: Send fewer bytes than the declared Content-Length, simulating a truncated transfer.
    truncate_downloads: bool = False

    #: Close the connection part-way through a download.
    drop_downloads: bool = False

    #: Fail this many command executions before starting to succeed. Used to prove that
    #: retry and recovery work, rather than only that failure is detected.
    fail_first_n_commands: int = 0

    #: Endpoints that should fail, by path. Allows isolating one broken call.
    failing_paths: frozenset[str] = field(default_factory=frozenset)

    @property
    def is_healthy(self) -> bool:
        """Whether this scenario describes a camera that simply works."""
        return self == HEALTHY


HEALTHY = Scenario()

BUSY = Scenario(busy=True)
STORAGE_FULL = Scenario(storage_full=True)
MALFORMED_JSON = Scenario(malformed_json=True)
SERVER_ERROR = Scenario(server_error=True)
UNACTIVATED = Scenario(unactivated=True)
SLOW = Scenario(latency_seconds=0.5)
REDIRECTING = Scenario(redirect_responses=True)
OVERSIZED = Scenario(oversized_responses=True)
TRUNCATED_DOWNLOAD = Scenario(truncate_downloads=True)
DROPPED_DOWNLOAD = Scenario(drop_downloads=True)
FLAKY = Scenario(fail_first_n_commands=2)
