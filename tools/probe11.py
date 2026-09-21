import json, urllib.request, urllib.error, sys, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def search(extra_data, **top):
    body = {"city_ids": ["1"], "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 24}}
    sd = {"form_data": {"data": extra_data}}
    body["search_data"] = sd
    body.update(top)
    req = urllib.request.Request("https://api.divar.ir/v8/postlist/w/search", data=json.dumps(body).encode(), method="POST")
    for k, v in [("user-agent", UA), ("content-type", "application/json"), ("accept", "application/json"),
                 ("origin", "https://divar.ir"), ("referer", "https://divar.ir/")]:
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
            st = 200
    except urllib.error.HTTPError as e:
        st, raw = e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}", []
    try:
        d = json.loads(raw)
    except Exception:
        return st, raw[:160], []
    rows = [(w["data"]["token"], w["data"]["title"], w["data"].get("middle_description_text"))
            for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
    head = d["list_top_widgets"][0]["data"].get("text") if d.get("list_top_widgets") else ""
    return st, head, rows

tests = [
    ("category=clothing", {"category": {"str": {"value": "clothing"}}}, {}),
    ("category=light", {"category": {"str": {"value": "light"}}}, {}),
    ("category=mobile-phones", {"category": {"str": {"value": "mobile-phones"}}}, {}),
    ("category=bogus", {"category": {"str": {"value": "bogus-xyz"}}}, {}),
    ("price number_range", {"category": {"str": {"value": "clothing"}},
                            "price": {"number_range": {"minimum": "100000", "maximum": "500000"}}}, {}),
    ("price number_range ints", {"category": {"str": {"value": "clothing"}},
                                 "price": {"number_range": {"minimum": 100000, "maximum": 500000}}}, {}),
    ("has-photo boolean", {"category": {"str": {"value": "clothing"}}, "has-photo": {"boolean": True}}, {}),
    ("has-photo bool", {"category": {"str": {"value": "clothing"}}, "has-photo": {"bool": True}}, {}),
    ("recent_ads str 1d", {"recent_ads": {"str": {"value": "1d"}}}, {}),
    ("sort price_asc", {"category": {"str": {"value": "clothing"}}, "sort": {"str": {"value": "price_asc"}}}, {}),
]
for name, data, top in tests:
    st, head, rows = search(data, **top)
    prices = [r[2] for r in rows[:5]]
    print(f"  {name}: {st} rows={len(rows)} head={head!r}")
    print(f"      titles={[r[1] for r in rows[:3]]}")
    print(f"      prices={prices}")
    sys.stdout.flush()
    time.sleep(1.0)
