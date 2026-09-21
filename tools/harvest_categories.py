"""Harvest the Divar category tree (slug + Persian name + parents).

One request per slug: the search response's seo_details.bread_crumb contains the
full ancestor chain with both slugs and Persian titles, which is exactly the
taxonomy the category filter accepts.

Run: python tools/harvest_categories.py
"""
import json, os, urllib.request, urllib.error, time, collections

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
OUT = "tools/categories_raw.json"


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    for k, v in [("user-agent", UA), ("content-type", "application/json"),
                 ("accept", "application/json"), ("origin", "https://divar.ir"), ("referer", "https://divar.ir/")]:
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return 200, json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return -1, None


def lookup(slug):
    body = {
        "city_ids": ["1"],
        "search_data": {"form_data": {"data": {"category": {"str": {"value": slug}}}},
                        "query": "a"} if slug != "ROOT" else {"form_data": {"data": {"category": {"str": {"value": "ROOT"}}}}},
        "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 1},
    }
    st, d = post("https://api.divar.ir/v8/postlist/w/search", body)
    if st != 200 or not d:
        return None
    crumb = (d.get("seo_details") or {}).get("bread_crumb") or []
    chain = []
    for item in crumb:
        name = item.get("name")
        sd = (item.get("search_data") or {}).get("form_data", {}).get("data", {})
        cs = ((sd.get("category") or {}).get("str") or {}).get("value")
        if cs:
            chain.append({"slug": cs, "name": name})
    if not chain:
        return None
    return chain


def main():
    known = {}
    if os.path.exists(OUT):
        known = json.load(open(OUT, encoding="utf-8"))
    seeds = {"ROOT"}
    if os.path.exists("tools/slug_harvest.json"):
        seeds |= set(json.load(open("tools/slug_harvest.json", encoding="utf-8"))["counts"].keys())
    seeds |= set(known.keys())

    for pass_no in range(1, 5):
        todo = sorted(s for s in seeds if s not in known)
        if not todo:
            break
        print(f"pass {pass_no}: {len(todo)} slugs to resolve", flush=True)
        for slug in todo:
            chain = lookup(slug)
            if chain:
                leaf = chain[0]
                parents = chain[1:]
                known[leaf["slug"]] = {
                    "slug": leaf["slug"],
                    "name": leaf["name"],
                    "parents": [p["name"] for p in parents if p["slug"] != "ROOT"],
                    "parent_slugs": [p["slug"] for p in parents if p["slug"] != "ROOT"],
                }
                print(f"  {slug} -> {leaf['name']}  (parents: {[p['slug'] for p in parents]})", flush=True)
                seeds |= {p["slug"] for p in parents}
            else:
                print(f"  {slug} -> INVALID", flush=True)
            json.dump(known, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            time.sleep(1.6)
    print("TOTAL categories:", len(known), flush=True)


if __name__ == "__main__":
    main()
