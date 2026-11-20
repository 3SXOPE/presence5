"""Game artwork from SteamGridDB, a community database of game logos, icons and covers.

SteamGridDB needs a free API key, from https://www.steamgriddb.com/profile/preferences/api.
Without a key, or when the database has nothing for a game, the client falls back to the
PlayStation Store cover it already uses.
"""
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

API = "https://www.steamgriddb.com/api/v2"

# Which kind of picture to ask for. Discord shows the large image in a square,
# so icons and square covers fit best; logos are usually wide.
KINDS = ("logo", "icon", "cover")
ENDPOINT = {"logo": "logos", "icon": "icons", "cover": "grids"}
FILTERS = {
    "logo": {"types": "static", "nsfw": "false", "humor": "false"},
    "icon": {"types": "static", "nsfw": "false", "humor": "false"},
    "cover": {"types": "static", "nsfw": "false", "humor": "false",
              "dimensions": "512x512,1024x1024"},
}


class ArtworkError(Exception): pass


def clean_name(name: str) -> str:
    """Strip trademark signs and edition clutter that make the search miss."""
    name = re.sub(r"[™®©]", "", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def _get(path: str, key: str, params: dict | None = None):
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": "presence5",
    })
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise ArtworkError("SteamGridDB rejected the API key") from e
        if e.code == 404:
            return None
        raise ArtworkError(f"SteamGridDB returned HTTP {e.code}") from e
    except (OSError, ValueError) as e:
        raise ArtworkError(f"could not reach SteamGridDB: {e!r}") from e

    if not body.get("success"):
        return None
    return body.get("data")


def find_game_id(name: str, key: str) -> int | None:
    results = _get(f"/search/autocomplete/{urllib.parse.quote(clean_name(name), safe='')}", key)
    if not results:
        return None
    # Prefer an exact name match over the search's first guess.
    wanted = clean_name(name).casefold()
    for game in results:
        if clean_name(game.get("name", "")).casefold() == wanted:
            return game["id"]
    return results[0]["id"]


def find_artwork(name: str, key: str, kind: str = "logo") -> str | None:
    """Return an image URL for the game, or None if the database has nothing.

    Raises ArtworkError for problems worth telling the user about, such as a bad key.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown artwork kind {kind!r}")

    game_id = find_game_id(name, key)
    if game_id is None:
        log.info("SteamGridDB has no game called %r", name)
        return None

    images = _get(f"/{ENDPOINT[kind]}/game/{game_id}", key, FILTERS[kind])
    if not images:
        log.info("SteamGridDB has no %s for %r (game %s)", kind, name, game_id)
        return None

    # Results come best-voted first.
    url = images[0].get("url")
    log.debug("SteamGridDB %s for %r: %s", kind, name, url)
    return url


if __name__ == "__main__":
    import os
    import sys
    logging.basicConfig(level=logging.DEBUG)
    print(find_artwork(sys.argv[1] if len(sys.argv) > 1 else "Devil May Cry 5",
                       os.environ["SGDB_API_KEY"],
                       sys.argv[2] if len(sys.argv) > 2 else "logo"))
