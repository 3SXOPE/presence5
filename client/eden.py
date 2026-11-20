"""ProsperoEden, the Eden emulator port for PS5
(https://github.com/cualquiercosa327/ProsperoEden).

To the console every game runs inside ProsperoEden's own title ID, so the
payload's usual answer is only "ProsperoEden is running". ProsperoEden has no
network API, but it writes a session log and remembers the last game it
started; the payload reads both (its EDEN command) and this module turns the
answer into a game name and a cover.
"""
import csv
import io
import json
import logging
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from ps5 import fetch_raw

log = logging.getLogger(__name__)

APP_NAME = "ProsperoEden"
TITLE_ID = "PPSA99008"
ICON_URL = "https://raw.githubusercontent.com/cualquiercosa327/ProsperoEden/main/sce_sys/icon0.png"
MENU_TEXT = "Browsing the Switch library"
GAME_LABEL = "Switch \u00b7 ProsperoEden"
# Game icons by program ID, as other presence tools use them.
ICON_BY_ID = "https://tinfoil.media/ti/{program_id}/512/512/"


def is_eden(title_id: str | None) -> bool:
    return title_id == TITLE_ID


@dataclass(frozen=True)
class EdenState:
    in_game: bool
    file: str | None = None        # the ROM's file name
    title: str | None = None       # the game's name, from the file name
    program_id: str | None = None  # "0100ABCD12345000", when the file name carries it
    age: int | None = None         # seconds since the game was started


_PROGRAM_ID = re.compile(r"\[(01[0-9A-Fa-f]{14})\]")
_EXTENSION = re.compile(r"\.(nsp|xci|nsz|xcz|nro)$", re.I)
_TAGS = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


def parse_rom_name(filename: str) -> tuple[str, str | None]:
    """('Sample Quest', '0100ABCD12345000') from 'Sample Quest [0100ABCD12345000][v0].nsp'."""
    found = _PROGRAM_ID.search(filename)
    program_id = found.group(1).upper() if found else None
    title = _EXTENSION.sub("", filename.strip())
    if title.startswith("[Game] "):
        title = title[7:]
    while True:  # "[id]", "[v0]", "(USA)" and the like at the end
        stripped = _TAGS.sub("", title)
        if stripped == title or not stripped.strip():
            break
        title = stripped
    if "_" in title and " " not in title:
        title = title.replace("_", " ")
    title = re.sub(r"\s+", " ", title).strip()
    return title or filename, program_id


def fetch_eden(host: str, port: int = 8000) -> EdenState | None:
    """What ProsperoEden is doing. None when the payload can't tell: it is an
    older payload, or ProsperoEden's log isn't where it is expected."""
    data = fetch_raw(host, "EDEN", port, timeout=5)
    state, last, age = None, None, None
    for line in data.splitlines()[1:]:  # the first line is the usual state line
        key, _, value = line.partition(" ")
        value = value.strip()
        if key == "STATE":
            state = value
        elif key == "LAST" and value:
            last = value
        elif key == "AGE" and value.isdigit():
            age = int(value)
    log.debug("EDEN reply: %r", data)
    if state not in ("game", "menu"):
        return None
    if state == "game" and last:
        title, program_id = parse_rom_name(last)
        return EdenState(True, last, title, program_id, age)
    return EdenState(False)


# ---------------------------------------------------------------------------
# Switch covers
#
# 1. Rich Presence U's title database (github.com/ninstar/rich-presence-u-db,
#    ODbL): about 900 Switch games, each with a 512x512 icon, listed by name.
# 2. Nintendo of Europe's game search: a square picture for most of the eShop.

RPU_LIST = "https://raw.githubusercontent.com/ninstar/rich-presence-u-db/main/titles/hac.csv"
RPU_ICON = "https://raw.githubusercontent.com/ninstar/rich-presence-u-db/main/titles/hac/{id}.{region}.jpg"
NINTENDO_EU = "https://searching.nintendo-europe.com/en/select"
_UA = {"User-Agent": "presence5 (Discord Rich Presence for PS5)"}


class SwitchCovers:
    def __init__(self):
        self.rows: list[dict] | None = None
        self.lock = threading.Lock()

    def _load(self) -> list[dict]:
        with self.lock:
            if self.rows is None:
                try:
                    req = urllib.request.Request(RPU_LIST, headers=_UA)
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        text = resp.read().decode("utf-8-sig", errors="replace")
                    self.rows = list(csv.DictReader(io.StringIO(text)))
                    log.info("Switch title list: %d games", len(self.rows))
                except (urllib.error.URLError, OSError, ValueError, csv.Error) as e:
                    log.warning("couldn't load the Switch title list: %s", e)
                    return []  # try again next time
            return self.rows

    def rich_presence_u(self, name: str) -> tuple[str, str] | None:
        """(official name, icon URL) from Rich Presence U's list."""
        from media import best_match
        rows = self._load()
        best = best_match(rows, lambda r: (r.get("US TITLE"), r.get("EU TITLE"), r.get("JP TITLE")),
                          name, least=2)
        if not best or not best.get("ID"):
            return None
        for region, column in (("us", "US"), ("eu", "EU"), ("jp", "JP")):
            if (best.get(column) or "").strip():
                shown = best.get("US TITLE") or best.get("EU TITLE") or best.get("JP TITLE") or name
                return shown.strip(), RPU_ICON.format(id=best["ID"], region=region)
        return None

    def nintendo_eu(self, name: str) -> tuple[str, str] | None:
        """(official name, square picture) from Nintendo of Europe's game search."""
        from media import best_match
        query = urllib.parse.urlencode({
            "q": name, "fq": "type:GAME AND system_type:nintendoswitch*",
            "rows": 10, "wt": "json", "start": 0,
        })
        try:
            req = urllib.request.Request(f"{NINTENDO_EU}?{query}", headers=_UA)
            with urllib.request.urlopen(req, timeout=8) as resp:
                docs = json.load(resp).get("response", {}).get("docs", [])
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.debug("Nintendo search failed: %s", e)
            return None
        docs = [d for d in docs if d.get("image_url_sq_s") or d.get("image_url")]
        best = best_match(docs, lambda d: (d.get("title"),), name, least=2)
        if not best:
            return None
        url = best.get("image_url_sq_s") or best.get("image_url")
        if url.startswith("//"):
            url = "https:" + url
        return best.get("title") or name, url
