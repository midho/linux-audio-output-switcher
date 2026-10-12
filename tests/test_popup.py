import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from linux_audio_output_switcher.config import PopupConfig
from linux_audio_output_switcher.popup import (
    MAX_UPDATE_BYTES,
    POPUP_HANDED_OVER_STATUS,
    POPUP_OVERTAKEN_STATUS,
    PopupExit,
    UpdateOrder,
    _acquire_launch_lock,
    _claim_popup,
    _close_popup_with_handoff,
    _decode_payload,
    _encode_payload,
    _payload,
    _receive_accepted_update,
    _receive_latest,
    _remove_stale_socket,
    _send_update,
    _try_popup_instance_lock,
    _unlink_owned_socket,
    _write_owner_token,
    launch_popup,
)
from linux_audio_output_switcher.runtime import (
    GENERATION_NAME,
    acquire_action_lock,
    prepare_runtime_directory,
)


class PayloadTests(unittest.TestCase):
    def test_payload_round_trip(self):
        config = PopupConfig(duration_ms=2850, bottom_margin=180, all_monitors=True)
        payload = _payload(config, "Desk speakers", "speakers", 7)
        self.assertEqual(payload["generation"], 7)
        self.assertEqual(_decode_payload(_encode_payload(payload)), payload)

    def test_invalid_or_oversized_updates_are_rejected(self):
        self.assertIsNone(_decode_payload(b"not JSON"))
        self.assertIsNone(_decode_payload(json.dumps({"version": 2}).encode()))
        with self.assertRaisesRegex(ValueError, "too large"):
            _encode_payload(_payload(PopupConfig(), "x" * MAX_UPDATE_BYTES, "audio", 1))

    def test_update_without_valid_generation_is_rejected(self):
        payload = _payload(PopupConfig(), "Speakers", "speakers", 3)
        for generation in (None, -1, True, "3", 2.0):
            with self.subTest(generation=generation):
                broken = {**payload, "generation": generation}
                if generation is None:
                    del broken["generation"]
                self.assertIsNone(_decode_payload(json.dumps(broken).encode()))


class SocketTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "popup.sock"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.server.bind(str(self.path))
        self.server.setblocking(False)
        self.addCleanup(self.server.close)

    def send(self, message, generation=1):
        data = _encode_payload(_payload(PopupConfig(), message, "audio", generation))
        self.assertTrue(_send_update(self.path, data))

    def test_rapid_updates_keep_only_latest_message(self):
        self.send("First", 1)
        self.send("Second", 2)
        self.send("Latest", 3)
        self.assertEqual(_receive_latest(self.server)["message"], "Latest")

    def test_queued_update_from_newer_action_wins_even_if_it_arrived_first(self):
        self.send("Newer", 2)
        self.send("Older", 1)
        latest = _receive_latest(self.server)
        self.assertEqual((latest["message"], latest["generation"]), ("Newer", 2))

    def test_same_generation_keeps_the_last_update(self):
        self.send("First", 4)
        self.send("Second", 4)
        self.assertEqual(_receive_latest(self.server)["message"], "Second")

    def test_invalid_datagram_does_not_replace_latest_valid_update(self):
        self.send("Valid")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(client.close)
        client.sendto(b"invalid", str(self.path))
        self.assertEqual(_receive_latest(self.server)["message"], "Valid")

    def test_full_queue_drops_update_without_blocking_or_removing_socket(self):
        data = _encode_payload(_payload(PopupConfig(), "Queued", "audio", 1))
        while True:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            client.setblocking(False)
            try:
                client.sendto(data, str(self.path))
            except BlockingIOError:
                client.close()
                break
            client.close()
        self.assertTrue(_send_update(self.path, data))
        self.assertTrue(self.path.is_socket())

    def test_cleanup_does_not_unlink_a_replacement_socket(self):
        owner_path = Path(self.temporary.name) / "popup.owner"
        _write_owner_token(owner_path, "old")
        self.server.close()
        self.path.unlink()
        _write_owner_token(owner_path, "new")
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(replacement.close)
        replacement.bind(str(self.path))
        _unlink_owned_socket(self.path, owner_path, "old")
        self.assertTrue(self.path.exists())
        self.assertEqual(owner_path.read_text(), "new")

    def test_owner_can_remove_its_socket_and_token(self):
        owner_path = Path(self.temporary.name) / "popup.owner"
        _write_owner_token(owner_path, "mine")
        _unlink_owned_socket(self.path, owner_path, "mine")
        self.assertFalse(self.path.exists())
        self.assertFalse(owner_path.exists())

    def test_stale_owned_socket_can_be_removed(self):
        self.server.close()
        _remove_stale_socket(self.path)
        self.assertFalse(self.path.exists())

    def test_non_socket_path_is_never_removed(self):
        self.server.close()
        self.path.unlink()
        self.path.write_text("keep", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "unsafe popup socket"):
            _remove_stale_socket(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "keep")


def payload(message, generation):
    return _payload(PopupConfig(), message, "audio", generation)


class UpdateOrderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = prepare_runtime_directory(Path(self.temporary.name) / "runtime")
        self.order = UpdateOrder(self.directory)

    def test_older_action_never_replaces_a_newer_one(self):
        self.assertTrue(self.order.accept(payload("Speakers", 2)))
        self.assertFalse(self.order.accept(payload("Headset", 1)))
        self.assertEqual(self.order.latest, 2)

    def test_same_or_newer_generation_is_shown(self):
        self.assertTrue(self.order.accept(payload("Headset", 1)))
        self.assertTrue(self.order.accept(payload("Warning", 1)))
        self.assertTrue(self.order.accept(payload("Speakers", 2)))

    def test_fresh_popup_rejects_a_result_older_than_the_last_action(self):
        # A fresh popup receives a label from an overtaken switch.
        with acquire_action_lock(self.directory) as first:
            pass
        with acquire_action_lock(self.directory) as second:
            pass
        self.assertEqual((first.generation, second.generation), (1, 2))
        self.assertFalse(self.order.accept(payload("Headset", 1)))
        self.assertTrue(self.order.accept(payload("Speakers", 2)))

    def test_unreadable_counter_does_not_block_updates(self):
        (self.directory / GENERATION_NAME).write_text("not a number", encoding="utf-8")
        self.assertTrue(self.order.accept(payload("Speakers", 1)))

    def test_expiry_drain_keeps_an_update_queued_before_close(self):
        path = self.directory / "popup.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(server.close)
        server.bind(str(path))
        server.setblocking(False)
        self.assertTrue(_send_update(path, _encode_payload(payload("Speakers", 1))))

        lock_descriptor = _acquire_launch_lock(self.directory)
        try:
            update = _receive_accepted_update(server, self.order)
        finally:
            os.close(lock_descriptor)

        self.assertEqual(update["message"], "Speakers")



class PopupClaimTests(unittest.TestCase):
    """Take popup ownership before loading GTK, or exit."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = prepare_runtime_directory(Path(self.temporary.name) / "runtime")
        self.socket_path = self.directory / "popup.sock"

    def generation(self):
        with acquire_action_lock(self.directory) as lock:
            return lock.generation

    def hold_display(self):
        # Another process holds the instance lock and update socket.
        descriptor = _try_popup_instance_lock(self.directory)
        self.assertIsNotNone(descriptor)
        self.addCleanup(os.close, descriptor)
        return descriptor

    def bind_display_socket(self):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        server.bind(str(self.socket_path))
        server.setblocking(False)
        self.addCleanup(server.close)
        return server

    def test_first_popup_owns_the_display(self):
        claim = _claim_popup(self.directory, payload("Headset", self.generation()))
        self.addCleanup(claim.server.close)
        lock_closed = []

        def release_lock():
            if not lock_closed:
                lock_closed.append(True)
                os.close(claim.lock_descriptor)

        self.addCleanup(release_lock)
        self.assertTrue(self.socket_path.is_socket())
        self.assertEqual(claim.order.latest, 1)
        # Keep the lock while the popup lives.
        self.assertIsNone(_try_popup_instance_lock(self.directory))
        # Release it at exit.
        release_lock()
        descriptor = _try_popup_instance_lock(self.directory)
        self.assertIsNotNone(descriptor)
        os.close(descriptor)

    def test_stale_initial_update_exits_without_binding(self):
        older = self.generation()
        self.generation()
        with self.assertRaises(PopupExit) as raised:
            _claim_popup(self.directory, payload("Headset", older))
        self.assertEqual(raised.exception.status, POPUP_OVERTAKEN_STATUS)
        self.assertFalse(self.socket_path.exists())
        # Ownership is free again.
        descriptor = _try_popup_instance_lock(self.directory)
        self.assertIsNotNone(descriptor)
        os.close(descriptor)

    def test_update_is_handed_to_the_popup_owning_the_display(self):
        self.hold_display()
        owner = self.bind_display_socket()
        generation = self.generation()
        with self.assertRaises(PopupExit) as raised:
            _claim_popup(self.directory, payload("Speakers", generation))
        self.assertEqual(raised.exception.status, POPUP_HANDED_OVER_STATUS)
        received = _receive_latest(owner)
        self.assertEqual((received["message"], received["generation"]), ("Speakers", generation))

    def test_hand_over_waits_for_the_owner_to_bind(self):
        self.hold_display()
        generation = self.generation()
        received = []

        def bind_later():
            time.sleep(0.05)
            owner = self.bind_display_socket()
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                update = _receive_latest(owner)
                if update is not None:
                    received.append(update["message"])
                    return
                time.sleep(0.01)

        thread = threading.Thread(target=bind_later, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5.0)
        with self.assertRaises(PopupExit) as raised:
            _claim_popup(self.directory, payload("Speakers", generation), handover_timeout=5.0)
        self.assertEqual(raised.exception.status, POPUP_HANDED_OVER_STATUS)
        thread.join(5.0)
        self.assertEqual(received, ["Speakers"])

    def test_hand_over_gives_up_when_the_owner_never_binds(self):
        self.hold_display()
        with self.assertRaises(PopupExit) as raised:
            _claim_popup(
                self.directory, payload("Speakers", self.generation()), handover_timeout=0.05
            )
        self.assertEqual(raised.exception.status, 1)
        self.assertFalse(self.socket_path.exists())

    def test_waiting_popup_takes_over_when_the_old_owner_releases(self):
        old_descriptor = _try_popup_instance_lock(self.directory)
        self.assertIsNotNone(old_descriptor)

        def release_old_owner():
            time.sleep(0.05)
            os.close(old_descriptor)

        thread = threading.Thread(target=release_old_owner)
        thread.start()
        self.addCleanup(thread.join, 2.0)

        claim = _claim_popup(
            self.directory,
            payload("Speakers", self.generation()),
            handover_timeout=1.0,
        )
        self.addCleanup(claim.server.close)
        self.addCleanup(os.close, claim.lock_descriptor)
        thread.join(2.0)

        self.assertFalse(thread.is_alive())
        self.assertTrue(self.socket_path.is_socket())
        self.assertEqual(claim.order.latest, 1)

    def test_general_shutdown_hands_off_an_update_queued_before_close(self):
        first_generation = self.generation()
        claim = _claim_popup(self.directory, payload("Headset", first_generation))
        instance_open = [True]

        def release_instance():
            if instance_open[0]:
                os.close(claim.lock_descriptor)
                instance_open[0] = False

        self.addCleanup(release_instance)
        self.addCleanup(claim.server.close)
        second_generation = self.generation()
        sent = threading.Event()
        let_sender_finish = threading.Event()
        shutdown_waiting = threading.Event()
        handoffs = []
        failures = []
        real_send = _send_update
        real_acquire = _acquire_launch_lock

        def paused_send(path, data):
            result = real_send(path, data)
            sent.set()
            let_sender_finish.wait(2.0)
            return result

        def observed_acquire(directory):
            if threading.current_thread().name == "popup-shutdown":
                shutdown_waiting.set()
            return real_acquire(directory)

        def send_update():
            try:
                launch_popup(
                    PopupConfig(), "Speakers", "speakers", second_generation
                )
            except BaseException as error:
                failures.append(error)

        def shutdown():
            try:
                _close_popup_with_handoff(
                    self.directory,
                    claim.server,
                    claim.order,
                    self.socket_path,
                    claim.owner_path,
                    claim.owner_token,
                    release_instance,
                    handoffs.append,
                )
            except BaseException as error:
                failures.append(error)

        with patch(
            "linux_audio_output_switcher.popup.prepare_runtime_directory",
            return_value=self.directory,
        ), patch(
            "linux_audio_output_switcher.popup.popup_environment", return_value={}
        ), patch(
            "linux_audio_output_switcher.popup._send_update", side_effect=paused_send
        ), patch(
            "linux_audio_output_switcher.popup._acquire_launch_lock",
            side_effect=observed_acquire,
        ):
            sender = threading.Thread(target=send_update, name="popup-sender")
            closer = threading.Thread(target=shutdown, name="popup-shutdown")
            sender.start()
            self.assertTrue(sent.wait(1.0))
            closer.start()
            self.assertTrue(shutdown_waiting.wait(1.0))
            self.assertTrue(closer.is_alive())
            let_sender_finish.set()
            sender.join(2.0)
            closer.join(2.0)

        self.assertFalse(sender.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(
            [(item["message"], item["generation"]) for item in handoffs],
            [("Speakers", second_generation)],
        )
        self.assertFalse(self.socket_path.exists())


class LaunchPopupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        base = Path(self.temporary.name) / "runtime"
        base.mkdir(mode=0o700)
        environment = patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(base)})
        environment.start()
        self.addCleanup(environment.stop)
        wayland = patch("linux_audio_output_switcher.popup.popup_environment", return_value={})
        wayland.start()
        self.addCleanup(wayland.stop)
        self.directory = prepare_runtime_directory()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.server.bind(str(self.directory / "popup.sock"))
        self.server.setblocking(False)
        self.addCleanup(self.server.close)

    def test_result_of_the_latest_action_is_sent(self):
        with acquire_action_lock(self.directory) as lock:
            pass
        launch_popup(PopupConfig(), "Speakers", "speakers", lock.generation)
        latest = _receive_latest(self.server)
        self.assertEqual((latest["message"], latest["generation"]), ("Speakers", 1))

    def test_result_overtaken_by_a_newer_action_is_not_sent(self):
        with acquire_action_lock(self.directory) as older:
            pass
        with acquire_action_lock(self.directory):
            pass
        launch_popup(PopupConfig(), "Headset", "headphones", older.generation)
        self.assertIsNone(_receive_latest(self.server))

    def test_update_waits_for_expiry_and_starts_a_replacement(self):
        lock_descriptor = _acquire_launch_lock(self.directory)
        attempted = threading.Event()
        real_acquire = _acquire_launch_lock

        def announced_acquire(directory):
            attempted.set()
            return real_acquire(directory)

        class ExitedProcess:
            def poll(self):
                return 1

        errors = io.StringIO()
        with patch(
            "linux_audio_output_switcher.popup._acquire_launch_lock",
            side_effect=announced_acquire,
        ), patch(
            "linux_audio_output_switcher.popup.subprocess.Popen",
            return_value=ExitedProcess(),
        ) as popen, redirect_stderr(errors):
            thread = threading.Thread(
                target=launch_popup,
                args=(PopupConfig(), "Speakers", "speakers", 1),
            )
            thread.start()
            self.assertTrue(attempted.wait(1.0))
            self.assertTrue(thread.is_alive())

            self.server.close()
            (self.directory / "popup.sock").unlink()
            os.close(lock_descriptor)

            thread.join(2.0)
            self.assertFalse(thread.is_alive())
        popen.assert_called_once()
        self.assertIn("did not start", errors.getvalue())

    def test_newer_update_wins_while_first_popup_start_is_slow(self):
        self.server.close()
        (self.directory / "popup.sock").unlink()
        with acquire_action_lock(self.directory) as first:
            pass

        first_starting = threading.Event()
        allow_first_start = threading.Event()
        newer_owned = threading.Event()
        claims = []
        failures = []

        class Process:
            def __init__(self, status):
                self.status = status

            def poll(self):
                return self.status

        def start_popup(data, _environment):
            update = _decode_payload(data)
            self.assertIsNotNone(update)
            if update["generation"] == first.generation:
                first_starting.set()
                if not allow_first_start.wait(2.0):
                    raise AssertionError("first popup start was not released")
            try:
                claim = _claim_popup(self.directory, update)
            except PopupExit as exit_request:
                return Process(exit_request.status)
            claims.append(claim)
            newer_owned.set()
            return Process(None)

        def launch(message, icon, generation):
            try:
                launch_popup(PopupConfig(), message, icon, generation)
            except BaseException as error:
                failures.append(error)

        first_thread = threading.Thread(
            target=launch,
            args=("Headset", "headphones", first.generation),
        )
        second_thread = None
        with patch(
            "linux_audio_output_switcher.popup._start_popup_process",
            side_effect=start_popup,
        ):
            first_thread.start()
            self.assertTrue(first_starting.wait(1.0))
            with acquire_action_lock(self.directory) as second:
                pass
            second_thread = threading.Thread(
                target=launch,
                args=("Speakers", "speakers", second.generation),
            )
            second_thread.start()
            try:
                self.assertTrue(newer_owned.wait(1.0))
            finally:
                allow_first_start.set()
            first_thread.join(2.0)
            second_thread.join(2.0)

        self.assertFalse(first_thread.is_alive())
        self.assertFalse(second_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.addCleanup(claim.server.close)
        self.addCleanup(os.close, claim.lock_descriptor)
        self.assertEqual(claim.order.latest, second.generation)
        self.assertIsNone(_receive_accepted_update(claim.server, claim.order))
        self.assertIsNone(_try_popup_instance_lock(self.directory))

    def test_newer_update_wins_during_slow_shutdown_replacement(self):
        self.server.close()
        (self.directory / "popup.sock").unlink()
        with acquire_action_lock(self.directory) as first:
            pass
        old_claim = _claim_popup(
            self.directory, payload("Headset", first.generation)
        )
        instance_open = [True]

        def release_old_instance():
            if instance_open[0]:
                os.close(old_claim.lock_descriptor)
                instance_open[0] = False

        self.addCleanup(release_old_instance)
        self.addCleanup(old_claim.server.close)
        with acquire_action_lock(self.directory) as replacement:
            pass
        self.assertTrue(
            _send_update(
                self.directory / "popup.sock",
                _encode_payload(payload("Speakers", replacement.generation)),
            )
        )

        replacement_starting = threading.Event()
        allow_replacement = threading.Event()
        newer_owned = threading.Event()
        claims = []
        handoff_statuses = []
        failures = []

        class Process:
            def __init__(self, status):
                self.status = status

            def poll(self):
                return self.status

        def slow_handoff(update):
            replacement_starting.set()
            if not allow_replacement.wait(2.0):
                raise AssertionError("shutdown replacement was not released")
            try:
                claim = _claim_popup(self.directory, update)
            except PopupExit as exit_request:
                handoff_statuses.append(exit_request.status)
            else:
                claims.append(claim)

        def start_popup(data, _environment):
            update = _decode_payload(data)
            self.assertIsNotNone(update)
            try:
                claim = _claim_popup(self.directory, update)
            except PopupExit as exit_request:
                return Process(exit_request.status)
            claims.append(claim)
            newer_owned.set()
            return Process(None)

        def shutdown():
            try:
                _close_popup_with_handoff(
                    self.directory,
                    old_claim.server,
                    old_claim.order,
                    self.directory / "popup.sock",
                    old_claim.owner_path,
                    old_claim.owner_token,
                    release_old_instance,
                    slow_handoff,
                )
            except BaseException as error:
                failures.append(error)

        def launch_newer(generation):
            try:
                launch_popup(PopupConfig(), "HDMI", "audio", generation)
            except BaseException as error:
                failures.append(error)

        shutdown_thread = threading.Thread(target=shutdown)
        newer_thread = None
        with patch(
            "linux_audio_output_switcher.popup._start_popup_process",
            side_effect=start_popup,
        ):
            shutdown_thread.start()
            self.assertTrue(replacement_starting.wait(1.0))
            with acquire_action_lock(self.directory) as newer:
                pass
            newer_thread = threading.Thread(
                target=launch_newer, args=(newer.generation,)
            )
            newer_thread.start()
            try:
                self.assertTrue(newer_owned.wait(1.0))
            finally:
                allow_replacement.set()
            newer_thread.join(2.0)
            shutdown_thread.join(2.0)

        self.assertFalse(newer_thread.is_alive())
        self.assertFalse(shutdown_thread.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(handoff_statuses, [POPUP_HANDED_OVER_STATUS])
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.addCleanup(claim.server.close)
        self.addCleanup(os.close, claim.lock_descriptor)
        self.assertEqual(claim.order.latest, newer.generation)
        self.assertIsNone(_receive_accepted_update(claim.server, claim.order))
        self.assertIsNone(_try_popup_instance_lock(self.directory))

    def launch_without_running_popup(self, exit_status):
        # Fake a process that exits before binding its update socket.
        self.server.close()
        (self.directory / "popup.sock").unlink(missing_ok=True)

        class ExitedProcess:
            def poll(self):
                return exit_status

        errors = io.StringIO()
        with patch(
            "linux_audio_output_switcher.popup.subprocess.Popen",
            return_value=ExitedProcess(),
        ) as popen, redirect_stderr(errors):
            launch_popup(PopupConfig(), "Speakers", "speakers", 1)
        popen.assert_called_once()
        return errors.getvalue()

    def test_expected_popup_exits_are_quiet(self):
        for status in (POPUP_OVERTAKEN_STATUS, POPUP_HANDED_OVER_STATUS):
            with self.subTest(status=status):
                self.assertEqual(self.launch_without_running_popup(status), "")

    def test_popup_process_that_fails_to_start_is_reported(self):
        self.assertIn("did not start", self.launch_without_running_popup(1))

    def test_popup_process_still_starting_is_not_reported_as_failed(self):
        with patch("linux_audio_output_switcher.popup.STARTUP_WAIT_SECONDS", 0.05):
            errors = self.launch_without_running_popup(None)
        self.assertIn("still starting", errors)
        self.assertNotIn("did not start", errors)


if __name__ == "__main__":
    unittest.main()
