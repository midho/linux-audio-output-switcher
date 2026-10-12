from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass

from .config import OutputConfig


@dataclass(frozen=True)
class Sink:
    index: int
    name: str
    description: str
    properties: dict[str, str]

    def property(self, key: str) -> str:
        if key == "name":
            return self.name
        if key == "description":
            return self.description
        return self.properties.get(key, "")


@dataclass(frozen=True)
class SinkInput:
    index: int
    sink: int


class PulseCommandError(RuntimeError):
    pass


class AmbiguousOutputError(RuntimeError):
    pass


def parse_sinks(output: str) -> list[Sink]:
    try:
        raw_sinks = json.loads(output)
    except json.JSONDecodeError as error:
        raise PulseCommandError(f"pactl returned invalid sink JSON: {error}") from error
    if not isinstance(raw_sinks, list):
        raise PulseCommandError("pactl sink JSON is not a list")
    sinks: list[Sink] = []
    for raw in raw_sinks:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw["index"])
            name = str(raw["name"])
        except (KeyError, TypeError, ValueError):
            continue
        description = str(raw.get("description") or name)
        raw_properties = raw.get("properties", {})
        if not isinstance(raw_properties, dict):
            raw_properties = {}
        properties = {
            str(key): str(value)
            for key, value in raw_properties.items()
            if isinstance(value, (str, int, float))
        }
        sinks.append(Sink(index, name, description, properties))
    return sinks


def parse_sink_inputs(output: str) -> list[SinkInput]:
    try:
        raw_inputs = json.loads(output)
    except json.JSONDecodeError as error:
        raise PulseCommandError(f"pactl returned invalid stream JSON: {error}") from error
    if not isinstance(raw_inputs, list):
        raise PulseCommandError("pactl stream JSON is not a list")
    inputs: list[SinkInput] = []
    for raw in raw_inputs:
        if not isinstance(raw, dict):
            continue
        try:
            inputs.append(SinkInput(int(raw["index"]), int(raw["sink"])))
        except (KeyError, TypeError, ValueError):
            continue
    return inputs


def sink_matches(configured: OutputConfig, sink: Sink) -> bool:
    bluetooth = bool(sink.properties.get("api.bluez5.address"))
    configured_keys = {key for key, _value in configured.match}
    has_physical_identity = bool(
        configured_keys & {"api.bluez5.address", "device.serial"}
    )
    changing_profile_keys = {"api.bluez5.profile", "device.profile.name"}
    return all(
        sink.property(key).strip().casefold() == expected.strip().casefold()
        for key, expected in configured.match
        if not (bluetooth and has_physical_identity and key in changing_profile_keys)
    )


def resolve_outputs(
    configured: tuple[OutputConfig, ...], sinks: list[Sink]
) -> dict[str, Sink]:
    resolved: dict[str, Sink] = {}
    used_indexes: set[int] = set()
    for output in configured:
        candidates = [sink for sink in sinks if sink_matches(output, sink)]
        if len(candidates) > 1:
            raise AmbiguousOutputError(
                f"{output.label!r} matches more than one connected output. Add another match property"
            )
        if not candidates:
            continue
        candidate = candidates[0]
        if candidate.index in used_indexes:
            raise AmbiguousOutputError(
                f"more than one configured output matches {candidate.description!r}"
            )
        resolved[output.id] = candidate
        used_indexes.add(candidate.index)
    return resolved


def slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (cleaned or "output")[:32].rstrip("-")


def is_headphone_sink(sink: Sink) -> bool:
    form = sink.properties.get("device.form_factor", "").casefold()
    icons = " ".join((
        sink.properties.get("device.icon_name", ""),
        sink.properties.get("api.bluez5.icon", ""),
    )).casefold()
    words = ("headphone", "headset", "hands-free", "handsfree", "earbud")
    if any(word in form for word in words):
        return True
    if form and form != "unknown":
        return False
    if any(word in icons for word in words):
        return True
    return any(word in sink.description.casefold() for word in words)


def icon_for_sink(sink: Sink) -> str:
    if is_headphone_sink(sink):
        return "headphones"
    form = sink.properties.get("device.form_factor", "").casefold()
    description = sink.description.casefold()
    if "speaker" in form or "speaker" in description or "hdmi" in description:
        return "speakers"
    return "audio"


def stable_match_for_sink(sink: Sink) -> tuple[tuple[str, str], ...]:
    properties = sink.properties
    selected: list[str] = []
    if properties.get("api.bluez5.address"):
        # A Bluetooth address identifies the physical device. Profile names
        # change when the same device moves between music and call modes.
        selected = ["api.bluez5.address"]
    elif properties.get("device.serial"):
        selected = ["device.serial"]
        if properties.get("device.profile.name"):
            selected.append("device.profile.name")
    elif properties.get("device.bus_path"):
        selected = ["device.bus_path"]
        if properties.get("device.profile.name"):
            selected.append("device.profile.name")
    elif properties.get("device.vendor.id") and properties.get("device.product.id"):
        selected = ["device.vendor.id", "device.product.id"]
        if properties.get("device.profile.name"):
            selected.append("device.profile.name")
    else:
        selected = ["name"]
    return tuple(sorted((key, sink.property(key)) for key in selected))


class PulseClient:
    def __init__(self, executable: str = "pactl", timeout: float = 4.0):
        self.executable = executable
        self.timeout = timeout
        self._json_support_checked = False

    def command(self, *arguments: str) -> str:
        try:
            result = subprocess.run(
                [self.executable, *arguments],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise PulseCommandError(
                f"pactl timed out after {self.timeout:.1f} seconds"
            ) from error
        except OSError as error:
            raise PulseCommandError(f"cannot run {self.executable}: {error}") from error
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise PulseCommandError(detail or f"pactl exited with status {result.returncode}")
        return result.stdout.strip()

    def version(self) -> str:
        return self.command("--version").splitlines()[0]

    def server_info(self) -> str:
        return self.command("info")

    def require_json_support(self) -> None:
        if self._json_support_checked:
            return
        try:
            # Help is handled by the client and does not need an audio server.
            self.command("--format=json", "--help")
        except PulseCommandError as error:
            raise PulseCommandError(
                "this pactl client does not support JSON output. pactl 16.0 or newer is required"
            ) from error
        self._json_support_checked = True

    def list_sinks(self) -> list[Sink]:
        self.require_json_support()
        return parse_sinks(self.command("--format=json", "list", "sinks"))

    def list_sink_inputs(self) -> list[SinkInput]:
        self.require_json_support()
        return parse_sink_inputs(self.command("--format=json", "list", "sink-inputs"))

    def get_default_sink(self) -> str:
        return self.command("get-default-sink")

    def set_default_sink(self, sink_name: str) -> None:
        self.command("set-default-sink", sink_name)

    def set_sink_mute(self, sink_name: str, muted: bool) -> None:
        self.command("set-sink-mute", sink_name, "1" if muted else "0")

    def move_sink_input(self, input_index: int, sink_name: str) -> None:
        self.command("move-sink-input", str(input_index), sink_name)
