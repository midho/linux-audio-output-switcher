from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .config import (
    ConfigError,
    OutputConfig,
    config_path,
    load_config,
    render_config,
    write_config,
)
from .popup import launch_popup
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
from .runtime import ActionBusyError, acquire_action_lock, read_action_generation
from .switcher import OutputSwitcher, SwitchError, SwitchResult, connected_outputs


def configs_from_sinks(sinks: list[Sink]) -> list[OutputConfig]:
    used_ids: set[str] = set()
    outputs: list[OutputConfig] = []
    for sink in sinks:
        base = slug(sink.description)
        output_id = base
        suffix = 2
        while output_id in used_ids:
            tail = f"-{suffix}"
            output_id = f"{base[:32-len(tail)]}{tail}"
            suffix += 1
        used_ids.add(output_id)
        outputs.append(OutputConfig(
            id=output_id,
            label=sink.description,
            icon=icon_for_sink(sink),
            match=stable_match_for_sink(sink),
            mute_on_activate="unmute" if is_headphone_sink(sink) else "mute",
        ))
    return outputs


def select_sinks(
    sinks: list[Sink],
    default_name: str,
    requested_names: list[str] | None,
    select_all: bool = False,
) -> list[Sink]:
    if requested_names and select_all:
        raise ConfigError("use --sink or --all, not both")
    if requested_names:
        selected: list[Sink] = []
        for requested in requested_names:
            match = next((sink for sink in sinks if sink.name == requested), None)
            if match is None:
                raise ConfigError(f"audio output not found: {requested}")
            if match in selected:
                raise ConfigError(f"audio output selected more than once: {requested}")
            selected.append(match)
        return selected

    if select_all:
        return list(sinks)

    if not sys.stdin.isatty():
        raise ConfigError("interactive selection needs a terminal. Use --sink or --all")
    print("Connected audio outputs:")
    for index, sink in enumerate(sinks, start=1):
        marker = "*" if sink.name == default_name else " "
        print(f"  {index}. {marker} {sink.description}")
        print(f"       {sink.name}")
    answer = input("Enter output numbers in cycle order (blank for all): ").strip()
    if not answer:
        return list(sinks)
    try:
        numbers = [int(value) for value in answer.replace(",", " ").split()]
    except ValueError as error:
        raise ConfigError("selection must contain only output numbers") from error
    if len(set(numbers)) != len(numbers):
        raise ConfigError("do not select the same output twice")
    if any(number < 1 or number > len(sinks) for number in numbers):
        raise ConfigError("an output number is outside the displayed list")
    return [sinks[number - 1] for number in numbers]


def configure(
    selected_path: Path,
    force: bool,
    requested_names: list[str] | None,
    select_all: bool = False,
) -> int:
    client = PulseClient()
    try:
        sinks = client.list_sinks()
        default_name = client.get_default_sink() if sinks else ""
        chosen = select_sinks(sinks, default_name, requested_names, select_all)
        excluded = [sink for sink in sinks if sink not in chosen]
        outputs = configs_from_sinks([*chosen, *excluded])
        outputs = [
            replace(output, include=index < len(chosen))
            for index, output in enumerate(outputs)
        ]
        resolved = resolve_outputs(tuple(outputs), sinks)
        if len(resolved) != len(outputs):
            raise ConfigError("generated match rules did not resolve every connected output")
        write_config(selected_path, render_config(outputs), force=force)
    except (PulseCommandError, AmbiguousOutputError, ConfigError) as error:
        print(f"Configuration failed: {error}", file=sys.stderr)
        return 1
    print(f"Wrote {selected_path}")
    for output in outputs:
        status = "included" if output.include else "excluded"
        print(f"  {output.id}: {output.label} ({status})")
    if not outputs:
        print("No outputs are connected. New outputs will join when available.")
    return 0


def list_outputs(show_properties: bool = False, selected_path: Path | None = None) -> int:
    client = PulseClient()
    try:
        default_name = client.get_default_sink()
        sinks = client.list_sinks()
        settings = load_config(selected_path) if selected_path and selected_path.exists() else None
        available, resolved = connected_outputs(settings, sinks) if settings else ([], {})
    except (PulseCommandError, AmbiguousOutputError, ConfigError) as error:
        print(error, file=sys.stderr)
        return 1
    included = {resolved[output.id].index: output for output in available}
    excluded = {}
    if settings:
        excluded = {
            resolved[output.id].index: output
            for output in settings.outputs
            if not output.include and output.id in resolved
        }
    for sink in sinks:
        marker = "*" if sink.name == default_name else " "
        print(f"{marker} {sink.index}: {sink.description}")
        print(f"    {sink.name}")
        if settings:
            if sink.index in included:
                print(f"    cycle id = {included[sink.index].id}")
            elif sink.index in excluded:
                print("    excluded from cycle")
            else:
                print("    not in cycle")
        if show_properties:
            for key, value in stable_match_for_sink(sink):
                print(f"    {key} = {value}")
    return 0


def doctor(selected_path: Path) -> int:
    failures = 0
    warnings = 0

    def report(kind: str, message: str) -> None:
        print(f"{kind}: {message}")

    executable = shutil.which("pactl")
    if executable is None:
        report("FAIL", "pactl is not installed")
        return 1
    client = PulseClient(executable=executable)
    try:
        report("PASS", client.version())
        client.require_json_support()
        report("PASS", "pactl JSON output is supported")
        info = client.server_info()
        server_line = next(
            (line for line in info.splitlines() if line.startswith("Server Name:")),
            "audio server is reachable",
        )
        report("PASS", server_line)
        settings = load_config(selected_path)
        report("PASS", f"configuration is valid: {selected_path}")
        sinks = client.list_sinks()
        available, resolved = connected_outputs(settings, sinks)
        default_name = client.get_default_sink()
    except (PulseCommandError, AmbiguousOutputError, ConfigError) as error:
        report("FAIL", str(error))
        return 1

    for output in settings.outputs:
        if not output.include:
            report("PASS", f"{output.label} is excluded from the cycle")
            continue
        sink = resolved.get(output.id)
        if sink is None:
            report("WARN", f"{output.label} is not connected")
            warnings += 1
            continue
        marker = " and is the default" if sink.name == default_name else ""
        report("PASS", f"{output.label} matches {sink.description}{marker}")

    known_ids = {output.id for output in settings.outputs}
    for output in available:
        if output.id not in known_ids:
            report("PASS", f"{output.label} joined the cycle automatically")

    if len(available) < 2:
        kind = "WARN" if settings.auto_include_new else "FAIL"
        report(kind, "fewer than two included outputs are connected")
        if settings.auto_include_new:
            warnings += 1
        else:
            failures += 1

    if settings.popup.enabled:
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            gi.require_version("GtkLayerShell", "0.1")
            from gi.repository import Gtk, GtkLayerShell
            _ = Gtk, GtkLayerShell
        except (ImportError, ValueError):
            report("WARN", "GTK 3 or GTK Layer Shell bindings are missing. Switching still works")
            warnings += 1
        else:
            report("PASS", "GTK 3 and GTK Layer Shell bindings are installed")
        if os.environ.get("WAYLAND_DISPLAY"):
            report("PASS", f"Wayland session is visible as {os.environ['WAYLAND_DISPLAY']}")
        else:
            report("WARN", "this shell has no WAYLAND_DISPLAY. Popup may be unavailable")
            warnings += 1

    if failures:
        print(f"Result: {failures} failure(s), {warnings} warning(s)")
        return 1
    print(f"Result: ready with {warnings} warning(s)")
    return 0


def message_for_result(result: SwitchResult) -> str:
    return "\n".join((result.label, *result.warnings))


def run_action(selected_path: Path, command: str, output_id: str | None = None) -> int:
    settings = None
    generation = None
    try:
        settings = load_config(selected_path)
        client = PulseClient(timeout=settings.pactl_timeout_seconds)
        switcher = OutputSwitcher(settings, client)
        with acquire_action_lock() as lock:
            # This number keeps popup results in switch order across processes.
            generation = lock.generation
            if command == "show":
                result = switcher.current_result()
            elif command == "set":
                result = switcher.choose(output_id=output_id)
            else:
                direction = -1 if command == "previous" else 1
                result = switcher.choose(direction=direction)
        message = message_for_result(result)
        print(message)
        launch_popup(settings.popup, message, result.icon, generation)
        return 0
    except ActionBusyError as error:
        # A rejected command must not borrow the winner's popup number.
        print(str(error), file=sys.stderr)
        return 1
    except (ConfigError, PulseCommandError, SwitchError, RuntimeError) as error:
        message = str(error)
        print(message, file=sys.stderr)
        if settings is not None:
            if generation is None:
                generation = read_action_generation()
            launch_popup(settings.popup, message, "warning", generation)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linux-audio-output-switcher",
        description="Cycle selected PulseAudio-compatible outputs on Linux",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, default=config_path())
    subparsers = parser.add_subparsers(dest="command", required=True)
    configure_parser = subparsers.add_parser(
        "configure", help="select outputs and write a configuration"
    )
    configure_parser.add_argument("--force", action="store_true")
    configure_parser.add_argument(
        "--sink", action="append", dest="sinks", help="include this connected sink"
    )
    configure_parser.add_argument(
        "--all", action="store_true", help="include every currently connected sink"
    )
    list_parser = subparsers.add_parser("list", help="list connected audio outputs")
    list_parser.add_argument(
        "--properties", action="store_true", help="show automatic stable match properties"
    )
    subparsers.add_parser("doctor", help="run read-only checks")
    subparsers.add_parser("next", help="select the next connected configured output")
    subparsers.add_parser("previous", help="select the previous connected configured output")
    subparsers.add_parser("toggle", help="alias for next")
    set_parser = subparsers.add_parser("set", help="select a configured output directly")
    set_parser.add_argument("output_id")
    subparsers.add_parser("show", help="show the current output without changing it")
    return parser


def main() -> None:
    arguments = build_parser().parse_args()
    if arguments.command == "configure":
        status = configure(arguments.config, arguments.force, arguments.sinks, arguments.all)
    elif arguments.command == "list":
        status = list_outputs(arguments.properties, arguments.config)
    elif arguments.command == "doctor":
        status = doctor(arguments.config)
    elif arguments.command == "set":
        status = run_action(arguments.config, "set", arguments.output_id)
    else:
        status = run_action(arguments.config, arguments.command)
    raise SystemExit(status)
