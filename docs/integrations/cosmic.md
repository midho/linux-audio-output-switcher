# COSMIC keyboard shortcuts

Add a shortcut in **Settings → Keyboard → Keyboard shortcuts → Custom**.

For a two-output switch, use:

```text
linux-audio-output-switcher toggle
```

You can also bind these commands:

| Action | Command |
|---|---|
| Next connected output | `linux-audio-output-switcher next` |
| Previous connected output | `linux-audio-output-switcher previous` |
| Show the current output | `linux-audio-output-switcher show` |
| Select one output | `linux-audio-output-switcher set OUTPUT_ID` |

Use the full path if COSMIC cannot find the command:

```text
/home/YOUR_NAME/.local/bin/linux-audio-output-switcher toggle
```

COSMIC keeps custom shortcuts together. The installer leaves that group alone
to protect shortcuts you already made.
