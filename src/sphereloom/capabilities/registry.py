"""Resolving what the active backend can do, and refusing what it cannot before any I/O.

Two sources feed the answer. The static table says what a backend can do. An adapter's
runtime refinements say what *this* camera, on *this* firmware, can do in practice. One
rule governs how they combine:

    Runtime information may narrow a capability. It may never widen one.

A documented vendor gap, or a capability SphereLoom does not provide yet, cannot be turned
into "available" by anything a device reports.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from sphereloom.capabilities.data import STATIC_CAPABILITIES
from sphereloom.domain.capabilities import Capability, CapabilityStatus
from sphereloom.domain.errors import InternalError, UnsupportedCapabilityError

type Refinements = Mapping[Capability, CapabilityStatus]


class CapabilityRegistry:
    """Answers "can this backend do this, and if not, why not?" for one backend."""

    def __init__(
        self,
        backend: str,
        table: Mapping[str, Mapping[Capability, CapabilityStatus]] = STATIC_CAPABILITIES,
    ) -> None:
        declared = table.get(backend)
        if declared is None or set(declared) != set(Capability):
            message = f"No complete capability declaration exists for the {backend!r} backend."
            raise InternalError(message)
        self._backend = backend
        self._declared = declared

    @property
    def backend(self) -> str:
        """The backend name reported in results and errors."""
        return self._backend

    def resolve(
        self, capability: Capability, runtime: Refinements | None = None
    ) -> CapabilityStatus:
        """Return the status of `capability`, narrowed by `runtime` but never widened."""
        declared = self._declared[capability]
        if not declared.is_available or runtime is None:
            return declared
        reported = runtime.get(capability)
        if reported is None or reported.is_available:
            return declared
        # Keyed by the table, so a refinement filed under the wrong capability cannot change
        # which capability it narrows.
        return reported.model_copy(update={"capability": capability})

    def resolve_all(self, runtime: Refinements | None = None) -> list[CapabilityStatus]:
        """Return every capability's status, in declaration order."""
        return [self.resolve(capability, runtime) for capability in Capability]

    def require(self, capability: Capability, runtime: Refinements | None = None) -> None:
        """Raise `UnsupportedCapabilityError` unless `capability` is available.

        Synchronous and free of I/O, so it can run before anything touches a device.
        """
        status = self.resolve(capability, runtime)
        if status.is_available:
            return
        message = (
            f"The capability {capability.value} is not available on the {self._backend} "
            f"backend. {status.reason}"
        )
        raise UnsupportedCapabilityError(
            message,
            backend=self._backend,
            capability=capability.value,
            status=status.status.value,
            reason=status.reason,
            docs_url=status.docs_url,
        )


async def guarded[T](
    registry: CapabilityRegistry,
    capability: Capability,
    operation: Callable[[], Awaitable[T]],
    runtime: Refinements | None = None,
) -> T:
    """Run `operation` only if `capability` is available; otherwise raise without calling it.

    `operation` is a factory rather than an awaitable, so a refused call never creates the
    coroutine that would perform the I/O.
    """
    registry.require(capability, runtime)
    return await operation()
