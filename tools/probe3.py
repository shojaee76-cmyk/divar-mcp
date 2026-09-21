import json, urllib.request, urllib.error, sys, collections

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("user-agent", UA)
    req.add_header("accept", "application/json, text/plain, */*")
    req.add_header("origin", "https://divar.ir")
    req.add_header("referer", "https://divar.ir/")
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"

print("### 1. post detail")
st, raw = call("GET", "https://api.divar.ir/v8/posts-v2/web/gaxWZCEz")
print("detail:", st, len(raw))
if st == 200:
    open("tools/detail_raw.json", "w", encoding="utf-8").write(raw)
    d = json.loads(raw)
    print("detail top keys:", list(d.keys()))

print("### 2. categories in filters payload")
f = json.load(open("tools/filters_tehran.json", encoding="utf-8"))
for w in f["page"]["widget_list"]:
    wt = w.get("widget_type"); dd = w.get("data", {})
    key = dd.get("field", {}).get("key")
    print(" -", wt, "field=", key, "opts=", len(dd.get("options", []) or []))
    if key in ("category",):
        for o in (dd.get("options") or [])[:40]:
            print("      ", o.get("value"), "|", o.get("label"))

print("### 3. text search variants")
variants = [
    ("form_data q", {"city_ids": ["1"], "search_data": {"form_data": {"data": {"q": {"str": {"value": "پژو"}}}}},
                     "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 3}}),
    ("query key", {"city_ids": ["1"], "search_data": {"query": "پژو"},
                   "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 3}}),
    ("category buy-residential", {"city_ids": ["1"],
                                  "search_data": {"form_data": {"data": {"category": {"str": {"value": "buy-residential"}}}}},
                                  "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 3}}),
]
for name, body in variants:
    st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/search", body)
    n = 0
    ttl = ""
    if st == 200:
        try:
            d = json.loads(raw)
            rows = [w for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
            n = len(rows)
            ttl = rows[0]["data"]["title"] if rows else ""
            top = d.get("list_top_widgets", [])
            head = top[0]["data"].get("text") if top else ""
        except Exception as e:
            head = f"parse {e}"
    print(f" - {name}: {st} rows={n} head={head!r} first={ttl!r}")
    sys.stdout.flush()

print("### 4. place suggestion")
for u in ["https://api.divar.ir/v5/place/suggestion?q=tehran",
          "https://api.divar.ir/v8/place/suggestion?q=tehran"]:
    st, raw = call("GET", u)
    print(" -", u, st, len(raw), raw[:200].replace("\n", " "))
