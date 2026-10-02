"""The dependency container shared by every tool.

Built once per process during the server lifespan and handed to tools explicitly, so there
is no global mutable state and tests can substitute any collaborator.
"""

from __future__ import annotations

from dataclasses import dataclass

from sphereloom.config import Settings
from sphereloom.domain.clock import Clock
from sphereloom.domain.ids import IdFactory
from sphereloom.security.workspace import Workspace


@dataclass(frozen=True, slots=True)
class AppContext:
    """Everything a tool is allowed to reach for."""

    settings: Settings
    workspace: Workspace
    clock: Clock
    id_factory: IdFactory
