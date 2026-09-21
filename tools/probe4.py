import json, urllib.request, urllib.error, sys, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in [("user-agent", UA), ("accept", "application/json, text/plain, */*"),
                 ("origin", "https://divar.ir"), ("referer", "https://divar.ir/")]:
        req.add_header(k, v)
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"

PG = {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 6}

def rows_of(raw):
    d = json.loads(raw)
    out = []
    for w in d.get("list_widgets", []):
        if w.get("widget_type") == "POST_ROW":
            dd = w["data"]
            out.append((dd.get("token"), dd.get("title"), dd.get("middle_description_text"),
                        dd["action"]["payload"].get("web_info", {}).get("city_persian")))
    head = d["list_top_widgets"][0]["data"].get("text") if d.get("list_top_widgets") else ""
    return out, head

print("### A. text search filter check (query=پژو)")
st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/search",
               {"city_ids": ["1"], "search_data": {"query": "پژو"}, "pagination_data": PG})
r, head = rows_of(raw)
print(st, head)
for t in r[:6]:
    print("   ", t[1], "|", t[2], "|", t[3])

print("### B. pagination (page 1 vs 2, category-less query 'پژو')")
toks = []
for p in (1, 2, 3):
    st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/search",
                   {"city_ids": ["1"], "search_data": {"query": "پژو"},
                    "pagination_data": {"@type": PG["@type"], "page": p, "page_size": 6}})
    r, head = rows_of(raw)
    print(f"   page {p}: {st} n={len(r)} head={head!r} toks={[x[0] for x in r[:3]]}")
    toks.append([x[0] for x in r])
    time.sleep(1)
print("   overlap p1&p2:", set(toks[0]) & set(toks[1]), " p2&p3:", set(toks[1]) & set(toks[2]))

print("### C. category filter variants")
cats = ["buy-residential", "rent-residential", "buy-car", "light-vehicle", "digital-goods",
        "services", "jobs", "personal", "home-kitchen", "buy-apartment"]
for c in cats:
    st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/search",
                   {"city_ids": ["1"], "search_data": {"form_data": {"data": {"category": {"str": {"value": c}}}}},
                    "pagination_data": PG})
    if st == 200:
        r, head = rows_of(raw)
        print(f"   {c}: 200 n={len(r)} head={head!r} first={r[0][1] if r else ''!r}")
    else:
        print(f"   {c}: {st} {raw[:120]}")
    time.sleep(0.7)

print("### D. category listing endpoint hunt")
for u, b in [("https://api.divar.ir/v8/categories", None),
             ("https://api.divar.ir/v8/postlist/w/categories", {"city_ids": ["1"]}),
             ("https://api.divar.ir/v8/cities", None),
             ("https://api.divar.ir/v8/place/cities", None),
             ("https://api.divar.ir/v8/postlist/w/filters", {"city_ids": ["1"], "category": "buy-car"})]:
    st, raw = call("POST" if b is not None else "GET", u, b)
    print(f"   {u}: {st} len={len(raw)} {raw[:160]!r}".replace("\n", " "))
