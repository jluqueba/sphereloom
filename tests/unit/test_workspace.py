"""The path jail is the boundary between an agent's chosen filename and the filesystem."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from sphereloom.domain.errors import PathJailError
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
    with pytest.raises(PathJailError, match="longer than"):
        workspace.resolve("downloads/" + "a" * (MAX_COMPONENT_BYTES + 1) + ".insv")


def test_a_multibyte_component_is_measured_in_bytes(workspace: Workspace) -> None:
    """Filesystems cap a name in bytes, so 255 multibyte characters is still too long."""
    name = "ñ" * 200  # 400 bytes in UTF-8

    with pytest.raises(PathJailError, match="longer than"):
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


def test_an_enormous_raw_path_is_rejected_before_it_is_scanned(workspace: Workspace) -> None:
    """The limit ran after `strip()` copied the string and `set()` scanned every character.

    The argument comes from a tool call, so that work happened before the limit it
    violates applied. The guard is the time taken, not only the error.
    """
    huge = "a" * 50_000_000

    started = time.monotonic()
    with pytest.raises(PathJailError, match="longer than"):
        workspace.resolve(huge)
    elapsed = time.monotonic() - started

    assert elapsed < 1.0
