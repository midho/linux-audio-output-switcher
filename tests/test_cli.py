import io
import os
import socket
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from linux_audio_output_switcher import cli
from linux_audio_output_switcher.cli import (
    configs_from_sinks,
    configure,
    doctor,
    run_action,
    select_sinks,
)
from linux_audio_output_switcher.config import (
    ConfigError,
    OutputConfig,
    PopupConfig,
    load_config,
    render_config,
)
from linux_audio_output_switcher.popup import UpdateOrder, _payload, _receive_latest
from linux_audio_output_switcher.pulse import PulseCommandError, Sink
from linux_audio_output_switcher.runtime import (
    acquire_action_lock,
    prepare_runtime_directory,
)


SINKS = [
    Sink(4, "usb-output", "USB Headset", {
        "device.serial": "ABC",
        "device.profile.name": "analog-stereo",
    }),
    Sink(9, "hdmi-output", "HDMI Speakers", {
        "device.bus_path": "pci-1",
        "device.profile.name": "hdmi-stereo",
    }),
]


class FakeClient:
    def get_default_sink(self):
        return "usb-output"

    def list_sinks(self):
        return list(SINKS)


class CliTests(unittest.TestCase):
    def test_noninteractive_selection_uses_exact_sink_names(self):
        selected = select_sinks(
            SINKS, "usb-output", ["hdmi-output", "usb-output"]
        )
        self.assertEqual([sink.name for sink in selected], ["hdmi-output", "usb-output"])

    def test_unknown_requested_sink_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "not found"):
            select_sinks(SINKS, "usb-output", ["missing", "usb-output"])

    def test_generated_configs_use_stable_properties(self):
        outputs = configs_from_sinks(SINKS)
        self.assertEqual(outputs[0].match, (
            ("device.profile.name", "analog-stereo"),
            ("device.serial", "ABC"),
        ))
        self.assertEqual(outputs[1].match, (
            ("device.bus_path", "pci-1"),
            ("device.profile.name", "hdmi-stereo"),
        ))
        self.assertEqual([item.mute_on_activate for item in outputs], ["unmute", "mute"])

    def test_all_current_sinks_can_be_selected_without_a_terminal(self):
        self.assertEqual(
            select_sinks(SINKS, "usb-output", None, select_all=True), SINKS
        )

    def test_blank_interactive_selection_includes_every_current_sink(self):
        with patch.object(cli.sys, "stdin") as stdin, \
             patch("builtins.input", return_value=""), redirect_stdout(io.StringIO()):
            stdin.isatty.return_value = True
            selected = select_sinks(SINKS, "usb-output", None)
        self.assertEqual(selected, SINKS)

    def test_setup_saves_initial_exclusion_and_enables_new_devices(self):
        third = Sink(12, "bt-output", "Bluetooth Headset", {
            "api.bluez5.address": "AA:BB:CC:DD:EE:FF",
            "device.form_factor": "headset",
        })

        class ThreeSinkClient(FakeClient):
            def list_sinks(self):
                return [*SINKS, third]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            with patch("linux_audio_output_switcher.cli.PulseClient", return_value=ThreeSinkClient()):
                with redirect_stdout(io.StringIO()):
                    status = configure(path, False, ["usb-output", "hdmi-output"])
            settings = load_config(path)
        self.assertEqual(status, 0)
        self.assertTrue(settings.auto_include_new)
        self.assertEqual([item.include for item in settings.outputs], [True, True, False])
        self.assertEqual(settings.outputs[2].label, "Bluetooth Headset")

    def test_doctor_reports_unsupported_client_before_server_check(self):
        class UnsupportedClient:
            def __init__(self, executable):
                self.executable = executable

            def version(self):
                return "pactl 15.0"

            def require_json_support(self):
                raise PulseCommandError(
                    "this pactl client does not support JSON output. pactl 16.0 or newer is required"
                )

            def server_info(self):
                raise AssertionError("server must not be contacted")

        output = io.StringIO()
        with patch("linux_audio_output_switcher.cli.shutil.which", return_value="/test/pactl"), \
             patch("linux_audio_output_switcher.cli.PulseClient", UnsupportedClient), \
             redirect_stdout(output):
            status = doctor(Path("missing.toml"))
        self.assertEqual(status, 1)
        self.assertIn("PASS: pactl 15.0", output.getvalue())
        self.assertIn("FAIL: this pactl client does not support JSON output", output.getvalue())

    def test_doctor_distinguishes_server_connection_failure(self):
        class OfflineClient:
            def __init__(self, executable):
                self.executable = executable

            def version(self):
                return "pactl 16.1"

            def require_json_support(self):
                return None

            def server_info(self):
                raise PulseCommandError("Connection failure: Connection refused")

        output = io.StringIO()
        with patch("linux_audio_output_switcher.cli.shutil.which", return_value="/test/pactl"), \
             patch("linux_audio_output_switcher.cli.PulseClient", OfflineClient), \
             redirect_stdout(output):
            status = doctor(Path("missing.toml"))
        self.assertEqual(status, 1)
        self.assertIn("PASS: pactl JSON output is supported", output.getvalue())
        self.assertIn("FAIL: Connection failure", output.getvalue())


HEADSET = OutputConfig("one", "Headset", "headphones", (("device.serial", "ONE"),))
SPEAKERS = OutputConfig("two", "Speakers", "speakers", (("device.serial", "TWO"),))


class SwitchingFakeClient:
    def __init__(self):
        self.sinks = [
            Sink(4, "sink-one", "USB Headset", {"device.serial": "ONE"}),
            Sink(8, "sink-two", "Desk Speakers", {"device.serial": "TWO"}),
        ]
        self.default = "sink-two"

    def list_sinks(self):
        return list(self.sinks)

    def get_default_sink(self):
        return self.default

    def set_default_sink(self, name):
        self.default = name

    def set_sink_mute(self, _name, _muted):
        return None

    def list_sink_inputs(self):
        return []


class PopupOrderingTests(unittest.TestCase):
    """Old command results must not replace a newer popup."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        base = root / "runtime"
        base.mkdir(mode=0o700)
        self.enterContext(patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(base)}))
        # Use a bound socket as the popup and a fake Wayland session.
        self.enterContext(
            patch("linux_audio_output_switcher.popup.popup_environment", return_value={})
        )
        self.directory = prepare_runtime_directory()
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.server.bind(str(self.directory / "popup.sock"))
        self.server.setblocking(False)
        self.addCleanup(self.server.close)

        self.config_path = root / "config.toml"
        self.config_path.write_text(render_config([HEADSET, SPEAKERS]), encoding="utf-8")
        self.client = SwitchingFakeClient()
        self.enterContext(
            patch("linux_audio_output_switcher.cli.PulseClient", lambda timeout: self.client)
        )

        # Pause the first popup after the old switch releases its lock.
        self.first_publish_reached = threading.Event()
        self.resume_first_publish = threading.Event()
        self.publish_calls = []
        self.publish_guard = threading.Lock()
        real_launch_popup = cli.launch_popup

        def paced_launch_popup(config, message, icon, generation):
            with self.publish_guard:
                first = not self.publish_calls
                self.publish_calls.append((message, generation))
            if first:
                self.first_publish_reached.set()
                if not self.resume_first_publish.wait(5.0):
                    raise AssertionError("the paused publish was never resumed")
            real_launch_popup(config, message, icon, generation)

        self.enterContext(patch("linux_audio_output_switcher.cli.launch_popup", paced_launch_popup))
        self.addCleanup(self.resume_first_publish.set)
        self.output = io.StringIO()
        self.errors = io.StringIO()
        self.enterContext(redirect_stdout(self.output))
        self.enterContext(redirect_stderr(self.errors))

    def run_in_thread(self, *arguments):
        status = []
        thread = threading.Thread(
            target=lambda: status.append(run_action(self.config_path, *arguments)),
            daemon=True,
        )
        thread.start()
        self.addCleanup(self.finish_thread, thread)
        return thread, status

    def finish_thread(self, thread):
        # Always release the paused command before joining its thread.
        self.resume_first_publish.set()
        thread.join(5.0)

    def test_older_result_cannot_replace_newer_switch(self):
        older, older_status = self.run_in_thread("set", "one")
        self.assertTrue(self.first_publish_reached.wait(5.0))
        # Headset is selected, but its popup has not been sent yet.
        self.assertEqual(self.client.default, "sink-one")
        self.assertEqual(self.publish_calls, [("Headset", 1)])

        self.assertEqual(run_action(self.config_path, "set", "two"), 0)
        self.assertEqual(self.client.default, "sink-two")
        newer = _receive_latest(self.server)
        self.assertEqual((newer["message"], newer["generation"]), ("Speakers", 2))
        popup = UpdateOrder(self.directory)
        self.assertTrue(popup.accept(newer))

        self.resume_first_publish.set()
        older.join(5.0)
        self.assertEqual(older_status, [0])
        self.assertEqual(self.publish_calls, [("Headset", 1), ("Speakers", 2)])
        # The old command skips its popup after the new switch takes the lock.
        self.assertIsNone(_receive_latest(self.server))
        self.assertFalse(popup.accept(_payload(PopupConfig(), "Headset", "headphones", 1)))
        self.assertEqual(popup.latest, 2)
        self.assertEqual(self.client.default, "sink-two")

    def test_sequential_commands_publish_increasing_generations(self):
        self.resume_first_publish.set()
        for arguments in (("set", "one"), ("set", "two"), ("set", "one")):
            self.assertEqual(run_action(self.config_path, *arguments), 0)
        self.assertEqual(self.publish_calls, [("Headset", 1), ("Speakers", 2), ("Headset", 3)])
        latest = _receive_latest(self.server)
        self.assertEqual((latest["message"], latest["generation"]), ("Headset", 3))
        self.assertTrue(UpdateOrder(self.directory).accept(latest))
        self.assertEqual(self.client.default, "sink-one")

    def test_rejected_command_does_not_replace_the_current_popup(self):
        self.resume_first_publish.set()
        with acquire_action_lock() as held:
            self.assertEqual(run_action(self.config_path, "set", "one"), 1)
        self.assertEqual(self.client.default, "sink-two")
        self.assertEqual(held.generation, 1)
        self.assertEqual(self.publish_calls, [])
        warning = "another audio-output switch is already in progress"
        self.assertIn(warning, self.errors.getvalue())
        self.assertNotIn(warning, self.output.getvalue())


if __name__ == "__main__":
    unittest.main()
