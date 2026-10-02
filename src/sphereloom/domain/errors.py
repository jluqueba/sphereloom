"""Error taxonomy.

Every failure a tool can report has exactly one code and one exception class, and there is
exactly one rendering path to the wire envelope. Tools never raise bare exceptions, and
never invent codes, so an agent can branch on `code` reliably.

See `docs/features/osc-camera-control/plan.md` section 7 and ADR-0003.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class ErrorCode(StrEnum):
    """The closed set of error codes SphereLoom can return."""

    UNSUPPORTED = "unsupported"
    NOT_CONNECTED = "not_connected"
    CAMERA_BUSY = "camera_busy"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    CONFIRMATION_REQUIRED = "confirmation_required"
    CONFIRMATION_INVALID = "confirmation_invalid"
    PERMISSION_DENIED = "permission_denied"
    PATH_OUTSIDE_WORKSPACE = "path_outside_workspace"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    STORAGE_FULL = "storage_full"
    JOB_FAILED = "job_failed"
    INTERNAL = "internal"


class SphereLoomError(Exception):
    """Base class for every error SphereLoom reports through a tool result.

    Messages are written for a human and a model at once: they state what failed, why, and
    what to do next. They must never contain tokens, network credentials or absolute paths.
    """

    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL
    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        backend: str | None = None,
        capability: str | None = None,
        reason: str | None = None,
        docs_url: str | None = None,
        available_in: str | None = None,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.backend = backend
        self.capability = capability
        self.reason = reason
        self.docs_url = docs_url
        self.available_in = available_in
        self.details: dict[str, JsonValue] = details or {}

    def to_envelope(self) -> dict[str, JsonValue]:
        """Render the error as the wire envelope shared by every tool."""
        payload: dict[str, JsonValue] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
        }
        optional: dict[str, str | None] = {
            "backend": self.backend,
            "capability": self.capability,
            "reason": self.reason,
            "docs_url": self.docs_url,
            "available_in": self.available_in,
        }
        payload.update({key: value for key, value in optional.items() if value is not None})
        if self.details:
            payload["details"] = self.details
        return {"error": payload}


class UnsupportedCapabilityError(SphereLoomError):
    """The active backend or camera model cannot perform this operation.

    This is a first-class answer, not a bug: it carries the reason, a documentation link and
    the milestone that will deliver the capability, when one is planned.
    """

    code = ErrorCode.UNSUPPORTED


class NotConnectedError(SphereLoomError):
    """The camera is unreachable, or the endpoint is not a spherical camera."""

    code = ErrorCode.NOT_CONNECTED
    retryable = True


class CameraBusyError(SphereLoomError):
    """The camera is already running a capture or another command."""

    code = ErrorCode.CAMERA_BUSY
    retryable = True


class InvalidArgumentError(SphereLoomError):
    """Tool input failed validation before any device call was attempted."""

    code = ErrorCode.INVALID_ARGUMENT


class NotFoundError(SphereLoomError):
    """The requested file URI or job identifier does not exist."""

    code = ErrorCode.NOT_FOUND


class ConfirmationRequiredError(SphereLoomError):
    """A destructive operation needs an explicit confirmation token to proceed."""

    code = ErrorCode.CONFIRMATION_REQUIRED


class ConfirmationInvalidError(SphereLoomError):
    """The confirmation token expired, was already used, or targets something else."""

    code = ErrorCode.CONFIRMATION_INVALID


class PermissionDeniedError(SphereLoomError):
    """The operation is disabled by configuration."""

    code = ErrorCode.PERMISSION_DENIED


class PathJailError(SphereLoomError):
    """The resolved destination escapes the configured workspace."""

    code = ErrorCode.PATH_OUTSIDE_WORKSPACE


class OperationTimeoutError(SphereLoomError):
    """A deadline was exceeded before the operation reported completion."""

    code = ErrorCode.TIMEOUT
    retryable = True


class RateLimitedError(SphereLoomError):
    """An internal rate limit rejected the call."""

    code = ErrorCode.RATE_LIMITED
    retryable = True


class StorageFullError(SphereLoomError):
    """The camera storage or the host workspace has no room left."""

    code = ErrorCode.STORAGE_FULL


class JobFailedError(SphereLoomError):
    """Background work finished in a failed state."""

    code = ErrorCode.JOB_FAILED


class InternalError(SphereLoomError):
    """An unexpected condition. Anything that reaches a user here is a defect."""

    code = ErrorCode.INTERNAL


#: Every concrete error class, indexed by its code. The exhaustiveness test in
#: `tests/unit/test_errors.py` asserts this mapping covers `ErrorCode` completely, so a new
#: code cannot be added without a matching exception class.
ERROR_CLASSES: dict[ErrorCode, type[SphereLoomError]] = {
    ErrorCode.UNSUPPORTED: UnsupportedCapabilityError,
    ErrorCode.NOT_CONNECTED: NotConnectedError,
    ErrorCode.CAMERA_BUSY: CameraBusyError,
    ErrorCode.INVALID_ARGUMENT: InvalidArgumentError,
    ErrorCode.NOT_FOUND: NotFoundError,
    ErrorCode.CONFIRMATION_REQUIRED: ConfirmationRequiredError,
    ErrorCode.CONFIRMATION_INVALID: ConfirmationInvalidError,
    ErrorCode.PERMISSION_DENIED: PermissionDeniedError,
    ErrorCode.PATH_OUTSIDE_WORKSPACE: PathJailError,
    ErrorCode.TIMEOUT: OperationTimeoutError,
    ErrorCode.RATE_LIMITED: RateLimitedError,
    ErrorCode.STORAGE_FULL: StorageFullError,
    ErrorCode.JOB_FAILED: JobFailedError,
    ErrorCode.INTERNAL: InternalError,
}
