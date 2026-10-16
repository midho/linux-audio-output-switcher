# Linux Audio Output Switcher

Use one key to switch between speakers, headphones and other connected audio
outputs. Choose the outputs you want, then cycle through them in order.

## Features

- Cycle two or more chosen outputs.
- New devices join automatically.
- Disconnected devices leave the cycle, and saved exclusions still apply when they reconnect.
- Detected headphones unmute on selection, while other new outputs start muted by default.
- Move playing audio when the app allows it.
- Choose one screen for the popup, or show it on all screens.

## Screenshots

<img src="docs/screenshots/headphones.png" alt="Audio output popup with a headphones icon and the label Headphones" width="250">
<img src="docs/screenshots/speakers.png" alt="Audio output popup with a speakers icon and the label Speakers" width="210">

Real popup windows, captured with example labels.

## Before you start

You need Linux, Python 3.11 or newer, and PulseAudio or PipeWire's
PulseAudio-compatible service. The `pactl` command must support JSON output,
available in version 16.0 or a compatible backport.

The popup needs Wayland and GTK Layer Shell. It was tested with COSMIC.
Audio switching also works on other desktops. On X11 or a compositor without
GTK Layer Shell, turn the popup off in the settings file.

## Quick start on Pop!_OS 24.04

1. Open a terminal. Install the required packages:

   ```bash
   sudo apt install git python3 pulseaudio-utils python3-gi gir1.2-gtk-3.0 gir1.2-gtklayershell-0.1
   ```

   On another Linux distribution, install the equivalent packages with its
   package manager. The popup packages are optional.

2. Download and install the program:

   ```bash
   git clone https://github.com/midho/linux-audio-output-switcher.git
   cd linux-audio-output-switcher
   ./install.sh
   ```

3. Choose your outputs when setup asks.

   Press Enter to include all connected outputs. Or enter their numbers in
   the order you want, such as `1 3 2`. Any connected outputs you leave out
   stay excluded. Devices connected later join automatically.

   You can finish setup with one output, but cycling needs at least two
   included outputs connected at the same time.

4. Check the setup and show the current output:

   ```bash
   ~/.local/bin/linux-audio-output-switcher doctor
   ~/.local/bin/linux-audio-output-switcher show
   ```

   These commands do not switch the output.

5. Try one switch:

   ```bash
   ~/.local/bin/linux-audio-output-switcher next
   ```

   With the generated settings, detected headphones unmute when selected.
   Other outputs start muted. Use your desktop's sound controls to unmute
   them when ready.

## Choose your key

Open **Settings → Keyboard → Keyboard shortcuts → Custom** in COSMIC. Add a
custom shortcut, paste this command and choose a free key combination:

```text
linux-audio-output-switcher next
```

Each press selects the next connected output. This works with two outputs
or a longer list. To cycle backward, use `linux-audio-output-switcher previous`.

See the [COSMIC guide](docs/integrations/cosmic.md),
[other-desktop guide](docs/integrations/generic-desktop.md), or
[Input Remapper and Vaydeer guide](docs/integrations/input-remapper.md). They
also explain how to use the full command path if your shortcut cannot find it.

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

`toggle` means the same as `next`. `set OUTPUT_ID` selects a particular output.
Run `list` to find its ID. Disconnected or excluded targets cannot be selected.

## Configuration

Your settings file is:

```text
~/.config/linux-audio-output-switcher/config.toml
```

The [settings guide](docs/configuration.md) covers output order, exclusions,
friendly names, Bluetooth matching and mute rules. Use
[config.example.toml](config.example.toml) as a reference.

- Set `auto_include_new = false` to keep a fixed list of outputs.
- Change `auto_headphones` and `auto_other` to choose `"mute"`, `"unmute"` or
  `"keep"` for newly discovered devices.
- Set `move_playing_streams = false` to leave existing playback where it is.
- Set `all_monitors = true` in the `[popup]` table to show the popup on every screen.

Saved device rules take priority over automatic rules. If an unfamiliar
headset starts muted, give it its own rule in the settings file.

Keep the file private. It can contain device serial numbers or Bluetooth
addresses. Re-running `configure --force` replaces saved labels and mute
rules, so make a copy before using it.

## Troubleshooting

- **A device is missing:** connect it and check that it appears in your
  desktop's sound settings and in `linux-audio-output-switcher list`.
- **Speakers are silent after switching:** they start muted with the default
  setup. Unmute them with your desktop's sound controls.
- **One app stays on the old output:** reopen its playback or choose the
  output inside that app.
- **No popup:** switching still works. Check the optional Wayland dependencies,
  or set `enabled = false` in the `[popup]` table to disable it.
- **JSON support error:** install `pactl` 16.0 or newer, or a compatible backport.

Run these checks if the problem remains:

```bash
linux-audio-output-switcher doctor
linux-audio-output-switcher list
pactl info
```

The switcher changes playback outputs. Microphones and volume levels are
left alone. An application that manages its own output can ignore the system
default or move itself again.

## Update and remove

Open a terminal in the project folder to download an update and install it:

```bash
git pull --ff-only
./install.sh
```

Your settings are kept. To remove the program while keeping them:

```bash
./uninstall.sh
```

To remove the settings too:

```bash
./uninstall.sh --purge
```

The uninstaller keeps unrelated files. There is no background service to stop.

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
