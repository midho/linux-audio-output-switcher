import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

from linux_audio_output_switcher.config import (
    ConfigError,
    OutputConfig,
    load_config,
    parse_config,
    render_config,
    write_config,
)


OUTPUTS = [
    OutputConfig(
        "headset",
        "Headset",
        "headphones",
        (("device.serial", "SERIAL"),),
        "unmute",
    ),
    OutputConfig(
        "speakers",
        "Speakers",
        "speakers",
        (("device.bus_path", "pci-1"),),
        "mute",
    ),
]


class ConfigTests(unittest.TestCase):
    def test_rendered_config_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            write_config(path, render_config(OUTPUTS))
            loaded = load_config(path)
        self.assertEqual(loaded.outputs, tuple(OUTPUTS))
        self.assertTrue(loaded.move_playing_streams)
        self.assertTrue(loaded.auto_include_new)
        self.assertEqual((loaded.auto_headphones, loaded.auto_other), ("unmute", "mute"))
        self.assertFalse(loaded.popup.all_monitors)

    def test_legacy_config_keeps_fixed_selection(self):
        settings = parse_config({
            "version": 1,
            "outputs": [
                {"id": "one", "label": "One", "match": {"name": "one"}},
                {"id": "two", "label": "Two", "match": {"name": "two"}},
            ],
        })
        self.assertFalse(settings.auto_include_new)

    def test_auto_config_can_start_without_connected_outputs(self):
        settings = parse_config({"version": 1, "auto_include_new": True})
        self.assertEqual(settings.outputs, ())

    def test_exclusion_and_auto_rules_round_trip(self):
        excluded = OutputConfig("other", "Other", "audio", (("name", "other"),), include=False)
        settings = parse_config(tomllib.loads(render_config(
            [*OUTPUTS, excluded], auto_headphones="keep", auto_other="mute"
        )))
        self.assertFalse(settings.outputs[-1].include)
        self.assertEqual(settings.auto_headphones, "keep")

    def test_invalid_auto_settings_are_rejected(self):
        for data, message in (
            ({"auto_include_new": "yes"}, "auto_include_new"),
            ({"auto_include_new": True, "auto_headphones": "sometimes"}, "auto_headphones"),
            ({"auto_include_new": True, "auto_other": 1}, "auto_other"),
            ({"auto_include_new": True, "outputs": [{
                "id": "one", "label": "One", "match": {"name": "one"}, "include": "no"
            }]}, "include"),
        ):
            with self.subTest(data=data), self.assertRaisesRegex(ConfigError, message):
                parse_config({"version": 1, **data})

    def test_existing_config_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            write_config(path, render_config(OUTPUTS))
            with self.assertRaises(ConfigError):
                write_config(path, render_config(OUTPUTS))

    def test_read_failure_has_a_clear_config_error(self):
        path = Path("/example/config.toml")
        with patch.object(Path, "open", side_effect=PermissionError("permission denied")):
            with self.assertRaisesRegex(
                ConfigError, r"cannot read configuration .*permission denied"
            ):
                load_config(path)

    def test_write_failure_has_a_clear_config_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            with patch(
                "linux_audio_output_switcher.config.tempfile.NamedTemporaryFile",
                side_effect=OSError("read-only file system"),
            ):
                with self.assertRaisesRegex(
                    ConfigError, r"cannot write configuration .*read-only file system"
                ):
                    write_config(path, render_config(OUTPUTS))

    def test_two_outputs_are_required(self):
        with self.assertRaisesRegex(ConfigError, "at least two"):
            parse_config({
                "version": 1,
                "outputs": [{
                    "id": "one", "label": "One", "icon": "audio",
                    "match": {"name": "one"},
                }],
            })

    def test_duplicate_match_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "identical match"):
            parse_config({
                "version": 1,
                "outputs": [
                    {"id": "one", "label": "One", "match": {"name": "same"}},
                    {"id": "two", "label": "Two", "match": {"name": "same"}},
                ],
            })

    def test_non_text_icon_is_rejected_cleanly(self):
        with self.assertRaisesRegex(ConfigError, "icon"):
            parse_config({
                "version": 1,
                "outputs": [
                    {"id": "one", "label": "One", "icon": [], "match": {"name": "one"}},
                    {"id": "two", "label": "Two", "match": {"name": "two"}},
                ],
            })

    def test_invalid_mute_behavior_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "mute_on_activate"):
            parse_config({
                "version": 1,
                "outputs": [
                    {
                        "id": "one",
                        "label": "One",
                        "mute_on_activate": "sometimes",
                        "match": {"name": "one"},
                    },
                    {"id": "two", "label": "Two", "match": {"name": "two"}},
                ],
            })

    def test_all_monitor_popup_setting_is_loaded(self):
        settings = parse_config({
            "version": 1,
            "popup": {"all_monitors": True},
            "outputs": [
                {"id": "one", "label": "One", "match": {"name": "one"}},
                {"id": "two", "label": "Two", "match": {"name": "two"}},
            ],
        })
        self.assertTrue(settings.popup.all_monitors)

    def test_non_boolean_all_monitor_setting_is_rejected(self):
        with self.assertRaisesRegex(ConfigError, "all_monitors"):
            parse_config({
                "version": 1,
                "popup": {"all_monitors": "yes"},
                "outputs": [
                    {"id": "one", "label": "One", "match": {"name": "one"}},
                    {"id": "two", "label": "Two", "match": {"name": "two"}},
                ],
            })


if __name__ == "__main__":
    unittest.main()
