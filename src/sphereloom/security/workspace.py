"""The workspace path jail.

Every byte SphereLoom writes lands inside one configured directory. An AI agent choosing a
destination filename is untrusted input, so the rule is enforced here, once, rather than
trusted to each call site.

Three escapes are closed: parent traversal (`../../etc/passwd`), absolute paths, and
symlinks that point outside the root. Containment is checked *after* full resolution, which
is what makes the symlink case work.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePath

from sphereloom.domain.errors import PathJailError

#: Characters that are illegal in Windows filenames and meaningless in POSIX ones. Rejecting
#: them everywhere keeps behaviour identical across platforms.
_FORBIDDEN_CHARS = frozenset('<>:"|?*\0')


class Workspace:
    """A directory that confines all file output."""

    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    @property
    def root(self) -> Path:
        """The absolute, symlink-resolved workspace root."""
        return self._root

    def ensure(self) -> None:
        """Create the workspace directory if it does not exist."""
        self._root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str | PurePath) -> Path:
        """Resolve a caller-supplied relative path to an absolute path inside the jail.

        Raises:
            PathJailError: if the input is absolute, contains forbidden characters, or
                resolves outside the workspace root.
        """
        text = str(relative).strip()
        if not text:
            raise PathJailError(
                "A destination path is required. Provide a path relative to the workspace, "
                "for example 'downloads/clip.insv'.",
            )

        if _FORBIDDEN_CHARS & set(text):
            raise PathJailError(
                "The destination path contains characters that are not allowed in a "
                "filename. Use letters, digits, dots, dashes, underscores and forward "
                "slashes.",
            )

        candidate = PurePath(text)
        if candidate.is_absolute() or candidate.drive or text.startswith(("/", "\\")):
            raise PathJailError(
                "Absolute destination paths are rejected. SphereLoom writes only inside its "
                "configured workspace, so supply a path relative to it, for example "
                "'downloads/clip.insv'.",
            )

        resolved = (self._root / candidate).expanduser().resolve()
        if not self._is_contained(resolved):
            raise PathJailError(
                "The destination path resolves outside the SphereLoom workspace and was "
                "rejected. This happens with '..' segments or with a symlink pointing "
                "elsewhere.",
            )
        return resolved

    def relative_display(self, path: Path) -> str:
        """Render a path for user-facing output, relative to the workspace root.

        Absolute paths usually embed a username, so they are never returned to callers.
        """
        try:
            return path.resolve().relative_to(self._root).as_posix()
        except ValueError:
            return path.name

    @contextmanager
    def atomic_write(self, relative: str | PurePath) -> Iterator[tuple[Path, Path]]:
        """Write through a temporary file, then rename into place.

        Yields the temporary path to write to and the final destination. An interrupted
        download therefore never leaves a half-written file that looks complete.
        """
        destination = self.resolve(relative)
        destination.parent.mkdir(parents=True, exist_ok=True)

        handle, temp_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".partial",
        )
        os.close(handle)
        temp_path = Path(temp_name)
        try:
            yield temp_path, destination
            temp_path.replace(destination)
        except BaseException:
            temp_path.unlink(missing_ok=True)
            raise

    def _is_contained(self, resolved: Path) -> bool:
        try:
            return resolved == self._root or resolved.is_relative_to(self._root)
        except ValueError:  # pragma: no cover - differing drives on Windows
            return False
