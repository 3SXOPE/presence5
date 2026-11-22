# Presence5 v1.0.2

Presence5 can now start by itself. The payload hasn't changed, so the `presence.elf` from v1.0.0 still works. A fresh copy is included anyway.

## New: starting by itself

Two new **Startup** options in the app, for Linux and Windows:

- **Open presence5 when I log in (in the tray):** every time you log in to your PC, presence5 opens hidden in the tray instead of with its window open.
- **Start sharing as soon as presence5 opens:** presses **Start** for you with your saved settings. It's ticked for you when you turn on the first option.

With both on, there's nothing to click. Log in, and your PS5 shows up on Discord as soon as the console and Discord are on. If either isn't on yet, presence5 keeps waiting for it.

- **Linux:** adds `presence5.desktop` to `~/.config/autostart`. KDE, GNOME, Xfce, Cinnamon and most other desktops use that folder.
- **Windows:** adds presence5 to your startup apps, for your account only. No administrator rights needed.

Put the program somewhere it will stay (not Downloads) before you turn the option on. If you move it or download a new version, open it once and the startup entry follows it. Untick the option to remove the entry.

For everything else since the original project, see the [v1.0.0](https://github.com/3SXOPE/presence5/releases/tag/v1.0.0) and [v1.0.1](https://github.com/3SXOPE/presence5/releases/tag/v1.0.1) releases.

## Files

| File | What it is |
| --- | --- |
| `presence.elf` | The payload, for the PS5 (same code as v1.0.0) |
| `presence5-linux` | The Linux app |
| `presence5-linux-cli` | The Linux command-line version |
| `presence5-windows.exe` | The Windows app |
| `presence5-cli.exe` | The Windows command-line version |
| Source code (zip / tar.gz) | The full code for this version |

## Credits

Original project by [AmineWallah](https://github.com/AmineWallah). Changes by 3SXOPE, built with help from [Claude](https://claude.ai) by Anthropic.
