from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import socket
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import PopupConfig
from .runtime import (
    popup_environment,
    prepare_runtime_directory,
    read_action_generation,
)


ICON_NAMES = {
    "headphones": "audio-headphones-symbolic",
    "speakers": "audio-speakers-symbolic",
    "audio": "audio-card-symbolic",
    "warning": "dialog-warning-symbolic",
}
POPUP_SOCKET_NAME = "popup.sock"
POPUP_LAUNCH_LOCK_NAME = "popup-launch.lock"
POPUP_INSTANCE_LOCK_NAME = "popup-instance.lock"
POPUP_OWNER_NAME = "popup.owner"
MAX_UPDATE_BYTES = 16_384
STARTUP_WAIT_SECONDS = 1.0
# Initial update lost to a newer switch.
POPUP_OVERTAKEN_STATUS = 3
# Update passed to the process already showing the popup.
POPUP_HANDED_OVER_STATUS = 4
QUIET_POPUP_STATUSES = (POPUP_OVERTAKEN_STATUS, POPUP_HANDED_OVER_STATUS)


def _payload(
    config: PopupConfig, message: str, icon: str, generation: int
) -> dict[str, Any]:
    return {
        "version": 1,
        "generation": generation,
        "message": message,
        "icon": icon if icon in ICON_NAMES else "audio",
        "duration_ms": config.duration_ms,
        "bottom_margin": config.bottom_margin,
        "monitor_model": config.monitor_model,
        "all_monitors": config.all_monitors,
    }


def _encode_payload(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_UPDATE_BYTES:
        raise ValueError("audio popup update is too large")
    return data


def _decode_payload(data: bytes) -> dict[str, Any] | None:
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return None
    generation = payload.get("generation")
    message = payload.get("message")
    icon = payload.get("icon")
    duration_ms = payload.get("duration_ms")
    bottom_margin = payload.get("bottom_margin")
    monitor_model = payload.get("monitor_model")
    all_monitors = payload.get("all_monitors")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        return None
    if not isinstance(message, str) or len(message.encode("utf-8")) > 12_000:
        return None
    if icon not in ICON_NAMES:
        return None
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
        return None
    if not 250 <= duration_ms <= 10_000:
        return None
    if isinstance(bottom_margin, bool) or not isinstance(bottom_margin, int):
        return None
    if not 0 <= bottom_margin <= 1_000:
        return None
    if not isinstance(monitor_model, str) or not isinstance(all_monitors, bool):
        return None
    return payload


def _socket_path(directory: Path) -> Path:
    return directory / POPUP_SOCKET_NAME


def _acquire_launch_lock(directory: Path) -> int:
    descriptor = os.open(
        directory / POPUP_LAUNCH_LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _start_popup_process(data: bytes, environment: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "linux_audio_output_switcher.popup",
            "--payload",
            data.decode("utf-8"),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        start_new_session=True,
        env=environment,
    )


def _send_update(path: Path, data: bytes) -> bool:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        # A full popup queue must not hold up the audio switch.
        client.setblocking(False)
        client.sendto(data, str(path))
        return True
    except BlockingIOError:
        # Keep the busy listener's socket. Only the update is dropped.
        print("Audio popup is busy. Update dropped.", file=sys.stderr)
        return True
    except OSError:
        return False
    finally:
        client.close()


def _remove_stale_socket(path: Path) -> None:
    try:
        details = path.lstat()
    except FileNotFoundError:
        return
    if details.st_uid != os.getuid() or not stat.S_ISSOCK(details.st_mode):
        raise RuntimeError(f"unsafe popup socket path: {path}")
    path.unlink()


def _wait_for_socket(path: Path, process: subprocess.Popen, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            details = path.lstat()
        except FileNotFoundError:
            details = None
        if details is not None and stat.S_ISSOCK(details.st_mode):
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.01)
    return False


def launch_popup(config: PopupConfig, message: str, icon: str, generation: int) -> None:
    if not config.enabled:
        return
    environment = popup_environment()
    if environment is None:
        return
    try:
        data = _encode_payload(_payload(config, message, icon, generation))
        directory = prepare_runtime_directory()
        path = _socket_path(directory)
        # Sending and closing use the same lock. Starting a process and waiting
        # for its socket happen after releasing it so a newer update can proceed.
        lock_descriptor = _acquire_launch_lock(directory)
        try:
            if generation < read_action_generation(directory):
                # A newer switch took the lock. Skip this old label.
                return
            if _send_update(path, data):
                return
        finally:
            os.close(lock_descriptor)
        process = _start_popup_process(data, environment)
        if not _wait_for_socket(path, process, STARTUP_WAIT_SECONDS):
            status = process.poll()
            if status in QUIET_POPUP_STATUSES:
                # Another process owns the popup and received this update.
                return
            if status is None:
                print("Audio popup is still starting.", file=sys.stderr)
                return
            print("Audio popup unavailable: popup process did not start.", file=sys.stderr)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Audio popup unavailable: {error}", file=sys.stderr)


def _write_owner_token(path: Path, token: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, token.encode("ascii"))
    finally:
        os.close(descriptor)


def _owner_matches(path: Path, token: str) -> bool:
    try:
        details = path.lstat()
        if details.st_uid != os.getuid() or not stat.S_ISREG(details.st_mode):
            return False
        return path.read_text(encoding="ascii") == token
    except (OSError, UnicodeError):
        return False


def _bind_update_socket(path: Path, owner_path: Path, token: str) -> socket.socket:
    _remove_stale_socket(path)
    _write_owner_token(owner_path, token)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        server.bind(str(path))
        os.chmod(path, 0o600)
        server.setblocking(False)
        return server
    except BaseException:
        server.close()
        if _owner_matches(owner_path, token):
            owner_path.unlink(missing_ok=True)
        raise


def _try_popup_instance_lock(directory: Path) -> int | None:
    """Take the lock owned by the popup process that displays updates."""
    descriptor = os.open(
        directory / POPUP_INSTANCE_LOCK_NAME, os.O_CREAT | os.O_RDWR, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


class PopupExit(Exception):
    """The popup process must exit with ``status`` without showing anything."""

    def __init__(self, status: int):
        super().__init__(status)
        self.status = status


@dataclass
class PopupClaim:
    lock_descriptor: int
    order: UpdateOrder
    server: socket.socket
    owner_path: Path
    owner_token: str


def _claim_popup(
    directory: Path,
    initial_payload: dict[str, Any],
    handover_timeout: float = STARTUP_WAIT_SECONDS,
) -> PopupClaim:
    """Own the display for this popup process, or raise PopupExit.

    Only one popup process may own the socket. A process that finds another
    owner, for example because it was slow to start, hands its update to that
    owner. The owner applies the usual switch order.
    A stale initial update exits without showing anything.
    """
    path = _socket_path(directory)
    data = _encode_payload(initial_payload)
    deadline = time.monotonic() + handover_timeout
    while True:
        descriptor = None
        lock_descriptor = _acquire_launch_lock(directory)
        try:
            if _send_update(path, data):
                raise PopupExit(POPUP_HANDED_OVER_STATUS)
            descriptor = _try_popup_instance_lock(directory)
            if descriptor is not None:
                order = UpdateOrder(directory)
                if not order.accept(initial_payload):
                    # A newer switch finished during startup.
                    raise PopupExit(POPUP_OVERTAKEN_STATUS)
                owner_path = directory / POPUP_OWNER_NAME
                owner_token = secrets.token_hex(16)
                server = _bind_update_socket(path, owner_path, owner_token)
                return PopupClaim(
                    descriptor, order, server, owner_path, owner_token
                )
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
        finally:
            os.close(lock_descriptor)
        if time.monotonic() >= deadline:
            raise PopupExit(1)
        time.sleep(0.01)


def _receive_latest(server: socket.socket) -> dict[str, Any] | None:
    # Datagram arrival order may differ from switch order.
    latest = None
    while True:
        try:
            data = server.recv(MAX_UPDATE_BYTES + 1)
        except BlockingIOError:
            return latest
        except OSError:
            return latest
        if len(data) > MAX_UPDATE_BYTES:
            continue
        payload = _decode_payload(data)
        if payload is not None and (
            latest is None or payload["generation"] >= latest["generation"]
        ):
            latest = payload


def _receive_accepted_update(
    server: socket.socket, order: UpdateOrder
) -> dict[str, Any] | None:
    update = _receive_latest(server)
    if update is None or not order.accept(update):
        return None
    return update


class UpdateOrder:
    """Rejects updates from actions older than the newest one already seen.

    The newest generation is the larger of the last accepted update and the
    persisted counter, so a result from an action that was overtaken by a
    later one is never shown, even by a freshly started popup process.
    """

    def __init__(self, directory: Path):
        self.directory = directory
        self.latest = -1

    def accept(self, payload: dict[str, Any]) -> bool:
        generation = payload["generation"]
        if generation < max(self.latest, read_action_generation(self.directory)):
            return False
        self.latest = generation
        return True


def _close_popup_with_handoff(
    directory: Path,
    server: socket.socket,
    order: UpdateOrder,
    path: Path,
    owner_path: Path,
    owner_token: str,
    release_instance: Callable[[], None],
    handoff: Callable[[dict[str, Any]], None],
) -> None:
    """Close one popup without losing an update already accepted by its socket."""
    update = None
    lock_descriptor = _acquire_launch_lock(directory)
    try:
        update = _receive_accepted_update(server, order)
        try:
            server.close()
            _unlink_owned_socket(path, owner_path, owner_token)
        finally:
            release_instance()
    finally:
        os.close(lock_descriptor)
    if update is not None:
        handoff(update)


def _unlink_owned_socket(path: Path, owner_path: Path, token: str) -> None:
    if not _owner_matches(owner_path, token):
        return
    try:
        details = path.lstat()
    except FileNotFoundError:
        details = None
    if details is not None and stat.S_ISSOCK(details.st_mode) and details.st_uid == os.getuid():
        path.unlink()
    if _owner_matches(owner_path, token):
        owner_path.unlink(missing_ok=True)


def run_popup(initial_payload: dict[str, Any]) -> int:
    import gi

    gi.require_version("Gdk", "3.0")
    gi.require_version("Gtk", "3.0")
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import Gdk, GLib, Gtk, GtkLayerShell

    ready, _ = Gtk.init_check([])
    if not ready or not GtkLayerShell.is_supported():
        print("Audio popup: the Wayland desktop is not available.", file=sys.stderr)
        return 1

    directory = prepare_runtime_directory()
    try:
        claim = _claim_popup(directory, initial_payload)
    except PopupExit as exit_request:
        return exit_request.status
    path = _socket_path(directory)
    order = claim.order
    server = claim.server
    owner_path = claim.owner_path
    owner_token = claim.owner_token
    instance_lock_descriptor: int | None = claim.lock_descriptor
    watch_id = None
    timer_id = None
    views: list[tuple[Any, Any, Any]] = []
    current_layout: tuple[int, str, bool] | None = None
    closed = False

    def release_instance_lock() -> None:
        nonlocal instance_lock_descriptor
        if instance_lock_descriptor is not None:
            os.close(instance_lock_descriptor)
            instance_lock_descriptor = None

    css = Gtk.CssProvider()
    css.load_from_data(b"""
        #audio-output-popup { background-color: transparent; }
        #audio-output-card {
            background-color: rgba(30, 29, 36, 0.97);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 18px;
            padding: 14px 20px;
        }
        #audio-output-card label { color: #f5f3fa; font-size: 15px; }
        #audio-output-card image { color: #ded9ee; }
    """)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )

    def close_server(*, launch_lock_held: bool = False) -> None:
        nonlocal closed, watch_id
        if closed:
            return
        closed = True
        if watch_id is not None:
            GLib.source_remove(watch_id)
            watch_id = None
        lock_descriptor = None
        try:
            if not launch_lock_held:
                lock_descriptor = _acquire_launch_lock(directory)
            server.close()
            _unlink_owned_socket(path, owner_path, owner_token)
        finally:
            if lock_descriptor is not None:
                os.close(lock_descriptor)

    def handoff_after_shutdown(payload: dict[str, Any]) -> None:
        for window, _image, _label in views:
            window.destroy()
        views.clear()
        environment = popup_environment()
        if environment is None:
            print("Audio popup unavailable: Wayland session is gone.", file=sys.stderr)
            return
        try:
            data = _encode_payload(payload)
            process = _start_popup_process(data, environment)
            if _wait_for_socket(path, process, STARTUP_WAIT_SECONDS):
                return
            status = process.poll()
            if status in QUIET_POPUP_STATUSES:
                return
            if status is None:
                print("Audio popup replacement is still starting.", file=sys.stderr)
                return
            print("Audio popup unavailable: replacement did not start.", file=sys.stderr)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"Audio popup unavailable: {error}", file=sys.stderr)

    def expire() -> bool:
        nonlocal timer_id
        timer_id = None
        lock_descriptor = _acquire_launch_lock(directory)
        try:
            # A sender may have queued an update just before this timer ran.
            # Read it while closing is blocked by the same lock.
            update = _receive_accepted_update(server, order)
            if update is None:
                close_server(launch_lock_held=True)
                release_instance_lock()
        finally:
            os.close(lock_descriptor)
        if update is not None:
            show(update)
            return False
        Gtk.main_quit()
        return False

    def popup_monitors(payload: dict[str, Any]) -> list[Any]:
        display = Gdk.Display.get_default()
        monitors = [
            display.get_monitor(index) for index in range(display.get_n_monitors())
        ]
        if payload["all_monitors"]:
            return monitors or [None]
        selected = None
        for candidate in monitors:
            if payload["monitor_model"] and (
                candidate.get_model() or ""
            ).casefold() == payload["monitor_model"].casefold():
                selected = candidate
                break
        return [selected]

    def rebuild(payload: dict[str, Any]) -> None:
        nonlocal views, current_layout
        for window, _image, _label in views:
            window.destroy()
        views = []
        current_layout = (
            payload["bottom_margin"],
            payload["monitor_model"],
            payload["all_monitors"],
        )
        for monitor in popup_monitors(payload):
            window = Gtk.Window(title="Audio output")
            window.set_name("audio-output-popup")
            window.set_decorated(False)
            window.set_resizable(False)
            window.set_accept_focus(False)
            window.set_skip_taskbar_hint(True)
            window.set_skip_pager_hint(True)

            GtkLayerShell.init_for_window(window)
            if monitor is not None:
                GtkLayerShell.set_monitor(window, monitor)
            GtkLayerShell.set_namespace(window, "linux-audio-output-switcher-popup")
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_mode(window, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_exclusive_zone(window, 0)
            GtkLayerShell.set_anchor(window, GtkLayerShell.Edge.BOTTOM, True)
            GtkLayerShell.set_margin(
                window, GtkLayerShell.Edge.BOTTOM, payload["bottom_margin"]
            )

            card = Gtk.Box(spacing=11)
            card.set_name("audio-output-card")
            image = Gtk.Image()
            card.pack_start(image, False, False, 0)
            label = Gtk.Label()
            label.set_justify(Gtk.Justification.CENTER)
            card.pack_start(label, False, False, 0)
            window.add(card)
            views.append((window, image, label))

    def show(payload: dict[str, Any]) -> None:
        nonlocal timer_id
        layout = (
            payload["bottom_margin"],
            payload["monitor_model"],
            payload["all_monitors"],
        )
        if layout != current_layout:
            rebuild(payload)
        icon_name = ICON_NAMES[payload["icon"]]
        for window, image, label in views:
            image.set_from_icon_name(icon_name, Gtk.IconSize.BUTTON)
            label.set_text(payload["message"])
            window.show_all()
        if timer_id is not None:
            GLib.source_remove(timer_id)
        timer_id = GLib.timeout_add(payload["duration_ms"], expire)

    def receive_updates(_source: int, condition: Any) -> bool:
        if condition & GLib.IO_IN:
            update = _receive_latest(server)
            if update is not None and order.accept(update):
                show(update)
        return True

    try:
        show(initial_payload)
        watch_id = GLib.io_add_watch(server.fileno(), GLib.IO_IN, receive_updates)
        Gtk.main()
    finally:
        try:
            if closed:
                release_instance_lock()
            else:
                _close_popup_with_handoff(
                    directory,
                    server,
                    order,
                    path,
                    owner_path,
                    owner_token,
                    release_instance_lock,
                    handoff_after_shutdown,
                )
        finally:
            for window, _image, _label in views:
                window.destroy()
    return 0


def popup_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    arguments = parser.parse_args()
    payload = _decode_payload(arguments.payload.encode("utf-8"))
    if payload is None:
        print("Audio popup: invalid update.", file=sys.stderr)
        return 2
    try:
        return run_popup(payload)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(popup_main())
