import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from linux_audio_output_switcher.runtime import (
    GENERATION_NAME,
    acquire_action_lock,
    popup_environment,
    prepare_runtime_directory,
    read_action_generation,
)


class RuntimeTests(unittest.TestCase):
    def test_each_action_lock_gets_the_next_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = prepare_runtime_directory(Path(temporary) / "runtime")
            self.assertEqual(read_action_generation(directory), 0)
            generations = []
            for _ in range(3):
                with acquire_action_lock(directory) as lock:
                    generations.append(lock.generation)
                    self.assertEqual(read_action_generation(directory), lock.generation)
            self.assertEqual(generations, [1, 2, 3])
            self.assertEqual(oct((directory / GENERATION_NAME).stat().st_mode & 0o777), "0o600")

    def test_rejected_lock_does_not_consume_a_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = prepare_runtime_directory(Path(temporary) / "runtime")
            with acquire_action_lock(directory) as held:
                with self.assertRaisesRegex(RuntimeError, "already in progress"):
                    acquire_action_lock(directory)
                self.assertEqual(read_action_generation(directory), held.generation)

    def test_failed_generation_write_releases_the_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = prepare_runtime_directory(Path(temporary) / "runtime")
            with patch(
                "linux_audio_output_switcher.runtime.os.replace",
                side_effect=OSError("no space left on device"),
            ):
                with self.assertRaisesRegex(RuntimeError, "cannot record the switch order"):
                    acquire_action_lock(directory)
            # Closing the descriptor released the lock.
            with acquire_action_lock(directory) as lock:
                self.assertEqual(lock.generation, 1)

    def test_unreadable_generation_file_restarts_from_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = prepare_runtime_directory(Path(temporary) / "runtime")
            (directory / GENERATION_NAME).write_text("garbage", encoding="utf-8")
            self.assertEqual(read_action_generation(directory), 0)
            with acquire_action_lock(directory) as lock:
                self.assertEqual(lock.generation, 1)

    def test_valid_wayland_socket_is_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            display_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            display_socket.bind(str(base / "wayland-9"))
            old_runtime = os.environ.get("XDG_RUNTIME_DIR")
            old_display = os.environ.get("WAYLAND_DISPLAY")
            os.environ["XDG_RUNTIME_DIR"] = str(base)
            os.environ["WAYLAND_DISPLAY"] = "wayland-9"
            try:
                environment = popup_environment()
                self.assertIsNotNone(environment)
                self.assertEqual(environment["WAYLAND_DISPLAY"], "wayland-9")
            finally:
                display_socket.close()
                if old_runtime is None:
                    os.environ.pop("XDG_RUNTIME_DIR", None)
                else:
                    os.environ["XDG_RUNTIME_DIR"] = old_runtime
                if old_display is None:
                    os.environ.pop("WAYLAND_DISPLAY", None)
                else:
                    os.environ["WAYLAND_DISPLAY"] = old_display


if __name__ == "__main__":
    unittest.main()
