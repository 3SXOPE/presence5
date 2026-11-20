"""Movies, TV and the PS5's own system apps.

The console reports system apps by their NPXS title ID, which means nothing to
a person. This module names them, says which ones are for watching, and finds
a poster for a movie disc.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import unicodedata
import urllib.request
from dataclasses import dataclass
from difflib import SequenceMatcher

log = logging.getLogger(__name__)

DISC_PLAYER = "NPXS40140"  # the Blu-ray / DVD player

# System apps the console can report as running. ("menu", text) shows like the
# home screen with that text; ("watch", name) is something being watched.
SYSTEM_APPS = {
    DISC_PLAYER: ("watch", "Blu-ray Disc Player"),
    "NPXS40075": ("watch", "Media Gallery"),
    "NPXS40053": ("menu", "Browsing TV & Video"),
    "NPXS40054": ("menu", "Browsing Music"),
    "NPXS40047": ("menu", "Browsing PlayStation Store"),
    "NPXS40037": ("menu", "Browsing PlayStation Plus"),
    "NPXS40063": ("menu", "Browsing Discover"),
    "NPXS40071": ("menu", "Browsing the Game Library"),
    "NPXS40139": ("menu", "Browsing the App Library"),
    "NPXS40056": ("menu", "Browsing All Apps"),
    "NPXS40008": ("menu", "In Settings"),
    "NPXS40074": ("menu", "Using Remote Play"),
    "NPXS40099": ("menu", "In Share Play"),
}


def system_app(title_id: str) -> tuple[str, str] | None:
    """(kind, text) for a system app; every NPXS title is one."""
    if title_id in SYSTEM_APPS:
        return SYSTEM_APPS[title_id]
    if title_id.startswith("NPXS"):
        return ("menu", "Browsing the menu")
    return None


# Store apps that are for watching or listening, matched by name.
WATCH_APPS = [
    "netflix", "youtube", "youtube tv", "disney+", "disney plus", "prime video", "amazon prime video",
    "crunchyroll", "max", "hbo max", "hulu", "apple tv", "plex", "twitch", "peacock",
    "paramount+", "tubi", "pluto tv", "funimation", "jellyfin", "emby", "vlc",
    "dazn", "espn", "britbox", "mubi", "rakuten tv", "now", "stan", "binge", "iplayer",
    "bbc iplayer", "itvx", "my5", "channel 4", "all 4", "crave", "viki", "hidive",
]
LISTEN_APPS = ["spotify", "apple music", "youtube music", "deezer", "tidal", "amazon music"]


def _norm(name: str) -> str:
    name = re.sub(r"[™®©]", "", name).casefold()
    return re.sub(r"\s+", " ", name).strip()


def app_kind(name: str) -> str:
    """'watch', 'listen' or 'play' for a store app's name."""
    n = _norm(name)
    for kind, names in (("listen", LISTEN_APPS), ("watch", WATCH_APPS)):
        for app in names:
            # "Netflix", "Netflix (PS5)", "Crunchyroll – Anime"; not "Max Payne"
            if n == app or re.match(re.escape(app) + r"\s*[:(–-]", n):
                return kind
    return "play"


# ---------------------------------------------------------------------------
# Disc titles
#
# A Blu-ray names itself in its metadata file, or failing that in its volume
# label, and both are written for shelves rather than people:
#   "MARVEL STUDIOS' DEADPOOL AND WOLVERINE - Ultra HD™"
#   "Re:ZERO Starting Life in Another World S3 D1"
#   "TORADORA_D2"
# parse_disc_title() splits off the season, disc and volume numbers and the
# format words, leaving a name to look up. The poster lookup then gives the
# official title, which is what Discord shows.

@dataclass
class DiscTitle:
    name: str                  # cleaned name, for display when nothing better is found
    season: int | None = None
    disc: int | None = None
    volume: int | None = None

    @property
    def is_series(self) -> bool:
        return self.season is not None or self.disc is not None or self.volume is not None

    def detail(self) -> str:
        """'Season 3 · Disc 1'."""
        parts = []
        if self.season is not None:
            parts.append(f"Season {self.season}")
        if self.volume is not None:
            parts.append(f"Vol. {self.volume}")
        if self.disc is not None:
            parts.append(f"Disc {self.disc}")
        return " · ".join(parts)


_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
                 "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"

_SEASON = re.compile(rf"\b(?:season|series|saison|staffel|s)\s*0*{_NUM}\b", re.I)
_DISC = re.compile(rf"\b(?:disc|disk|dvd|d)\s*0*{_NUM}\b", re.I)
_VOLUME = re.compile(rf"\b(?:volume|vol\.?)\s*0*{_NUM}\b", re.I)
_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
             "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "final": None}
# "The Complete First Season", "Season One - Complete"
_ORDINAL_SEASON = re.compile(r"\b(?:the\s+)?(?:complete\s+)?(first|second|third|fourth|fifth|sixth|seventh|"
                             r"eighth|ninth|tenth)\s+(?:season|series)\b(?:\s*[-:]?\s*complete)?", re.I)
_OF_TOTAL = re.compile(r"\s*(?:of|/)\s*\d{1,2}\b", re.I)  # "Disc 1 of 3"

_FORMAT_NOISE = re.compile(
    r"[\[(]?\b(?:4k\s*)?(?:ultra\s*hd|uhd)(?:\s*blu[- ]?ray)?\b[\])]?|"
    r"[\[(]?\bblu[- ]?ray(?:\s*disc)?\b[\])]?|"
    r"[\[(]?\b(?:bd|bd25|bd50|bd66|bd100|dvd|hdr10\+?|dolby vision|digital copy|"
    r"feature film|bonus disc|special features|steelbook)\b[\])]?",
    re.I)
# Edition words: they're on the box, not in the databases.
_EDITION = re.compile(
    r"[\[(]?\b(?:the\s+)?(?:\d+(?:st|nd|rd|th)\s+anniv(?:ersary)?(?:\s+edition)?|anniv(?:ersary)?\s+edition|"
    r"\d+(?:st|nd|rd|th)|anniv|"
    r"(?:special|collector'?s|limited|deluxe|ultimate|definitive|remastered|restored|criterion|"
    r"extended|unrated|theatrical|director'?s cut|final cut|anniversary|steelbook|premium|"
    r"standard|digital)\s+(?:edition|cut|collection|version)|"
    r"director'?s cut|extended cut|unrated cut|theatrical cut|final cut)\b[\])]?",
    re.I)
# Names some discs carry instead of a title.
_GENERIC = {"", "blu ray", "bluray", "blu ray disc", "bd", "bd rom", "bdrom", "bdmv", "disc", "disc 1",
            "title", "untitled", "no title", "movie", "feature", "main feature", "video", "dvd",
            "volume", "vol", "new volume", "label", "disc title", "title name", "uhd", "4k"}


def is_generic(name: str | None) -> bool:
    """True for names that aren't a title at all: '.', 'BLU-RAY', 'DISC1'."""
    if not name:
        return True
    key = re.sub(r"[^a-z0-9]+", " ", _unescape(name).casefold()).strip()
    letters = sum(c.isalpha() for c in key)
    return letters < 2 or key in _GENERIC or re.fullmatch(r"(disc|disk|volume|vol|bd|dvd)\s*\d*", key) is not None


# Studio names put in front of the title on the box.
_STUDIO_PREFIX = re.compile(
    r"^(?:marvel studios'?|marvel's|disney\s*[•·-]?\s*pixar'?s?|pixar'?s|dreamworks'?|"
    r"walt disney'?s|disney'?s|lucasfilm'?s?|a24'?s?|universal pictures'?|warner bros\.?'?)\s+",
    re.I)
_LABEL_TAIL = re.compile(r"(\s+(US|USA|UK|EU|EUR|NA|DE|FR|JP|WS|FS|R\d))+$", re.I)


def _number(text: str) -> int:
    text = text.lower()
    return _WORD_NUMBERS.get(text) or int(text)


def _unescape(name: str) -> str:
    return (name.replace("&amp;", "&").replace("&apos;", "'").replace("&quot;", '"')
                .replace("&lt;", "<").replace("&gt;", ">").replace("&#39;", "'"))


def parse_disc_title(raw: str, from_label: bool = False) -> DiscTitle:
    name = _unescape(raw)
    name = re.sub(r"[™®©]", "", name)
    if "_" in name and " " not in name.strip():
        from_label = True  # a title written like a label: "DEXTER_S1D3"
    if from_label:
        name = re.sub(r"[_.]+", " ", name)

    # Numbers written without spaces: "S1D3", "SEASON2", "DISC3", "2DISC".
    name = re.sub(r"(?i)\bS(\d{1,2})[\s._-]*D(\d{1,2})\b", r"S\1 D\2", name)
    name = re.sub(r"(?i)\b(season|series|disc|disk|volume|vol)(\d)", r"\1 \2", name)
    name = re.sub(r"(?i)(\d)(disc|disk)\b", r"\1 \2", name)

    found = {}
    m = _ORDINAL_SEASON.search(name)
    if m:
        found["season"] = _ORDINALS[m.group(1).lower()]
        name = name[:m.start()] + " " + name[m.end():]
    for key, pattern in (("disc", _DISC), ("season", _SEASON), ("volume", _VOLUME)):
        match = None
        for m in pattern.finditer(name):
            match = m  # the last one: "S3 D1" has its numbers at the end
        if match:
            # A lone "S"/"D" only counts next to the end or another marker,
            # so "D Gray-man" or "S Club" stay intact.
            if key in found:
                continue
            short = re.match(r"(?i)[sd]\s*\d", match.group(0)) is not None
            if short and match.end() < len(name.rstrip()) - 4 and key != "season":
                continue
            found[key] = _number(match.group(1))
            name = name[:match.start()] + " " + _OF_TOTAL.sub("", name[match.end():], count=1)

    name = _FORMAT_NOISE.sub(" ", name)
    name = _EDITION.sub(" ", name)
    if from_label:
        name = _LABEL_TAIL.sub("", name)
    name = _STUDIO_PREFIX.sub("", name.strip())
    name = re.sub(r"\(\s*\)|\[\s*\]", " ", name)
    name = re.sub(r"[\s:–—,-]+$", "", name)     # separators left at the end
    name = re.sub(r"^[\s:–—,-]+", "", name)
    name = re.sub(r"\s+", " ", name).strip() or _unescape(raw).strip()
    return DiscTitle(tidy_case(name), found.get("season"), found.get("disc"), found.get("volume"))


def tidy_case(name: str) -> str:
    """Disc names are often all capitals: 'THE BACKROOMS' -> 'The Backrooms'."""
    letters = [c for c in name if c.isalpha()]
    if letters and all(c.isupper() for c in letters):
        small = {"a", "an", "and", "of", "the", "in", "on", "at", "to", "for", "or", "vs"}
        words = name.lower().split()
        roman = re.compile(r"^(i{1,3}|iv|v|vi{1,3}|ix|x)[:.,!?]*$")
        return " ".join(w.upper() if roman.match(w) else
                        w if i and w in small else w[:1].upper() + w[1:]
                        for i, w in enumerate(words))
    return name


def merge_titles(main: DiscTitle | None, other: DiscTitle | None) -> DiscTitle | None:
    """The title from the disc's metadata, with numbers the volume label adds
    ("Dexter: Season 5" + "DEXTER_S5_D2" -> season 5, disc 2)."""
    if main is None or other is None:
        return main or other
    return DiscTitle(main.name,
                     main.season if main.season is not None else other.season,
                     main.disc if main.disc is not None else other.disc,
                     main.volume if main.volume is not None else other.volume)


def title_of_disc(name: str | None, label: str | None) -> DiscTitle | None:
    """What a disc is, from its metadata title and its volume label. The title
    wins unless it's a placeholder such as "."; the label fills in season and
    disc numbers the title leaves out."""
    from_name = parse_disc_title(name) if not is_generic(name) else None
    from_label = parse_disc_title(label, from_label=True) if not is_generic(label) else None
    if from_name and is_generic(from_name.name):
        from_name = None
    if from_label and is_generic(from_label.name):
        from_label = None
    return merge_titles(from_name, from_label)


# ---------------------------------------------------------------------------
# Posters
#
# For a series with a season number, that season's poster is looked for first:
#   TMDB (with a key), AniList (anime), TVmaze, iTunes, Wikipedia's season article.
# Otherwise, or when no source has the season, the film's or show's own poster:
#   TMDB (with a key), Cinemeta (Stremio's IMDb-based catalogue), iTunes, Wikipedia.

@dataclass
class Poster:
    title: str   # the official title
    url: str
    source: str
    imdb: str | None = None  # "tt0773262", when the source knows it


_STOP = {"the", "a", "an", "of", "and", "in", "on", "to", "for"}


def _key(name: str) -> str:
    """For comparing titles: 'Deadpool & Wolverine' == 'DEADPOOL AND WOLVERINE'."""
    name = _unescape(name).casefold().replace("&", " and ")
    # "Pokémon" == "Pokemon": drop accents
    name = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    name = re.sub(r"[™®©'’`]", "", name)
    name = re.sub(r"[^\w]+", " ", name)
    name = re.sub(r"^(the|a|an) ", "", name.strip())
    return re.sub(r"\s+", " ", name).strip()


def match_score(found: str, wanted: str) -> int:
    """How well a database title fits the disc's name.

    3: the same title, ignoring case, punctuation and spacing ("FIGHTCLUB").
    2: one is the start of the other, by whole words: the disc says "ReZero",
       the database "Re:ZERO -Starting Life in Another World-".
    1: every word of the disc's name is in the title, and they are most of it.
    0: something else.
    """
    a, b = _key(found), _key(wanted)
    if not a or not b:
        return 0
    if a.replace(" ", "") == b.replace(" ", ""):
        return 3
    for long_, short in ((a, b), (b, a)):
        flat = short.replace(" ", "")
        if len(flat) < 4:
            continue
        joined = ""
        for word in long_.split():
            joined += word
            if joined == flat:
                return 2
            if len(joined) >= len(flat):
                break
    wa = [w for w in a.split() if w not in _STOP]
    wb = [w for w in b.split() if w not in _STOP]
    if len(wb) >= 2 and set(wb) <= set(wa) and len(wb) * 2 >= len(wa):
        return 1
    # Nearly the same spelling ("Starwars: A New Hope" / "Star Wars - New Hope").
    if SequenceMatcher(None, a, b).ratio() >= 0.8:
        return 1
    return 0


def same_title(found: str, wanted: str) -> bool:
    return match_score(found, wanted) >= 2


def best_match(items, names_of, wanted: str, least: int = 1):
    """The item whose name fits best; the earlier one (the search's own ranking) on a tie."""
    best, best_score = None, least - 1
    for item in items:
        score = max((match_score(n, wanted) for n in names_of(item) if n), default=0)
        if score > best_score:
            best, best_score = item, score
    return best


_UA = {"User-Agent": "presence5 (Discord Rich Presence for PS5)"}


class PosterKeyError(Exception):
    pass


def _get_json(url: str, headers: dict | None = None, body: dict | None = None,
              key_errors: bool = False):
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {**_UA, **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
        hdrs["Accept"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        log.debug("%s: HTTP %s", url.split("?")[0], e.code)
        if key_errors and e.code in (401, 403):
            raise PosterKeyError() from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.debug("%s: %s", url.split("?")[0], e)
    return None


# --- TMDB ---------------------------------------------------------------------

TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMAGE = "https://image.tmdb.org/t/p/w500"


def _tmdb_auth(key: str) -> tuple[dict, dict]:
    # A v4 "read access token" is long and goes in a header; a v3 key is a parameter.
    return ({"Authorization": f"Bearer {key}"}, {}) if len(key) > 40 else ({}, {"api_key": key})


def _tmdb_search(title: DiscTitle, key: str, kind: str) -> dict | None:
    headers, auth = _tmdb_auth(key)
    query = urllib.parse.urlencode({"query": title.name, "include_adult": "false", **auth})
    data = _get_json(f"{TMDB_API}/search/{kind}?{query}", headers, key_errors=True) or {}
    return best_match(data.get("results", [])[:10],
                      lambda r: (r.get("title"), r.get("name"), r.get("original_title"),
                                 r.get("original_name")), title.name)


def tmdb_season_poster(title: DiscTitle, key: str, imdb: str | None = None) -> Poster | None:
    headers, auth = _tmdb_auth(key)
    show = None
    if imdb:  # the exact show, no name search
        q = urllib.parse.urlencode({"external_source": "imdb_id", **auth})
        found = _get_json(f"{TMDB_API}/find/{imdb}?{q}", headers, key_errors=True) or {}
        show = (found.get("tv_results") or [None])[0]
    show = show or _tmdb_search(title, key, "tv")
    if not show:
        return None
    q = urllib.parse.urlencode(auth)
    season = _get_json(f"{TMDB_API}/tv/{show['id']}/season/{title.season}?{q}", headers) or {}
    if season.get("poster_path"):
        return Poster(show.get("name") or title.name, TMDB_IMAGE + season["poster_path"], "TMDB season")
    return None


def tmdb_poster(title: DiscTitle, key: str) -> Poster | None:
    for kind in (("tv", "movie") if title.is_series else ("movie", "tv")):
        r = _tmdb_search(title, key, kind)
        if r and r.get("poster_path"):
            return Poster(r.get("title") or r.get("name") or title.name,
                          TMDB_IMAGE + r["poster_path"], "TMDB")
    return None


# --- AniList (anime: every season is its own entry, with its own cover) ----------

ANILIST = "https://graphql.anilist.co"
_ANILIST_FIELDS = ("id format startDate { year month } title { romaji english } synonyms "
                   "coverImage { extraLarge large } "
                   "relations { edges { relationType node { id type format } } }")
_ANILIST_SEARCH = ("query ($q: String) { Page(perPage: 20) { media(search: $q, type: ANIME) { "
                   + _ANILIST_FIELDS + " } } }")
_ANILIST_BY_ID = "query ($id: Int) { Media(id: $id) { " + _ANILIST_FIELDS + " } }"
_SERIES_FORMATS = ("TV", "TV_SHORT", "ONA")
_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
# How sequels are numbered: "Season 3", "3rd Season", "Overlord III", "My Hero Academia 4".
_SEASON_WORD = re.compile(r"\b(?:season\s*(\d{1,2})|(\d{1,2})(?:st|nd|rd|th)\s+season)\b", re.I)
_SEASON_TAIL = re.compile(r"\s+(?:([2-9])|(ii|iii|iv|v|vi|vii|viii|ix|x))$", re.I)
_PART = re.compile(r"\b(?:part|cour)\s*(\d+|ii|iii)\b|\b2nd cour\b", re.I)


def split_season(name: str) -> tuple[str, int | None]:
    """('Overlord', 3) from 'Overlord III'; (name, None) when the name has no number."""
    name = _PART.sub(" ", name).strip()
    m = _SEASON_WORD.search(name)
    if m:
        base = re.sub(r"[\s:–]+$", "", name[:m.start()] + name[m.end():]).strip()
        return base, int(m.group(1) or m.group(2))
    m = _SEASON_TAIL.search(name)
    if m:
        number = int(m.group(1)) if m.group(1) else _ROMAN[m.group(2).lower()]
        return name[:m.start()].strip(), number
    return name, None


def _ani_names(media: dict) -> list[str]:
    t = media.get("title") or {}
    return [n for n in (t.get("english"), t.get("romaji")) if n] + (media.get("synonyms") or [])


def _ani_started(media: dict) -> tuple:
    d = media.get("startDate") or {}
    return (d.get("year") or 9999, d.get("month") or 99)


def _ani_cover(media: dict) -> str | None:
    cover = media.get("coverImage") or {}
    return cover.get("extraLarge") or cover.get("large")


def _ani_season_fit(media: dict, wanted: str) -> tuple[int, int | None]:
    """(how well the name fits, the season number in the name) for an entry."""
    best = (0, None)
    for n in _ani_names(media):
        base, number = split_season(n)
        score = match_score(base, wanted)
        if score > best[0]:
            best = (score, number)
    return best


def anilist_season_poster(title: DiscTitle) -> Poster | None:
    data = _get_json(ANILIST, body={"query": _ANILIST_SEARCH, "variables": {"q": title.name}}) or {}
    results = [m for m in (data.get("data") or {}).get("Page", {}).get("media", []) or []
               if m.get("format") in _SERIES_FORMATS]
    fits = [(m, *_ani_season_fit(m, title.name)) for m in results]
    fits = [f for f in fits if f[1] >= 2]
    if not fits:
        return None

    def pick(season: int):
        # Entries with no number in the name are the first season (or a
        # differently named sequel: the earliest one is the first season).
        same = [f for f in fits if (f[2] or 1) == season]
        if season == 1:
            same = [f for f in same if f[2] is None] or same
        # The first half of a split season before its "Part 2".
        same.sort(key=lambda f: (any(_PART.search(n) for n in _ani_names(f[0])), -f[1],
                                 _ani_started(f[0])))
        return same[0][0] if same else None

    first = pick(1) or min(fits, key=lambda f: _ani_started(f[0]))[0]
    shown = split_season((first.get("title") or {}).get("english") or _ani_names(first)[0])[0]
    found = pick(title.season)
    source = "AniList"
    if not found and title.season > 1:
        found = _ani_follow_sequels(first, title.season)
        source = "AniList sequels"
    if found and _ani_cover(found):
        return Poster(shown, _ani_cover(found), source)
    return None


def _ani_follow_sequels(first: dict, season: int) -> dict | None:
    """Season N by walking the 'sequel' links from the first season, for sequels
    with names of their own. Films and specials on the way aren't counted, and
    neither is the second half of a split season."""
    current, count = first, 1
    for _ in range(16):
        sequels = [e["node"] for e in ((current.get("relations") or {}).get("edges") or [])
                   if e.get("relationType") == "SEQUEL" and (e.get("node") or {}).get("type") == "ANIME"]
        if not sequels:
            return None
        sequels.sort(key=lambda n: n.get("format") not in _SERIES_FORMATS)
        data = _get_json(ANILIST, body={"query": _ANILIST_BY_ID, "variables": {"id": sequels[0]["id"]}}) or {}
        current = (data.get("data") or {}).get("Media")
        if not current:
            return None
        if current.get("format") not in _SERIES_FORMATS:
            continue
        names = _ani_names(current)
        numbered = [n for n in (split_season(x)[1] for x in names) if n]
        if numbered:
            count = numbered[0]
        elif not any(_PART.search(n) for n in names):
            count += 1
        if count == season:
            return current
        if count > season:
            return None
    return None


# --- TVmaze -------------------------------------------------------------------

TVMAZE = "https://api.tvmaze.com"


def tvmaze_season_poster(title: DiscTitle, imdb: str | None = None) -> Poster | None:
    show = None
    if imdb:  # the exact show by its IMDb id, no name search
        show = _get_json(f"{TVMAZE}/lookup/shows?imdb={imdb}")
        show = show if isinstance(show, dict) and show.get("id") else None
    if not show:
        q = urllib.parse.urlencode({"q": title.name})
        shows = [e.get("show") or {} for e in (_get_json(f"{TVMAZE}/search/shows?{q}") or [])[:8]]
        show = best_match(shows, lambda s: (s.get("name"),), title.name)
    if not show:
        return None
    for season in _get_json(f"{TVMAZE}/shows/{show['id']}/seasons") or []:
        image = season.get("image") or {}
        if season.get("number") == title.season and (image.get("original") or image.get("medium")):
            return Poster(show["name"], image.get("original") or image["medium"], "TVmaze season")
    return None


# --- Cinemeta -------------------------------------------------------------------

CINEMETA = "https://v3-cinemeta.strem.io/catalog/{kind}/top/search={query}.json"


def cinemeta_poster(title: DiscTitle) -> Poster | None:
    for kind in (("series", "movie") if title.is_series else ("movie", "series")):
        url = CINEMETA.format(kind=kind, query=urllib.parse.quote(title.name))
        metas = [m for m in ((_get_json(url) or {}).get("metas") or [])[:10] if m.get("poster")]
        meta = best_match(metas, lambda m: (m.get("name"),), title.name)
        if meta:
            imdb = meta.get("imdb_id") or meta.get("id") or ""
            return Poster(meta["name"], meta["poster"].replace("/poster/small/", "/poster/medium/"),
                          "Cinemeta", imdb if re.fullmatch(r"tt\d+", imdb) else None)
    return None


# --- iTunes -------------------------------------------------------------------

ITUNES_SEARCH = "https://itunes.apple.com/search"
_ITUNES_SEASON = re.compile(r",?\s*(?:season|series)\s*(\d+).*$", re.I)


def _itunes(term: str, media: str, entity: str) -> list[dict]:
    query = urllib.parse.urlencode({"term": term, "media": media, "entity": entity, "limit": 50})
    results = (_get_json(f"{ITUNES_SEARCH}?{query}") or {}).get("results") or []
    return [r for r in results if r.get("artworkUrl100")]


def _itunes_art(r: dict) -> str:
    return r["artworkUrl100"].replace("100x100bb", "600x600bb")


def itunes_season_poster(title: DiscTitle) -> Poster | None:
    seasons = []
    for r in _itunes(f"{title.name} season {title.season}", "tvShow", "tvSeason"):
        m = _ITUNES_SEASON.search(r.get("collectionName", ""))
        if m and int(m.group(1)) == title.season:
            seasons.append(r)
    best = best_match(seasons, lambda r: (_ITUNES_SEASON.sub("", r.get("collectionName", "")),
                                          r.get("artistName")), title.name, least=2)
    if best:
        return Poster(best.get("artistName") or title.name, _itunes_art(best), "iTunes season")
    return None


def itunes_poster(title: DiscTitle) -> Poster | None:
    searches = [("tvShow", "tvSeason", "collectionName"), ("movie", "movie", "trackName")]
    if not title.is_series:
        searches.reverse()
    for media, entity, field in searches:
        best = best_match(_itunes(title.name, media, entity),
                          lambda r: (_ITUNES_SEASON.sub("", r.get(field, "")),), title.name, least=2)
        if best:
            return Poster(_ITUNES_SEASON.sub("", best.get(field, "")) or title.name,
                          _itunes_art(best), "iTunes")
    return None


# --- Wikipedia -------------------------------------------------------------------

WIKI_API = "https://en.wikipedia.org/w/api.php"
_SCREEN = re.compile(r"\b(film|movie|television|tv series|miniseries|series|anime|documentary|season)\b", re.I)


def _wiki_search(search: str) -> list[dict]:
    query = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": 2,
        "generator": "search", "gsrsearch": search, "gsrlimit": 10,
        "prop": "pageimages|description", "piprop": "thumbnail", "pithumbsize": 600,
        "pilicense": "any",
    })
    pages = (_get_json(f"{WIKI_API}?{query}") or {}).get("query", {}).get("pages", [])
    return sorted(pages, key=lambda p: p.get("index", 99))


def _wiki_bare(title: str) -> str:
    return re.sub(r"\s*\([^)]*\)$", "", title)  # "Backrooms (film)" -> "Backrooms"


def wikipedia_season_poster(title: DiscTitle) -> Poster | None:
    """The season's own article ("Dexter season 5"), whose picture is its cover."""
    pattern = re.compile(rf"^(.+?),?\s*\(?(?:season|series) {title.season}\)?$", re.I)
    pages = []
    for page in _wiki_search(f"{title.name} season {title.season}"):
        m = pattern.match(page.get("title", ""))
        if m and page.get("thumbnail", {}).get("source"):
            pages.append((page, _wiki_bare(m.group(1))))
    best = best_match(pages, lambda p: (p[1],), title.name, least=2)
    if best:
        return Poster(best[1], best[0]["thumbnail"]["source"], "Wikipedia season")
    return None


def wikipedia_poster(title: DiscTitle) -> Poster | None:
    pages = [p for p in _wiki_search(f"{title.name} film OR series")
             if p.get("thumbnail", {}).get("source")
             and _SCREEN.search(p.get("description", "") + " " + p.get("title", ""))]
    best = best_match(pages, lambda p: (_wiki_bare(p.get("title", "")),), title.name, least=3)
    if best:
        return Poster(_wiki_bare(best["title"]), best["thumbnail"]["source"], "Wikipedia")
    return None


def wikipedia_game_cover(name: str) -> str | None:
    """The cover from a video game's Wikipedia article, for games no other
    source has artwork for."""
    pages = [p for p in _wiki_search(f"{name} video game")
             if p.get("thumbnail", {}).get("source")
             and re.search(r"\bgame\b", p.get("description", "") + " " + p.get("title", ""), re.I)]
    best = best_match(pages, lambda p: (_wiki_bare(p.get("title", "")),), name, least=3)
    return best["thumbnail"]["source"] if best else None


# --- Putting them together -----------------------------------------------------------
#
# The disc's name is first pinned to one film or show (Cinemeta gives its
# official title and IMDb id). Sources that can look a show up by that id do,
# so they can't land on a different show with a similar name; the others
# search by the disc's name and then by the official title.

def _season_sources(tmdb_key: str | None, imdb: str | None) -> list:
    sources = []
    if tmdb_key:
        sources.append(("TMDB season", lambda t: tmdb_season_poster(t, tmdb_key, imdb)))
    return sources + [("AniList", anilist_season_poster),
                      ("TVmaze season", lambda t: tvmaze_season_poster(t, imdb)),
                      ("iTunes season", itunes_season_poster),
                      ("Wikipedia season", wikipedia_season_poster)]


def _title_sources(tmdb_key: str | None) -> list:
    sources = [("TMDB", lambda t: tmdb_poster(t, tmdb_key))] if tmdb_key else []
    return sources + [("iTunes", itunes_poster), ("Wikipedia", wikipedia_poster)]


def _first(sources: list, titles: list, report: list | None) -> Poster | None:
    chosen = None
    for name, source in sources:
        for title in titles:
            try:
                found = source(title)
                note = f"{found.title} -> {found.url}" if found else "nothing"
            except PosterKeyError:
                log.warning("TMDB refused the key; check it in the settings")
                found, note = None, "key refused"
            except Exception as e:  # one broken source mustn't stop the others
                log.debug("poster source %s failed: %r", name, e)
                found, note = None, f"error: {e!r}"
            if report is not None:
                report.append(f"  {name:17} [{title.name}]: {note}")
            if found:
                break
        if found and chosen is None:
            chosen = found
            if report is None:
                break
    return chosen


def find_poster(title: DiscTitle, tmdb_key: str | None = None,
                report: list | None = None) -> Poster | None:
    """The poster for a disc. With report (a list), every source is tried and
    what each answered is added to it."""
    try:
        ident = cinemeta_poster(title)
    except Exception as e:
        log.debug("Cinemeta failed: %r", e)
        ident = None
    if report is not None:
        report.append(f"  identified as: {ident.title} ({ident.imdb})" if ident
                      else "  identified as: (Cinemeta has no match)")
    titles = [title]
    if ident and _key(ident.title) != _key(title.name):
        titles.append(DiscTitle(ident.title, title.season, title.disc, title.volume))
    imdb = ident.imdb if ident else None

    season = None
    if title.season is not None:
        season = _first(_season_sources(tmdb_key, imdb), titles, report)
    show = None
    if season is None or report is not None:
        sources = _title_sources(tmdb_key)
        if tmdb_key:  # TMDB, then Cinemeta's own poster, then the rest
            show = _first(sources[:1], titles, report)
            sources = sources[1:]
        show = show or ident or _first(sources, titles, report)
    found = season or show
    if found and ident:
        found = Poster(ident.title, found.url, found.source, imdb)
    if report is not None:
        report.append(f"  => {found.source}: {found.title} -> {found.url}" if found else "  => nothing")
    if found:
        log.info("poster for %r: %s (%s)", title.name, found.title, found.source)
    else:
        log.info("no poster found for %r", title.name)
    return found
