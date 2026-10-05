"""Deterministic time.

Two distinct notions live here, and conflating them causes real bugs.

`Clock` answers "what time is it?" and is what timestamps and expiry windows use. It is
wall time, so it can jump backwards when the system clock is corrected.

`Monotonic` answers "how much time has passed?" and never goes backwards. Deadlines and
elapsed-time measurements must use it: a backward clock correction during a capture could
otherwise extend an operational timeout indefinitely, or trip it instantly.

Both are injectable so tests can control time without sleeping.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """A source of the current time."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...


class Monotonic(Protocol):
    """A source of elapsed time that never moves backwards."""

    def elapsed(self) -> float:
        """Return seconds from an arbitrary fixed point. Only differences are meaningful."""
        ...


class SystemClock:
    """The real wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class SystemMonotonic:
    """The real monotonic source."""

    def elapsed(self) -> float:
        return time.monotonic()


class FakeClock:
    """A manually advanced wall clock for tests."""

    def __init__(self, start: datetime | None = None) -> None:
        self._now = datetime(2026, 1, 1, tzinfo=UTC) if start is None else start

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self._now += timedelta(seconds=seconds)


class FakeMonotonic:
    """A manually advanced monotonic source for tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._elapsed = start

    def elapsed(self) -> float:
        return self._elapsed

    def advance(self, seconds: float) -> None:
        """Move elapsed time forward. Negative values are rejected by construction."""
        if seconds < 0:
            message = "A monotonic source cannot move backwards."
            raise ValueError(message)
        self._elapsed += seconds
