# Audio output settings

Start with the [quick-start guide](../README.md#quick-start-on-pop_os-2404).
This page explains output matching, exclusions and popup behavior. See the
[example configuration](../config.example.toml) for a complete settings file.

## Configuration

The default file is:

```text
~/.config/linux-audio-output-switcher/config.toml
```

Run configuration again if you want to change the initial choices. This
replaces saved labels and mute rules, so keep a copy if you have edited them:

```bash
linux-audio-output-switcher configure --force
```

For a non-interactive setup, use `--all`, or pass the exact sink names shown by
`list`. Sinks present now but left out of `--sink` are saved as exclusions:

```bash
linux-audio-output-switcher configure --all
linux-audio-output-switcher configure --sink SINK_NAME_1 --sink SINK_NAME_2
```

The generated file stores device properties that survive a restart. It keeps
rules for the outputs seen during setup. New outputs are discovered on each
command and are not written to the file. Temporary sink indexes are not used
as identities. A saved matching rule must identify one connected output. An
ambiguous match stops the switch.

Run `list --properties` to see the automatic stable match for each connected
output. A manual `[outputs.match]` table may use any string property reported
by `pactl`, plus the special keys `name` and `description`. Add more properties
when two devices would otherwise match the same rule.

Important settings:

- `auto_include_new`: add connected outputs that do not match a saved rule.
  New configurations set this to `true`. Older configurations without this
  setting keep their fixed-list behavior until you add `auto_include_new = true`.
- `auto_headphones` and `auto_other`: choose `"mute"`, `"unmute"`, or `"keep"`
  for newly discovered headphones and other outputs. Defaults are `"unmute"`
  and `"mute"`. This happens before the output becomes active.
- `move_playing_streams`: move active playback from managed outputs to the new
  output. Set it to `false` to change only the default for future playback.
- `popup.monitor_model`: optional exact GTK monitor model for popup placement.
  Leave it empty to let the Wayland compositor choose the display. It is
  ignored when `popup.all_monitors` is `true`.
- `popup.all_monitors`: show an identical popup on every connected monitor.
  The default is `false`.
- `[[outputs]].mute_on_activate`: use `"mute"`, `"unmute"`, or `"keep"` for
  that output. A manually written rule defaults to `"keep"` when this setting
  is omitted. Setup writes `"unmute"` for detected headphones and `"mute"`
  for other outputs. The rule is applied before the output becomes active,
  so a muted output cannot briefly play sound.
- `[[outputs]].include`: set this to `false` to keep a matched output out of
  the cycle. Exclusions saved during setup stay excluded after reconnecting.
- `[[outputs]]`: saved cycle order, label, icon, mute behavior, and property
  match rules. A saved rule takes priority over automatic detection.

Bluetooth rules use the device address, not its current audio profile. Saved
labels, exclusions, and mute rules therefore stay with the same device when it
changes between music and call modes. Two Bluetooth devices remain separate
because their addresses differ.

Headphone detection checks `device.form_factor` first, then the device icon
and description. It treats the standard `hands-free` form as a headset. Other
or unrecognized devices follow `auto_other` and are muted by default. If a
headset lacks useful metadata,
give it a saved `[[outputs]]` rule with `mute_on_activate = "unmute"`.

Your configuration may contain device serial numbers or Bluetooth addresses.
Keep it private. Git ignores `config.toml` in this repository.

While the popup is visible, another switch updates its label and icon on each
selected monitor and restarts the timer. Commands take a numbered place under
the switch lock. The popup ignores an older result if a newer command has taken
its place. One process owns the popup display at a time. If a second process
starts late, it sends its update to the owner and exits. After the timer, the
popup exits and the next switch gets a fresh Wayland surface. A private runtime
socket carries updates, and the next request replaces a socket left by a
crashed popup. Popup work runs after the audio switch. Its launch lock covers
only the short socket and ownership changes. Process startup and
shutdown replacement happen outside that lock, so a newer update can proceed
while an older popup is still starting or stopping. A full queue drops an
optional update, and startup has a one-second limit. An update queued as the
popup expires or shuts down is shown by that process or handed to a
replacement. A command rejected because another switch is running reports the
problem in the terminal without changing the popup.

## Cycle order and commands

`toggle` is an alias for `next`. With two included outputs, both commands
switch between the pair. If the current default is excluded, `next` selects
the first available output and `previous` selects the last.

The cycle uses saved outputs first, in their configured order, then new outputs
sorted by description and name. Disconnected and excluded outputs are skipped.
`next`, `previous`, and `toggle` need at least two included, connected outputs.
`set` reports a disconnected or excluded target and leaves the default alone.
Run `list` to see each connected output's cycle ID, or to check why it is not
in the cycle.

## Installation details

On first install, the configuration tool lists connected outputs. Press Enter
to include all of them, or enter their numbers in cycle order. Outputs you
leave out are excluded. A headset connected later joins on its own. You can
finish setup with fewer than two connected outputs, but cycling waits until
two included outputs are available.

On another distribution, install the equivalent packages from its package
manager. The popup dependencies are optional.

The installer writes to your user folders. It does not run `sudo`, edit desktop
shortcuts, or overwrite a configuration you already have. There is no
background service. Before copying files, it checks JSON support without
contacting the audio server. `doctor` then checks the client, server connection,
configuration, and selected outputs.

## Requirements

The switching core needs Python 3.11 or newer and a `pactl` client that accepts
`--format=json`. PulseAudio's client gained JSON output in version 16.0.
Compatible backports also work. This is a client requirement even when the
audio server is PipeWire.

The popup needs GTK 3, PyGObject, GTK Layer Shell and a Wayland desktop. It is
optional. Set `enabled = false` in the `[popup]` table on X11 or a compositor
without GTK Layer Shell. Audio switching still works.
