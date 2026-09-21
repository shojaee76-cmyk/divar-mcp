"""Harvest Divar city id -> name map. Polite: 1 request per ~2s."""
import json, urllib.request, urllib.error, time, os, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
OUT = "tools/cities_raw.json"
found = {}
if os.path.exists(OUT):
    found = json.load(open(OUT, encoding="utf-8"))

def probe(cid):
    body = {"city_ids": [str(cid)],
            "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 1}}
    req = urllib.request.Request("https://api.divar.ir/v8/postlist/w/search", data=json.dumps(body).encode(), method="POST")
    for k, v in [("user-agent", UA), ("content-type", "application/json"), ("accept", "application/json"),
                 ("origin", "https://divar.ir"), ("referer", "https://divar.ir/")]:
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        return None, str(e)[:80]
    bc = (d.get("seo_details") or {}).get("bread_crumb") or []
    if not bc:
        return None, "no-breadcrumb"
    return bc[0].get("name"), None

for cid in range(1, 401):
    if str(cid) in found:
        continue
    name, err = probe(cid)
    if name:
        found[str(cid)] = name
        print(f"{cid}: {name}", flush=True)
    elif err and "timed out" in err:
        time.sleep(3)
    json.dump(found, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    time.sleep(2.0)
print("DONE total:", len(found), flush=True)
