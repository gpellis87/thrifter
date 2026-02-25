"""
Persistent user settings stored as a JSON file in data/.

Controls data-source preferences (API vs scrape) and FB Marketplace toggle.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "settings.json"

_DEFAULTS = {
    "ebay_mode": "auto",          # "api", "scrape", or "auto" (try API, fall back to scrape)
    "fb_marketplace_enabled": True,
    "craigslist_enabled": False,
    "offerup_enabled": False,
}

_cache: dict | None = None


def _ensure_dir():
    _FILE.parent.mkdir(parents=True, exist_ok=True)


def load() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    _ensure_dir()
    if _FILE.exists():
        try:
            _cache = {**_DEFAULTS, **json.loads(_FILE.read_text())}
            return _cache
        except Exception as e:
            log.warning("Could not read settings: %s", e)
    _cache = dict(_DEFAULTS)
    return _cache


def save(settings: dict) -> dict:
    global _cache
    _ensure_dir()
    merged = {**load(), **settings}
    for k in list(merged.keys()):
        if k not in _DEFAULTS:
            del merged[k]
    _FILE.write_text(json.dumps(merged, indent=2))
    _cache = merged
    return merged


def get(key: str):
    return load().get(key, _DEFAULTS.get(key))
