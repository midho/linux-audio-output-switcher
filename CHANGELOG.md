# Changelog

## 0.1.0 - 2026-10-12

### Fixed

- Keep Bluetooth settings when a device changes between music and call
  profiles. The physical address now identifies the device, while different
  Bluetooth devices keep separate rules.
- Treat the standard `hands-free` form as a headset so automatic rules unmute
  it before selection.
- Keep popup updates that arrive as the old popup expires or shuts down. The
  update now refreshes the old popup or starts a replacement.
- Keep popup startup and replacement waits outside the launch lock. A newer
  update can take ownership while an older popup is still starting or stopping.
- Report configuration read and write problems as clear configuration errors.
- Number actions under the switch lock. A late result from an older command
  cannot replace the popup for a newer switch.
- Give one popup process display ownership. A second process hands its update
  to the owner and exits. Stale initial updates exit quietly, and a slow start
  is reported separately from a failed start.
- Send popup updates without blocking the switch. If the live queue fills,
  drop the optional update, leave its socket in place, and report the drop.
- Keep lock-contention warnings in the terminal. They do not replace the
  winning command's popup.
- Remove generated Python bytecode for known application modules during
  uninstall without deleting unrelated files or saved configuration.
- Reuse one visible popup process for rapid output changes. New updates replace
  the label and icon on every popup copy and restart the hide timer, while safe
  socket ownership and launch locking recover from crashes and startup races.
- Require and check `pactl` JSON-output support before installation and before
  JSON list operations. Diagnostics now distinguish an unsupported client from
  an unavailable audio server.

### Added

- Add newly connected outputs to the cycle without rewriting configuration.
  Keep initial exclusions across reconnects and skip outputs that disconnect.
- Unmute newly found headphones and headsets before selecting them. Mute other
  new outputs by default. Saved mute rules take priority.
- Show cycle IDs and exclusions in `list`, and allow first-run setup with one
  connected output or `configure --all` without a terminal.
- Fake-`pactl` tests for later device connections, exclusions, and disconnects.
- Clean-user installation tests that run the installed launcher and verify
  configuration, diagnostics, update replacement, uninstall, and purge.
- Stateful fake-`pactl` command-line tests covering real subprocess arguments,
  routing state, mute and move order, malformed output, failures, and timeouts.
- Cycle forward or backward through selected audio outputs.
- Select a configured output directly.
- Stable output matching from PipeWire/PulseAudio properties.
- Optional movement of playing streams between managed outputs.
- Per-output mute, unmute, or unchanged behavior when an output is selected.
- Optional popup copies on every connected monitor.
- Optional non-focusing COSMIC Wayland popup.
- Interactive configuration, output listing, and read-only diagnostics.
- Generic Linux desktop integration and inspectable match properties.
- User-only installer, uninstaller, integration guides, and tests.
