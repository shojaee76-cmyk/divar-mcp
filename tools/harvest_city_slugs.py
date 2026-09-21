"""Harvest divar.ir city page slugs (the /s/<slug> path segment) with validation.

A slug is only trustworthy when it comes from Divar's own payload AND belongs to
the city we asked for: we search a city for a few tokens, then read
GET /v8/posts-v2/web/{token} and accept city.second_slug only if
city.city_id equals the id we queried. Pseudo-ids ("کل ایران") and posts that
leak in from elsewhere are rejected instead of producing a wrong URL.

divar.ir serves the same SPA shell for /s/<anything>, so HTTP status cannot be
used to check a slug, Divar's own payload is the only source of truth.

Run: python tools/harvest_city_slugs.py [--limit N] [--max-id N]
Output: tools/city_slugs_raw.json   {"1": "tehran", ...}
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
OUT = os.path.join("tools", "city_slugs_raw.json")
CITIES = os.path.join("src", "divar_mcp", "data", "cities.json")
TOKENS_PER_CITY = 4


def req(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    for k, v in [("user-agent", UA), ("accept", "application/json, text/plain, */*"),
                 ("origin", "https://divar.ir"), ("referer", "https://divar.ir/")]:
        r.add_header(k, v)
    if data:
        r.add_header("content-type", "application/json")
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def slug_for(city_id: str) -> str | None:
    search = {
        "city_ids": [city_id],
        "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData",
                            "page": 1, "page_size": TOKENS_PER_CITY},
    }
    try:
        payload = req("POST", "https://api.divar.ir/v8/postlist/w/search", search)
    except Exception:
        return None
    tokens = [w["data"]["token"] for w in payload.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
    for token in tokens:
        time.sleep(1.5)
        try:
            detail = req("GET", f"https://api.divar.ir/v8/posts-v2/web/{token}")
        except Exception:
            continue
        city = detail.get("city") or {}
        if str(city.get("city_id")) == str(city_id) and city.get("second_slug"):
            return city["second_slug"]
    return None


def main() -> int:
    limit = None
    max_id = None
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if "--max-id" in sys.argv:
        max_id = int(sys.argv[sys.argv.index("--max-id") + 1])

    cities = json.load(open(CITIES, encoding="utf-8"))
    known = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    ids = [cid for cid in cities if cid not in known]
    if max_id:
        ids = [cid for cid in ids if int(cid) <= max_id]
    if limit:
        ids = ids[:limit]
    print(f"{len(ids)} cities to resolve ({len(known)} already stored)", flush=True)

    for cid in ids:
        try:
            slug = slug_for(cid)
        except Exception as exc:
            slug = None
            print(f"  {cid} error {exc}", flush=True)
        if slug:
            known[cid] = slug
            print(f"{cid} {cities[cid]} -> {slug}", flush=True)
        else:
            print(f"{cid} {cities[cid]} -> UNVERIFIED (no matching post)", flush=True)
        with open(OUT, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(known, fh, ensure_ascii=False, indent=1)
            fh.write("\n")
        time.sleep(1.6)
    print("TOTAL verified slugs:", len(known), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
