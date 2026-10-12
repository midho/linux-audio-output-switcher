from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ICONS = {"headphones", "speakers", "audio"}
MUTE_ON_ACTIVATE_VALUES = {"keep", "mute", "unmute"}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class OutputConfig:
    id: str
    label: str
    icon: str
    match: tuple[tuple[str, str], ...]
    mute_on_activate: str = "keep"
    include: bool = True


@dataclass(frozen=True)
class PopupConfig:
    enabled: bool = True
    duration_ms: int = 2850
    bottom_margin: int = 180
    all_monitors: bool = False
    monitor_model: str = ""


@dataclass(frozen=True)
class Settings:
    version: int
    move_playing_streams: bool
    pactl_timeout_seconds: float
    popup: PopupConfig
    outputs: tuple[OutputConfig, ...]
    auto_include_new: bool = False
    auto_headphones: str = "unmute"
    auto_other: str = "mute"


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "linux-audio-output-switcher" / "config.toml"


def _integer(data: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    value = data.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"popup.{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ConfigError(f"popup.{name} must be between {minimum} and {maximum}")
    return value


def _text(data: dict[str, Any], name: str) -> str:
    value = data.get(name, "")
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"output {name} must be non-empty text")
    if len(value) > 128:
        raise ConfigError(f"output {name} is too long")
    return value.strip()


def parse_config(data: dict[str, Any]) -> Settings:
    version = data.get("version", 1)
    if version != 1:
        raise ConfigError(f"unsupported configuration version: {version!r}")

    move_streams = data.get("move_playing_streams", True)
    if not isinstance(move_streams, bool):
        raise ConfigError("move_playing_streams must be true or false")
    timeout = data.get("pactl_timeout_seconds", 4.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise ConfigError("pactl_timeout_seconds must be a number")
    timeout = float(timeout)
    if not 0.5 <= timeout <= 15.0:
        raise ConfigError("pactl_timeout_seconds must be between 0.5 and 15.0")

    auto_include_new = data.get("auto_include_new", False)
    if not isinstance(auto_include_new, bool):
        raise ConfigError("auto_include_new must be true or false")
    auto_headphones = data.get("auto_headphones", "unmute")
    auto_other = data.get("auto_other", "mute")
    for name, value in (("auto_headphones", auto_headphones), ("auto_other", auto_other)):
        if not isinstance(value, str) or value not in MUTE_ON_ACTIVATE_VALUES:
            raise ConfigError(f"{name} must be keep, mute, or unmute")

    popup_data = data.get("popup", {})
    if not isinstance(popup_data, dict):
        raise ConfigError("popup must be a table")
    popup_enabled = popup_data.get("enabled", True)
    all_monitors = popup_data.get("all_monitors", False)
    monitor_model = popup_data.get("monitor_model", "")
    if not isinstance(popup_enabled, bool):
        raise ConfigError("popup.enabled must be true or false")
    if not isinstance(all_monitors, bool):
        raise ConfigError("popup.all_monitors must be true or false")
    if not isinstance(monitor_model, str):
        raise ConfigError("popup.monitor_model must be text")
    popup = PopupConfig(
        enabled=popup_enabled,
        duration_ms=_integer(popup_data, "duration_ms", 2850, 250, 10000),
        bottom_margin=_integer(popup_data, "bottom_margin", 180, 0, 1000),
        all_monitors=all_monitors,
        monitor_model=monitor_model.strip(),
    )

    raw_outputs = data.get("outputs", [])
    if not isinstance(raw_outputs, list):
        raise ConfigError("outputs must be a list of tables")

    outputs: list[OutputConfig] = []
    ids: set[str] = set()
    matches: set[tuple[tuple[str, str], ...]] = set()
    for raw_output in raw_outputs:
        if not isinstance(raw_output, dict):
            raise ConfigError("each outputs entry must be a table")
        output_id = _text(raw_output, "id").lower()
        if not ID_PATTERN.fullmatch(output_id):
            raise ConfigError(
                f"output id {output_id!r} must use lowercase letters, numbers, _ or -"
            )
        if output_id in ids:
            raise ConfigError(f"duplicate output id: {output_id}")
        icon = raw_output.get("icon", "audio")
        if not isinstance(icon, str) or icon not in ICONS:
            raise ConfigError(f"output {output_id!r} icon must be headphones, speakers, or audio")
        mute_on_activate = raw_output.get("mute_on_activate", "keep")
        if (
            not isinstance(mute_on_activate, str)
            or mute_on_activate not in MUTE_ON_ACTIVATE_VALUES
        ):
            raise ConfigError(
                f"output {output_id!r} mute_on_activate must be keep, mute, or unmute"
            )
        include = raw_output.get("include", True)
        if not isinstance(include, bool):
            raise ConfigError(f"output {output_id!r} include must be true or false")
        raw_match = raw_output.get("match")
        if not isinstance(raw_match, dict) or not raw_match or len(raw_match) > 16:
            raise ConfigError(f"output {output_id!r} must have 1 to 16 match properties")
        match_items: list[tuple[str, str]] = []
        for key, value in raw_match.items():
            if not isinstance(key, str) or not key.strip():
                raise ConfigError(f"output {output_id!r} has an invalid match key")
            if not isinstance(value, str) or not value.strip():
                raise ConfigError(f"output {output_id!r} match values must be non-empty text")
            if len(key) > 128 or len(value) > 256:
                raise ConfigError(f"output {output_id!r} match property is too long")
            match_items.append((key.strip(), value.strip()))
        match_tuple = tuple(sorted(match_items))
        if match_tuple in matches:
            raise ConfigError("configured outputs must not have identical match rules")
        output = OutputConfig(
            id=output_id,
            label=_text(raw_output, "label"),
            icon=icon,
            match=match_tuple,
            mute_on_activate=mute_on_activate,
            include=include,
        )
        ids.add(output_id)
        matches.add(match_tuple)
        outputs.append(output)

    if not auto_include_new and sum(output.include for output in outputs) < 2:
        raise ConfigError("at least two included [[outputs]] entries are required")

    return Settings(
        version=1,
        move_playing_streams=move_streams,
        pactl_timeout_seconds=timeout,
        popup=popup,
        outputs=tuple(outputs),
        auto_include_new=auto_include_new,
        auto_headphones=auto_headphones,
        auto_other=auto_other,
    )


def load_config(path: Path | None = None) -> Settings:
    selected = path or config_path()
    try:
        with selected.open("rb") as config_file:
            return parse_config(tomllib.load(config_file))
    except FileNotFoundError as error:
        raise ConfigError(f"configuration not found: {selected}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {selected}: {error}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigError(f"cannot read configuration {selected}: {error}") from error


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def render_config(
    outputs: list[OutputConfig],
    *,
    auto_include_new: bool = True,
    auto_headphones: str = "unmute",
    auto_other: str = "mute",
) -> str:
    lines = [
        "version = 1",
        "move_playing_streams = true",
        "pactl_timeout_seconds = 4.0",
        f"auto_include_new = {str(auto_include_new).lower()}",
        f"auto_headphones = {_toml_string(auto_headphones)}",
        f"auto_other = {_toml_string(auto_other)}",
        "",
        "[popup]",
        "enabled = true",
        "duration_ms = 2850",
        "bottom_margin = 180",
        "all_monitors = false",
        "monitor_model = \"\"",
    ]
    for output in outputs:
        lines.extend([
            "",
            "[[outputs]]",
            f"id = {_toml_string(output.id)}",
            f"label = {_toml_string(output.label)}",
            f"icon = {_toml_string(output.icon)}",
            f"mute_on_activate = {_toml_string(output.mute_on_activate)}",
            f"include = {str(output.include).lower()}",
            "",
            "[outputs.match]",
        ])
        for key, value in output.match:
            lines.append(f"{_toml_string(key)} = {_toml_string(value)}")
    return "\n".join(lines) + "\n"


def write_config(path: Path, content: str, *, force: bool = False) -> None:
    temporary_name = ""
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise ConfigError(f"refusing to replace a symbolic link: {path}")
        if path.exists() and not force:
            raise ConfigError(
                f"configuration already exists: {path}. Use --force to replace it"
            )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".config.", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o600)
        os.replace(temporary_name, path)
    except ConfigError:
        raise
    except OSError as error:
        raise ConfigError(f"cannot write configuration {path}: {error}") from error
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink(missing_ok=True)
            except OSError:
                pass
