import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from linux_audio_output_switcher.config import OutputConfig, render_config


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT_ONE = OutputConfig(
    "one", "Headset", "headphones", (("device.serial", "ONE"),), "unmute"
)
OUTPUT_TWO = OutputConfig(
    "two", "Speakers", "speakers", (("device.serial", "TWO"),), "mute"
)


class PactlCommandFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.runtime = self.root / "runtime"
        self.bin.mkdir()
        self.runtime.mkdir(mode=0o700)
        self.state_path = self.root / "state.json"
        self.log_path = self.root / "commands.jsonl"
        self.config_path = self.root / "config.toml"
        self.write_state()
        config = render_config([OUTPUT_ONE, OUTPUT_TWO], auto_include_new=False)
        config = config.replace("pactl_timeout_seconds = 4.0", "pactl_timeout_seconds = 0.5")
        config = config.replace("enabled = true", "enabled = false", 1)
        self.config_path.write_text(config, encoding="utf-8")
        self.write_fake_pactl()
        self.environment = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "PYTHONPATH": str(PROJECT / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_RUNTIME_DIR": str(self.runtime),
        }

    def initial_state(self):
        return {
            "mode": "normal",
            "default": "sink-one",
            "mutes": {"sink-one": False, "sink-two": False},
            "sinks": [
                {
                    "index": 4,
                    "name": "sink-one",
                    "description": "USB Headset",
                    "properties": {
                        "device.serial": "ONE",
                        "device.profile.name": "analog-stereo",
                    },
                },
                {
                    "index": 8,
                    "name": "sink-two",
                    "description": "Desk Speakers",
                    "properties": {
                        "device.serial": "TWO",
                        "device.profile.name": "stereo-output",
                    },
                },
                {
                    "index": 12,
                    "name": "unmanaged",
                    "description": "Unmanaged Output",
                    "properties": {"device.serial": "OTHER"},
                },
            ],
            "inputs": [{"index": 20, "sink": 4}, {"index": 21, "sink": 12}],
        }

    def write_state(self, **changes):
        state = self.initial_state()
        state.update(changes)
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def read_state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def commands(self):
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def mutating_commands(self):
        names = {"set-sink-mute", "set-default-sink", "move-sink-input"}
        return [command for command in self.commands() if command and command[0] in names]

    def write_fake_pactl(self):
        program = self.bin / "pactl"
        program.write_text(textwrap.dedent(f"""\
            #!/usr/bin/env python3
            import json
            import pathlib
            import sys
            import time

            state_path = pathlib.Path({str(self.state_path)!r})
            log_path = pathlib.Path({str(self.log_path)!r})
            args = sys.argv[1:]
            with log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(args) + "\\n")
            state = json.loads(state_path.read_text(encoding="utf-8"))

            if args == ["--format=json", "--help"]:
                print('--format=FORMAT  Either "normal" or "json"')
                raise SystemExit(0)
            if args == ["--version"]:
                print("pactl 16.1")
                raise SystemExit(0)
            if args == ["info"]:
                print("Server Name: Test PulseAudio")
                raise SystemExit(0)
            if args == ["get-default-sink"]:
                print(state["default"])
                raise SystemExit(0)
            if args == ["--format=json", "list", "sinks"]:
                if state["mode"] == "malformed-sinks":
                    print("not JSON")
                    raise SystemExit(0)
                if state["mode"] == "sink-list-error":
                    print("Connection failure: test server unavailable", file=sys.stderr)
                    raise SystemExit(1)
                if state["mode"] == "sink-list-timeout":
                    time.sleep(2)
                print(json.dumps(state["sinks"]))
                raise SystemExit(0)
            if args == ["--format=json", "list", "sink-inputs"]:
                print(json.dumps(state["inputs"]))
                raise SystemExit(0)
            if len(args) == 3 and args[0] == "set-sink-mute":
                state["mutes"][args[1]] = args[2] == "1"
                state_path.write_text(json.dumps(state), encoding="utf-8")
                raise SystemExit(0)
            if len(args) == 2 and args[0] == "set-default-sink":
                state["default"] = args[1]
                state_path.write_text(json.dumps(state), encoding="utf-8")
                raise SystemExit(0)
            if len(args) == 3 and args[0] == "move-sink-input":
                target = next(sink for sink in state["sinks"] if sink["name"] == args[2])
                for item in state["inputs"]:
                    if item["index"] == int(args[1]):
                        item["sink"] = target["index"]
                state_path.write_text(json.dumps(state), encoding="utf-8")
                raise SystemExit(0)
            print("unsupported fake pactl command: " + " ".join(args), file=sys.stderr)
            raise SystemExit(2)
        """), encoding="utf-8")
        program.chmod(0o755)

    def run_cli(self, *arguments):
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "linux_audio_output_switcher",
                "--config",
                str(self.config_path),
                *arguments,
            ],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )

    def test_list_properties_uses_pactl_json_command(self):
        result = self.run_cli("list", "--properties")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("USB Headset", result.stdout)
        self.assertIn("device.serial = ONE", result.stdout)
        self.assertIn(["--format=json", "list", "sinks"], self.commands())
        self.assertEqual(self.mutating_commands(), [])

    def test_next_mutes_selects_and_moves_in_order(self):
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Speakers")
        state = self.read_state()
        self.assertEqual(state["default"], "sink-two")
        self.assertTrue(state["mutes"]["sink-two"])
        self.assertEqual(state["inputs"], [{"index": 20, "sink": 8}, {"index": 21, "sink": 12}])
        self.assertEqual(self.mutating_commands(), [
            ["set-sink-mute", "sink-two", "1"],
            ["set-default-sink", "sink-two"],
            ["move-sink-input", "20", "sink-two"],
        ])

    def test_direct_selection_unmutes_before_selecting(self):
        self.write_state(default="sink-two", mutes={"sink-one": True, "sink-two": False})
        result = self.run_cli("set", "one")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()["default"], "sink-one")
        self.assertFalse(self.read_state()["mutes"]["sink-one"])
        self.assertEqual(self.mutating_commands()[:2], [
            ["set-sink-mute", "sink-one", "0"],
            ["set-default-sink", "sink-one"],
        ])

    def test_new_hands_free_headset_unmutes_before_switching(self):
        self.config_path.write_text(
            self.config_path.read_text(encoding="utf-8").replace(
                "auto_include_new = false", "auto_include_new = true"
            ),
            encoding="utf-8",
        )
        state = self.read_state()
        state["sinks"].append({
            "index": 16,
            "name": "bt-headset",
            "description": "Bluetooth Audio",
            "properties": {
                "api.bluez5.address": "AA:BB:CC:DD:EE:FF",
                "device.form_factor": "hands-free",
            },
        })
        state["default"] = "sink-two"
        state["mutes"]["bt-headset"] = True
        state["inputs"] = [{"index": 20, "sink": 8}]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "Bluetooth Audio")
        self.assertEqual(self.read_state()["default"], "bt-headset")
        self.assertFalse(self.read_state()["mutes"]["bt-headset"])
        self.assertEqual(self.read_state()["inputs"], [{"index": 20, "sink": 16}])
        self.assertEqual(self.mutating_commands()[:3], [
            ["set-sink-mute", "bt-headset", "0"],
            ["set-default-sink", "bt-headset"],
            ["move-sink-input", "20", "bt-headset"],
        ])

    def test_setup_excludes_current_output_but_accepts_later_headset(self):
        configured = self.run_cli(
            "configure", "--force", "--sink", "sink-one", "--sink", "sink-two"
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        saved = self.config_path.read_text(encoding="utf-8")
        self.assertIn("auto_include_new = true", saved)
        self.assertIn("include = false", saved)

        state = self.read_state()
        state["sinks"].append({
            "index": 16,
            "name": "bt-headset",
            "description": "Bluetooth Headset",
            "properties": {
                "api.bluez5.address": "AA:BB:CC:DD:EE:FF",
                "device.form_factor": "headset",
            },
        })
        state["default"] = "sink-two"
        state["mutes"]["bt-headset"] = True
        state["inputs"] = [{"index": 20, "sink": 8}]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        listed = self.run_cli("list")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn(
            "12: Unmanaged Output\n    unmanaged\n    excluded from cycle",
            listed.stdout,
        )
        self.assertIn(
            "16: Bluetooth Headset\n    bt-headset\n"
            "    cycle id = bluetooth-headset",
            listed.stdout,
        )
        diagnosed = self.run_cli("doctor")
        self.assertEqual(diagnosed.returncode, 0, diagnosed.stdout)
        self.assertIn("Bluetooth Headset joined the cycle automatically", diagnosed.stdout)
        switched = self.run_cli("next")
        self.assertEqual(switched.returncode, 0, switched.stderr)
        self.assertEqual(self.read_state()["default"], "bt-headset")

        state = self.read_state()
        state["sinks"] = [sink for sink in state["sinks"] if sink["name"] != "bt-headset"]
        state["default"] = "sink-two"
        state["inputs"] = [{"index": 20, "sink": 8}]
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        after_disconnect = self.run_cli("next")
        self.assertEqual(after_disconnect.returncode, 0, after_disconnect.stderr)
        self.assertEqual(self.read_state()["default"], "sink-one")

    def test_unknown_output_is_rejected_without_changes(self):
        result = self.run_cli("set", "missing")
        self.assertEqual(result.returncode, 1)
        self.assertIn("unknown output", result.stderr)
        self.assertEqual(self.mutating_commands(), [])
        self.assertEqual(self.read_state()["default"], "sink-one")

    def test_malformed_sink_json_is_rejected_without_changes(self):
        self.write_state(mode="malformed-sinks")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid sink JSON", result.stderr)
        self.assertEqual(self.mutating_commands(), [])

    def test_pactl_failure_is_reported_without_changes(self):
        self.write_state(mode="sink-list-error")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 1)
        self.assertIn("test server unavailable", result.stderr)
        self.assertEqual(self.mutating_commands(), [])

    def test_pactl_timeout_is_reported_without_changes(self):
        self.write_state(mode="sink-list-timeout")
        result = self.run_cli("next")
        self.assertEqual(result.returncode, 1)
        self.assertIn("timed out after 0.5 seconds", result.stderr)
        self.assertEqual(self.mutating_commands(), [])


if __name__ == "__main__":
    unittest.main()
