import json, urllib.request, urllib.error, re, sys, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def call(method, url, body=None, raw_headers=None):
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

def rows(raw):
    d = json.loads(raw)
    out = []
    for w in d.get("list_widgets", []):
        if w.get("widget_type") == "POST_ROW":
            dd = w["data"]
            wi = dd.get("action", {}).get("payload", {}).get("web_info", {})
            out.append((dd.get("token"), dd.get("title"), wi.get("district_persian")))
    return out, d

print("### A. real pagination via echoed cursor")
st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/search",
               {"city_ids": ["1"], "search_data": {"query": "پژو"}, "pagination_data": {
                   "@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 6}})
r1, d1 = rows(raw)
print("page1:", [t[0] for t in r1], "has_next:", d1["pagination"]["has_next_page"])
cur = d1["pagination"]["data"]
cur["page"] = cur.get("page", 2)
cur["layer_page"] = cur.get("layer_page", 1) + 1
st, raw2 = call("POST", "https://api.divar.ir/v8/postlist/w/search",
                {"city_ids": ["1"], "search_data": {"query": "پژو"}, "pagination_data": cur})
if st == 200:
    r2, d2 = rows(raw2)
    print("page2 echo-cursor:", st, [t[0] for t in r2][:6], "has_next:", d2["pagination"]["has_next_page"])
    print("   overlap:", bool(set(x[0] for x in r1) & set(x[0] for x in r2)))
else:
    print("page2 FAILED", st, raw2[:200])

print("### B. subcategory filters (with category in body)")
for cat in ("services", "jobs", "personal"):
    st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/filters",
                   {"city_ids": ["1"], "search_data": {"form_data": {"data": {"category": {"str": {"value": cat}}}}}})
    keys = []
    if st == 200:
        try:
            keys = [w["data"].get("field", {}).get("key") for w in json.loads(raw)["page"]["widget_list"]]
        except Exception as e:
            keys = ["parse:" + str(e)]
    print(f"   {cat}: {st} fields={keys}")
    time.sleep(0.6)

print("### C. harvest slugs from divar.ir HTML")
for u in ("https://divar.ir/s/tehran", "https://divar.ir/"):
    st, html = call("GET", u)
    print(f"   {u}: {st} len={len(html)}")
    if st == 200 and len(html) > 1000:
        slugs = sorted(set(re.findall(r'/(?:s/[a-z0-9\-]+)/?([a-z0-9\-]{3,40})(?:[/?"])', html)))
        cats = sorted(set(re.findall(r'"category(?:_slug)?"\s*:\s*"([a-z0-9\-]+)"', html)))
        print("   url slugs:", slugs[:60])
        print("   json cats:", cats[:60])
        open("tools/divar_page.html", "w", encoding="utf-8").write(html)
        break
