import json, urllib.request, urllib.error, sys, time, collections

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def call(url, body):
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

def s(data, **top):
    body = {"city_ids": ["1"], "search_data": {"form_data": {"data": data}},
            "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 24}}
    body.update(top)
    st, raw = call("https://api.divar.ir/v8/postlist/w/search", body)
    try:
        d = json.loads(raw)
    except Exception:
        return st, raw[:150], []
    rows = [w["data"] for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
    head = d["list_top_widgets"][0]["data"].get("text") if d.get("list_top_widgets") else ""
    return st, head, rows

print("### has-photo shapes")
for label, val in [("boolean {}", {"has-photo": {"boolean": {}}}),
                   ('boolean "true"', {"has-photo": {"boolean": "true"}}),
                   ("bool {}", {"has-photo": {"bool": {}}})]:
    st, head, rows = s({**val, "category": {"str": {"value": "clothing"}}})
    imgs = collections.Counter(bool(r.get("image_url")) for r in rows)
    print(f"  {label}: {st} rows={len(rows)} images={dict(imgs)} {head[:80] if st!=200 else ''}")
    time.sleep(0.8)

print("### recent_ads really filters? (bottom_description_text)")
for v in ("3h", "7d", None):
    data = {"recent_ads": {"str": {"value": v}}} if v else {}
    st, head, rows = s({**data, "category": {"str": {"value": "clothing"}}})
    bot = [r.get("bottom_description_text") for r in rows[:6]]
    print(f"  recent_ads={v}: {st} rows={len(rows)} bottoms={bot}")
    time.sleep(0.8)

print("### district filter shape")
for label, val in [("repeated_string list", {"districts": {"repeated_string": {"value": ["bolvar-e-keshavarz"]}}}),
                   ("repeated_string str", {"districts": {"repeated_string": {"value": "bolvar-e-keshavarz"}}}),
                   ("str list", {"districts": {"str": {"value": ["bolvar-e-keshavarz"]}}})]:
    st, head, rows = s({**val, "category": {"str": {"value": "jobs"}}})
    dists = [((r.get("action") or {}).get("payload") or {}).get("web_info", {}).get("district_persian") for r in rows[:4]]
    print(f"  {label}: {st} rows={len(rows)} dists={dists} {head[:70] if st!=200 else ''}")
    time.sleep(0.8)

print("### query + category together")
st, head, rows = s({"category": {"str": {"value": "mobile-phones"}}, "query": {"str": {"value": "iphone"}}})
print("  form_data query:", st, head[:90])
st, head, rows = s({"category": {"str": {"value": "mobile-phones"}}}, search_data_override=None) if False else (None, None, None)
body = {"city_ids": ["1"], "search_data": {"query": "iphone", "form_data": {"data": {"category": {"str": {"value": "mobile-phones"}}}}},
        "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 24}}
st, raw = call("https://api.divar.ir/v8/postlist/w/search", body)
d = json.loads(raw) if st == 200 else {}
rows = [w["data"] for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"] if st == 200 else []
print("  top-level query:", st, "rows=", len(rows), [r.get("title") for r in rows[:4]])
