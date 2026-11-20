"""PS5SX2, the PS2 emulator for PS5 (https://github.com/Swordpdf/PS5SX2).

To the console every PS2 game runs inside PS5SX2's own title ID (or one of its
home-screen tiles), so the payload can only say "PS5SX2 is running". PS5SX2's
settings page on the console has a small JSON API that says which PS2 game is
playing, with its serial; the serial finds the game's cover.
"""
import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)

APP_NAME = "PS5SX2"
MAIN_ID = "PPSA99203"
# The main app, its disc tiles (PPSA99230-99238) and its game tiles (PPSA99251-99260).
TITLE_IDS = ({MAIN_ID}
             | {f"PPSA992{n:02d}" for n in range(30, 39)}
             | {f"PPSA992{n:02d}" for n in range(51, 61)})

# The settings page takes the first free port of these.
WEB_PORTS = range(8844, 8852)
ICON_URL = "https://raw.githubusercontent.com/Swordpdf/PS5SX2/main/ps5/sce_sys/icon0.png"
# PS2 covers by serial, the same set PCSX2 and PS5SX2 download theirs from.
COVER_URL = "https://raw.githubusercontent.com/xlenore/ps2-covers/main/covers/default/{serial}.jpg"
MENU_TEXT = "Browsing the PS2 library"

TIMEOUT = 2
RETRY_AFTER = 20   # seconds to wait before looking for the settings page again
GRACE = 2          # missed polls before a playing game is dropped


def is_ps5sx2(title_id: str | None) -> bool:
    return title_id in TITLE_IDS


@dataclass(frozen=True)
class EmuState:
    title: str | None    # the PS2 game's name, None in PS5SX2's menu
    serial: str | None   # e.g. "SCUS-97399", None when PS5SX2 doesn't know it

    @property
    def in_game(self) -> bool:
        return self.title is not None


_EXTENSIONS = re.compile(r"\.(iso|bin|cue|chd|cso|zso|gz|elf|img|mdf|nrg)$", re.I)
# "SLUS_209.46.Grand Theft Auto San Andreas": a serial in front, as in OPL-style file names.
_SERIAL_PREFIX = re.compile(r"^[A-Z]{4}[_-]?\d{3}\.?\d{2}[\s._-]+", re.I)
_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


def clean_title(title: str) -> str:
    """'God of War (USA).iso' or 'SCUS_973.99.God of War' -> 'God of War'."""
    title = _EXTENSIONS.sub("", title.strip())
    title = _SERIAL_PREFIX.sub("", title) or title
    while True:
        stripped = _TAGS.sub("", title)
        if stripped == title or not stripped:
            break
        title = stripped
    return title.strip() or title


class EmulatorAPI:
    def __init__(self, host: str):
        self.host = host
        self.port: int | None = None  # the port that answered last time
        self.cover_ok: dict[str, bool] = {}
        self.last: EmuState | None = None
        self.misses = 0
        self.retry_at = 0.0

    def _get(self, port: int) -> dict | None:
        # No Origin header and the console's IP as Host: what PS5SX2 accepts.
        req = urllib.request.Request(f"http://{self.host}:{port}/api/state",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)

    def state(self) -> EmuState | None:
        """What PS5SX2 is doing, or None if its settings page can't be reached."""
        if self.port is None and time.monotonic() < self.retry_at:
            return None
        found = self._poll()
        if found is not None:
            self.last, self.misses = found, 0
            return found
        # A missed answer or two (Wi-Fi, a busy console) keeps what was showing.
        self.misses += 1
        if self.last is not None and self.misses <= GRACE:
            return self.last
        self.last = None
        self.retry_at = time.monotonic() + RETRY_AFTER
        return None

    def _poll(self) -> EmuState | None:
        ports = [self.port] if self.port else []
        ports += [p for p in WEB_PORTS if p != self.port]
        for port in ports:
            try:
                data = self._get(port)
            except (urllib.error.URLError, OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("app") != APP_NAME:
                continue
            if self.port != port:
                log.info("PS5SX2 settings page found on port %s", port)
            self.port = port
            playing = data.get("playing") if data.get("mode") == "game" else None
            if not isinstance(playing, dict):
                return EmuState(None, None)
            serial = (playing.get("serial") or "").strip().upper() or None
            title = (playing.get("title") or playing.get("id") or "").strip()
            title = clean_title(title) if title else (serial or "PS2 game")
            return EmuState(title, serial)
        if self.port is not None:
            log.warning("PS5SX2 settings page stopped answering")
        elif self.misses == 0:
            log.info("PS5SX2's settings page isn't answering on ports %s-%s; showing "
                     "PS5SX2 without the PS2 game", WEB_PORTS[0], WEB_PORTS[-1])
        self.port = None
        return None

    def cover_url(self, serial: str | None) -> str | None:
        """The PS2 cover for a serial, if the cover set has one."""
        if not serial:
            return None
        if serial not in self.cover_ok:
            url = COVER_URL.format(serial=serial)
            try:
                req = urllib.request.Request(url, method="HEAD")
                with urllib.request.urlopen(req, timeout=5):
                    self.cover_ok[serial] = True
            except urllib.error.HTTPError as e:
                # 404: no cover. Anything else: don't know, assume it's there.
                self.cover_ok[serial] = e.code != 404
            except (urllib.error.URLError, OSError):
                return url  # offline now; try again next time
            log.debug("PS2 cover for %s: %s", serial, "found" if self.cover_ok[serial] else "none")
        return COVER_URL.format(serial=serial) if self.cover_ok[serial] else None
