"""Bundled reference data: cities, categories, city page slugs.

Kept in its own module so both the client and the resolver can use it without a
circular import. All three files are harvested from Divar itself and refreshed
with tools/harvest_*.py + tools/build_data.py.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"

_city_cache: dict | None = None
_category_cache: list | None = None
_city_slug_cache: dict | None = None


def load_cities() -> dict:
    """{'1': 'تهران', ...} with an inverted name -> id map."""
    global _city_cache
    if _city_cache is None:
        raw = json.loads((DATA_DIR / "cities.json").read_text(encoding="utf-8"))
        _city_cache = {"by_id": raw, "by_name": {v: k for k, v in raw.items()}}
    return _city_cache


def load_categories() -> list[dict]:
    """[{'slug': 'mobile-phones', 'name': 'موبایل', 'parents': [...]}]"""
    global _category_cache
    if _category_cache is None:
        try:
            _category_cache = json.loads((DATA_DIR / "categories.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _category_cache = []
    return _category_cache or []


def load_city_slugs() -> dict:
    """{'1': 'tehran', ...}: the ASCII path segment divar.ir wants in /s/<slug>."""
    global _city_slug_cache
    if _city_slug_cache is None:
        try:
            path = DATA_DIR / "city_slugs.json"
            _city_slug_cache = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            _city_slug_cache = {}
    return _city_slug_cache


def category_slug_set() -> set[str]:
    return {entry.get("slug") for entry in load_categories() if entry.get("slug")}


def find_category(value: str | None) -> dict | None:
    """Look a category up by slug (case-insensitive) or exact Persian name."""
    if not value:
        return None
    needle = str(value).strip().lower().replace("\u200c", "")
    for entry in load_categories():
        if (entry.get("slug") or "").lower() == needle:
            return entry
        if (entry.get("name") or "").replace("\u200c", "").lower() == needle:
            return entry
    return None


def reset_cache() -> None:
    """Test helper: forget the loaded files (after regenerating data)."""
    global _city_cache, _category_cache, _city_slug_cache
    _city_cache = _category_cache = _city_slug_cache = None
