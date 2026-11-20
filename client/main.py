from ps5 import (fetch_state, GameState, ConsoleUnreachable, fetch_param, fetch_disc, DiscInfo,
                 fetch_firmware)
from titles import TitleInfo, title_from_param, lookup_ps4_title, store_image_for
from discord_games import GameList
from artwork import find_artwork, ArtworkError, KINDS as ART_KINDS
from homebrew import homebrew_icon
from version import __version__
from media import (DISC_PLAYER, system_app, app_kind, find_poster, title_of_disc,
                   wikipedia_game_cover)
import ps5sx2
from ps5sx2 import EmulatorAPI, is_ps5sx2
import eden
from eden import fetch_eden, is_eden
from discord_ipc import DiscordIPC, DiscordUnavailable, APP_ID, default_discord_path
import os
import time
import urllib.error
import urllib.request
import logging
import argparse
import ipaddress

log = logging.getLogger(__name__)

PAYLOAD_PORT = 8000  # Fixed in the PS5 payload

CONSOLE_NAME = "PlayStation 5"
# The line under the game's name while playing.
PLAYING_TEXT = "Playing on PlayStation 5"
WATCHING_TEXT = "Watching on PlayStation 5"
LISTENING_TEXT = "Listening on PlayStation 5"
# The PlayStation logo Discord shows. Discord can only show pictures from a public
# web address, so the logo in assets/ps-logo.png has to be online somewhere. The
# first of these that loads is used; --logo-url (or the GUI's "Console logo")
# goes in front.
CONSOLE_IMAGES = [
    "https://raw.githubusercontent.com/AmineWallah/presence5/main/assets/ps-logo.png",
    "https://raw.githubusercontent.com/sxope3/presence5/main/assets/ps-logo.png",
    # The same white-on-black look, drawn from the Simple Icons PlayStation mark by
    # the images.weserv.nl image service.
    "https://images.weserv.nl/?url=cdn.simpleicons.org/playstation/white"
    "&w=360&h=360&fit=contain&cbg=black&bg=black&output=png",
    # Last resort, always online: dashboard-icons (Apache 2.0).
    "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/playstation.png",
]
CONSOLE_IMAGE = CONSOLE_IMAGES[-1]
MENU_TEXT = "Browsing the menu"

# Discord's activity types: the header word on the card and in the member list.
PLAYING, LISTENING, WATCHING = 0, 2, 3
DISC_RETRIES = 3  # polls to wait for a disc that wasn't readable yet

# Discord's status_display_type: which line the member list shows after "Playing".
SHOW_NAME, SHOW_STATE, SHOW_DETAILS = 0, 1, 2

UNREACHABLE_MAX_WAIT = 30  # seconds between checks while the console is off or offline
# What the user can choose to show next to the firmware (the exploit, HEN and
# tools they use), in the order they appear, with a page for a button.
SETUP_ITEMS = {
    "Relapse": "https://github.com/ntfargo/Relapse-Exploit",
    "etaHEN": "https://github.com/etaHEN/etaHEN",
    "onionHEN": "https://github.com/aydencharles/onionHEN",
    "kstuff": "https://github.com/EchoStretch/kstuff",
    "ShadowMount+": None,
}
FIRMWARE_REFRESH = 600  # seconds between asking for the firmware again
_UNSET = object()  # "nothing polled yet", so the first answer always counts as a change


def image_online(url: str) -> bool:
    try:
        # A small GET rather than HEAD: some image services refuse HEAD.
        req = urllib.request.Request(url, headers={"Range": "bytes=0-1023",
                                                   "User-Agent": "presence5"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            resp.read(1024)
            return resp.headers.get_content_maintype() == "image"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def pick_console_image(custom: str | None = None) -> str:
    """The first console logo address that serves an image."""
    candidates = ([custom] if custom else []) + CONSOLE_IMAGES
    for url in candidates[:-1]:
        if image_online(url):
            log.info("console logo: %s", url)
            return url
        log.info("console logo not found at %s", url)
    return candidates[-1]


class PresenceClient:
    def __init__(self, host: str, port: int, discord_path: str | None = None,
                 show_menu: bool = True, sgdb_key: str | None = None, art_kind: str = "logo",
                 logo_url: str | None = None, tmdb_key: str | None = None,
                 show_firmware: bool = True, setup: list | None = None):
        self.host = host
        self.port = port
        self.show_menu = show_menu
        self.sgdb_key = sgdb_key or None
        self.art_kind = art_kind
        self.ipc = DiscordIPC(APP_ID, discord_path)
        self.previous_id = _UNSET
        self.desired = None
        self.synced = False
        self.discord_down = False
        self.title_cache = {}
        self.games = GameList()  # Discord's own game list, for icons
        self.platform_ok = True   # set False once Discord refuses the "platform" field
        self.buttons_ok = True    # set False once Discord refuses buttons
        self.show_firmware = show_firmware
        self.firmware: str | None = None
        self.firmware_at = -1e9
        # The exploit, HEN and tools picked in the settings, e.g. ["Relapse", "kstuff"].
        self.setup = [x.strip() for x in (setup or []) if x and x.strip()]
        self.art_warning: str | None = None  # last SteamGridDB problem, for the GUI
        self.console_image = pick_console_image(logo_url or None)
        self.tmdb_key = tmdb_key or None  # TMDB, for movie and TV posters
        self.disc_retries = 0
        self.posters: dict[tuple, object] = {}
        self.menu_text = MENU_TEXT
        self.emu = EmulatorAPI(host)  # PS5SX2's settings page, for the PS2 game playing

        self.title_id: str | None = None
        self.title: TitleInfo | None = None
        self.started_at: int | None = None  # Unix time the game (or menu) started
        self.in_menu = False
        self.misses = 0  # checks in a row the console didn't answer
        self.eden_covers: dict[str, tuple[str | None, str, str]] = {}
        self.switch_covers = eden.SwitchCovers()
        self.eden_warned = False

    @property
    def console_reachable(self) -> bool:
        return self.previous_id != "unreachable"

    def wait_time(self, interval: float) -> float:
        """Seconds until the next check. While the console doesn't answer, the
        checks get further apart (up to every 30 s) instead of hammering it."""
        if self.misses <= 1:
            return interval
        return min(UNREACHABLE_MAX_WAIT, max(interval, interval * 2 ** min(self.misses - 1, 4)))

    @property
    def discord_connected(self) -> bool:
        return self.ipc.connected

    def resolve_title(self, title_id: str) -> TitleInfo | None:
        if title_id in self.title_cache:
            log.debug("title cache hit for %s", title_id)
            return self.title_cache[title_id]

        # 1. The game's own param.json, from the console (PS5 titles, homebrew).
        try:
            param = fetch_param(self.host, self.port)
        except ConsoleUnreachable:
            log.warning("could not fetch title info for %s, console unreachable", title_id)
            return None

        info = title_from_param(param, with_image=False) if param is not None else None
        source = "param.json"

        # 2. Sony's title metadata service (PS4 titles, which have no param.json).
        if info is None:
            log.debug("no param.json for %s, trying the metadata service", title_id)
            info = lookup_ps4_title(title_id)
            source = "metadata service"

        # Artwork, by the name found above. The first source with a picture wins.
        if info is not None:
            art, art_source = None, None
            emulator = is_ps5sx2(title_id)
            # a. Homebrew: the community catalog's icon, by title ID.
            if emulator:
                art, art_source = ps5sx2.ICON_URL, "PS5SX2"
            elif is_eden(title_id):
                art, art_source = eden.ICON_URL, eden.APP_NAME
            else:
                listed = homebrew_icon(title_id)
                if listed:
                    art, art_source = listed[1], "homebrew catalog"
            # b. Discord's own game list: the icon Discord shows for the game on PC.
            #    Not for PS5SX2's tiles: they're named after PS2 games, and the name
            #    "God of War" would find the 2018 game.
            if not art:
                art = self.games.icon_url(info.name)
                if art:
                    art_source = "Discord"
            # c. SteamGridDB, when a key is set.
            if not art and self.sgdb_key:
                try:
                    art = find_artwork(info.name, self.sgdb_key, self.art_kind)
                    self.art_warning = None
                except ArtworkError as e:
                    log.warning("%s; using the store cover instead", e)
                    self.art_warning = str(e)
                if art:
                    art_source = f"SteamGridDB {self.art_kind}"
            # d. The PlayStation Store cover (PS5) or the metadata service's icon (PS4).
            if not art:
                art = store_image_for(param) if param is not None else info.image_url
                art_source = "store" if art else None
            info = TitleInfo(name=info.name, image_url=art)
            source += f", artwork from {art_source or 'nowhere'}"

        self.title_cache[title_id] = info

        if info is None:
            log.info("no title info for %s, using the title ID", title_id)
        else:
            log.info("resolved %s -> %s (from %s)", title_id, info.name, source)
        return info

    def step(self) -> None:
        # 1. What is the console doing?
        try:
            state = fetch_state(self.host, self.port)
            if self.misses:
                log.info("console is answering again")
                self.firmware_at = -1e9  # it may have been restarted: ask again
            self.misses = 0
            self._refresh_firmware()
            current_id = state.title_id if state else None
            emu = None
            if state is not None and is_ps5sx2(state.title_id):
                # Inside PS5SX2 the PS2 game can change without the title ID changing.
                emu = self.emu.state()
                if emu is not None:
                    current_id = (state.title_id, emu.serial or emu.title or "menu")
            eden_state = None
            if state is not None and is_eden(state.title_id):
                # The same for ProsperoEden; the payload reads what it is doing.
                eden_state = fetch_eden(self.host, self.port)
                if eden_state is not None:
                    current_id = (state.title_id, eden_state.file if eden_state.in_game else "menu")
                elif not self.eden_warned:
                    log.warning("the payload can't tell what ProsperoEden is playing: send this "
                                "version's presence.elf to the console")
                    self.eden_warned = True

            if current_id != self.previous_id:
                self.disc_retries = 0
                if emu is not None:
                    self._show_emulator(state, emu)
                elif eden_state is not None:
                    self._show_eden(state, eden_state)
                elif state is not None and system_app(state.title_id):
                    self._show_system(state, *system_app(state.title_id))
                elif state is not None:
                    info = self.resolve_title(state.title_id)
                    kind = app_kind(info.name) if info else "play"
                    log.info("%s started: %s (%s, elapsed %ss)",
                             "game" if kind == "play" else "app",
                             info.name if info else state.title_id, state.title_id, state.elapsed)
                    self.desired = build_activity(state, info, self.console_image, kind)
                    self.title_id = state.title_id
                    self.title = info
                    self.started_at = self.desired["timestamps"]["start"]
                    self.in_menu = False
                else:
                    self._clear_game()
                    if self.show_menu:
                        log.info("console on the home screen")
                        self.desired = build_menu_activity(int(time.time()), self.console_image)
                        self.started_at = self.desired["timestamps"]["start"]
                        self.in_menu = True
                        self.menu_text = MENU_TEXT
                    else:
                        log.info("console idle, clearing presence")
                        self.desired = None
                self.synced = False
                self.previous_id = current_id

            elif self.disc_retries and state is not None and state.title_id == DISC_PLAYER:
                # The disc wasn't readable a moment ago; the drive may still be spinning up.
                self._show_disc(state)
                self.synced = False

        except ConsoleUnreachable:
            self.misses += 1
            # One missed answer (a Wi-Fi blip, a busy console) keeps the card as it is.
            if self.misses >= 2:
                if self.previous_id != "unreachable":
                    log.warning("console unreachable at %s:%s, clearing presence; will keep "
                                "checking, less often", self.host, self.port)
                    self.desired = None
                    self.synced = False
                    self._clear_game()
                self.previous_id = "unreachable"

        # 2. Did Discord go away while nothing else changed?
        if self.synced and not self.ipc.is_alive():
            log.warning("lost the connection to Discord")
            self.ipc.close()
            self.synced = False

        # 3. Bring Discord in line with what should be showing.
        if not self.synced:
            try:
                if not self.ipc.connected:
                    self.ipc.connect()
                    log.info("connected to Discord at %s", self.ipc.path)
                activity = self._with_setup(self.desired)
                if activity and not self.platform_ok:
                    activity = {k: v for k, v in activity.items() if k != "platform"}
                if activity and not self.buttons_ok:
                    activity = {k: v for k, v in activity.items() if k != "buttons"}
                accepted = self.ipc.set_activity(activity)
                if not accepted and activity and ("platform" in activity or "buttons" in activity):
                    # Optional fields: drop them for the rest of the run and resend.
                    if "platform" in activity:
                        log.warning("Discord refused the PlayStation 5 platform tag; sending without it")
                        self.platform_ok = False
                    if "buttons" in activity:
                        log.warning("Discord refused the button; sending without it")
                        self.buttons_ok = False
                    activity = {k: v for k, v in activity.items() if k not in ("platform", "buttons")}
                    self.ipc.set_activity(activity)
                self.synced = True
                self.discord_down = False
                log.debug("presence %s", "updated" if self.desired else "cleared")
            except DiscordUnavailable:
                if not self.discord_down:
                    log.warning("Discord is not reachable (%s). Is it running? Will keep retrying.",
                                self.ipc.requested_path or "looked in the usual places")
                self.discord_down = True

    def _refresh_firmware(self) -> None:
        if not self.show_firmware or time.monotonic() - self.firmware_at < FIRMWARE_REFRESH:
            return
        self.firmware_at = time.monotonic()
        try:
            found = fetch_firmware(self.host, self.port)
        except ConsoleUnreachable:
            return
        if found is None and self.firmware is None:
            log.info("this payload doesn't report the firmware; send this version's presence.elf")
        if found != self.firmware:
            if found:
                log.info("console firmware: %s", found)
            self.firmware = found
            self.synced = False  # resend the card with it

    def _with_setup(self, activity: dict | None) -> dict | None:
        """The card with the firmware and the picked exploit, HEN and tools:
        - home-screen and system-app cards: on the last line, plus buttons (up
          to two) to the pages of the picked items;
        - every card: when hovering over the small logo (or the PlayStation logo)."""
        firmware = self.firmware if self.show_firmware else None
        parts = list(self.setup)
        if not activity or (not firmware and not parts):
            return activity
        summary = " \u00b7 ".join(([f"FW {firmware}"] if firmware else []) + parts)
        activity = dict(activity)
        assets = dict(activity.get("assets") or {})
        if assets.get("small_image"):
            assets["small_text"] = f"{assets.get('small_text') or CONSOLE_NAME} \u00b7 {summary}"[:128]
        elif assets.get("large_image") == self.console_image:
            assets["large_text"] = f"{assets.get('large_text') or CONSOLE_NAME} \u00b7 {summary}"[:128]
        activity["assets"] = assets
        if activity.get("name") == CONSOLE_NAME and "state" not in activity:
            line = [f"Firmware {firmware}"] if firmware else []
            activity["state"] = " \u00b7 ".join(line + parts)[:128]
            fw = f" \u00b7 FW {firmware}" if firmware else ""
            buttons = []
            for item in parts:
                url = SETUP_ITEMS.get(item)
                if url and len(buttons) < 2:
                    buttons.append({"label": (item if buttons else f"{item}{fw}")[:32], "url": url})
            if buttons:
                activity["buttons"] = buttons
        return activity

    def _show_system(self, state: GameState, kind: str, text: str) -> None:
        """One of the PS5's own apps: a menu-like screen, or something being watched."""
        self._clear_game()
        self.title_id = state.title_id
        if kind == "menu":
            if self.show_menu:
                log.info("system app %s: %s", state.title_id, text)
                since = int(time.time()) - state.elapsed
                self.desired = build_menu_activity(since, self.console_image, text)
                self.started_at = since
                self.in_menu = True
                self.menu_text = text
            else:
                self.desired = None
        elif state.title_id == DISC_PLAYER:
            self._show_disc(state)
        else:
            since = int(time.time()) - state.elapsed
            self.title = TitleInfo(name=text, image_url=None)
            self.desired = build_watch_activity(text, None, since, self.console_image)
            self.started_at = since

    def _show_disc(self, state: GameState) -> None:
        """A movie or TV disc in the Blu-ray player."""
        try:
            disc = fetch_disc(self.host, self.port, debug=log.isEnabledFor(logging.DEBUG))
        except ConsoleUnreachable:
            disc = None
        if disc is None:
            log.warning("the payload didn't answer the DISC question: send this version's "
                        "presence.elf to the console")
        elif not disc.name:
            log.info("disc reply: %r", disc.raw)
        if disc is None or (disc.kind == "NONE" and not disc.label):
            # Not readable yet, or a payload that predates the DISC command.
            self.disc_retries = 0 if self.disc_retries >= DISC_RETRIES else self.disc_retries + 1
        else:
            self.disc_retries = 0
        disc = disc or DiscInfo("NONE")

        if disc.kind == "BD":
            fmt = "4K Ultra HD" if disc.uhd else "Blu-ray"
        else:
            fmt = {"DVD": "DVD"}.get(disc.kind)
        title, poster, shown = None, None, None
        log.info("disc metadata: name %r, label %r", disc.name, disc.label)
        title = title_of_disc(disc.name, disc.label)
        if title:
            cache_key = (title.name, title.season)
            if cache_key not in self.posters:
                self.posters[cache_key] = find_poster(title, self.tmdb_key)
            found = self.posters[cache_key]
            shown = found.title if found else title.name
            poster = found.url if found else None
        since = int(time.time()) - state.elapsed
        shown = shown or fmt or "Blu-ray Disc Player"
        line = " · ".join(p for p in (title.detail() if title else "", fmt or "") if p) or None
        log.info("disc: %s (%s)", shown, line or "unreadable")
        self.desired = build_watch_activity(shown, poster, since, self.console_image, line)
        self.title = TitleInfo(name=shown, image_url=poster)
        self.title_id = state.title_id
        self.started_at = since
        self.in_menu = False

    def _show_emulator(self, state: GameState, emu: "ps5sx2.EmuState") -> None:
        # The clock restarts when the PS2 game changes inside PS5SX2; when PS5SX2
        # was only just noticed, it runs from when PS5SX2 started.
        prev = self.previous_id
        was_emulator = (isinstance(prev, tuple) and is_ps5sx2(prev[0])) or \
            (isinstance(prev, str) and is_ps5sx2(prev))
        since = int(time.time()) - (0 if was_emulator else state.elapsed)
        if emu.in_game:
            cover = self.emu.cover_url(emu.serial)
            log.info("PS2 game: %s (%s)", emu.title, emu.serial or "no serial")
            self.desired = build_ps2_activity(emu.title, cover, since, self.console_image)
            self.title = TitleInfo(name=emu.title, image_url=cover or ps5sx2.ICON_URL)
        else:
            log.info("in PS5SX2's menu")
            self.desired = build_emulator_menu_activity(since, self.console_image)
            self.title = TitleInfo(name=ps5sx2.APP_NAME, image_url=ps5sx2.ICON_URL)
        self.title_id = emu.serial or state.title_id
        self.started_at = since
        self.in_menu = False

    def _eden_cover(self, st: "eden.EdenState") -> tuple[str | None, str, str]:
        """(cover URL, where it came from, the game's official name) for a game
        running in ProsperoEden."""
        if st.file in self.eden_covers:
            return self.eden_covers[st.file]
        art, source, name = None, "nowhere", st.title
        for label, lookup in (("Rich Presence U", self.switch_covers.rich_presence_u),
                              ("Nintendo", self.switch_covers.nintendo_eu)):
            try:
                found = lookup(st.title)
            except Exception as e:  # one broken source mustn't stop the others
                log.debug("%s cover lookup failed: %r", label, e)
                found = None
            if found:
                name, art, source = found[0], found[1], label
                break
        if not art and st.program_id:  # the game's own icon, by its program ID
            url = eden.ICON_BY_ID.format(program_id=st.program_id)
            if image_online(url):
                art, source = url, "its program ID"
        if not art:
            art = self.games.icon_url(st.title)
            source = "Discord" if art else source
        if not art and self.sgdb_key:
            try:
                art = find_artwork(st.title, self.sgdb_key, self.art_kind)
                self.art_warning = None
            except ArtworkError as e:
                self.art_warning = str(e)
            source = f"SteamGridDB {self.art_kind}" if art else source
        if not art:
            try:
                art = wikipedia_game_cover(st.title)
            except Exception as e:
                log.debug("Wikipedia cover lookup failed: %r", e)
            source = "Wikipedia" if art else source
        self.eden_covers[st.file] = (art, source, name)
        return art, source, name

    def _show_eden(self, state: GameState, st: "eden.EdenState") -> None:
        now = int(time.time())
        prev = self.previous_id
        was_eden = (isinstance(prev, tuple) and is_eden(prev[0])) or prev == eden.TITLE_ID
        if st.in_game:
            cover, source, name = self._eden_cover(st)
            # The game started when ProsperoEden saved it as the last game.
            since = now - st.age if st.age is not None and st.age <= state.elapsed + 5 else \
                now - (0 if was_eden else state.elapsed)
            log.info("ProsperoEden game: %s (file %r), artwork from %s", name, st.file, source)
            self.desired = build_emulated_activity(name, cover, since, self.console_image,
                                                   eden.APP_NAME, eden.ICON_URL, eden.GAME_LABEL)
            self.title = TitleInfo(name=name, image_url=cover or eden.ICON_URL)
        else:
            since = now - (0 if was_eden else state.elapsed)
            log.info("in ProsperoEden's library")
            self.desired = build_emulator_menu_activity(since, self.console_image, eden.APP_NAME,
                                                        eden.MENU_TEXT, eden.ICON_URL)
            self.title = TitleInfo(name=eden.APP_NAME, image_url=eden.ICON_URL)
        self.title_id = st.program_id or state.title_id
        self.started_at = since
        self.in_menu = False

    def _clear_game(self) -> None:
        self.title_id = None
        self.title = None
        self.started_at = None
        self.in_menu = False

    def close(self) -> None:
        self.ipc.close()


def build_activity(state: GameState, info: TitleInfo | None,
                   console_image: str = CONSOLE_IMAGE, kind: str = "play") -> dict:
    """In a game: the game's name as the title, "Playing on PlayStation 5" under it.

    The member list reads "Playing <game>".
    """
    game = info.name if info else state.title_id
    activity = {
        "type": {"watch": WATCHING, "listen": LISTENING}.get(kind, PLAYING),
        "name": game,
        "details": {"watch": WATCHING_TEXT, "listen": LISTENING_TEXT}.get(kind, PLAYING_TEXT),
        # Tells Discord the activity is on a PS5. Experimental: only Discord's own
        # PlayStation integration is documented as using it.
        "platform": "ps5",
        "status_display_type": SHOW_NAME,
        "timestamps": {"start": int(time.time()) - state.elapsed},
        "assets": {"small_image": console_image, "small_text": CONSOLE_NAME},
    }
    if info and info.image_url:
        activity["assets"]["large_image"] = info.image_url
        activity["assets"]["large_text"] = game
    else:
        # No artwork: put the console logo in the big slot instead.
        activity["assets"] = {"large_image": console_image, "large_text": CONSOLE_NAME}

    log.debug("built activity: %s", activity)
    return activity


def build_menu_activity(since: int, console_image: str = CONSOLE_IMAGE,
                        text: str = MENU_TEXT) -> dict:
    """On the home screen: "Playing PlayStation 5 / Browsing the menu"."""
    activity = {
        "type": 0,
        "name": CONSOLE_NAME,
        "details": text,
        "status_display_type": SHOW_NAME,
        "timestamps": {"start": since},
        "assets": {"large_image": console_image, "large_text": CONSOLE_NAME},
    }
    log.debug("built menu activity: %s", activity)
    return activity


def build_watch_activity(name: str, poster: str | None, since: int,
                         console_image: str = CONSOLE_IMAGE, label: str | None = None) -> dict:
    """Watching something: "Watching <name>"."""
    timestamps = {"start": since}
    activity = {
        "type": WATCHING,
        "name": name,
        "details": WATCHING_TEXT,
        "status_display_type": SHOW_NAME,
        "timestamps": timestamps,
        "assets": {"small_image": console_image, "small_text": CONSOLE_NAME},
    }
    if label:
        activity["state"] = label
    if poster:
        activity["assets"]["large_image"] = poster
        activity["assets"]["large_text"] = name
    else:
        activity["assets"] = {"large_image": console_image, "large_text": CONSOLE_NAME}
    log.debug("built watch activity: %s", activity)
    return activity


def build_emulated_activity(title: str, cover: str | None, since: int, console_image: str,
                            app_name: str, app_icon: str, label: str | None = None) -> dict:
    """A game running in an emulator: the game's name and cover, with the
    emulator's icon in the corner."""
    activity = {
        "type": 0,
        "name": title,
        "details": PLAYING_TEXT,
        "state": label or app_name,
        "platform": "ps5",
        "status_display_type": SHOW_NAME,
        "timestamps": {"start": since},
        "assets": {
            "large_image": cover or app_icon,
            "large_text": title,
            "small_image": app_icon if cover else console_image,
            "small_text": app_name if cover else CONSOLE_NAME,
        },
    }
    log.debug("built emulated activity: %s", activity)
    return activity


def build_ps2_activity(title: str, cover: str | None, since: int,
                       console_image: str = CONSOLE_IMAGE) -> dict:
    """A PS2 game in PS5SX2: the game's name, its PS2 cover, PS5SX2 in the corner."""
    return build_emulated_activity(title, cover, since, console_image, ps5sx2.APP_NAME,
                                   ps5sx2.ICON_URL, "PS2 \u00b7 PS5SX2")


def build_emulator_menu_activity(since: int, console_image: str = CONSOLE_IMAGE,
                                 app_name: str = ps5sx2.APP_NAME, text: str = ps5sx2.MENU_TEXT,
                                 app_icon: str = ps5sx2.ICON_URL) -> dict:
    """An emulator open, no game started yet."""
    activity = {
        "type": 0,
        "name": app_name,
        "details": text,
        "platform": "ps5",
        "status_display_type": SHOW_NAME,
        "timestamps": {"start": since},
        "assets": {
            "large_image": app_icon,
            "large_text": app_name,
            "small_image": console_image,
            "small_text": CONSOLE_NAME,
        },
    }
    log.debug("built emulator menu activity: %s", activity)
    return activity


def poster_test(name: str, tmdb_key: str | None) -> None:
    title = title_of_disc(name, None)
    if title is None:
        print(f"{name!r} is a placeholder, not a title")
        return
    print(f"read as: {title.name!r}  {title.detail() or '(no season or disc number)'}")
    print("TMDB key: " + ("set" if tmdb_key else "not set"))
    report: list[str] = []
    find_poster(title, tmdb_key, report)
    print("\n".join(report))


def valid_ipv4(ip_str: str) -> str:
    """For argparse validation"""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{ip_str}' is not a valid IPv4 address.") from None
    if ip.version != 4:
        raise argparse.ArgumentTypeError(f"'{ip_str}' is an IPv6 address, expected IPv4.")
    return ip_str


def positive_seconds(value: str) -> float:
    """For argparse validation"""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a number.") from None
    if seconds <= 0:
        raise argparse.ArgumentTypeError("the interval must be greater than 0.")
    return seconds


def main():
    parser = argparse.ArgumentParser(
        prog='presence5',
        description='A Discord Rich Presence client for PlayStation 5 game activity display'
    )

    parser.add_argument('--version', action='version', version=f"presence5 {__version__}")
    parser.add_argument('ps5_ip', type=valid_ipv4, nargs='?',
                        help="Target PS5 IP Address (e.g, 192.168.1.1)")
    parser.add_argument('--poster-test', metavar='NAME',
                        help="Show how a disc name is read and what every poster source "
                             "answers for it, then exit. Example: --poster-test \"Dexter S5 D1\"")
    parser.add_argument('--discord', '-d', default=None, metavar='PATH',
                        help="Path to Discord's IPC socket or pipe, if auto-detection fails "
                             f"(usually {default_discord_path()})".replace('%', '%%'))
    parser.add_argument('--interval', '-i', type=positive_seconds, default=5,
                        help="Activity refresh interval in seconds (default: %(default)s)")
    parser.add_argument('--no-menu', action='store_true',
                        help="Show nothing on Discord while the console is on the home screen")
    parser.add_argument('--sgdb-key', default=os.environ.get("SGDB_API_KEY"), metavar='KEY',
                        help="SteamGridDB API key, for game artwork (or set SGDB_API_KEY)")
    parser.add_argument('--art', choices=ART_KINDS, default="logo",
                        help="Which SteamGridDB picture to show (default: %(default)s)")
    parser.add_argument('--tmdb-key', default=os.environ.get("TMDB_API_KEY"), metavar='KEY',
                        help="TMDB API key or read access token, for movie and TV posters "
                             "(or set TMDB_API_KEY)")
    parser.add_argument('--no-firmware', action='store_true',
                        help="Don't show the console's firmware")
    parser.add_argument('--show', default="", metavar='LIST',
                        help="What to show with the firmware, comma-separated, e.g. "
                             "\"Relapse,kstuff\". Known: " + ", ".join(SETUP_ITEMS) +
                             "; anything else is shown as typed")
    parser.add_argument('--logo-url', default=None, metavar='URL',
                        help="Public address of the PlayStation logo to show (default: the "
                             "project's assets/ps-logo.png)")
    parser.add_argument('--verbose', '-v', action='store_true', help="Enable verbose logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.poster_test:
        poster_test(args.poster_test, args.tmdb_key)
        return
    if not args.ps5_ip:
        parser.error("the PS5's IP address is required")

    client = PresenceClient(args.ps5_ip, PAYLOAD_PORT, args.discord,
                            show_menu=not args.no_menu, sgdb_key=args.sgdb_key, art_kind=args.art,
                            logo_url=args.logo_url, tmdb_key=args.tmdb_key,
                            show_firmware=not args.no_firmware,
                            setup=[x for x in args.show.split(",") if x.strip()])
    try:
        while True:
            client.step()
            time.sleep(client.wait_time(args.interval))
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        client.close()


if __name__ == "__main__":
    main()
