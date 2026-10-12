# Generic Linux desktop shortcuts

The command works outside COSMIC. Bind it with your desktop, window manager,
macro pad, or any shortcut tool that can run a program.

For a two-output switch, bind a key to:

```text
linux-audio-output-switcher toggle
```

Other bindings you might want:

| Action | Command |
|---|---|
| Next connected output | `linux-audio-output-switcher next` |
| Previous connected output | `linux-audio-output-switcher previous` |
| Show the current output | `linux-audio-output-switcher show` |
| Select one configured output | `linux-audio-output-switcher set OUTPUT_ID` |

Use the full path if the desktop cannot find commands in `~/.local/bin`:

```text
/home/YOUR_NAME/.local/bin/linux-audio-output-switcher toggle
```

The popup needs Wayland and GTK Layer Shell. On X11 or an unsupported Wayland
compositor, set `popup.enabled = false`. Audio switching still works.
