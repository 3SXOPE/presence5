# Presence5 v1.0.0

A fork of [AmineWallah/presence5](https://github.com/AmineWallah/presence5) (based on v0.1.1-beta). **Send the new `presence.elf` to your PS5**, the payload has changed.

Tested on firmware 13.42 with CachyOS and Windows 11.

## Changes compared to the original

### The Discord card
- The game's name is now the card's title and the text in Discord's member list, with *Playing on PlayStation 5* under it (before: "PlayStation 5" was the title).
- New *Browsing the menu* card on the home screen, which can be turned off.
- Settings, the PlayStation Store and the other system screens show their names instead of a code like `NPXS40008`.
- Streaming apps (Netflix, YouTube, Crunchyroll and others) show as *Watching*, music apps as *Listening to*.
- Game artwork from Discord's own game list first, then SteamGridDB (optional free key), then the PlayStation Store.
- New white-on-black PlayStation logo; you can use your own with the **Console logo** setting.
- Optional: your firmware and the exploit, HEN and tools you pick (Relapse, etaHEN, onionHEN, kstuff, ShadowMount+, or your own), on the home-screen card, in the logo's hover text and as buttons.

### Emulators and homebrew (new)
- **PS5SX2:** shows the PS2 game being played with its PS2 cover, and switches when you start another game inside the emulator. Fixes God of War (2005) showing the 2018 game's cover.
- **ProsperoEden:** shows the Switch game being played with its cover, and *Browsing the Switch library* in its menu.
- Homebrew icons from the PS5 homebrew catalog.

### Blu-ray and DVD (new)
- *Watching* the movie or show instead of `NPXS40140`, with its official title and poster.
- *4K Ultra HD* label for UHD discs; season and disc number for series (e.g. *Season 3 · Disc 1*).
- Posters from TMDB (optional key), Cinemeta, AniList, TVmaze, iTunes and Wikipedia, with the right season's poster where one exists.
- Works with discs that have a placeholder title, using the disc's label instead.

### Fixes
- When the network dropped, the payload looped on "Accept failed" notifications, slowed the console down and had to be re-sent. It now waits and reconnects by itself.
- The client keeps the card through a missed answer and checks less often while the console is off, instead of retrying constantly.
- Sending a new `presence.elf` now replaces one that is already running (before: "Bind failed").

### The app
- New Presence5 logo for the window, tray and Windows programs.
- Version shown in the window title and with `--version`.
- New settings: Poster key, Console logo, Show firmware, Show with it. New command-line options: `--tmdb-key`, `--logo-url`, `--no-firmware`, `--show`, `--poster-test`.
- `build-windows.bat` builds the Windows version with one double-click, and GitHub builds every release automatically.
- A ready-made source for PS5 Payload Manager with the Presence5 logo (`payload-manager/`).

## Files

| File | What it is |
| --- | --- |
| `presence.elf` | The payload, for the PS5 |
| `presence5-linux`, `presence5-linux-cli` | The app and the command-line version for Linux |
| `presence5-windows.exe`, `presence5-cli.exe` | The same for Windows |

## Credits
Original project by [AmineWallah](https://github.com/AmineWallah). Changes by 3SXOPE, built with help from [Claude](https://claude.ai) by Anthropic.



