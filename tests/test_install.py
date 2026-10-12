import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.fake_bin = self.root / "bin"
        self.home.mkdir()
        self.fake_bin.mkdir()
        pactl = self.fake_bin / "pactl"
        pactl.write_text(
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  \"--format=json --help\") exit 0 ;;\n"
            "  \"--version\") printf '%s\\n' 'pactl 16.1'; exit 0 ;;\n"
            "  \"info\") printf '%s\\n' 'Server Name: Test PulseAudio'; exit 0 ;;\n"
            "  \"get-default-sink\") printf '%s\\n' 'sink-one'; exit 0 ;;\n"
            "  \"--format=json list sinks\")\n"
            "    printf '%s\\n' "
            "'[{\"index\":1,\"name\":\"sink-one\",\"description\":\"USB Headset\","
            "\"properties\":{\"device.serial\":\"ONE\"}},"
            "{\"index\":2,\"name\":\"sink-two\",\"description\":\"Desk Speakers\","
            "\"properties\":{\"device.serial\":\"TWO\"}}]'\n"
            "    exit 0 ;;\n"
            "esac\n"
            "exit 1\n",
            encoding="utf-8",
        )
        pactl.chmod(0o755)
        self.environment = {
            **os.environ,
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "PATH": f"{self.fake_bin}:{os.environ['PATH']}",
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def tearDown(self):
        self.temporary.cleanup()

    def run_script(self, name, *arguments):
        return subprocess.run(
            [str(PROJECT / name), *arguments],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_install_update_uninstall_and_purge(self):
        first = self.run_script("install.sh", "--no-configure")
        self.assertEqual(first.returncode, 0, first.stderr)
        launcher = self.home / ".local/bin/linux-audio-output-switcher"
        self.assertTrue(launcher.is_file())

        config_dir = self.home / ".config/linux-audio-output-switcher"
        config = config_dir / "config.toml"
        configured = subprocess.run(
            [
                launcher,
                "--config",
                config,
                "configure",
                "--sink",
                "sink-one",
                "--sink",
                "sink-two",
            ],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        self.assertTrue(config.is_file())
        original_config = config.read_text(encoding="utf-8")
        self.assertIn('id = "usb-headset"', original_config)
        version = subprocess.run(
            [launcher, "--version"],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(version.returncode, 0, version.stderr)

        installed_cli = (
            self.home
            / ".local/share/linux-audio-output-switcher/linux_audio_output_switcher/cli.py"
        )
        installed_cli.write_text("old installed file\n", encoding="utf-8")
        second = self.run_script("install.sh", "--no-configure")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(config.read_text(encoding="utf-8"), original_config)
        self.assertEqual(
            installed_cli.read_text(encoding="utf-8"),
            (PROJECT / "src/linux_audio_output_switcher/cli.py").read_text(
                encoding="utf-8"
            ),
        )
        doctor = subprocess.run(
            [launcher, "--config", config, "doctor"],
            cwd=PROJECT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        self.assertIn("Result: ready", doctor.stdout)

        unrelated = config_dir / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")
        removed = self.run_script("uninstall.sh")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertTrue(config.is_file())
        self.assertTrue(unrelated.is_file())
        self.assertFalse(launcher.exists())

        purged = self.run_script("uninstall.sh", "--purge")
        self.assertEqual(purged.returncode, 0, purged.stderr)
        self.assertFalse(config.exists())
        self.assertTrue(unrelated.is_file())

    def test_install_rejects_pactl_without_json_support(self):
        pactl = self.fake_bin / "pactl"
        pactl.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        pactl.chmod(0o755)
        result = self.run_script("install.sh", "--no-configure")
        self.assertEqual(result.returncode, 1)
        self.assertIn("pactl 16.0 or newer", result.stderr)
        self.assertFalse((self.home / ".local/bin/linux-audio-output-switcher").exists())

    def test_uninstall_removes_generated_bytecode_and_keeps_unrelated_files(self):
        installed = self.run_script("install.sh", "--no-configure")
        self.assertEqual(installed.returncode, 0, installed.stderr)
        app_dir = self.home / ".local/share/linux-audio-output-switcher"
        package_dir = app_dir / "linux_audio_output_switcher"
        environment = dict(self.environment)
        environment.pop("PYTHONDONTWRITEBYTECODE", None)
        environment["PYTHONPATH"] = str(app_dir)
        imported = subprocess.run(
            [sys.executable, "-c", "import linux_audio_output_switcher.cli"],
            cwd=self.root,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertTrue(list((package_dir / "__pycache__").glob("*.pyc")))
        unrelated = app_dir / "keep.txt"
        unrelated.write_text("keep", encoding="utf-8")

        removed = self.run_script("uninstall.sh")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertFalse(package_dir.exists())
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
