# Input Remapper and Vaydeer

In Input Remapper's graphical editor, map the Vaydeer's center key to F16.
Then bind F16 to the switch command in your desktop.

For example:

| Vaydeer button | Remapped key | Shortcut command |
|---|---|---|
| Center (5) | `KEY_F16` | `linux-audio-output-switcher toggle` |

Choose a normal **Key or Macro** output with `KEY_F16`. Leave repeat off. One
press should make one switch.

Test the preset, then turn on **Autoload** if you want it applied at login or
reconnect. Some Vaydeer keypads need a separate Linux activation step before
they send key events. That step is outside this project.
