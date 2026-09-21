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

from . import analytics
from .datasets import (  # re-exported for backwards compatibility
    DATA_DIR,
    find_category,
    load_categories,
    load_cities,
    load_city_slugs,
)
from .normalize import (
    extract_token,
    human_toman,
    parse_age_hours,
    parse_int,
    parse_jalali_datetime,
    parse_price,
)
from .resolve import resolve_category_input, resolve_city_input, suggest_categories, suggest_cities
from .store import Store, get_store

API_BASE = "https://api.divar.ir"
WEB_BASE = "https://divar.ir"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

PAGINATION_TYPE = "type.googleapis.com/post_list.PaginationData"


class DivarError(RuntimeError):
    """Any Divar API or input failure, with machine-readable repair hints.

    ``suggestions`` and ``hint`` exist so an agent can fix its own call instead
    of giving up: a wrong category comes back with the closest real slugs, a
    wrong city with the closest real city names.
    """

    def __init__(
        self,
        message: str,
        status: int | None = None,
        code: int | None = None,
        *,
        suggestions: list | None = None,
        hint: str | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.suggestions = suggestions or []
        self.hint = hint
        self.retryable = retryable

    def __str__(self) -> str:  # pragma: no cover - trivial
        bits = [self.message]
        if self.status:
            bits.append(f"(HTTP {self.status})")
        if self.hint:
            bits.append(f"- {self.hint}")
        return " ".join(bits)

    def as_dict(self) -> dict:
        return {
            "error": self.message,
            "status": self.status,
            "code": self.code,
            "hint": self.hint,
            "suggestions": self.suggestions,
            "retryable": self.retryable,
        }



# ------------------------------------------------------------------ data files

# The reference-data loaders now live in datasets.py (imported and re-exported
# at the top of this module for backwards compatibility with older callers).


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
        """Return (city_id, persian_name); forgiving, with suggestions on failure."""
        resolved = resolve_city_input(city)
        if resolved:
            return resolved
        raise DivarError(
            f"unknown city {city!r}",
            suggestions=suggest_cities(str(city), limit=5),
            hint="Pass a city id or a Persian name; divar_list_cities lists all of them.",
        )

    def resolve_cities(self, cities) -> tuple[list[str], list[str]]:
        """Normalize one city, a name, or a list of them into (ids, names)."""
        if isinstance(cities, (str, int)) or cities is None:
            cid, name = self.resolve_city(cities)
            return [cid], [name]
        ids, names = [], []
        for entry in cities:
            cid, name = self.resolve_city(entry)
            if cid not in ids:
                ids.append(cid)
                names.append(name)
        if not ids:
            cid, name = self.resolve_city(None)
            return [cid], [name]
        return ids, names

    def resolve_category(self, category: str | None) -> str | None:
        """Validated slug (or None for 'everything'), with did-you-mean on failure."""
        if category is None or str(category).strip() == "":
            return None
        slug, suggestions = resolve_category_input(category)
        if slug:
            return slug
        if suggestions:
            raise DivarError(
                f"unknown category {category!r}",
                suggestions=[
                    {"slug": s.get("slug"), "name": s.get("name"), "parents": s.get("parents")}
                    for s in suggestions
                ],
                hint="Use one of the suggested slugs from divar_list_categories.",
            )
        raise DivarError(
            f"unknown category {category!r}",
            suggestions=[],
            hint="Run divar_list_categories (Persian or English text) to find the slug.",
        )

    # -------------------------------------------------------------- search
    @staticmethod
    def _build_search_body(
        city_id: str | list[str],
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
        # Divar's own payloads use {"category": {"str": {"value": "ROOT"}}} for
        # "every category", but the API rejects ROOT as a filter value. Treat it
        # as "no category filter" so an agent copying Divar's vocabulary works.
        if category and category.strip().upper() != "ROOT":
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

        body: dict = {"city_ids": city_id if isinstance(city_id, list) else [city_id]}
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

    def _walk_pages(
        self,
        *,
        city_ids: list[str],
        query: str | None = None,
        category: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        district_ids: list[str] | None = None,
        has_photo: bool = False,
        brand_model: str | None = None,
        pages: int = 1,
        page_size: int = 24,
    ):
        """Yield one dict per cursor page (Divar ignores ``page``; the cursor rules)."""
        cursor = None
        for current in range(1, max(1, int(pages)) + 1):
            body = self._build_search_body(
                city_ids, query, category, price_min, price_max,
                district_ids, has_photo, brand_model, current, page_size, cursor,
            )
            payload = self._request("POST", "/v8/postlist/w/search", body)
            rows = [
                self._parse_row(w["data"])
                for w in payload.get("list_widgets", [])
                if w.get("widget_type") == "POST_ROW"
            ]
            pagination = payload.get("pagination") or {}
            top = payload.get("list_top_widgets") or []
            yield {
                "page": current,
                "rows": rows,
                "headline": (top[0].get("data") or {}).get("text", "") if top else "",
                "has_next_page": bool(pagination.get("has_next_page")),
                "cursor": pagination.get("data"),
                "search_id": payload.get("search_id"),
                "elapsed": getattr(self, "_last_elapsed", None),
            }
            if not pagination.get("has_next_page"):
                return
            cursor_next = pagination.get("data")
            if not cursor_next:
                return
            cursor = dict(cursor_next)
            for key in ("@type", "search_uid", "viewed_tokens"):
                cursor.pop(key, None)

    def _resolve_filters(self, city, cities, category):
        if cities and city is None:
            ids, names = self.resolve_cities(cities)
        elif cities:
            ids, names = self.resolve_cities(cities)
            one_id, one_name = self.resolve_city(city)
            if one_id not in ids:
                ids.insert(0, one_id)
                names.insert(0, one_name)
        else:
            ids, names = self.resolve_cities(city)
        return ids, names, self.resolve_category(category)

    def _record(self, posts: list[dict], *, city: str | None, city_id: str | None,
                category: str | None, query: str | None, store: Store | None) -> int:
        """Best-effort history recording; never let it break a search."""
        try:
            target = store or get_store()
            return target.record_posts(posts, city=city, city_id=city_id,
                                       category=category, query=query)
        except Exception:
            return 0

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
        cities: list | None = None,
        record: bool = True,
        store: Store | None = None,
    ) -> dict:
        """One page of listings. ``page>1`` follows Divar's cursor internally."""
        started = time.monotonic()
        ids, names, slug = self._resolve_filters(city, cities, category)
        result: dict = {}
        for page_data in self._walk_pages(
            city_ids=ids, query=query, category=slug, price_min=price_min, price_max=price_max,
            district_ids=district_ids, has_photo=has_photo, brand_model=brand_model,
            pages=max(1, int(page)), page_size=page_size,
        ):
            result = {
                "city_id": ids[0],
                "city_id_list": ids,
                "city": names[0],
                "cities": names,
                "query": query,
                "category": slug,
                "page": page_data["page"],
                "page_size": page_size,
                "count": len(page_data["rows"]),
                "has_next_page": page_data["has_next_page"],
                "headline": page_data["headline"],
                "posts": page_data["rows"],
                "cursor": page_data["cursor"],
                "search_id": page_data["search_id"],
            }
        if sort:
            result["posts"] = self._sort_posts(result.get("posts", []), sort)
        result["sort"] = sort
        if record:
            result["recorded"] = self._record(
                result.get("posts", []), city=names[0], city_id=ids[0],
                category=slug, query=query, store=store,
            )
        result["meta"] = self.meta_stats(started)
        return result

    def meta_stats(self, started: float | None = None) -> dict:
        meta = {
            "requests_made": self.request_count,
            "cached_responses": len(self._cache),
            "rate_limit_seconds": self.min_interval,
            "cache_ttl_seconds": self.cache_ttl,
        }
        if started is not None:
            meta["elapsed_seconds"] = round(time.monotonic() - started, 2)
        return meta

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

    def search_many(
        self,
        pages: int = 1,
        record: bool = True,
        store: Store | None = None,
        **kwargs,
    ) -> dict:
        """Search several cursor-pages, de-duplicated, in one call."""
        started = time.monotonic()
        pages = max(1, int(pages))
        ids, names, slug = self._resolve_filters(
            kwargs.get("city"), kwargs.get("cities"), kwargs.get("category")
        )
        collected: list[dict] = []
        seen: set[str] = set()
        headline = ""
        search_id = None
        page_size = kwargs.get("page_size", 24)
        fetched = 0
        for page_data in self._walk_pages(
            city_ids=ids,
            query=kwargs.get("query"),
            category=slug,
            price_min=kwargs.get("price_min"),
            price_max=kwargs.get("price_max"),
            district_ids=kwargs.get("district_ids"),
            has_photo=kwargs.get("has_photo", False),
            brand_model=kwargs.get("brand_model"),
            pages=pages,
            page_size=page_size,
        ):
            fetched += 1
            if fetched == 1:
                headline = page_data["headline"]
                search_id = page_data["search_id"]
            for row in page_data["rows"]:
                if row["token"] and row["token"] not in seen:
                    seen.add(row["token"])
                    collected.append(row)

        result = {
            "city_id": ids[0],
            "city_id_list": ids,
            "city": names[0],
            "cities": names,
            "pages_fetched": fetched,
            "count": len(collected),
            "posts": collected,
            "query": kwargs.get("query"),
            "category": slug,
            "headline": headline,
            "search_id": search_id,
        }
        sort = kwargs.get("sort")
        if sort:
            result["posts"] = self._sort_posts(collected, sort)
            result["sort"] = sort
        if record:
            result["recorded"] = self._record(
                collected, city=names[0], city_id=ids[0],
                category=slug, query=kwargs.get("query"), store=store,
            )
        result["meta"] = self.meta_stats(started)
        return result

    @staticmethod
    def brief(post: dict) -> dict:
        """Compact projection for agent context: the fields that matter, little else."""
        return {
            k: post[k]
            for k in ("token", "title", "price_toman", "price_human", "district", "city",
                      "time_text", "age_hours", "image_count", "url")
            if k in post and post[k] is not None
        }

    def brief_posts(self, posts: list[dict]) -> list[dict]:
        return [self.brief(p) for p in posts]

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
        body: dict = {"city_ids": city_id if isinstance(city_id, list) else [city_id]}
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
        cities: list | None = None,
        districts: bool = True,
        has_photo: bool = False,
    ) -> dict:
        """Price distribution for a query/category, from live listings.

        This is the tool a seller uses to answer "what is my item worth?".
        """
        found = self.search_many(
            pages=pages,
            city=city,
            cities=cities,
            query=query,
            category=category,
            price_min=price_min,
            price_max=price_max,
            page_size=page_size,
            has_photo=bool(has_photo),
        )
        posts = found.get("posts", [])
        stats: dict = {
            "city": found.get("city"),
            "city_id": found.get("city_id"),
            "cities": found.get("cities"),
            "query": query,
            "category": found.get("category"),
        }
        stats.update(analytics.price_summary(posts))
        stats["freshness"] = analytics.freshness(posts)
        priced = analytics.priced(posts)
        stats["cheapest"] = _brief(sorted(priced, key=lambda p: p["price_toman"])[:5])
        stats["priciest"] = _brief(sorted(priced, key=lambda p: p["price_toman"], reverse=True)[:5])
        if districts:
            stats["by_district"] = analytics.district_breakdown(posts)[:12]
        stats["recorded"] = found.get("recorded")
        stats["meta"] = found.get("meta")
        return stats

    # ------------------------------------------------------- advanced features
    def find_deals(
        self,
        city: str | int | None = None,
        query: str | None = None,
        category: str | None = None,
        pages: int = 3,
        price_min: int | None = None,
        price_max: int | None = None,
        min_discount: float = 0.05,
        require_photo: bool = False,
        has_photo: bool = False,
        limit: int = 10,
        cities: list | None = None,
    ) -> dict:
        """Listings priced below the live market for the same query."""
        found = self.search_many(
            pages=pages, city=city, cities=cities, query=query, category=category,
            price_min=price_min, price_max=price_max, has_photo=bool(has_photo),
        )
        posts = found.get("posts", [])
        report = analytics.rank_deals(
            posts, limit=limit, min_discount=min_discount, require_photo=require_photo
        )
        report.update(
            {
                "city": found.get("city"),
                "city_id": found.get("city_id"),
                "cities": found.get("cities"),
                "query": query,
                "category": found.get("category"),
                "pages_sampled": found.get("pages_fetched"),
                "meta": found.get("meta"),
            }
        )
        report["deals"] = [
            dict(self.brief(deal), deal_score=deal["deal_score"], reasons=deal["reasons"],
                 price_vs_median_pct=deal["factors"].get("price_vs_median_pct"))
            for deal in report["deals"]
        ]
        report["suspicious"] = [
            dict(self.brief(row), insight=row["insight"],
                 price_vs_median_pct=row["price_vs_median_pct"])
            for row in report.get("suspicious", [])
        ]
        return report

    def appraise_post(
        self,
        token_or_url: str,
        city: str | int | None = None,
        pages: int = 2,
    ) -> dict:
        """Judge one listing's asking price against live comparables."""
        post = self.get_post(token_or_url)
        comps = self.search_many(
            pages=pages,
            city=city if city is not None else post.get("city_id") or post.get("city"),
            category=post.get("category"),
            brand_model=post.get("brand_model"),
            page_size=60,
        )
        others = comps.get("posts", [])
        others_for_math = others
        if post.get("brand_model") and others:
            head = post["brand_model"].split()[0].lower()
            same_model = [p for p in others if head in (p.get("title") or "").lower()]
            if len(same_model) >= 5:
                others_for_math = same_model
        report = analytics.appraise(
            post.get("price_toman"), others_for_math, brand_model=post.get("brand_model")
        )
        report.update(
            {
                "token": post.get("token"),
                "title": post.get("title"),
                "url": post.get("url"),
                "city": post.get("city"),
                "district": post.get("district"),
                "category": post.get("category"),
                "attributes": post.get("attributes"),
                "price_context": {
                    "asking": post.get("price_toman"),
                    "asking_human": post.get("price_human"),
                    "price_note": post.get("price_note"),
                    "posted_at": post.get("posted_at"),
                    "age_text": post.get("relative_time"),
                },
                "meta": comps.get("meta"),
            }
        )
        return report

    def market_breakdown(
        self,
        city: str | int | None = None,
        query: str | None = None,
        category: str | None = None,
        pages: int = 2,
        min_listings: int = 1,
        has_photo: bool = False,
        cities: list | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
    ) -> dict:
        """Where the stock sits: price by district, bands and freshness for one query."""
        found = self.search_many(
            pages=pages, city=city, cities=cities, query=query, category=category,
            page_size=60, has_photo=bool(has_photo), price_min=price_min, price_max=price_max,
        )
        posts = found.get("posts", [])
        return {
            "city": found.get("city"),
            "city_id": found.get("city_id"),
            "query": query,
            "category": found.get("category"),
            "sampled_posts": len(posts),
            "summary": analytics.price_summary(posts),
            "by_district": analytics.district_breakdown(posts, min_listings=min_listings),
            "freshness": analytics.freshness(posts),
            "meta": found.get("meta"),
        }

    def price_trend(
        self,
        city: str | int | None = None,
        query: str | None = None,
        category: str | None = None,
        days: int = 30,
        store: Store | None = None,
    ) -> dict:
        """Local price history for this exact filter (every search adds a data point)."""
        target = store or get_store()
        cid, cname = self.resolve_city(city)
        slug = self.resolve_category(category)
        series = target.price_history(city=cname, category=slug, query=query, days=days)
        report = analytics.trend_report(series)
        report.update({"city": cname, "city_id": cid, "query": query, "category": slug})
        return report

    def export_rows(
        self,
        city: str | int | None = None,
        query: str | None = None,
        category: str | None = None,
        pages: int = 3,
        price_min: int | None = None,
        price_max: int | None = None,
        path: str | None = None,
        fmt: str = "csv",
        full: bool = False,
        has_photo: bool = False,
        cities: list | None = None,
    ) -> dict:
        """Dump listings to a CSV/JSONL file on disk and return the path."""
        import csv as _csv

        found = self.search_many(
            pages=max(1, min(int(pages), 10)), city=city, cities=cities, query=query,
            category=category, price_min=price_min, price_max=price_max,
            has_photo=bool(has_photo),
        )
        posts = found.get("posts", [])
        fmt = "jsonl" if str(fmt).lower() in ("jsonl", "json") else "csv"
        out_path = Path(path) if path else Path.cwd() / f"divar-{int(time.time())}.{fmt}"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        columns = ["token", "title", "price_toman", "price_human", "price_note", "city",
                   "district", "time_text", "age_hours", "image_count", "url"]
        if fmt == "jsonl":
            with out_path.open("w", encoding="utf-8", newline="\n") as fh:
                for post in posts:
                    fh.write(json.dumps(post, ensure_ascii=False) + "\n")
        else:
            if posts and full:
                columns = list(posts[0].keys())
            with out_path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = _csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
                writer.writeheader()
                for post in posts:
                    writer.writerow({k: post.get(k) for k in columns})
        return {
            "path": str(out_path.resolve()),
            "format": fmt,
            "rows": len(posts),
            "columns": columns,
            "city": found.get("city"),
            "query": query,
            "category": found.get("category"),
            "note": "utf-8-sig CSV opens correctly in Excel for Persian text.",
            "meta": found.get("meta"),
        }

    def watch_create(self, name: str, params: dict, store: Store | None = None,
                     pages: int = 1) -> dict:
        """Save a search as a watch and remember today's results as the baseline."""
        target = store or get_store()
        params = {k: v for k, v in params.items() if k not in ("pages",)}
        found = self.search_many(pages=max(1, min(int(pages or 1), 3)), **params)
        tokens = [p["token"] for p in found.get("posts", [])]
        saved = target.save_watch(name, params)
        if "error" in saved:
            raise DivarError(f"could not save watch: {saved['error']}", hint="Check the store path.")
        target.diff_watch(name, tokens, update=True)  # seed the baseline silently
        return {
            "name": name,
            "params": params,
            "baseline_listings": len(tokens),
            "city": found.get("city"),
            "note": "The current listings are now the baseline; divar_watch_check reports only newer ones.",
        }

    def watch_list(self, store: Store | None = None) -> dict:
        target = store or get_store()
        watches = target.list_watches()
        return {"count": len(watches), "watches": watches, "store": target.path}

    def watch_check(self, name: str, pages: int = 1, store: Store | None = None) -> dict:
        """Run a saved search and report listings never seen for it before."""
        target = store or get_store()
        watch = target.get_watch(name)
        if not watch:
            known = [w["name"] for w in target.list_watches()]
            raise DivarError(
                f"no watch named {name!r}",
                suggestions=known,
                hint="divar_watch_list shows saved watches; divar_watch_create adds one.",
            )
        params = dict(watch["params"])
        params.pop("pages", None)
        found = self.search_many(pages=pages, **params)
        posts = found.get("posts", [])
        tokens = [p["token"] for p in posts]
        fresh = target.diff_watch(name, tokens, update=True)
        fresh_set = set(fresh)
        new_posts = [self.brief(p) for p in posts if p["token"] in fresh_set]
        return {
            "watch": name,
            "params": params,
            "checked_at_listings": len(posts),
            "new_listings": len(new_posts),
            "new": new_posts,
            "city": found.get("city"),
            "total_new_since_created": (target.get_watch(name) or {}).get("new_total"),
            "meta": found.get("meta"),
        }

    def watch_delete(self, name: str, store: Store | None = None) -> dict:
        target = store or get_store()
        removed = target.delete_watch(name)
        return {"watch": name, "deleted": removed}

    def health(self, probe: bool = True) -> dict:
        """Self-diagnosis: datasets, store, and whether divar.ir answers right now."""
        started = time.monotonic()
        report: dict = {
            "api_base": self.api_base,
            "datasets": {
                "cities": len(load_cities()["by_id"]),
                "categories": len(load_categories()),
                "city_slugs": len(load_city_slugs()),
            },
            "store": get_store().stats().as_dict(),
        }
        if probe:
            try:
                payload = self._request(
                    "POST", "/v8/postlist/w/search",
                    {"city_ids": ["1"],
                     "pagination_data": {"@type": PAGINATION_TYPE, "page": 1, "page_size": 1}},
                )
                report["api"] = {
                    "reachable": True,
                    "latency_seconds": round(time.monotonic() - started, 2),
                    "sample_rows": sum(1 for w in payload.get("list_widgets", [])
                                       if w.get("widget_type") == "POST_ROW"),
                }
            except DivarError as exc:
                report["api"] = {
                    "reachable": False,
                    "status": exc.status,
                    "error": exc.message[:200],
                    "retryable": True,
                    "hint": (
                        "divar.ir did not answer: from Iran that usually means the line or VPN is down "
                        "rather than a bad request, so retry shortly."
                    ),
                }
        report["meta"] = self.meta_stats(started)
        return report

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
