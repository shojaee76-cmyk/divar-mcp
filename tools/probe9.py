import json, urllib.request, urllib.error, re, sys, time, collections

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in [("user-agent", UA), ("accept", "application/json, text/plain, */*"),
                 ("origin", "https://divar.ir"), ("referer", "https://divar.ir/")]:
        req.add_header(k, v)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"

PG = {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 24}

def wtypes(raw):
    try:
        d = json.loads(raw)
    except Exception:
        return None, []
    ws = d.get("page", {}).get("widget_list") or d.get("list_widgets") or []
    return d, [w.get("widget_type") for w in ws]

print("### A. category discovery matrix")
matrix = [
    ("filters ROOT cat", "POST", "https://api.divar.ir/v8/postlist/w/filters",
     {"city_ids": ["1"], "search_data": {"form_data": {"data": {"category": {"str": {"value": "ROOT"}}}}}}),
    ("filters tab=categories", "POST", "https://api.divar.ir/v8/postlist/w/filters",
     {"city_ids": ["1"], "current_tab": "categories"}),
    ("search tab=categories", "POST", "https://api.divar.ir/v8/postlist/w/search",
     {"city_ids": ["1"], "current_tab": "categories", "search_data": {"form_data": {"data": {"category": {"str": {"value": "ROOT"}}}}}, "pagination_data": PG}),
    ("filters url+cat", "POST", "https://api.divar.ir/v8/postlist/w/filters?category=services", {"city_ids": ["1"]}),
    ("v8/postlist/w/categories POST", "POST", "https://api.divar.ir/v8/postlist/w/categories", {"city_ids": ["1"]}),
    ("v8/category/tree", "GET", "https://api.divar.ir/v8/category/tree", None),
    ("v8/postlist/categories", "POST", "https://api.divar.ir/v8/postlist/categories", {"city_ids": ["1"]}),
    ("v5/postlist/w/categories", "POST", "https://api.divar.ir/v5/postlist/w/categories", {"city_ids": ["1"]}),
]
for name, m, u, b in matrix:
    st, raw = call(m, u, b)
    d, wt = wtypes(raw)
    print(f"  {name}: {st} len={len(raw)} widgets={wt[:14] if wt else raw[:90]!r}")
    time.sleep(0.5)

print("\n### B. harvest category slugs from seo linked_data (top cities)")
cities = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12", "13", "14", "15"]
cat_count = collections.Counter()
cat_pers = {}
city_name = {}
for c in cities:
    st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/search",
                   {"city_ids": [c], "pagination_data": PG})
    if st != 200:
        print("  city", c, st); continue
    d = json.loads(raw)
    ld = (d.get("seo_details") or {}).get("linked_data") or []
    bc = (d.get("seo_details") or {}).get("bread_crumb") or []
    if bc:
        city_name[c] = bc[0].get("name")
    for item in ld:
        cs = item.get("category")
        if cs:
            cat_count[cs] += 1
            cat_pers.setdefault(cs, item.get("category_slug_persian") or item.get("link") or "")
    print(f"  city {c} ({city_name.get(c)}): linked={len(ld)} distinct_cats_so_far={len(cat_count)}")
    time.sleep(0.8)

print("\nCITIES FOUND:", json.dumps(city_name, ensure_ascii=False))
print("\nDISTINCT SLUGS:", len(cat_count))
for s, n in cat_count.most_common():
    print(f"  {s}  x{n}  {cat_pers.get(s,'')}")
json.dump({"counts": cat_count, "cities": city_name}, open("tools/slug_harvest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
