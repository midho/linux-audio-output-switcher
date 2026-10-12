# Linux Audio Output Switcher

Cycle through the Linux audio outputs connected right now. New outputs join the
cycle automatically unless you have excluded them. The switcher works with
PulseAudio and PipeWire's PulseAudio-compatible service. It can move playing
audio and show an optional Wayland popup without taking keyboard focus.

## Supported in version 0.1.0

- Linux with Python 3.11 or newer and a `pactl` client that supports JSON
  output. PulseAudio's `pactl` gained this feature in version 16.0.
- PulseAudio or PipeWire's PulseAudio-compatible service.
- Audio outputs visible to `pactl`, including USB, HDMI, Bluetooth, PCI, and
  virtual outputs.
- Automatic inclusion of new outputs and skipping of disconnected ones.
- Keyboard shortcuts from any desktop that can run a command.
- An optional GTK Layer Shell popup on Wayland, tested with COSMIC.

The switcher changes the default output. It can also move playing streams that
are currently on one of its managed outputs and optionally mute or unmute the
new output. It does not change microphones, output volume, or stream volume.
An application that manages its own output may ignore the system default or
move itself again.

## Install

The switching core needs Python 3.11 or newer and `pactl` JSON output support.
The client must accept `pactl --format=json`. Compatible backports
also work. This is a requirement for the `pactl` command-line client even when
the audio server is PipeWire. The popup also needs GTK 3, PyGObject, GTK Layer
Shell, and a Wayland desktop.

On Debian, Ubuntu, or Pop!_OS, install them with:

```bash
sudo apt install pulseaudio-utils python3-gi gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1
```

Then run:

```bash
git clone https://github.com/midho/linux-audio-output-switcher.git
cd linux-audio-output-switcher
./install.sh
```

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

Run the health check:

```bash
linux-audio-output-switcher doctor
```

## Commands

```text
linux-audio-output-switcher next
linux-audio-output-switcher previous
linux-audio-output-switcher toggle
linux-audio-output-switcher set OUTPUT_ID
linux-audio-output-switcher show
linux-audio-output-switcher list
linux-audio-output-switcher list --properties
linux-audio-output-switcher doctor
linux-audio-output-switcher configure
```

`toggle` is an alias for `next`. With two included outputs, both commands
switch between the pair. If the current default is excluded, `next` selects
the first available output and `previous` selects the last.

The cycle uses saved outputs first, in their configured order, then new outputs
sorted by description and name. Disconnected and excluded outputs are skipped.
`next`, `previous`, and `toggle` need at least two included, connected outputs.
`set` reports a disconnected or excluded target and leaves the default alone.
Run `list` to see each connected output's cycle ID, or to check why it is not
in the cycle.

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

## Connect a keyboard control

- [Generic Linux desktop shortcuts](docs/integrations/generic-desktop.md)
- [COSMIC shortcuts](docs/integrations/cosmic.md)
- [Input Remapper and Vaydeer](docs/integrations/input-remapper.md)

The installer does not edit either application's configuration files.

## Troubleshooting

```bash
linux-audio-output-switcher doctor
linux-audio-output-switcher list
pactl info
```

- If an output is missing, connect it and make sure it appears in your desktop's
  sound settings and in `linux-audio-output-switcher list`.
- If `doctor` says JSON output is unsupported, install `pactl` 16.0 or newer,
  or a distribution package that backports `--format=json` support.
- If JSON support passes but the server connection fails, check that PulseAudio
  or PipeWire's PulseAudio-compatible service is running for the current user.
- If a match becomes ambiguous, run `configure --force` and select the outputs
  again.
- If one application stays on the old output, reopen its playback or choose the
  output inside that application.
- The popup is optional. Switching still works when GTK Layer Shell is missing.
- If a popup process crashes, the next popup request replaces its stale private
  runtime socket. Unrelated files are never removed from that path.
- On X11 or a Wayland compositor without GTK Layer Shell, set
  `popup.enabled = false`. The command-line switching core still works.

## Update and remove

Run `./install.sh` again to update program files. Your configuration is kept.

Remove the program while keeping the configuration:

```bash
./uninstall.sh
```

The uninstaller removes the installed program files and their Python cache
files. It keeps your configuration and unrelated files in the same directory.

Also remove the configuration:

```bash
./uninstall.sh --purge
```

## Development

Run the hardware-free tests:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
bash -n install.sh uninstall.sh scripts/linux-audio-output-switcher
```

The tests run the real command-line app against a fake `pactl` that records
routing state. They check listing, device matching, automatic discovery,
exclusions, disconnects, mute-before-activation, stream movement, command
order, bad JSON, failures, timeouts, and rejected requests. Other tests cover
full popup queues, popup ownership, update order, and installer behavior. One
test pauses an older switch just before its popup, runs a newer switch, then
checks that the old label cannot replace the new one. Popup tests also pause
startup and shutdown replacement to make sure the newest update still gets one
owner. Installer tests work in a temporary user folder and check configuration,
diagnostics, update, uninstall, and purge. These tests leave your audio routing
alone. Live tests are separate because they change the default output.

## License

MIT. PipeWire, PulseAudio, and `pactl` are separate system components under
their own licenses.
