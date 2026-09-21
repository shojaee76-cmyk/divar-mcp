"""Forgiving input resolution: turn what an agent typed into valid Divar values.

An agent should not have to know that "tehran" is city id 1 or that the phone
category is the slug `mobile-phones`. Anything close enough is accepted, and
anything wrong comes back with concrete suggestions instead of a bare 400.
"""

from __future__ import annotations

import difflib

from .datasets import find_category, load_categories, load_cities, load_city_slugs

_PERSIAN_FIXES = str.maketrans({"ي": "ی", "ك": "ک", "\u200c": "", "\u200f": "", "\u200e": ""})


def _norm(text: str) -> str:
    return str(text).strip().lower().translate(_PERSIAN_FIXES)


def suggest_cities(query: str, limit: int = 5) -> list[dict]:
    """Closest city names for a free-text query (Persian or Latin, typos included)."""
    cities = load_cities()["by_id"]
    slugs = load_city_slugs()
    needle = _norm(query)
    if not needle:
        return []
    scored: list[tuple[float, dict]] = []
    for cid, name in cities.items():
        slug = slugs.get(str(cid), "")
        hay = _norm(name)
        ratio = max(
            difflib.SequenceMatcher(None, needle, hay).ratio(),
            difflib.SequenceMatcher(None, needle, _norm(slug)).ratio() if slug else 0.0,
        )
        if needle in hay or hay in needle or (slug and needle in _norm(slug)):
            ratio += 0.35
        if ratio > 0.45:
            scored.append((ratio, {"id": cid, "name": name, "slug": slug or None}))
    scored.sort(key=lambda item: -item[0])
    return [entry for _, entry in scored[:limit]]


def suggest_categories(query: str, limit: int = 5) -> list[dict]:
    """Closest category slugs/names for a free-text query."""
    needle = _norm(query)
    if not needle:
        return []
    scored: list[tuple[float, dict]] = []
    for entry in load_categories():
        slug = entry.get("slug") or ""
        name = entry.get("name") or ""
        parents = " ".join(entry.get("parents") or [])
        hay = _norm(" ".join([slug, name, parents]))
        ratio = max(
            difflib.SequenceMatcher(None, needle, _norm(slug)).ratio(),
            difflib.SequenceMatcher(None, needle, _norm(name)).ratio(),
            0.9 if needle in hay else 0.0,
        )
        if ratio > 0.4:
            scored.append((ratio, entry))
    scored.sort(key=lambda item: -item[0])
    return [entry for _, entry in scored[:limit]]


def resolve_city_input(city: str | int | None) -> tuple[str, str] | None:
    """('city_id', 'Persian name') or None when nothing plausible matched."""
    cities = load_cities()
    slugs = load_city_slugs()
    if city is None or str(city).strip() == "":
        return ("1", cities["by_id"].get("1", "تهران"))
    token = str(city).strip()
    if token.isdigit():
        name = cities["by_id"].get(token)
        return (token, name if name else token)
    normalized = _norm(token)
    for cid, name in cities["by_id"].items():
        if _norm(name) == normalized:
            return (cid, name)
    # an ASCII city slug ("tehran", "mashhad") is a first-class way to ask
    for cid, slug in slugs.items():
        if _norm(slug) == normalized and cid in cities["by_id"]:
            return (cid, cities["by_id"][cid])
    exact = [entry for entry in suggest_cities(token, limit=5) if _norm(entry["name"]) == normalized]
    if exact:
        return (exact[0]["id"], exact[0]["name"])
    partial = suggest_cities(token, limit=2)
    if partial:
        first = partial[0]
        if _norm(token) and _norm(token) in _norm(first["name"]):
            return (first["id"], first["name"])
    return None


def resolve_category_input(category: str | None) -> tuple[str | None, list[dict]]:
    """(slug or None, suggestions). None slug means no category filter."""
    if category is None or str(category).strip() == "":
        return None, []
    token = str(category).strip()
    # Divar writes category ROOT for "everything" but rejects it as a filter
    if token.upper() == "ROOT":
        return None, []
    entry = find_category(token)
    if entry:
        return entry["slug"], []
    suggestions = suggest_categories(token, limit=5)
    exact_slug = next((s for s in suggestions if _norm(s.get("slug", "")) == _norm(token)), None)
    if exact_slug:
        return exact_slug["slug"], []
    if len(suggestions) == 1:
        return suggestions[0]["slug"], []
    return None, suggestions
