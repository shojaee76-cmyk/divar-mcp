import json, urllib.request, urllib.error, time, collections

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

def get(url):
    req = urllib.request.Request(url)
    for k, v in [("user-agent", UA), ("accept", "application/json"), ("origin", "https://divar.ir")]:
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def rows_of(raw):
    d = json.loads(raw)
    return [w["data"] for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]

print("### district filter verification (jobs, city 1)")
base = {"city_ids": ["1"], "search_data": {"form_data": {"data": {"category": {"str": {"value": "jobs"}}}}},
        "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 24}}
st, raw = post("https://api.divar.ir/v8/postlist/w/search", base)
plain = collections.Counter(((r.get("action") or {}).get("payload") or {}).get("web_info", {}).get("district_persian") for r in rows_of(raw))
print("  unfiltered districts:", dict(list(plain.items())[:8]))
time.sleep(1)
base["search_data"]["form_data"]["data"]["districts"] = {"repeated_string": {"values": ["bolvar-e-keshavarz"]}}
st, raw = post("https://api.divar.ir/v8/postlist/w/search", base)
if st == 200:
    rs = rows_of(raw)
    filt = collections.Counter(((r.get("action") or {}).get("payload") or {}).get("web_info", {}).get("district_persian") for r in rs)
    print(f"  filtered: {st} n={len(rs)} districts={dict(filt)}")
else:
    print("  filtered:", st, raw[:200])

print("\n### detail remaining sections")
d = json.load(open("tools/detail_raw.json", encoding="utf-8"))
for s in d["sections"][4:]:
    print("--", s["section_name"])
    print(json.dumps(s, ensure_ascii=False)[:1200])
print("\n-- webengage:", json.dumps(d.get("webengage"), ensure_ascii=False)[:500])
print("-- contact:", json.dumps(d.get("contact"), ensure_ascii=False)[:600])
print("-- city:", json.dumps(d.get("city"), ensure_ascii=False))
print("-- share:", json.dumps(d.get("share"), ensure_ascii=False))
