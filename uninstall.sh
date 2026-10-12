#!/bin/sh
set -eu

purge=0
if [ "${1:-}" = "--purge" ]; then
    purge=1
    shift
fi
if [ "$#" -ne 0 ]; then
    printf '%s\n' "Usage: ./uninstall.sh [--purge]" >&2
    exit 2
fi

data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
app_dir="$data_home/linux-audio-output-switcher"
package_dir="$app_dir/linux_audio_output_switcher"
cache_dir="$package_dir/__pycache__"
bin_file="$HOME/.local/bin/linux-audio-output-switcher"
config_dir="$config_home/linux-audio-output-switcher"

rm -f -- "$bin_file"
for module_name in \
    __init__ __main__ cli config popup pulse runtime switcher; do
    rm -f -- "$package_dir/$module_name.py" "$package_dir/$module_name.pyc"
    for cache_file in "$cache_dir/$module_name".*.pyc; do
        if [ -e "$cache_file" ] || [ -L "$cache_file" ]; then
            rm -f -- "$cache_file"
        fi
    done
done
rmdir -- "$cache_dir" 2>/dev/null || true
rmdir -- "$package_dir" 2>/dev/null || true
rmdir -- "$app_dir" 2>/dev/null || true

if [ "$purge" -eq 1 ]; then
    rm -f -- "$config_dir/config.toml"
    rmdir -- "$config_dir" 2>/dev/null || true
    printf '%s\n' "Removed the program and configuration."
else
    printf '%s\n' "Removed the program. The configuration was kept."
fi
