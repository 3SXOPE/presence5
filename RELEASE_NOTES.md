# Presence5 v1.0.0

The first full release. Send the new `presence.elf` to the console: the payload has changed a lot
since v0.1.1-beta.

## The card

- The game's name is the card's title and the text in Discord's member list, with *Playing on
  PlayStation 5* under it.
- Game artwork from Discord's own game list, then SteamGridDB (optional key), then the
  PlayStation Store.
- *Browsing the menu* on the home screen; Settings, the PlayStation Store and the other system
  screens are named instead of showing a code.
- Streaming apps (Netflix, YouTube, Crunchyroll…) show as *Watching*, music apps as *Listening to*.
- A white-on-black PlayStation logo, or your own with the **Console logo** setting.
- Optional: the console's firmware, plus the exploit, HEN and tools you tick (Relapse, etaHEN,
  onionHEN, kstuff, ShadowMount+, or your own), on the home-screen card, in the small logo's hover
  text and as buttons.

## Emulators and homebrew

- **PS5SX2:** the PS2 game being played, with its PS2 cover, switching when you start another game
  inside the emulator.
- **ProsperoEden:** the Switch game being played, with its cover, and *Browsing the Switch
  library* in its menu.
- Homebrew icons from the PS5 homebrew catalog.

## Blu-ray and DVD

- *Watching* the movie or show, with its official title and poster, *4K Ultra HD* for UHD discs,
  and the season and disc number for series.
- Covers from TMDB (optional key), Cinemeta, AniList, TVmaze, iTunes and Wikipedia, with the right
  season's poster where one exists.

## Reliability

- When the network drops, the payload waits and reopens its connection instead of flooding the
  screen with "Accept failed" and slowing the console down. It no longer needs to be re-sent
  afterwards.
- The client keeps the card through a missed answer and checks less often while the console is
  off.
- Sending a new `presence.elf` replaces one that is already running.

## Files

| File | What it is |
| --- | --- |
| `presence.elf` | The payload, for the PS5 |
| `presence5-linux`, `presence5-linux-cli` | The app and the command-line version for Linux |
| `presence5-windows.exe`, `presence5-cli.exe` | The same for Windows |

Tested on firmware 13.42 with CachyOS. Discord shows the card only when its desktop app is open and
activity sharing is on.
