"""The path jail is the boundary between an agent's chosen filename and the filesystem."""

from __future__ import annotations

import errno
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from sphereloom.domain.errors import PathJailError, PermissionDeniedError, StorageFullError
from sphereloom.security.workspace import (
    MAX_COMPONENT_BYTES,
    MAX_PATH_LENGTH,
    TEMP_NAME_OVERHEAD,
    Workspace,
)


def test_a_simple_relative_path_resolves_inside_the_workspace(workspace: Workspace) -> None:
    resolved = workspace.resolve("downloads/clip.insv")

    assert resolved.is_relative_to(workspace.root)
    assert resolved.name == "clip.insv"


def test_nested_directories_are_allowed(workspace: Workspace) -> None:
    resolved = workspace.resolve("a/b/c/file.jpg")

    assert resolved.is_relative_to(workspace.root)


@pytest.mark.parametrize(
    "candidate",
    [
        "../escape.txt",
        "../../escape.txt",
        "downloads/../../escape.txt",
        "a/b/../../../escape.txt",
    ],
)
def test_parent_traversal_is_rejected(workspace: Workspace, candidate: str) -> None:
    with pytest.raises(PathJailError):
        workspace.resolve(candidate)


@pytest.mark.parametrize(
    "candidate",
    ["/etc/passwd", "C:\\Windows\\System32\\config", "\\\\server\\share\\file", "/tmp/x"],
)
def test_absolute_paths_are_rejected(workspace: Workspace, candidate: str) -> None:
    with pytest.raises(PathJailError):
        workspace.resolve(candidate)


def test_empty_paths_are_rejected(workspace: Workspace) -> None:
    with pytest.raises(PathJailError):
        workspace.resolve("   ")


@pytest.mark.parametrize("candidate", ["bad|name.txt", 'quote".txt', "null\0byte.txt"])
def test_forbidden_characters_are_rejected(workspace: Workspace, candidate: str) -> None:
    with pytest.raises(PathJailError):
        workspace.resolve(candidate)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="creating symlinks on Windows needs privileges that CI runners may not grant",
)
def test_a_symlink_pointing_outside_the_workspace_is_rejected(
    workspace: Workspace, tmp_path: Path
) -> None:
    """Containment is checked after resolution, which is what catches this case."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace.root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathJailError):
        workspace.resolve("escape/secret.txt")


def test_an_absurdly_long_path_is_rejected(workspace: Workspace) -> None:
    """The path comes from a tool call, so an agent can supply anything.

    A clear refusal beats an operating-system error raised halfway through a download.
    """
    with pytest.raises(PathJailError, match="longer than"):
        workspace.resolve("a" * (MAX_PATH_LENGTH + 1))


def test_a_single_overlong_component_is_rejected(workspace: Workspace) -> None:
    """A total-length check alone lets one enormous component through."""
    with pytest.raises(PathJailError, match="One part of the destination path"):
        workspace.resolve("downloads/" + "a" * (MAX_COMPONENT_BYTES + 1) + ".insv")


def test_a_multibyte_component_is_measured_in_bytes(workspace: Workspace) -> None:
    """Filesystems cap a name in bytes, so 255 multibyte characters is still too long."""
    name = "ñ" * 200  # 400 bytes in UTF-8

    with pytest.raises(PathJailError, match="One part of the destination path"):
        workspace.resolve(f"downloads/{name}.insv")


def test_an_overlong_component_is_rejected_by_resolution(
    workspace: Workspace,
) -> None:
    """A name past the component limit never reaches the write path at all."""
    long_name = "a" * (MAX_COMPONENT_BYTES + 1)

    with pytest.raises(PathJailError), workspace.atomic_write(f"downloads/{long_name}.insv"):
        pass  # pragma: no cover - the context manager raises on entry


def test_a_long_but_usable_path_is_accepted(workspace: Workspace) -> None:
    """Being strict must not reject names a user could reasonably choose."""
    with workspace.atomic_write("downloads/" + "a" * 100 + ".insv") as (temp_path, destination):
        temp_path.write_bytes(b"ok")

    assert destination.read_bytes() == b"ok"


def test_a_lone_surrogate_is_rejected_as_a_path_error(workspace: Workspace) -> None:
    """A validator that crashes on bad input has failed at the one job it has.

    A JSON escape such as \\ud800 produces a string that cannot be encoded at all, which
    would otherwise escape the promised taxonomy as a UnicodeEncodeError.
    """
    with pytest.raises(PathJailError):
        workspace.resolve("downloads/\ud800.insv")


def test_relative_display_hides_absolute_layout(workspace: Workspace) -> None:
    """User-facing output must not leak the absolute path, which embeds a username."""
    resolved = workspace.resolve("downloads/clip.insv")

    assert workspace.relative_display(resolved) == "downloads/clip.insv"


def test_atomic_write_publishes_only_on_success(workspace: Workspace) -> None:
    with workspace.atomic_write("downloads/clip.insv") as (temp_path, destination):
        temp_path.write_bytes(b"complete")
        assert not destination.exists(), "the destination must not appear until committed"

    assert destination.read_bytes() == b"complete"


def test_atomic_write_leaves_nothing_behind_on_failure(workspace: Workspace) -> None:
    """An interrupted download must never leave a file that looks complete."""
    message = "transfer interrupted"
    destination = workspace.resolve("downloads/clip.insv")

    def interrupted_transfer() -> None:
        with workspace.atomic_write("downloads/clip.insv") as (temp_path, _):
            temp_path.write_bytes(b"partial")
            raise RuntimeError(message)

    with pytest.raises(RuntimeError, match=message):
        interrupted_transfer()

    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []


def test_atomic_write_rejects_an_escaping_destination(workspace: Workspace) -> None:
    def escaping_write() -> None:
        with workspace.atomic_write("../escape.txt"):
            pass  # pragma: no cover - the context manager raises on entry

    with pytest.raises(PathJailError):
        escaping_write()


def test_a_name_that_only_fits_without_its_temporary_file_is_refused(
    workspace: Workspace,
) -> None:
    """`resolve` bounds the name; `atomic_write` writes through a longer one.

    A name in this range passed validation and then failed in the kernel partway through a
    download, which is the failure MAX_COMPONENT_BYTES exists to prevent. The promise is a
    PathJailError, not an ENAMETOOLONG from inside a download.
    """
    name = "a" * (MAX_COMPONENT_BYTES - TEMP_NAME_OVERHEAD - 3) + ".jpg"

    workspace.resolve(name)

    with pytest.raises(PathJailError), workspace.atomic_write(name):
        pass  # pragma: no cover - the context manager raises on entry


def test_a_name_that_fits_with_its_temporary_file_is_written(
    workspace: Workspace,
) -> None:
    name = "a" * (MAX_COMPONENT_BYTES - TEMP_NAME_OVERHEAD - 4) + ".jpg"

    with workspace.atomic_write(name) as (temp_path, destination):
        temp_path.write_bytes(b"data")

    assert destination.read_bytes() == b"data"


def test_an_enormous_raw_path_is_rejected_before_it_is_scanned(
    workspace: Workspace, instrumented_str: Any
) -> None:
    """The limit ran after `strip()` copied the string and `set()` scanned every character.

    The argument comes from a tool call, so that work happened before the limit it
    violates applied. The verdict is the same either way, so the test observes the work:
    `strip`, `encode` and iteration all fail the test if they run before the length check.
    """
    oversized = instrumented_str("a" * (MAX_PATH_LENGTH + 1))

    with pytest.raises(PathJailError, match="longer than"):
        workspace.resolve(oversized)

    assert oversized.consumed == 0


def test_the_total_path_limit_counts_bytes_not_characters(workspace: Workspace) -> None:
    """PATH_MAX is 4096 *bytes* on Linux, so a character count is not the same limit.

    924 characters of emoji is 3624 bytes: it passes a character check and is then refused
    by the kernel, which is the failure the limit exists to prevent.
    """
    emoji_path = "/".join(["\U0001f600" * 60] * 15 + ["short.jpg"])

    assert len(emoji_path) < MAX_PATH_LENGTH
    assert len(emoji_path.encode("utf-8")) > MAX_PATH_LENGTH

    with pytest.raises(PathJailError, match="more than the"):
        workspace.resolve(emoji_path)


@pytest.mark.parametrize(
    "name",
    ["trail. /a.jpg", "trailing /a.jpg", "dot./a.jpg", "file. ", "name ."],
)
def test_a_component_the_filesystem_would_rewrite_is_refused(
    workspace: Workspace, name: str
) -> None:
    """Windows silently strips a trailing space or dot, so the write lands elsewhere.

    `trail. /a.jpg` created `trail\\` and the rename then had nowhere to go, surfacing as a
    raw FileNotFoundError after the caller had already written the data.
    """
    with pytest.raises(PathJailError, match="space or a dot"):
        workspace.resolve(name)


@pytest.mark.parametrize("failing_call", ["mkdir", "mkstemp", "replace"])
def test_a_refusal_from_the_operating_system_becomes_a_path_jail_error(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, failing_call: str
) -> None:
    """Validation cannot anticipate every filesystem rule, so a refusal is translated.

    There are three places the operating system gets the final say, and guarding only some
    of them moves the failure rather than removing it. The refusal is injected rather than
    provoked with a specific name, because which names a kernel rejects differs by platform:
    a tab is refused on Windows and perfectly legal on Linux, so a name-based test would
    assert a platform quirk instead of this guarantee.
    """
    refused = OSError(22, "Invalid argument")

    def refuse(*args: object, **kwargs: object) -> object:
        raise refused

    if failing_call == "mkdir":
        monkeypatch.setattr(Path, "mkdir", refuse)
    elif failing_call == "mkstemp":
        monkeypatch.setattr(tempfile, "mkstemp", refuse)
    else:
        monkeypatch.setattr(Path, "replace", refuse)

    with (
        pytest.raises(PathJailError),
        workspace.atomic_write("downloads/clip.insv") as (
            temp_path,
            _destination,
        ),
    ):
        temp_path.write_bytes(b"data")


def test_a_failed_cleanup_does_not_replace_the_original_failure(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a file another handle still holds raises on Windows.

    If the cleanup were unguarded it would replace the in-flight exception with a raw
    OSError, losing the reason the download failed in the first place.
    """

    def refuse_unlink(*args: object, **kwargs: object) -> object:
        raise OSError(32, "The process cannot access the file")

    monkeypatch.setattr(Path, "unlink", refuse_unlink)

    with pytest.raises(ZeroDivisionError), workspace.atomic_write("downloads/clip.insv"):
        _ = 1 / 0


@pytest.mark.parametrize("name", [".", "./", "downloads/..", "a/../"])
def test_the_workspace_root_itself_is_not_a_destination(workspace: Workspace, name: str) -> None:
    """These all resolve to the root, and `atomic_write` would then write outside the jail.

    The temporary file is created in `destination.parent`, which for the root is the
    workspace's *parent* directory: the whole payload lands outside before the rename
    fails.
    """
    with pytest.raises(PathJailError, match="not the workspace directory itself"):
        workspace.resolve(name)


@pytest.mark.parametrize(
    ("number", "expected"),
    [
        (errno.ENOSPC, StorageFullError),
        (errno.EDQUOT, StorageFullError),
        (errno.EACCES, PermissionDeniedError),
        (errno.EPERM, PermissionDeniedError),
        (errno.EROFS, PermissionDeniedError),
        (errno.EINVAL, PathJailError),
        (errno.ENAMETOOLONG, PathJailError),
    ],
)
@pytest.mark.parametrize("failing_call", ["mkdir", "mkstemp", "replace"])
def test_a_filesystem_failure_keeps_its_own_taxonomy(
    workspace: Workspace,
    monkeypatch: pytest.MonkeyPatch,
    failing_call: str,
    number: int,
    expected: type[Exception],
) -> None:
    """Translating every OSError to a path error told a caller with a full disk to rename.

    The cause is what distinguishes them, and all three operating-system touchpoints share
    one mapping so they cannot diverge.
    """

    def refuse(*args: object, **kwargs: object) -> object:
        raise OSError(number, "refused")

    if failing_call == "mkdir":
        monkeypatch.setattr(Path, "mkdir", refuse)
    elif failing_call == "mkstemp":
        monkeypatch.setattr(tempfile, "mkstemp", refuse)
    else:
        monkeypatch.setattr(Path, "replace", refuse)

    with (
        pytest.raises(expected),
        workspace.atomic_write("downloads/clip.insv") as (
            temp_path,
            _destination,
        ),
    ):
        temp_path.write_bytes(b"data")
