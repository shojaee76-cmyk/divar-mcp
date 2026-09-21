import json, urllib.request, urllib.error, time, collections

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def call(body):
    req = urllib.request.Request("https://api.divar.ir/v8/postlist/w/search", data=json.dumps(body).encode(), method="POST")
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

def run(data, page_size=24):
    b = {"city_ids": ["1"], "search_data": {"form_data": {"data": data}},
         "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": page_size}}
    st, raw = call(b)
    try:
        d = json.loads(raw)
    except Exception:
        return st, raw[:200], []
    rows = [w["data"] for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
    return st, "", rows

print("### recent_ads: token-set comparison, slow category (paintings-picture)")
sets = {}
for v in ("3h", "7d"):
    st, err, rows = run({"recent_ads": {"str": {"value": v}}, "category": {"str": {"value": "paintings-picture"}}})
    sets[v] = {r["token"] for r in rows}
    print(f"  {v}: {st} n={len(rows)} {err[:120]}")
    time.sleep(1.2)
if sets.get("3h") and sets.get("7d"):
    print("   equal?", sets["3h"] == sets["7d"], "3h<=7d?", sets["3h"] <= sets["7d"])

print("### districts shapes round 2")
shapes = [
    ("repeated_string values", {"districts": {"repeated_string": {"values": ["bolvar-e-keshavarz"]}}}),
    ("repeated_str value", {"districts": {"repeated_str": {"value": ["bolvar-e-keshavarz"]}}}),
    ("list value", {"districts": {"list": {"value": ["bolvar-e-keshavarz"]}}}),
    ("repeated_string value int", {"districts": {"repeated_string": {"value": [1234]}}}),
]
for label, val in shapes:
    st, err, rows = run({**val, "category": {"str": {"value": "jobs"}}})
    dists = [((r.get("action") or {}).get("payload") or {}).get("web_info", {}).get("district_persian") for r in rows[:3]]
    print(f"  {label}: {st} n={len(rows)} dists={dists} {err[:130] if st != 200 else ''}")
    time.sleep(1.0)

print("### lazy filter endpoint hunt")
lazy = {"@type": "type.googleapis.com/post_list.LazyFilterPayload", "filter_name": "districts",
        "version": "82", "place_ids": ["1"], "category": "ROOT"}
for path in ("/v8/postlist/w/filters/lazy", "/v8/postlist/w/filter/lazy", "/v8/postlist/w/lazy-filters"):
    for body in ({"query": "کشاورز", "lazy_payload": lazy}, {"query": "کشاورز", **lazy}):
        req = urllib.request.Request("https://api.divar.ir" + path, data=json.dumps(body).encode(), method="POST")
        for k, v in [("user-agent", UA), ("content-type", "application/json"), ("origin", "https://divar.ir")]:
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                st, txt = 200, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            st, txt = e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            st, txt = -1, str(e)
        print(f"  {path} [{list(body)[:2]}]: {st} {txt[:150]!r}")
        time.sleep(0.6)

print("### district slugs from detail of a post in a known district")
st, raw = call({"city_ids": ["1"], "search_data": {"form_data": {"data": {"category": {"str": {"value": "jobs"}}}}},
                "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 3}})
d = json.loads(raw)
print("  webengage sample:", json.dumps(d.get("seo_details", {}).get("linked_data", [{}])[0], ensure_ascii=False)[:400] if d.get("seo_details") else "")
