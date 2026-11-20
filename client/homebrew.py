"""Icons for PS5 homebrew, from the community homebrew catalog, by title ID.

Homebrew has no store page and isn't in Discord's game list, and its own icon
lives on the console where Discord can't load it. The catalog
(https://github.com/darkxex/ps5-homebrew-catalog) lists many homebrew apps
with a public icon URL.
"""
import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

CATALOG_URL = "https://raw.githubusercontent.com/darkxex/ps5-homebrew-catalog/main/apps/{title_id}.json"
_TITLE_ID = re.compile(r"^[A-Z]{4}\d{5}$")


def homebrew_icon(title_id: str) -> tuple[str, str] | None:
    """(name, icon URL) for a homebrew title ID listed in the catalog, else None."""
    if not _TITLE_ID.match(title_id or ""):
        return None
    try:
        with urllib.request.urlopen(CATALOG_URL.format(title_id=title_id), timeout=5) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            log.debug("homebrew catalog: HTTP %s for %s", e.code, title_id)
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        log.debug("homebrew catalog unreachable: %s", e)
        return None
    icon = data.get("icon_url") if isinstance(data, dict) else None
    if not icon or not str(icon).startswith("https://"):
        return None
    return str(data.get("name") or ""), str(icon)
