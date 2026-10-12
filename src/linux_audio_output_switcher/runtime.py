from __future__ import annotations

import fcntl
import os
import re
from dataclasses import dataclass
from pathlib import Path


WAYLAND_NAME = re.compile(r"^wayland-[A-Za-z0-9_.-]+$")


def runtime_directory() -> Path:
    base = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
    return base / "linux-audio-output-switcher"


def prepare_runtime_directory(path: Path | None = None) -> Path:
    selected = path or runtime_directory()
    selected.mkdir(mode=0o700, parents=True, exist_ok=True)
    if selected.is_symlink() or selected.stat().st_uid != os.getuid():
        raise RuntimeError(f"unsafe runtime directory: {selected}")
    os.chmod(selected, 0o700)
    return selected


GENERATION_NAME = "switch.generation"


class ActionBusyError(RuntimeError):
    """Raised when another output change still owns the action lock."""


@dataclass
class ActionLock:
    descriptor: int
    # Number allocated while the switch lock is held.
    generation: int = 0

    def close(self) -> None:
        os.close(self.descriptor)

    def __enter__(self) -> "ActionLock":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()


def read_action_generation(directory: Path | None = None) -> int:
    """Return the generation of the newest action that took the lock."""
    path = (directory or runtime_directory()) / GENERATION_NAME
    try:
        return max(0, int(path.read_text(encoding="ascii").strip()))
    except (OSError, ValueError, UnicodeError):
        return 0


def _next_action_generation(directory: Path) -> int:
    # The lock serializes increments. Replace keeps readers from seeing a
    # partly written number.
    generation = read_action_generation(directory) + 1
    temporary = directory / f"{GENERATION_NAME}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        os.write(descriptor, str(generation).encode("ascii"))
    finally:
        os.close(descriptor)
    os.replace(temporary, directory / GENERATION_NAME)
    return generation


def acquire_action_lock(directory: Path | None = None) -> ActionLock:
    selected = prepare_runtime_directory(directory)
    descriptor = os.open(selected / "switch.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ActionBusyError("another audio-output switch is already in progress") from error
    except OSError:
        os.close(descriptor)
        raise
    try:
        generation = _next_action_generation(selected)
    except OSError as error:
        os.close(descriptor)
        raise RuntimeError(f"cannot record the switch order: {error}") from error
    return ActionLock(descriptor, generation)


def popup_environment() -> dict[str, str] | None:
    display = os.environ.get("WAYLAND_DISPLAY", "")
    base = runtime_directory().parent
    if not WAYLAND_NAME.fullmatch(display):
        return None
    path = base / display
    try:
        if not path.is_socket() or path.stat().st_uid != os.getuid():
            return None
    except OSError:
        return None
    return {
        **os.environ,
        "GDK_BACKEND": "wayland",
        "XDG_RUNTIME_DIR": str(base),
        "WAYLAND_DISPLAY": display,
        "XDG_SESSION_TYPE": "wayland",
    }
