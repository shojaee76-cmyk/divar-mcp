"""Tool implementations shared by the MCP server and the CLI.

The MCP layer only knows how to transport JSON; everything with meaning lives
here so it can be unit tested and reused (LangChain, plain scripts, cron).
"""

from __future__ import annotations

import os
import urllib.parse
from typing import Any

from .client import DivarClient, DivarError, WEB_BASE, load_categories, load_cities

SORT_VALUES = ["newest", "price_asc", "price_desc"]


def build_client() -> DivarClient:
    """Client configured from env (handy for slow networks / shared IPs)."""
    def _float(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    return DivarClient(
        timeout=_float("DIVAR_TIMEOUT", 25.0),
        min_interval=_float("DIVAR_MIN_INTERVAL", 0.8),
        cache_ttl=_float("DIVAR_CACHE_TTL", 180.0),
    )


def _apply_age_filter(result: dict, max_age_hours: float | None) -> dict:
    if max_age_hours is None:
        return result
    posts = result.get("posts") or []
    kept = [p for p in posts if p.get("age_hours") is None or p["age_hours"] <= max_age_hours]
    dropped = len(posts) - len(kept)
    result["posts"] = kept
    result["count"] = len(kept)
    if dropped:
        result["filtered_out_by_age"] = dropped
        result["max_age_hours"] = max_age_hours
    return result


def _trim(result: dict, limit: int | None) -> dict:
    if limit and result.get("posts"):
        result["posts"] = result["posts"][: int(limit)]
        result["count"] = len(result["posts"])
    result.pop("cursor", None)
    return result


# ------------------------------------------------------------------ tools


def divar_search(
    query: str | None = None,
    city: str | int | None = "تهران",
    category: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
    has_photo: bool = False,
    district_ids: list[str] | None = None,
    brand_model: str | None = None,
    sort: str | None = None,
    pages: int = 1,
    page_size: int = 24,
    max_age_hours: float | None = None,
    limit: int | None = None,
    client: DivarClient | None = None,
) -> dict:
    """Search live Divar listings and return normalized rows."""
    client = client or build_client()
    if sort and sort not in SORT_VALUES:
        raise DivarError(f"unknown sort {sort!r}; use one of {', '.join(SORT_VALUES)}")
    pages = max(1, min(int(pages or 1), 5))
    result = client.search_many(
        pages=pages,
        city=city,
        query=query,
        category=category,
        price_min=price_min,
        price_max=price_max,
        district_ids=district_ids,
        has_photo=bool(has_photo),
        brand_model=brand_model,
        page_size=max(1, min(int(page_size or 24), 60)),
        sort=sort,
    )
    result = _apply_age_filter(result, max_age_hours)
    result["source"] = "api.divar.ir (public read-only web API)"
    return _trim(result, limit)


def divar_get_post(token: str, client: DivarClient | None = None) -> dict:
    """Full public view of one post: price, attributes, images, dates, location."""
    client = client or build_client()
    post = client.get_post(token)
    post["source"] = "api.divar.ir (public read-only web API)"
    post["note"] = (
        "Divar does not expose the seller's phone number to logged-out clients; "
        "the buyer has to open the post in the app or web to use chat/call."
    )
    return post


def divar_price_analysis(
    query: str | None = None,
    category: str | None = None,
    city: str | int | None = "تهران",
    pages: int = 2,
    price_min: int | None = None,
    price_max: int | None = None,
    client: DivarClient | None = None,
) -> dict:
    """Comparable-listing price distribution, for valuing an item before listing it."""
    client = client or build_client()
    stats = client.price_stats(
        city=city,
        query=query,
        category=category,
        pages=max(1, min(int(pages or 2), 6)),
        price_min=price_min,
        price_max=price_max,
    )
    stats["source"] = "api.divar.ir (public read-only web API)"
    stats["how_to_read"] = (
        "suggested_ask_range = 40th-75th percentile of live prices. Prices are Toman. "
        "Divar's own marketplace supports haggling, so asking near p50-p75 keeps you "
        "inside the active band without being the cheapest listing."
    )
    return stats


def divar_similar_posts(
    token: str,
    city: str | int | None = None,
    limit: int = 12,
    client: DivarClient | None = None,
) -> dict:
    """Listings comparable to one post (same category/brand/city)."""
    client = client or build_client()
    result = client.similar_posts(token, city=city, limit=max(1, min(int(limit or 12), 40)))
    result["source"] = "api.divar.ir (public read-only web API)"
    return result


def divar_list_cities(query: str | None = None, limit: int = 400) -> dict:
    """Divar city ids used by the search filter (Persian names)."""
    items = load_cities()["by_id"]
    rows = [{"id": cid, "name": name} for cid, name in items.items()]
    if query:
        normalised = str(query).replace("\u200c", "").replace("ي", "ی").replace("ك", "ک")
        rows = [r for r in rows if normalised in r["name"].replace("\u200c", "") or r["id"] == str(query)]
    return {
        "count": len(rows),
        "cities": rows[: int(limit or 400)],
        "hint": "Pass either the id or the Persian name as `city` to divar_search.",
    }


def divar_list_categories(query: str | None = None, limit: int = 200) -> dict:
    """Divar category slugs accepted by the search filter, with Persian names."""
    items = load_categories()
    if query:
        q = str(query).lower().replace("\u200c", "")
        matched = []
        for item in items:
            haystack = " ".join([item.get("slug", ""), item.get("name", "")] + list(item.get("parents") or []))
            if q in haystack.lower():
                matched.append(item)
        items = matched
    return {
        "count": len(items),
        "categories": items[: int(limit or 200)],
        "hint": "Pass `slug` as `category` to divar_search. Parents show the breadcrumb on divar.ir.",
    }


def divar_post_filters(
    city: str | int | None = "تهران",
    category: str | None = None,
    client: DivarClient | None = None,
) -> dict:
    """The filter widgets Divar currently exposes for a city (and category)."""
    client = client or build_client()
    return client.filters(city=city, category=category)


def divar_search_url(
    query: str | None = None,
    city: str | int | None = "تهران",
    category: str | None = None,
    price_min: int | None = None,
    price_max: int | None = None,
) -> dict:
    """A divar.ir URL a human can open in a browser for the same search."""
    client = build_client()
    city_id, city_name = client.resolve_city(city)
    slug = None
    for entry in load_categories():
        if entry.get("slug") == category:
            slug = category
            break
    params = {}
    if query:
        params["q"] = query
    for key, value in (("price_min", price_min), ("price_max", price_max)):
        if value is not None:
            params[key] = int(value)
    path = "/s/" + urllib.parse.quote(city_name)
    if slug:
        path += "/" + urllib.parse.quote(slug)
    url = WEB_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return {
        "url": url,
        "city_id": city_id,
        "city": city_name,
        "category": category,
        "note": "Open in a browser; Divar's web UI accepts q and price_min/price_max query params.",
    }


# ------------------------------------------------------------------ spec

def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


_CITY = {"type": "string", "description": "City id (e.g. \"1\") or Persian name (e.g. \"تهران\")."}
_CATEGORY = {
    "type": "string",
    "description": "Divar category slug, e.g. mobile-phones, light, buy-residential. See divar_list_categories.",
}
_PRICE = {"type": "integer", "description": "Price in Toman (not Rial)."}

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "divar_search",
        "description": (
            "Search live divar.ir listings (Iran's largest classifieds). Read-only, no account needed. "
            "Supports text query, city, category, Toman price range, photo-only, district ids and "
            "brand/model, with cursor pagination. Returns normalized rows (token, title, price in Toman, "
            "city, district, relative time, image, url)."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "Free-text search, Persian (e.g. \"پژو ۲۰۶\")."},
                "city": _CITY,
                "category": _CATEGORY,
                "price_min": _PRICE,
                "price_max": _PRICE,
                "has_photo": {"type": "boolean", "description": "Only posts with photos."},
                "district_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Divar numeric district ids (e.g. [\"208\"]). Get them from divar_get_post district_id.",
                },
                "brand_model": {
                    "type": "string",
                    "description": "Exact brand/model string, e.g. \"samsung galaxy s21 5g\" (from divar_get_post).",
                },
                "sort": {"type": "string", "enum": SORT_VALUES, "description": "Client-side sort of the sampled rows."},
                "pages": {"type": "integer", "description": "Cursor pages to fetch (1-5, default 1)."},
                "page_size": {"type": "integer", "description": "Rows per page (max 60, default 24)."},
                "max_age_hours": {
                    "type": "number",
                    "description": "Drop posts older than this (Divar's own recency filter is unreliable; this is client-side).",
                },
                "limit": {"type": "integer", "description": "Return at most this many rows."},
            }
        ),
    },
    {
        "name": "divar_get_post",
        "description": (
            "Full public detail for one divar.ir post: title, description, price in Toman, structured "
            "attributes (brand/model, year, mileage, ...), image URLs, Jalali and ISO posted/updated dates, "
            "city/district, category breadcrumb and its district id (usable as a divar_search filter)."
        ),
        "inputSchema": _schema(
            {"token": {"type": "string", "description": "Post token (gaxi5lYL) or any divar.ir post URL."}},
            ["token"],
        ),
    },
    {
        "name": "divar_price_analysis",
        "description": (
            "Price distribution of comparable live listings for an item, to decide what to ask before "
            "posting an ad (median, p25/p75, suggested ask range, price bands, freshness, cheapest and "
            "priciest samples). Prices in Toman."
        ),
        "inputSchema": _schema(
            {
                "query": {"type": "string", "description": "Product text, e.g. \"آیفون ۱۳\"."},
                "category": _CATEGORY,
                "city": _CITY,
                "pages": {"type": "integer", "description": "Cursor pages to sample (1-6, default 2 ~= 48 posts)."},
                "price_min": _PRICE,
                "price_max": _PRICE,
            }
        ),
    },
    {
        "name": "divar_similar_posts",
        "description": (
            "Comparable listings for an existing post: same category, brand/model and city, excluding "
            "the post itself. Useful for a seller checking competition or a buyer checking the price."
        ),
        "inputSchema": _schema(
            {
                "token": {"type": "string", "description": "Post token or divar.ir URL."},
                "city": _CITY,
                "limit": {"type": "integer", "description": "Max comparables (default 12)."},
            },
            ["token"],
        ),
    },
    {
        "name": "divar_list_cities",
        "description": "Look up divar.ir city ids by Persian name (or list them all).",
        "inputSchema": _schema({"query": {"type": "string", "description": "Persian city name (e.g. \"مشهد\")."}}),
    },
    {
        "name": "divar_list_categories",
        "description": (
            "Look up divar.ir category slugs by Persian or English text, with their breadcrumb parents. "
            "Use the slug in divar_search/divar_price_analysis."
        ),
        "inputSchema": _schema({"query": {"type": "string", "description": "Text to match, e.g. \"موبایل\" or \"car\"."}}),
    },
    {
        "name": "divar_post_filters",
        "description": "Show which filters divar.ir exposes for a city/category (price range, districts, photo-only).",
        "inputSchema": _schema({"city": _CITY, "category": _CATEGORY}),
    },
    {
        "name": "divar_search_url",
        "description": "Build a divar.ir web URL for a search, so a human can open it in a browser.",
        "inputSchema": _schema(
            {
                "query": {"type": "string"},
                "city": _CITY,
                "category": _CATEGORY,
                "price_min": _PRICE,
                "price_max": _PRICE,
            }
        ),
    },
]

REGISTRY = {
    "divar_search": divar_search,
    "divar_get_post": divar_get_post,
    "divar_price_analysis": divar_price_analysis,
    "divar_similar_posts": divar_similar_posts,
    "divar_list_cities": divar_list_cities,
    "divar_list_categories": divar_list_categories,
    "divar_post_filters": divar_post_filters,
    "divar_search_url": divar_search_url,
}


def call_tool(name: str, arguments: dict | None) -> Any:
    fn = REGISTRY.get(name)
    if fn is None:
        raise DivarError(f"unknown tool: {name}")
    return fn(**(arguments or {}))
