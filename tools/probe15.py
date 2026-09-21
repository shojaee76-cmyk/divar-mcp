import json, urllib.request, urllib.error, urllib.parse, time, collections

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    for k, v in [("user-agent", UA), ("content-type", "application/json"), ("accept", "application/json"),
                 ("origin", "https://divar.ir"), ("referer", "https://divar.ir/")]:
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)

def d_of(raw, dflt=None):
    try:
        return json.loads(raw)
    except Exception:
        return dflt

print("### 1. numeric district id filter (jobs, id 208 = بلوار کشاورز)")
body = {"city_ids": ["1"], "search_data": {"form_data": {"data": {"category": {"str": {"value": "jobs"}},
        "districts": {"repeated_string": {"value": ["208"]}}}}},
        "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 24}}
st, raw = post("https://api.divar.ir/v8/postlist/w/search", body)
if st == 200:
    d = d_of(raw)
    rs = [w["data"] for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
    c = collections.Counter(((r.get("action") or {}).get("payload") or {}).get("web_info", {}).get("district_persian") for r in rs)
    print("  ", st, "n=", len(rs), dict(c))
else:
    print("  ", st, raw[:250])

print("### 2. district id filter with a WRONG id (99999) -> proves filtering")
body["search_data"]["form_data"]["data"]["districts"] = {"repeated_string": {"value": ["99999"]}}
st, raw = post("https://api.divar.ir/v8/postlist/w/search", body)
print("  ", st, (raw[:160] if st != 200 else "n=" + str(len([w for w in d_of(raw)['list_widgets'] if w['widget_type'] == 'POST_ROW']))))

print("### 3. lazy district search shapes on /filters")
lazy_core = {"@type": "type.googleapis.com/post_list.LazyFilterPayload", "filter_name": "districts",
             "version": "82", "place_ids": ["1"], "category": "ROOT"}
variants = [
    ("lazy_filter+query", {"city_ids": ["1"], "lazy_filter": {**lazy_core, "query": "کشاورز"}}),
    ("lazy_payload+query", {"city_ids": ["1"], "lazy_payload": {**lazy_core, "query": "کشاورز"}}),
    ("filter+query", {"city_ids": ["1"], "filter": {**lazy_core, "query": "کشاورز"}}),
    ("lazy_filter no query", {"city_ids": ["1"], "lazy_filter": lazy_core}),
    ("search_data+filter_name", {"city_ids": ["1"], "search_data": {"form_data": {}, "filter": {**lazy_core, "query": "کشاورز"}}}),
]
for label, b in variants:
    st, raw = post("https://api.divar.ir/v8/postlist/w/filters", b)
    d = d_of(raw)
    wt = [w.get("widget_type") for w in d["page"]["widget_list"]] if d and "page" in d else None
    print(f"  {label}: {st} len={len(raw)} widgets={wt if wt else raw[:110]!r}")
    time.sleep(0.6)

print("### 4. quick check: does /filters accept a 'query' for districts elsewhere")
for path in ("/v8/postlist/w/filters/query", "/v8/postlist/w/filters/districts", "/v8/place/search", "/v5/place/search"):
    req = urllib.request.Request("https://api.divar.ir" + path + "?q=" + urllib.parse.quote("کشاورز"), method="GET")
    for k, v in [("user-agent", UA), ("accept", "application/json"), ("origin", "https://divar.ir")]:
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print("  ", path, r.status, r.read()[:120])
    except urllib.error.HTTPError as e:
        print("  ", path, e.code, e.read()[:80])
    except Exception as e:
        print("  ", path, "ERR", str(e)[:60])
