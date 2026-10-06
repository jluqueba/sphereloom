"""The workspace path jail.

Every byte SphereLoom writes lands inside one configured directory. An AI agent choosing a
destination filename is untrusted input, so the rule is enforced here, once, rather than
trusted to each call site.

Three escapes are closed: parent traversal (`../../etc/passwd`), absolute paths, and
symlinks that point outside the root. Containment is checked *after* full resolution, which
is what makes the symlink case work.
"""

from __future__ import annotations

import errno
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePath

from sphereloom.domain.errors import (
    InternalError,
    PathJailError,
    PermissionDeniedError,
    SphereLoomError,
    StorageFullError,
)

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

#: Errors that mean the disk, not the name, refused the write.
_STORAGE_ERRNOS = frozenset({errno.ENOSPC, errno.EDQUOT})


def _translate_os_error(exc: OSError, *, during: str) -> SphereLoomError:
    """Map a filesystem refusal to the taxonomy entry that matches its cause.

    Translating every `OSError` to a path error was itself a defect: a full disk told the
    caller to rename the file, which is useless advice and hides a condition the taxonomy
    already has a code for. The errno is what distinguishes them, and all three
    operating-system touchpoints go through here so the mapping cannot diverge between them.
    """
    if exc.errno in _STORAGE_ERRNOS:
        return StorageFullError(
            f"There is not enough space in the workspace to {during}. Free some space and retry.",
        )

    if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
        return PermissionDeniedError(
            f"SphereLoom is not allowed to {during} in the workspace directory. Check the "
            "permissions on the configured workspace.",
        )

    # Only a refusal of the *name* is a path error. Anything else -- an I/O error, too many
    # open files, no memory -- says nothing about the name, and advising a rename would
    # send the caller after the wrong problem.
    if exc.errno in _NAME_REFUSALS:
        return PathJailError(
            f"The destination path was refused by the filesystem while trying to {during}. "
            "Use a name made of letters, digits, dots, dashes and underscores, without "
            "surrounding spaces or a trailing dot.",
        )

    return InternalError(
        f"The filesystem failed while SphereLoom was trying to {during}, for a reason "
        "unrelated to the file name. Retrying may succeed once the host recovers.",
    )


#: Errors with which a filesystem refuses a name it will not create. Windows reports an
#: invalid character as EINVAL and a name it silently rewrote, and therefore cannot find
#: again, as ENOENT.
_NAME_REFUSALS = frozenset(
    {
        errno.EINVAL,
        errno.ENAMETOOLONG,
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EISDIR,
        errno.EEXIST,
        errno.EILSEQ,
    }
)


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
        # A string is measured as given; only a path object needs converting first.
        raw = relative if isinstance(relative, str) else str(relative)
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
                f"{MAX_PATH_LENGTH} bytes this server accepts. Use a shorter name.",
            )

        # `strip()` only decides whether anything was supplied. The name itself is validated
        # as given: stripping it first quietly turned "clip.jpg " into "clip.jpg", the very
        # rewrite the check on each part below exists to refuse.
        if not raw.strip():
            raise PathJailError(
                "A destination path is required. Provide a path relative to the workspace, "
                "for example 'downloads/clip.insv'.",
            )
        text = raw

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
            # creates `trail\` and the final rename then has nowhere to land. Surrounding
            # whitespace is refused at either end for the same reason: a name the filesystem
            # or a caller's tooling will quietly rewrite is not a name this server can
            # honour, and refusing it is clearer than writing to a path nobody asked for.
            if part not in {".", ".."} and (part != part.strip() or part.endswith(".")):
                raise PathJailError(
                    "A part of the destination path starts or ends with whitespace, or ends "
                    "with a dot, which some filesystems silently remove. Use a name without "
                    "them.",
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
        # operating system gets the final say before any data is written. Validation cannot
        # anticipate every rule a filesystem enforces -- Windows silently strips a trailing
        # space or dot from a directory name, and rejects a tab outright -- so a refusal is
        # translated rather than enumerated, and translated by cause: a full disk is not a
        # bad path and must not be reported as one.
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)

            handle, temp_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".partial",
            )
        except OSError as exc:
            raise _translate_os_error(exc, during="create the download file") from exc

        temp_path = Path(temp_name)
        try:
            # Closing the descriptor is an operating-system touchpoint too: a delayed write
            # error can surface here. It runs inside the cleanup block, so the file mkstemp
            # just created is removed rather than left behind by a raw exception.
            try:
                os.close(handle)
            except OSError as exc:
                raise _translate_os_error(exc, during="create the download file") from exc
            # Writing the payload is where a full disk is most likely to be noticed, so a
            # storage refusal from the caller's body gets the same translation as one from
            # the calls around it. Only storage errnos are translated: the body also does
            # network I/O, and a connection reset is an `OSError` that says nothing about
            # the workspace and must reach the caller as it was raised.
            try:
                yield temp_path, destination
            except OSError as exc:
                if exc.errno in _STORAGE_ERRNOS:
                    raise _translate_os_error(exc, during="write the download") from exc
                raise
            # The rename is the third place the operating system gets the final say, and
            # guarding only the first two moved the failure here rather than removing it.
            # It can still run out of space: a rename allocates directory metadata.
            try:
                temp_path.replace(destination)
            except OSError as exc:
                raise _translate_os_error(
                    exc, during="move the completed download into place"
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
