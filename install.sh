#!/bin/sh
set -eu

configure=1

usage() {
    printf '%s\n' "Usage: ./install.sh [--no-configure]"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-configure) configure=0 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
    shift
done

for command_name in install python3; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        printf '%s\n' "Required command is missing: $command_name" >&2
        exit 1
    fi
done

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
    printf '%s\n' "Python 3.11 or newer is required." >&2
    exit 1
fi

if ! command -v pactl >/dev/null 2>&1; then
    printf '%s\n' "pactl is required. On Pop!_OS: sudo apt install pulseaudio-utils" >&2
    exit 1
fi

if ! pactl --format=json --help >/dev/null 2>&1; then
    printf '%s\n' "pactl 16.0 or newer with JSON output support is required." >&2
    printf '%s\n' "On Pop!_OS: sudo apt install pulseaudio-utils" >&2
    exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
app_dir="$data_home/linux-audio-output-switcher"
bin_dir="$HOME/.local/bin"
config_file="$config_home/linux-audio-output-switcher/config.toml"

install -d -m 0755 "$app_dir/linux_audio_output_switcher" "$bin_dir"

for source_file in "$script_dir"/src/linux_audio_output_switcher/*.py; do
    install -m 0644 \
        "$source_file" \
        "$app_dir/linux_audio_output_switcher/$(basename -- "$source_file")"
done

install -m 0755 \
    "$script_dir/scripts/linux-audio-output-switcher" \
    "$bin_dir/linux-audio-output-switcher"

if [ ! -e "$config_file" ] && [ "$configure" -eq 1 ]; then
    if ! "$bin_dir/linux-audio-output-switcher" configure; then
        printf '%s\n' "Output selection did not create a configuration." >&2
        printf '%s\n' "Run later: linux-audio-output-switcher configure" >&2
        exit 1
    fi
fi

printf '%s\n' "Installed linux-audio-output-switcher."
if [ -f "$config_file" ]; then
    printf '%s\n' "Run: linux-audio-output-switcher doctor"
else
    printf '%s\n' "No configuration exists. Run: linux-audio-output-switcher configure"
fi
