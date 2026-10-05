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
from contextlib import contextmanager, suppress
from pathlib import Path, PurePath

from sphereloom.domain.errors import PathJailError

#: Characters that are illegal in Windows filenames and meaningless in POSIX ones. Rejecting
#: them everywhere keeps behaviour identical across platforms.
_FORBIDDEN_CHARS = frozenset('<>:"|?*\0')

#: Longest relative path accepted. The value comes from a tool call, so it is untrusted
#: input: an agent can supply anything. Common filesystems reject far shorter paths, and a
#: clear refusal here is better than an operating-system error raised halfway through a
#: download.
MAX_PATH_LENGTH = 1024

#: Longest single path component. Most filesystems cap a name at 255 *bytes*, so a name of
#: 255 multibyte characters would still be rejected by the kernel; the bound is applied to
#: the encoded length for that reason. Without this, a total-length check alone lets one
#: enormous component through, and the failure surfaces as ENAMETOOLONG from inside a
#: download rather than as the PathJailError this class promises.
MAX_COMPONENT_BYTES = 255

#: Bytes that `atomic_write` adds to the destination name when it builds its temporary
#: file: a leading dot, a separating dot, eight random characters and the `.partial`
#: suffix. A name that fits the component limit on its own can still exceed it once the
#: temporary file is named, and that failure arrives from the kernel partway through a
#: download instead of as the PathJailError this class promises.
TEMP_NAME_OVERHEAD = 18


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
        # Two stages, because the first is free. A character count is never greater than
        # the byte count, so anything longer than the limit in characters is certainly over
        # it in bytes and can be refused without encoding at all. Only a plausible path --
        # at most MAX_PATH_LENGTH characters -- is then encoded to be measured properly.
        #
        # Bytes are what matters: PATH_MAX is 4096 *bytes* on Linux, so 924 characters of
        # emoji is 3624 bytes and would be refused by the kernel after passing a character
        # count. Both checks run before `strip()` copies the string and `set()` scans it.
        raw = str(relative)
        if len(raw) > MAX_PATH_LENGTH:
            raise PathJailError(
                f"The destination path is longer than {MAX_PATH_LENGTH} characters, which "
                "is more than this server accepts. Use a shorter name.",
            )

        try:
            raw_bytes = len(raw.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise PathJailError(
                "The destination path contains characters that cannot be encoded as a "
                "filename. Use letters, digits, dots, dashes, underscores and slashes.",
            ) from exc

        if raw_bytes > MAX_PATH_LENGTH:
            raise PathJailError(
                f"The destination path is {raw_bytes} bytes long, which is more than the "
                f"{MAX_PATH_LENGTH} this server accepts. Use a shorter name.",
            )

        text = raw.strip()
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

        for part in candidate.parts:
            try:
                encoded_length = len(part.encode("utf-8"))
            except UnicodeEncodeError as exc:
                # A lone surrogate, which a JSON escape such as \ud800 can produce, cannot
                # be encoded at all. A validator that crashes on bad input has failed at
                # the one job it has, so this becomes a refusal like any other.
                raise PathJailError(
                    "The destination path contains characters that cannot be encoded as a "
                    "filename. Use letters, digits, dots, dashes, underscores and forward "
                    "slashes.",
                ) from exc

            if encoded_length > MAX_COMPONENT_BYTES:
                raise PathJailError(
                    f"One part of the destination path is longer than "
                    f"{MAX_COMPONENT_BYTES} bytes, which no common filesystem accepts. Use "
                    "a shorter name.",
                )

            # Windows silently strips a trailing space or dot from a name, so `trail. /x`
            # creates `trail\` and the final rename then has nowhere to land. A name the
            # filesystem will quietly rewrite is not a name this server can honour, and
            # refusing it is clearer than writing to a path the caller did not ask for.
            if part not in {".", ".."} and part != part.rstrip(" ."):
                raise PathJailError(
                    "A part of the destination path ends with a space or a dot, which "
                    "some filesystems silently remove. Use a name without them.",
                )

        resolved = (self._root / candidate).expanduser().resolve()
        if not self._is_contained(resolved):
            raise PathJailError(
                "The destination path resolves outside the SphereLoom workspace and was "
                "rejected. This happens with '..' segments or with a symlink pointing "
                "elsewhere.",
            )

        # The root itself is not a destination. `.`, `./` and `downloads/..` all resolve to
        # it, and `atomic_write` would then place its temporary file in the root's *parent*
        # -- outside the jail -- and write the whole payload there before the rename failed.
        if resolved == self._root:
            raise PathJailError(
                "The destination path must name a file inside the workspace, not the "
                "workspace directory itself. Supply a name, for example 'downloads/clip.insv'.",
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

        # Checked before anything is created. `resolve` bounds the name on its own, but the
        # temporary file this method writes through is longer, so a name within 18 bytes of
        # the limit passes validation and then fails in the kernel mid-download.
        name_bytes = len(destination.name.encode("utf-8"))
        if name_bytes > MAX_COMPONENT_BYTES - TEMP_NAME_OVERHEAD:
            raise PathJailError(
                f"The file name is {name_bytes} bytes long. Writing it safely needs "
                f"{TEMP_NAME_OVERHEAD} more for a temporary file, which exceeds the "
                f"{MAX_COMPONENT_BYTES}-byte limit filesystems impose on a single name. "
                "Use a shorter name.",
            )

        # Creating the directory and the temporary file are the two places where the
        # operating system gets the final say. Validation cannot anticipate every rule a
        # filesystem enforces -- Windows silently strips a trailing space or dot from a
        # directory name, and rejects a tab outright -- so a refusal here is translated
        # rather than enumerated. Without this the caller sees a raw OSError from inside a
        # download instead of the PathJailError this class promises.
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)

            handle, temp_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".partial",
            )
        except OSError as exc:
            raise PathJailError(
                "The destination path was refused by the filesystem. Use a name made of "
                "letters, digits, dots, dashes and underscores, without trailing spaces "
                "or dots.",
            ) from exc

        os.close(handle)
        temp_path = Path(temp_name)
        try:
            yield temp_path, destination
            # The rename is the third place the operating system gets the final say, and
            # guarding only the first two moved the failure here rather than removing it.
            try:
                temp_path.replace(destination)
            except OSError as exc:
                raise PathJailError(
                    "The destination path was refused by the filesystem when the download "
                    "was moved into place. Nothing partial was left behind.",
                ) from exc
        except BaseException:
            # Every exit that is not a clean completion must remove the partial file,
            # including cancellation and KeyboardInterrupt: a half-written download left
            # under the final name would look like a complete one. The exception is
            # re-raised unchanged.
            #
            # The cleanup is itself guarded. On Windows, unlinking a file another handle
            # still holds raises PermissionError, which would replace the in-flight
            # exception with a raw OSError *and* abandon the partial file -- both of the
            # outcomes this block exists to prevent.
            # `contextlib.suppress` would be shorter but would move this reasoning away
            # from the code it explains: the original failure is what matters, and the
            # `.partial` suffix exists so a leftover is recognisable rather than mistaken
            # for a finished download.
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)
            raise

    def _is_contained(self, resolved: Path) -> bool:
        try:
            return resolved == self._root or resolved.is_relative_to(self._root)
        except ValueError:  # pragma: no cover - differing drives on Windows
            return False
