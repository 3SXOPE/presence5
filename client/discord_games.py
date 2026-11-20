"""Game icons from Discord's own game list.

Discord keeps a public list of the games it recognises (its "detectable applications"),
each with an icon. Matching the PlayStation title against that list by name gives the
same picture Discord shows for the game on PC.

The list comes from Discord itself, or from dgdb, a daily mirror of it on GitHub, if
Discord's copy can't be read. It is downloaded once per run, on the first game.
"""
import json
import logging
import re
import threading
import urllib.request

log = logging.getLogger(__name__)

SOURCES = [
    "https://discord.com/api/v9/applications/detectable",
    "https://cdn.jsdelivr.net/gh/teyd/dgdb@main/detectable.json",
    "https://raw.githubusercontent.com/teyd/dgdb/main/detectable.json",
]
ICON_URL = "https://cdn.discordapp.com/app-icons/{id}/{hash}.png?size=512"

# Endings that the PlayStation and PC releases often don't share, tried only when
# the full name finds nothing.
EDITION = re.compile(
    r"\s+(special|deluxe|definitive|complete|ultimate|gold|digital deluxe|standard|"
    r"game of the year|goty|anniversary|enhanced|remastered|ps5|ps4)( edition)?$"
    r"|\s+(director s cut)$"
    r"|\s+(edition)$"
)


def normalize(name: str) -> str:
    name = re.sub(r"[™®©]", "", name).casefold()
    name = re.sub(r"[^\w]+", " ", name)   # punctuation, colons, dashes -> spaces
    return re.sub(r"\s+", " ", name).strip()


def core(name: str) -> str:
    """The normalized name with edition words removed (repeatedly)."""
    n = normalize(name)
    while True:
        stripped = EDITION.sub("", n)
        if stripped == n or not stripped:
            return n
        n = stripped


class GameList:
    def __init__(self, sources: list[str] | None = None):
        self.sources = sources or SOURCES
        self._lock = threading.Lock()
        self._loaded = False
        self.by_name: dict[str, dict] = {}
        self.by_core: dict[str, dict] = {}

    def _download(self) -> list | None:
        for url in self.sources:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "presence5"})
                with urllib.request.urlopen(req, timeout=20) as response:
                    data = json.load(response)
                if isinstance(data, list) and data:
                    log.info("loaded %d games from %s", len(data), url)
                    return data
            except (OSError, ValueError) as e:
                log.debug("could not load the game list from %s: %r", url, e)
        log.warning("could not load Discord's game list; skipping Discord icons")
        return None

    def load(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self._loaded = True  # one attempt per run, even if it fails
            for app in self._download() or []:
                icon = app.get("icon_hash") or app.get("icon")
                if not icon or not app.get("id") or not app.get("name"):
                    continue
                entry = {"id": app["id"], "name": app["name"], "icon": icon}
                names = [app["name"]] + list(app.get("aliases") or [])
                for n in names:
                    self.by_name.setdefault(normalize(n), entry)
                    self.by_core.setdefault(core(n), entry)

    def find(self, name: str) -> dict | None:
        self.load()
        hit = self.by_name.get(normalize(name)) or self.by_core.get(core(name))
        if hit:
            log.debug("Discord knows %r as %r (app %s)", name, hit["name"], hit["id"])
        else:
            log.info("%r is not in Discord's game list", name)
        return hit

    def icon_url(self, name: str) -> str | None:
        hit = self.find(name)
        return ICON_URL.format(id=hit["id"], hash=hit["icon"]) if hit else None


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG)
    games = GameList()
    for title in sys.argv[1:] or ["Devil May Cry 5 Special Edition"]:
        print(title, "->", games.icon_url(title))
