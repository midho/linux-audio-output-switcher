import unittest

from linux_audio_output_switcher.config import OutputConfig, PopupConfig, Settings
from linux_audio_output_switcher.pulse import (
    PulseCommandError,
    Sink,
    SinkInput,
    stable_match_for_sink,
)
from linux_audio_output_switcher.switcher import OutputSwitcher, SwitchError


OUTPUT_ONE = OutputConfig("one", "Headset", "headphones", (("device.serial", "ONE"),))
OUTPUT_TWO = OutputConfig("two", "Speakers", "speakers", (("device.serial", "TWO"),))
OUTPUT_THREE = OutputConfig("three", "Other", "audio", (("device.serial", "THREE"),))


def settings(*outputs, move=True, auto=False):
    return Settings(1, move, 4.0, PopupConfig(enabled=False), tuple(outputs), auto)


class FakeClient:
    def __init__(self):
        self.sinks = [
            Sink(4, "sink-one", "Headset", {"device.serial": "ONE"}),
            Sink(8, "sink-two", "Speakers", {"device.serial": "TWO"}),
            Sink(12, "unmanaged", "Unmanaged", {"device.serial": "OTHER"}),
        ]
        self.default = "sink-one"
        self.inputs = [SinkInput(20, 4), SinkInput(21, 12)]
        self.moves = []
        self.mutes = []
        self.events = []
        self.fail_moves = set()
        self.remove_on_failed_move = set()

    def list_sinks(self):
        return list(self.sinks)

    def get_default_sink(self):
        return self.default

    def set_default_sink(self, name):
        self.events.append(("default", name))
        self.default = name

    def set_sink_mute(self, name, muted):
        self.events.append(("mute", name, muted))
        self.mutes.append((name, muted))

    def list_sink_inputs(self):
        return list(self.inputs)

    def move_sink_input(self, index, name):
        if index in self.fail_moves:
            if index in self.remove_on_failed_move:
                self.inputs = [item for item in self.inputs if item.index != index]
            raise PulseCommandError("stream disappeared")
        self.moves.append((index, name))
        target = next(sink for sink in self.sinks if sink.name == name)
        self.inputs = [
            SinkInput(item.index, target.index if item.index == index else item.sink)
            for item in self.inputs
        ]


class SwitcherTests(unittest.TestCase):
    def test_next_changes_default_and_moves_only_managed_streams(self):
        client = FakeClient()
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO), client).choose()
        self.assertEqual(result.output_id, "two")
        self.assertEqual(client.default, "sink-two")
        self.assertEqual(client.moves, [(20, "sink-two")])
        self.assertEqual(next(item for item in client.inputs if item.index == 21).sink, 12)

    def test_previous_from_unmanaged_selects_last_available(self):
        client = FakeClient()
        client.default = "unmanaged"
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO), client).choose(-1)
        self.assertEqual(result.output_id, "two")

    def test_disconnected_output_is_skipped_with_three_configured(self):
        client = FakeClient()
        result = OutputSwitcher(
            settings(OUTPUT_ONE, OUTPUT_THREE, OUTPUT_TWO), client
        ).choose()
        self.assertEqual(result.output_id, "two")

    def test_fewer_than_two_connected_outputs_is_safe(self):
        client = FakeClient()
        client.sinks = client.sinks[:1]
        with self.assertRaisesRegex(SwitchError, "at least two"):
            OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO), client).choose()
        self.assertEqual(client.default, "sink-one")

    def test_direct_set_requires_connected_output(self):
        client = FakeClient()
        with self.assertRaisesRegex(SwitchError, "not connected"):
            OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_THREE), client).choose(
                output_id="three"
            )

    def test_move_can_be_disabled(self):
        client = FakeClient()
        OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO, move=False), client).choose()
        self.assertEqual(client.moves, [])

    def test_output_can_be_muted_before_it_becomes_default(self):
        client = FakeClient()
        speakers = OutputConfig(
            "two", "Speakers", "speakers", (("device.serial", "TWO"),), "mute"
        )
        OutputSwitcher(settings(OUTPUT_ONE, speakers), client).choose()
        self.assertEqual(client.mutes, [("sink-two", True)])
        self.assertEqual(client.events[:2], [
            ("mute", "sink-two", True),
            ("default", "sink-two"),
        ])

    def test_output_can_be_unmuted_when_selected_directly(self):
        client = FakeClient()
        headset = OutputConfig(
            "one", "Headset", "headphones", (("device.serial", "ONE"),), "unmute"
        )
        client.default = "sink-two"
        OutputSwitcher(settings(headset, OUTPUT_TWO), client).choose(output_id="one")
        self.assertEqual(client.mutes, [("sink-one", False)])

    def test_failed_move_warns_when_stream_still_exists(self):
        client = FakeClient()
        client.fail_moves.add(20)
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO), client).choose()
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("stream 20", result.warnings[0])

    def test_stream_ending_during_move_does_not_warn(self):
        client = FakeClient()
        client.fail_moves.add(20)
        client.remove_on_failed_move.add(20)
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO), client).choose()
        self.assertEqual(result.warnings, ())

    def test_unconfirmed_default_does_not_move_streams(self):
        client = FakeClient()
        client.set_default_sink = lambda _name: None
        with self.assertRaisesRegex(SwitchError, "did not select"):
            OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO), client).choose()
        self.assertEqual(client.default, "sink-one")
        self.assertEqual(client.moves, [])

    def test_show_does_not_change_routing(self):
        client = FakeClient()
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO), client).current_result()
        self.assertEqual(result.output_id, "one")
        self.assertEqual(client.default, "sink-one")
        self.assertEqual(client.moves, [])

    def test_new_headset_joins_and_unmutes_before_switch(self):
        client = FakeClient()
        client.sinks = client.sinks[:2] + [Sink(16, "bluetooth", "Bluetooth Headset", {
            "api.bluez5.address": "AA:BB:CC:DD:EE:FF",
            "device.form_factor": "headset",
        })]
        client.default = "sink-two"
        client.inputs = [SinkInput(20, 8)]
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO, auto=True), client).choose()
        self.assertEqual(result.output_id, "bluetooth-headset")
        self.assertEqual(client.default, "bluetooth")
        self.assertEqual(client.events[:2], [("mute", "bluetooth", False), ("default", "bluetooth")])
        self.assertEqual(client.moves, [(20, "bluetooth")])

    def test_new_non_headset_is_muted_before_switch(self):
        client = FakeClient()
        client.default = "sink-two"
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO, auto=True), client).choose()
        self.assertEqual(result.output_id, "unmanaged")
        self.assertEqual(client.events[:2], [
            ("mute", "unmanaged", True), ("default", "unmanaged")
        ])

    def test_disconnected_saved_output_is_skipped(self):
        client = FakeClient()
        client.sinks = client.sinks[1:]
        client.default = "sink-two"
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO, auto=True), client).choose()
        self.assertEqual(result.output_id, "unmanaged")

    def test_initial_exclusion_stays_out_when_auto_is_on(self):
        client = FakeClient()
        excluded = OutputConfig(
            "other", "Unmanaged", "audio", (("device.serial", "OTHER"),), include=False
        )
        client.default = "sink-two"
        switcher = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO, excluded, auto=True), client)
        self.assertEqual(switcher.choose().output_id, "one")
        with self.assertRaisesRegex(SwitchError, "excluded"):
            switcher.choose(output_id="other")

    def test_saved_bluetooth_settings_survive_a_profile_change(self):
        address = "AA:BB:CC:DD:EE:FF"
        a2dp = Sink(16, "bluez-a2dp", "Raw device name", {
            "api.bluez5.address": address,
            "api.bluez5.profile": "a2dp-sink",
        })
        saved = OutputConfig(
            "travel-headset",
            "My travel headset",
            "headphones",
            (*stable_match_for_sink(a2dp), ("api.bluez5.profile", "a2dp-sink")),
            "unmute",
        )
        client = FakeClient()
        client.sinks = client.sinks[:2] + [Sink(
            17,
            "bluez-hfp",
            "Changed profile label",
            {
                "api.bluez5.address": address,
                "api.bluez5.profile": "headset-head-unit",
                "device.form_factor": "hands-free",
            },
        )]
        client.default = "sink-two"
        client.inputs = [SinkInput(20, 8)]

        result = OutputSwitcher(
            settings(OUTPUT_ONE, OUTPUT_TWO, saved, auto=True), client
        ).choose()

        self.assertEqual((result.output_id, result.label), (
            "travel-headset", "My travel headset"
        ))
        self.assertEqual(client.events[:2], [
            ("mute", "bluez-hfp", False),
            ("default", "bluez-hfp"),
        ])

    def test_excluded_bluetooth_device_stays_excluded_after_profile_change(self):
        excluded = OutputConfig(
            "travel-headset",
            "My travel headset",
            "headphones",
            (("api.bluez5.address", "AA:BB:CC:DD:EE:FF"),),
            "unmute",
            False,
        )
        client = FakeClient()
        client.sinks.append(Sink(16, "bluez-hfp", "Bluetooth Audio", {
            "api.bluez5.address": "AA:BB:CC:DD:EE:FF",
            "api.bluez5.profile": "headset-head-unit",
        }))
        switcher = OutputSwitcher(
            settings(OUTPUT_ONE, OUTPUT_TWO, excluded, auto=True), client
        )

        _, available, resolved = switcher.connected()

        self.assertIn("travel-headset", resolved)
        self.assertNotIn("travel-headset", [output.id for output in available])
        self.assertNotIn("bluetooth-audio", [output.id for output in available])

    def test_two_bluetooth_devices_keep_separate_matches(self):
        client = FakeClient()
        client.sinks = client.sinks[:2] + [
            Sink(16, "bluez-one", "Bluetooth Audio", {
                "api.bluez5.address": "AA:BB:CC:DD:EE:01",
                "api.bluez5.profile": "a2dp-sink",
            }),
            Sink(17, "bluez-two", "Bluetooth Audio", {
                "api.bluez5.address": "AA:BB:CC:DD:EE:02",
                "api.bluez5.profile": "headset-head-unit",
            }),
        ]

        _, available, resolved = OutputSwitcher(
            settings(OUTPUT_ONE, OUTPUT_TWO, auto=True), client
        ).connected()
        automatic = available[2:]

        self.assertEqual([output.id for output in automatic], [
            "bluetooth-audio", "bluetooth-audio-2"
        ])
        self.assertNotEqual(automatic[0].match, automatic[1].match)
        self.assertEqual(
            {resolved[output.id].index for output in automatic},
            {16, 17},
        )

    def test_auto_output_can_be_selected_directly(self):
        client = FakeClient()
        result = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO, auto=True), client).choose(
            output_id="unmanaged"
        )
        self.assertEqual(result.output_id, "unmanaged")

    def test_auto_outputs_have_stable_order_when_sink_order_changes(self):
        client = FakeClient()
        client.sinks.append(Sink(16, "a", "Alpha Audio", {"device.serial": "ALPHA"}))
        switcher = OutputSwitcher(settings(OUTPUT_ONE, OUTPUT_TWO, auto=True), client)
        _, first, _ = switcher.connected()
        client.sinks.reverse()
        _, second, _ = switcher.connected()
        self.assertEqual([item.id for item in first], [item.id for item in second])
        self.assertEqual([item.id for item in first][-2:], ["alpha-audio", "unmanaged"])


if __name__ == "__main__":
    unittest.main()
