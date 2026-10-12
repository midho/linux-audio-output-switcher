from __future__ import annotations

from dataclasses import dataclass

from .config import OutputConfig, Settings
from .pulse import (
    AmbiguousOutputError,
    PulseClient,
    PulseCommandError,
    Sink,
    icon_for_sink,
    is_headphone_sink,
    resolve_outputs,
    slug,
    stable_match_for_sink,
)


class SwitchError(RuntimeError):
    pass


@dataclass(frozen=True)
class SwitchResult:
    output_id: str
    label: str
    icon: str
    sink_name: str
    warnings: tuple[str, ...] = ()


def connected_outputs(
    settings: Settings, sinks: list[Sink]
) -> tuple[list[OutputConfig], dict[str, Sink]]:
    resolved = resolve_outputs(settings.outputs, sinks)
    available = [
        output for output in settings.outputs
        if output.include and output.id in resolved
    ]
    if not settings.auto_include_new:
        return available, resolved

    matched_indexes = {sink.index for sink in resolved.values()}
    used_ids = {output.id for output in settings.outputs}
    new_sinks = sorted(
        (sink for sink in sinks if sink.index not in matched_indexes),
        key=lambda sink: (sink.description.casefold(), sink.name.casefold()),
    )
    for sink in new_sinks:
        base = slug(sink.description)
        output_id = base
        suffix = 2
        while output_id in used_ids:
            tail = f"-{suffix}"
            output_id = f"{base[:32-len(tail)]}{tail}"
            suffix += 1
        used_ids.add(output_id)
        headphones = is_headphone_sink(sink)
        output = OutputConfig(
            id=output_id,
            label=sink.description,
            icon=icon_for_sink(sink),
            match=stable_match_for_sink(sink),
            mute_on_activate=(settings.auto_headphones if headphones else settings.auto_other),
        )
        available.append(output)
        resolved[output_id] = sink
    return available, resolved


class OutputSwitcher:
    def __init__(self, settings: Settings, client: PulseClient):
        self.settings = settings
        self.client = client

    def connected(self) -> tuple[list[Sink], list[OutputConfig], dict[str, Sink]]:
        sinks = self.client.list_sinks()
        try:
            available, resolved = connected_outputs(self.settings, sinks)
        except AmbiguousOutputError as error:
            raise SwitchError(str(error)) from error
        return sinks, available, resolved

    def current_result(self) -> SwitchResult:
        sinks, available, resolved = self.connected()
        current_name = self.client.get_default_sink()
        for configured in available:
            sink = resolved.get(configured.id)
            if sink is not None and sink.name == current_name:
                return SwitchResult(
                    configured.id, configured.label, configured.icon, sink.name
                )
        current = next((sink for sink in sinks if sink.name == current_name), None)
        if current is None:
            raise SwitchError(f"the current default output {current_name!r} is not available")
        return SwitchResult("", current.description, icon_for_sink(current), current.name)

    def choose(self, direction: int = 1, output_id: str | None = None) -> SwitchResult:
        _, available, resolved = self.connected()
        if output_id is not None:
            target_config = next((item for item in available if item.id == output_id), None)
            if target_config is None:
                configured = next(
                    (item for item in self.settings.outputs if item.id == output_id), None
                )
                if configured is not None and not configured.include:
                    raise SwitchError(f"{configured.label} is excluded. Output unchanged")
                if configured is not None:
                    raise SwitchError(f"{configured.label} is not connected. Output unchanged")
                choices = ", ".join(item.id for item in available)
                raise SwitchError(f"unknown output {output_id!r}. Choose one of: {choices}")
        else:
            if len(available) < 2:
                raise SwitchError(
                    "at least two configured audio outputs must be connected. Output unchanged"
                )
            current_name = self.client.get_default_sink()
            current_index = next(
                (index for index, item in enumerate(available) if resolved[item.id].name == current_name),
                None,
            )
            if current_index is None:
                target_config = available[0] if direction >= 0 else available[-1]
            else:
                target_config = available[(current_index + direction) % len(available)]

        target = resolved[target_config.id]
        managed = {item.id: resolved[item.id] for item in available}
        warnings = self.activate(target, managed, target_config.mute_on_activate)
        return SwitchResult(
            target_config.id,
            target_config.label,
            target_config.icon,
            target.name,
            tuple(warnings),
        )

    def activate(
        self,
        target: Sink,
        resolved: dict[str, Sink],
        mute_on_activate: str = "keep",
    ) -> list[str]:
        streams = self.client.list_sink_inputs() if self.settings.move_playing_streams else []
        managed_indexes = {sink.index for sink in resolved.values()}
        if mute_on_activate != "keep":
            self.client.set_sink_mute(target.name, mute_on_activate == "mute")
        self.client.set_default_sink(target.name)
        if self.client.get_default_sink() != target.name:
            raise SwitchError("the audio server did not select the requested output")

        warnings: list[str] = []
        for stream in streams:
            if stream.sink not in managed_indexes or stream.sink == target.index:
                continue
            try:
                self.client.move_sink_input(stream.index, target.name)
            except PulseCommandError:
                remaining = self.client.list_sink_inputs()
                if any(
                    item.index == stream.index and item.sink != target.index
                    for item in remaining
                ):
                    warnings.append(
                        f"Playback stream {stream.index} could not be moved. Reopen its playback."
                    )
        return warnings
