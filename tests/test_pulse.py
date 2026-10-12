import json
import unittest
from unittest.mock import Mock

from linux_audio_output_switcher.config import OutputConfig
from linux_audio_output_switcher.pulse import (
    AmbiguousOutputError,
    Sink,
    is_headphone_sink,
    parse_sink_inputs,
    parse_sinks,
    resolve_outputs,
    stable_match_for_sink,
    PulseClient,
    PulseCommandError,
)


class PulseTests(unittest.TestCase):
    def test_parse_sinks_and_inputs(self):
        sinks = parse_sinks(json.dumps([{
            "index": 4,
            "name": "usb-output",
            "description": "USB Headset",
            "properties": {"device.serial": "ABC"},
        }]))
        inputs = parse_sink_inputs(json.dumps([{"index": 9, "sink": 4}]))
        self.assertEqual(sinks[0].index, 4)
        self.assertEqual(sinks[0].properties["device.serial"], "ABC")
        self.assertEqual((inputs[0].index, inputs[0].sink), (9, 4))

    def test_non_object_properties_are_ignored(self):
        sinks = parse_sinks(json.dumps([{
            "index": 4, "name": "output", "properties": ["invalid"],
        }]))
        self.assertEqual(sinks[0].properties, {})

    def test_invalid_json_is_rejected_cleanly(self):
        with self.assertRaisesRegex(PulseCommandError, "invalid sink JSON"):
            parse_sinks("not JSON")

    def test_usb_match_uses_serial_and_profile(self):
        sink = Sink(4, "usb", "USB", {
            "device.serial": "ABC",
            "device.profile.name": "analog-stereo",
            "device.bus_path": "temporary-port",
        })
        self.assertEqual(stable_match_for_sink(sink), (
            ("device.profile.name", "analog-stereo"),
            ("device.serial", "ABC"),
        ))

    def test_bluetooth_match_uses_physical_address_not_profile(self):
        sink = Sink(5, "bluez", "Bluetooth", {
            "api.bluez5.address": "AA:BB:CC:DD:EE:FF",
            "api.bluez5.profile": "a2dp-sink",
            "device.serial": "bluez-device-serial",
            "device.profile.name": "a2dp-sink",
        })
        self.assertEqual(stable_match_for_sink(sink), (
            ("api.bluez5.address", "AA:BB:CC:DD:EE:FF"),
        ))

    def test_headphone_type_uses_device_metadata(self):
        bluetooth = Sink(5, "bluez", "Bluetooth Headset", {
            "device.form_factor": "headset",
            "device.icon_name": "audio-headset-bluetooth",
        })
        speakers = Sink(6, "hdmi", "HDMI output", {"device.form_factor": "speaker"})
        self.assertTrue(is_headphone_sink(bluetooth))
        self.assertFalse(is_headphone_sink(speakers))

    def test_hands_free_form_factor_is_private_audio(self):
        sink = Sink(5, "bluez", "Bluetooth Audio", {
            "device.form_factor": "hands-free",
        })
        self.assertTrue(is_headphone_sink(sink))

    def test_unknown_type_uses_icon_then_description(self):
        icon = Sink(5, "one", "Unknown", {"api.bluez5.icon": "audio-headphones"})
        description = Sink(6, "two", "USB Earbuds", {})
        unknown = Sink(7, "three", "USB Audio", {})
        self.assertTrue(is_headphone_sink(icon))
        self.assertTrue(is_headphone_sink(description))
        self.assertFalse(is_headphone_sink(unknown))

    def test_known_speaker_form_overrides_misleading_icon(self):
        sink = Sink(7, "speaker", "Speaker", {
            "device.form_factor": "speaker",
            "device.icon_name": "audio-headset",
        })
        self.assertFalse(is_headphone_sink(sink))

    def test_matching_does_not_depend_on_runtime_index(self):
        config = (
            OutputConfig("usb", "USB", "audio", (("device.serial", "ABC"),)),
            OutputConfig("hdmi", "HDMI", "audio", (("device.bus_path", "pci-1"),)),
        )
        sinks = [
            Sink(77, "usb-new", "USB", {"device.serial": "ABC"}),
            Sink(93, "hdmi-new", "HDMI", {"device.bus_path": "pci-1"}),
        ]
        resolved = resolve_outputs(config, sinks)
        self.assertEqual(resolved["usb"].index, 77)
        self.assertEqual(resolved["hdmi"].index, 93)

    def test_ambiguous_match_is_rejected(self):
        config = (OutputConfig("usb", "USB", "audio", (("device.serial", "ABC"),)),)
        sinks = [
            Sink(1, "one", "One", {"device.serial": "ABC"}),
            Sink(2, "two", "Two", {"device.serial": "ABC"}),
        ]
        with self.assertRaises(AmbiguousOutputError):
            resolve_outputs(config, sinks)

    def test_set_sink_mute_uses_explicit_state(self):
        client = PulseClient()
        commands = []
        client.command = lambda *arguments: commands.append(arguments) or ""
        client.set_sink_mute("speaker-output", True)
        client.set_sink_mute("headset-output", False)
        self.assertEqual(commands, [
            ("set-sink-mute", "speaker-output", "1"),
            ("set-sink-mute", "headset-output", "0"),
        ])

    def test_json_support_is_checked_once_per_client(self):
        client = PulseClient()
        client.command = Mock(return_value="")
        client.require_json_support()
        client.require_json_support()
        client.command.assert_called_once_with("--format=json", "--help")

    def test_old_pactl_gets_clear_compatibility_error(self):
        client = PulseClient()
        client.command = Mock(side_effect=PulseCommandError("unknown option --format"))
        with self.assertRaisesRegex(PulseCommandError, "16.0 or newer"):
            client.require_json_support()

    def test_list_commands_check_json_support(self):
        client = PulseClient()
        client.command = Mock(side_effect=["", "[]", "[]"])
        self.assertEqual(client.list_sinks(), [])
        self.assertEqual(client.list_sink_inputs(), [])
        self.assertEqual(client.command.call_args_list, [
            unittest.mock.call("--format=json", "--help"),
            unittest.mock.call("--format=json", "list", "sinks"),
            unittest.mock.call("--format=json", "list", "sink-inputs"),
        ])


if __name__ == "__main__":
    unittest.main()
