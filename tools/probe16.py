import json, urllib.request

d = json.load(open("tools/detail_raw.json", encoding="utf-8"))
for s in d["sections"]:
    if s["section_name"] in ("IMAGE", "TITLE"):
        print("==", s["section_name"])
        print(json.dumps(s, ensure_ascii=False)[:1800])
        print()

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def get(url):
    req = urllib.request.Request(url)
    for k, v in [("user-agent", UA), ("accept", "application/json"), ("origin", "https://divar.ir")]:
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    for k, v in [("user-agent", UA), ("content-type", "application/json"), ("origin", "https://divar.ir")]:
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

s = post("https://api.divar.ir/v8/postlist/w/search",
         {"city_ids": ["1"], "search_data": {"form_data": {"data": {"category": {"str": {"value": "mobile-phones"}}}}},
          "pagination_data": {"@type": "type.googleapis.com/post_list.PaginationData", "page": 1, "page_size": 3}})
tok = [w["data"]["token"] for w in s["list_widgets"] if w["widget_type"] == "POST_ROW"][0]
print("SALE POST TOKEN:", tok)
det = get(f"https://api.divar.ir/v8/posts-v2/web/{tok}")
json.dump(det, open("tools/detail_sale.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for sec in det["sections"]:
    if sec["section_name"] in ("LIST_DATA", "IMAGE", "DESCRIPTION", "TITLE"):
        print("==", sec["section_name"], json.dumps(sec, ensure_ascii=False)[:1400])
        print()
print("webengage:", json.dumps(det.get("webengage"), ensure_ascii=False))
print("seo title:", det.get("seo", {}).get("title"))
