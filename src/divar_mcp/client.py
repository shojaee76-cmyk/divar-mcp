"""Divar client: HTTP, polite rate limiting, caching, and response parsing.

Public read endpoints of divar.ir (no account, no API key needed):

  POST /v8/postlist/w/search    listing search (filters + cursor pagination)
  POST /v8/postlist/w/filters   filter schema for a city/category
  GET  /v8/posts-v2/web/{token} single post view

The official, key-gated API (``kenar``) is a different product and is not used
here; see the README for the difference.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .normalize import (
    extract_token,
    human_toman,
    parse_age_hours,
    parse_int,
    parse_jalali_datetime,
    parse_price,
)

API_BASE = "https://api.divar.ir"
WEB_BASE = "https://divar.ir"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PAGINATION_TYPE = "type.googleapis.com/post_list.PaginationData"

DATA_DIR = Path(__file__).parent / "data"


class DivarError(RuntimeError):
    """Raised for any Divar API failure, with the message Divar itself sent."""

    def __init__(self, message: str, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def __str__(self) -> str:  # pragma: no cover - trivial
        bits = [self.message]
        if self.status:
            bits.append(f"(HTTP {self.status})")
        return " ".join(bits)


# ------------------------------------------------------------------ data files

_city_cache: dict | None = None
_category_cache: dict | None = None


def load_cities() -> dict:
    """{'1': 'تهران', ...} plus an inverted name -> id map."""
    global _city_cache
    if _city_cache is None:
        raw = json.loads((DATA_DIR / "cities.json").read_text(encoding="utf-8"))
        by_name = {v: k for k, v in raw.items()}
        _city_cache = {"by_id": raw, "by_name": by_name}
    return _city_cache


def load_categories() -> list[dict]:
    """[{'slug': 'mobile-phones', 'name': 'موبایل', 'parents': [...]}]"""
    global _category_cache
    if _category_cache is None:
        _category_cache = json.loads((DATA_DIR / "categories.json").read_text(encoding="utf-8"))
    return _category_cache or []


# ------------------------------------------------------------------ client


@dataclass
class DivarClient:
    """Read-only client for divar.ir's public web API.

    ``min_interval`` keeps us polite (Divar throttles around 30 requests per
    minute per IP); ``cache_ttl`` avoids re-fetching identical requests.
    """

    timeout: float = 25.0
    min_interval: float = 0.8
    cache_ttl: float = 180.0
    max_retries: int = 3
    user_agent: str = DEFAULT_UA
    api_base: str = API_BASE
    cache_size: int = 256
    _last_call: float = field(default=0.0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _cache: dict = field(default_factory=dict, init=False, repr=False)
    _request_count: int = field(default=0, init=False, repr=False)

    # -------------------------------------------------------------- transport
    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    @property
    def request_count(self) -> int:
        return self._request_count

    def _cached(self, key: str):
        hit = self._cache.get(key)
        if not hit:
            return None
        expires, value = hit
        if expires < time.monotonic():
            self._cache.pop(key, None)
            return None
        return value

    def _store(self, key: str, value):
        if len(self._cache) >= self.cache_size:
            oldest = min(self._cache, key=lambda k: self._cache[k][0])
            self._cache.pop(oldest, None)
        self._cache[key] = (time.monotonic() + self.cache_ttl, value)
        return value

    def _attempt(self, method: str, url: str, payload: bytes | None) -> dict:
        """One HTTP round trip. Split out so tests can stub the transport."""
        req = urllib.request.Request(url, data=payload, method=method)
        req.add_header("user-agent", self.user_agent)
        req.add_header("accept", "application/json, text/plain, */*")
        req.add_header("accept-language", "fa-IR,fa;q=0.9,en;q=0.8")
        if payload is not None:
            req.add_header("content-type", "application/json")
        req.add_header("origin", WEB_BASE)
        req.add_header("referer", WEB_BASE + "/")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            code = None
            message = detail[:400]
            try:
                parsed = json.loads(detail)
                message = parsed.get("message", message)
                code = parsed.get("code")
            except Exception:
                pass
            raise DivarError(message, status=exc.code, code=code) from exc
        return json.loads(raw)

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        cache_key = ""
        if method == "GET" or body is not None:
            blob = f"{method}:{path}:{json.dumps(body, sort_keys=True, ensure_ascii=False)}"
            cache_key = hashlib.sha256(blob.encode()).hexdigest()
            cached = self._cached(cache_key)
            if cached is not None:
                return cached

        url = self.api_base + path
        payload = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            self._throttle()
            try:
                data = self._attempt(method, url, payload)
                self._request_count += 1
                if cache_key:
                    self._store(cache_key, data)
                return data
            except DivarError as exc:
                # 4xx (other than 429) are caller errors: fail fast, no retry.
                if exc.status and 400 <= exc.status < 500 and exc.status != 429:
                    raise
                last_error = exc
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.max_retries - 1:
                time.sleep(min(6.0, 1.2 * (2 ** attempt)) + random.random() * 0.4)

        if isinstance(last_error, DivarError):
            raise last_error
        raise DivarError(f"network error talking to divar.ir: {last_error}")

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _form_data(**fields) -> dict:
        data = {}
        for key, value in fields.items():
            if value in (None, "", [], False, (None, None)):
                continue
            if key in ("price_min", "price_max"):
                continue
            data[key] = value
        return data

    def resolve_city(self, city: str | int | None) -> tuple[str, str]:
        """Return (city_id, persian_name). Accepts id, Persian name or Persian alias."""
        cities = load_cities()["by_id"]
        if city is None or str(city).strip() == "":
            return "1", cities.get("1", "تهران")
        token = str(city).strip()
        if token.isdigit():
            return token, cities.get(token, token)
        if token in cities.values():
            name = token
            cid = load_cities()["by_name"][name]
            return cid, name
        # slug-ish or partial match
        normalised = token.replace("‌", "").replace("ي", "ی").replace("ك", "ک").strip()
        for cid, name in cities.items():
            if name.replace("‌", "") == normalised:
                return cid, name
        for cid, name in cities.items():
            if normalised and normalised in name:
                return cid, name
        raise DivarError(
            f"unknown city {city!r}. Use a city id (e.g. 1) or a Persian name from divar_list_cities."
        )

    # -------------------------------------------------------------- search
    @staticmethod
    def _build_search_body(
        city_id: str,
        query: str | None = None,
        category: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        district_ids: list[str] | None = None,
        has_photo: bool = False,
        brand_model: str | None = None,
        page: int = 1,
        page_size: int = 24,
        cursor: dict | None = None,
    ) -> dict:
        data: dict = {}
        if category:
            data["category"] = {"str": {"value": category}}
        if price_min is not None or price_max is not None:
            rng = {}
            if price_min is not None:
                rng["minimum"] = int(price_min)
            if price_max is not None:
                rng["maximum"] = int(price_max)
            data["price"] = {"number_range": rng}
        if district_ids:
            data["districts"] = {"repeated_string": {"value": [str(d) for d in district_ids]}}
        if brand_model:
            data["brand_model"] = {"repeated_string": {"value": [brand_model]}}
        if has_photo:
            data["has-photo"] = {"boolean": {}}

        search_data: dict = {}
        if query:
            search_data["query"] = query
        if data:
            search_data["form_data"] = {"data": data}

        body: dict = {"city_ids": [city_id]}
        if search_data:
            body["search_data"] = search_data
        if cursor:
            body["pagination_data"] = {
                "@type": PAGINATION_TYPE,
                "page": page,
                "page_size": max(1, min(int(page_size), 60)),
                **cursor,
            }
        else:
            body["pagination_data"] = {
                "@type": PAGINATION_TYPE,
                "page": max(1, int(page)),
                "page_size": max(1, min(int(page_size), 60)),
            }
        return body

    @staticmethod
    def _parse_row(row: dict) -> dict:
        payload = (row.get("action") or {}).get("payload") or {}
        web_info = payload.get("web_info") or {}
        token = row.get("token") or payload.get("token") or ""
        price_text = row.get("middle_description_text") or ""
        price, note = parse_price(price_text)
        bottom = row.get("bottom_description_text") or ""
        time_text = bottom.split(" در ")[0].strip() if " در " in bottom else bottom
        return {
            "token": token,
            "title": (row.get("title") or "").strip(),
            "price_toman": price,
            "price_text": price_text.strip(),
            "price_note": note,
            "price_human": human_toman(price),
            "city": web_info.get("city_persian"),
            "district": web_info.get("district_persian"),
            "time_text": time_text,
            "age_hours": parse_age_hours(time_text),
            "image": row.get("image_url"),
            "image_count": row.get("image_count"),
            "has_chat": row.get("has_chat"),
            "url": f"{WEB_BASE}/v/{token}" if token else None,
        }

    def search(
        self,
        city: str | int | None = None,
        query: str | None = None,
        category: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        district_ids: list[str] | None = None,
        has_photo: bool = False,
        brand_model: str | None = None,
        page: int = 1,
        page_size: int = 24,
        sort: str | None = None,
    ) -> dict:
        """One page of listings. ``page>1`` follows Divar's cursor internally."""
        city_id, city_name = self.resolve_city(city)
        cursor = None
        result: dict = {}
        for current in range(1, max(1, int(page)) + 1):
            body = self._build_search_body(
                city_id, query, category, price_min, price_max,
                district_ids, has_photo, brand_model, current, page_size, cursor,
            )
            payload = self._request("POST", "/v8/postlist/w/search", body)
            rows = [
                self._parse_row(w["data"])
                for w in payload.get("list_widgets", [])
                if w.get("widget_type") == "POST_ROW"
            ]
            pagination = payload.get("pagination") or {}
            cursor_next = pagination.get("data")
            headline = ""
            top = payload.get("list_top_widgets") or []
            if top:
                headline = (top[0].get("data") or {}).get("text", "")
            result = {
                "city_id": city_id,
                "city": city_name,
                "query": query,
                "category": category,
                "page": current,
                "page_size": page_size,
                "count": len(rows),
                "has_next_page": bool(pagination.get("has_next_page")),
                "headline": headline,
                "posts": rows,
                "cursor": cursor_next,
                "search_id": payload.get("search_id"),
            }
            if not pagination.get("has_next_page"):
                break
            if cursor_next:
                cursor = dict(cursor_next)
                cursor.pop("@type", None)
                cursor.pop("search_uid", None)
                cursor.pop("viewed_tokens", None)
            else:
                break

        if sort:
            result["posts"] = self._sort_posts(result.get("posts", []), sort)
        result["sort"] = sort
        return result

    @staticmethod
    def _sort_posts(posts: list[dict], sort: str) -> list[dict]:
        key = sort.lower()
        priced = [p for p in posts if p.get("price_toman")]
        unpriced = [p for p in posts if not p.get("price_toman")]
        if key in ("price_asc", "cheapest"):
            return sorted(priced, key=lambda p: p["price_toman"]) + unpriced
        if key in ("price_desc", "expensive"):
            return sorted(priced, key=lambda p: p["price_toman"], reverse=True) + unpriced
        if key in ("newest", "recent"):
            return sorted(posts, key=lambda p: p.get("age_hours") if p.get("age_hours") is not None else 1e9)
        if key in ("oldest",):
            return sorted(posts, key=lambda p: -(p.get("age_hours") or 0))
        return posts

    def search_many(self, pages: int = 1, **kwargs) -> dict:
        """Search several cursor-pages, de-duplicated, in one call."""
        pages = max(1, int(pages))
        collected: list[dict] = []
        seen: set[str] = set()
        meta: dict = {}
        cursor = None
        city_id, city_name = self.resolve_city(kwargs.get("city"))
        page_size = kwargs.get("page_size", 24)
        for current in range(1, pages + 1):
            body = self._build_search_body(
                city_id,
                kwargs.get("query"),
                kwargs.get("category"),
                kwargs.get("price_min"),
                kwargs.get("price_max"),
                kwargs.get("district_ids"),
                kwargs.get("has_photo", False),
                kwargs.get("brand_model"),
                current,
                page_size,
                cursor,
            )
            payload = self._request("POST", "/v8/postlist/w/search", body)
            rows = [
                self._parse_row(w["data"])
                for w in payload.get("list_widgets", [])
                if w.get("widget_type") == "POST_ROW"
            ]
            for row in rows:
                if row["token"] and row["token"] not in seen:
                    seen.add(row["token"])
                    collected.append(row)
            pagination = payload.get("pagination") or {}
            if not meta:
                top = payload.get("list_top_widgets") or []
                meta = {
                    "city_id": city_id,
                    "city": city_name,
                    "headline": (top[0].get("data") or {}).get("text", "") if top else "",
                    "search_id": payload.get("search_id"),
                }
            if not pagination.get("has_next_page"):
                break
            cursor_next = pagination.get("data")
            if not cursor_next:
                break
            cursor = dict(cursor_next)
            cursor.pop("@type", None)
            cursor.pop("search_uid", None)
            cursor.pop("viewed_tokens", None)

        meta.update(
            {
                "pages_fetched": min(pages, max(1, (len(collected) // max(1, int(page_size))) + 1)),
                "count": len(collected),
                "posts": collected,
                "query": kwargs.get("query"),
                "category": kwargs.get("category"),
            }
        )
        sort = kwargs.get("sort")
        if sort:
            meta["posts"] = self._sort_posts(collected, sort)
            meta["sort"] = sort
        return meta

    # -------------------------------------------------------------- post view
    @staticmethod
    def _parse_detail(payload: dict) -> dict:
        sections = {s.get("section_name"): s for s in payload.get("sections", [])}
        out: dict = {
            "token": (payload.get("webengage") or {}).get("token")
            or (payload.get("share") or {}).get("web_url", "").rstrip("/").split("/")[-1],
            "url": (payload.get("share") or {}).get("web_url"),
            "title": None,
            "description": None,
            "posted_text": None,
            "posted_at": None,
            "updated_at": None,
            "relative_time": None,
            "location_line": None,
            "price_toman": None,
            "price_text": None,
            "price_note": None,
            "attributes": {},
            "tags": [],
            "category": None,
            "category_path": [],
            "city": None,
            "city_id": None,
            "district": None,
            "district_id": None,
            "images": [],
            "chat_enabled": None,
            "status": None,
            "business_type": None,
        }

        webengage = payload.get("webengage") or {}
        out["category"] = webengage.get("category") or webengage.get("cat_3") or None
        out["status"] = webengage.get("status") or None
        out["business_type"] = webengage.get("business_type") or None
        if webengage.get("price"):
            out["price_toman"] = int(webengage["price"])
        city = payload.get("city") or {}
        if city:
            out["city"] = city.get("name")
            out["city_id"] = city.get("city_id")
            out["city_slug"] = city.get("second_slug")
        if webengage.get("district"):
            out["district_slug"] = webengage["district"]

        breadcrumb = sections.get("BREADCRUMB", {}).get("widgets", [])
        if breadcrumb:
            seen_slugs: set[str] = set()
            for item in (breadcrumb[0].get("data") or {}).get("parent_items", []):
                action = (item.get("action") or {}).get("payload") or {}
                slug = (
                    ((action.get("search_data") or {}).get("form_data") or {})
                    .get("data", {})
                    .get("category", {})
                    .get("str", {})
                    .get("value")
                )
                # Divar appends brand/model chips after the leaf category, which
                # repeat the leaf slug. Keep the real root -> leaf chain only.
                if slug and slug in seen_slugs:
                    break
                if slug:
                    seen_slugs.add(slug)
                out["category_path"].append({"title": item.get("title"), "slug": slug})

        for widget in sections.get("TITLE", {}).get("widgets", []):
            if widget.get("widget_type") == "LEGEND_TITLE_ROW":
                out["title"] = (widget.get("data") or {}).get("title")
            elif widget.get("widget_type") == "EXPANDABLE_SECTION":
                data = widget.get("data") or {}
                out["relative_time"], out["location_line"] = _split_relative(data.get("title"))
                for sub in data.get("widget_list", []):
                    text = (sub.get("data") or {}).get("text") or ""
                    if "انتشار آگهی" in text:
                        out["posted_text"] = text
                        first = text.split("\n")[0].split("انتشار آگهی:", 1)[-1].strip()
                        out["posted_at"] = parse_jalali_datetime(first)
                        if "\n" in text and "به" in text.split("\n")[1]:
                            second = text.split("\n")[1].split(":", 1)[-1].strip()
                            out["updated_at"] = parse_jalali_datetime(second)

        for widget in sections.get("DESCRIPTION", {}).get("widgets", []):
            if widget.get("widget_type") == "DESCRIPTION_ROW":
                out["description"] = (widget.get("data") or {}).get("text")

        for widget in sections.get("IMAGE", {}).get("widgets", []):
            data = widget.get("data") or {}
            for item in data.get("items", []) or []:
                image = item.get("image") or {}
                if image.get("url"):
                    out["images"].append(
                        {
                            "url": image.get("url"),
                            "thumbnail": image.get("thumbnail_url"),
                            "alt": image.get("alt"),
                        }
                    )

        for widget in sections.get("LIST_DATA", {}).get("widgets", []):
            data = widget.get("data") or {}
            if widget.get("widget_type") == "GROUP_INFO_ROW":
                for item in data.get("items", []) or []:
                    if item.get("title"):
                        out["attributes"][item["title"]] = item.get("value")
            elif data.get("title"):
                out["attributes"][data["title"]] = data.get("value")
                # Divar tags the brand/model row inside the action log; the field
                # name is at info.field and the canonical value at info.value
                # (jli.brand_model is a nested object, not the value string).
                info = (
                    ((widget.get("action_log") or {}).get("server_side_info") or {})
                    .get("info", {})
                )
                if info.get("field") == "brand_model" and info.get("value"):
                    out["brand_model"] = info["value"]

        for widget in sections.get("TAGS", {}).get("widgets", []):
            chips = ((widget.get("data") or {}).get("chip_list") or {}).get("chips") or []
            for chip in chips:
                action = (chip.get("action") or {}).get("payload") or {}
                form = ((action.get("search_data") or {}).get("form_data") or {}).get("data", {})
                district = ((form.get("districts") or {}).get("repeated_string") or {}).get("value") or []
                if district and not out["district_id"]:
                    out["district_id"] = district[0]
                keyword = ((form.get("q") or {}).get("str") or {}).get("value")
                out["tags"].append({"text": chip.get("text"), "keyword": keyword})

        # price: prefer an explicit "قیمت" attribute, else webengage price
        for key, value in out["attributes"].items():
            if key in ("قیمت", "قیمت کل", "اجارهٔ ماهانه", "ودیعه"):
                price, note = parse_price(str(value))
                if price is not None:
                    out["price_toman"] = price
                    out["price_text"] = str(value)
                    out["price_note"] = note
                    break
        else:
            if out["price_toman"]:
                out["price_text"] = human_toman(out["price_toman"])

        if out["title"] and not out["description"]:
            pass
        contact = payload.get("contact") or {}
        if contact:
            out["chat_enabled"] = contact.get("chat_enabled")
        # district name from the location line: "دقایقی پیش در تهران، دهقان"
        if out["location_line"]:
            parts = [p.strip() for p in out["location_line"].split("،")]
            if len(parts) >= 2:
                out["city"] = out.get("city") or parts[0]
                out["district"] = out.get("district") or parts[-1]
        out["price_human"] = human_toman(out["price_toman"])
        return out

    def get_post(self, token_or_url: str) -> dict:
        token = extract_token(token_or_url)
        if not token:
            raise DivarError("no post token given")
        payload = self._request("GET", f"/v8/posts-v2/web/{urllib.parse.quote(token)}")
        return self._parse_detail(payload)

    # -------------------------------------------------------------- meta
    def filters(self, city: str | int | None = None, category: str | None = None) -> dict:
        """The filter widgets Divar currently exposes for a city/category."""
        city_id, city_name = self.resolve_city(city)
        body: dict = {"city_ids": [city_id]}
        if category:
            body["search_data"] = {"form_data": {"data": {"category": {"str": {"value": category}}}}}
        payload = self._request("POST", "/v8/postlist/w/filters", body)
        widgets = ((payload.get("page") or {}).get("widget_list")) or []
        filters = []
        for widget in widgets:
            data = widget.get("data") or {}
            field_info = data.get("field") or {}
            entry = {
                "widget": widget.get("widget_type"),
                "key": field_info.get("key"),
                "value_type": field_info.get("type"),
                "label": data.get("title") or data.get("bottom_sheet_title"),
            }
            if data.get("options"):
                entry["options"] = [
                    {"value": o.get("value"), "display": o.get("display")} for o in data["options"]
                ]
            if field_info.get("key"):
                filters.append(entry)
        return {"city_id": city_id, "city": city_name, "category": category, "filters": filters}

    def cities(self, query: str | None = None) -> list[dict]:
        cities = load_cities()["by_id"]
        items = [{"id": cid, "name": name} for cid, name in cities.items()]
        if query:
            normalised = str(query).replace("‌", "").replace("ي", "ی").replace("ك", "ک")
            items = [
                c for c in items
                if normalised in c["name"].replace("‌", "") or normalised == c["id"]
            ]
        return items

    def categories(self, query: str | None = None) -> list[dict]:
        items = load_categories()
        if query:
            q = str(query).lower().replace("‌", "")
            matched = []
            for item in items:
                haystack = " ".join(
                    [item.get("slug", ""), item.get("name", "")] + list(item.get("parents", []))
                ).lower()
                if q in haystack:
                    matched.append(item)
            items = matched
        return items

    # -------------------------------------------------------------- analytics
    def price_stats(
        self,
        city: str | int | None = None,
        query: str | None = None,
        category: str | None = None,
        pages: int = 2,
        price_min: int | None = None,
        price_max: int | None = None,
        page_size: int = 24,
    ) -> dict:
        """Price distribution for a query/category, from live listings.

        This is the tool a seller uses to answer "what is my item worth?".
        """
        found = self.search_many(
            pages=pages,
            city=city,
            query=query,
            category=category,
            price_min=price_min,
            price_max=price_max,
            page_size=page_size,
        )
        posts = found.get("posts", [])
        priced = [p for p in posts if p.get("price_toman") and p["price_toman"] > 0]
        values = sorted(p["price_toman"] for p in priced)

        def pct(p: float) -> int | None:
            if not values:
                return None
            idx = min(len(values) - 1, max(0, int(round(p * (len(values) - 1)))))
            return values[idx]

        stats = {
            "city": found.get("city"),
            "city_id": found.get("city_id"),
            "query": query,
            "category": category,
            "sampled_posts": len(posts),
            "priced_posts": len(priced),
            "unpriced_posts": len(posts) - len(priced),
            "negotiable_posts": sum(1 for p in posts if p.get("price_note") == "negotiable"),
            "currency": "تومان (Toman)",
            "min": values[0] if values else None,
            "p25": pct(0.25),
            "p40": pct(0.40),
            "median": pct(0.50),
            "p75": pct(0.75),
            "max": values[-1] if values else None,
            "mean": int(sum(values) / len(values)) if values else None,
            "suggested_ask_range": [pct(0.40), pct(0.75)] if values else None,
            "suggested_ask_human": None,
        }
        if stats["suggested_ask_range"][0] if stats["suggested_ask_range"] else None:
            stats["suggested_ask_human"] = (
                f"{human_toman(stats['suggested_ask_range'][0])} - "
                f"{human_toman(stats['suggested_ask_range'][1])}"
            )
        for key in ("min", "p25", "p40", "median", "p75", "max", "mean"):
            stats[f"{key}_human"] = human_toman(stats[key])
        if values:
            buckets: dict[str, int] = {}
            for value in values:
                millions = value / 1_000_000
                if millions < 1:
                    label = "زیر ۱ میلیون"
                elif millions < 5:
                    label = "۱-۵ میلیون"
                elif millions < 20:
                    label = "۵-۲۰ میلیون"
                elif millions < 100:
                    label = "۲۰-۱۰۰ میلیون"
                elif millions < 500:
                    label = "۱۰۰-۵۰۰ میلیون"
                elif millions < 2000:
                    label = "۵۰۰ میلیون - ۲ میلیارد"
                else:
                    label = "بیش از ۲ میلیارد"
                buckets[label] = buckets.get(label, 0) + 1
            stats["price_bands"] = buckets
        ages = [p["age_hours"] for p in posts if p.get("age_hours") is not None]
        stats["freshness"] = {
            "sampled_with_time": len(ages),
            "under_24h": sum(1 for a in ages if a < 24),
            "under_7d": sum(1 for a in ages if a < 24 * 7),
        }
        stats["cheapest"] = _brief(sorted(priced, key=lambda p: p["price_toman"])[:5])
        stats["priciest"] = _brief(sorted(priced, key=lambda p: p["price_toman"], reverse=True)[:5])
        return stats

    def similar_posts(self, token_or_url: str, city: str | int | None = None, limit: int = 12) -> dict:
        """Comparable listings for a post: same category + brand/model + city."""
        post = self.get_post(token_or_url)
        found = self.search_many(
            pages=2,
            city=city if city is not None else post.get("city_id") or post.get("city"),
            category=post.get("category"),
            brand_model=post.get("brand_model"),
            page_size=max(24, min(60, limit * 2)),
        )
        posts = [p for p in found.get("posts", []) if p["token"] != post["token"]]
        return {
            "source_post": {
                "token": post.get("token"),
                "title": post.get("title"),
                "price_toman": post.get("price_toman"),
                "price_human": post.get("price_human"),
                "category": post.get("category"),
                "city": post.get("city"),
            },
            "count": len(posts[:limit]),
            "posts": posts[:limit],
        }


def _brief(posts: list[dict]) -> list[dict]:
    return [
        {
            "token": p["token"],
            "title": p["title"],
            "price_toman": p["price_toman"],
            "price_human": p["price_human"],
            "district": p.get("district"),
            "time_text": p.get("time_text"),
            "url": p.get("url"),
        }
        for p in posts
    ]


def _split_relative(text: str | None) -> tuple[str | None, str | None]:
    """'دقایقی پیش در تهران، دهقان' -> ('دقایقی پیش', 'تهران، دهقان')"""
    if not text:
        return None, None
    marker = " در "
    if marker in text:
        head, _, tail = text.partition(marker)
        return head.strip(), tail.strip()
    return text.strip(), None
