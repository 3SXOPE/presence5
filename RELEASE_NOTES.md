# Presence5 v1.0.1

A fix for the Linux programs. The payload hasn't changed: keep the `presence.elf` from v1.0.0.

## Fixed

- **Covers, posters and the PlayStation logo were missing on CachyOS, Arch and other non-Ubuntu Linux systems.** The programs built by GitHub looked for the internet security certificates where Ubuntu keeps them, so every secure lookup failed and the cards fell back to the coloured PlayStation logo. The program now finds the certificates wherever your system keeps them, and carries its own copy as a fallback.
- When the logo can't be checked from your PC, the card now uses Presence5's own white-on-black logo instead of the coloured one.
- A warning in the log when secure connections fail, so a problem like this is easy to spot.

Windows wasn't affected.

For everything new in v1.0, see the [v1.0.0 release](https://github.com/3SXOPE/presence5/releases/tag/v1.0.0).

## Files

| File | What it is |
| --- | --- |
| `presence.elf` | The payload, for the PS5 (same as v1.0.0) |
| `presence5-linux`, `presence5-linux-cli` | The app and the command-line version for Linux |
| `presence5-windows.exe`, `presence5-cli.exe` | The same for Windows |

## Credits
Original project by [AmineWallah](https://github.com/AmineWallah). Changes by 3SXOPE, built with help from [Claude](https://claude.ai) by Anthropic.
