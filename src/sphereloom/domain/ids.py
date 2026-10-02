"""Identifier and token generation.

Job identifiers are opaque and need only be unique. Confirmation tokens guard destructive
operations, so they are generated with a cryptographically secure source and compared in
constant time elsewhere.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Protocol


class IdFactory(Protocol):
    """Creates identifiers and security tokens."""

    def job_id(self) -> str:
        """Return a unique job identifier."""
        ...

    def confirmation_token(self) -> str:
        """Return an unguessable single-use confirmation token."""
        ...


class SecureIdFactory:
    """The production implementation."""

    def job_id(self) -> str:
        return f"job_{uuid.uuid4().hex[:16]}"

    def confirmation_token(self) -> str:
        return secrets.token_urlsafe(32)


class SequentialIdFactory:
    """A predictable factory for tests.

    Never use this outside tests: the tokens it produces are trivially guessable.
    """

    def __init__(self) -> None:
        self._jobs = 0
        self._tokens = 0

    def job_id(self) -> str:
        self._jobs += 1
        return f"job_{self._jobs:04d}"

    def confirmation_token(self) -> str:
        self._tokens += 1
        return f"confirm_{self._tokens:04d}"
