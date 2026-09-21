import json
d = json.load(open("tools/search_raw.json", encoding="utf-8"))
print("PAGINATION:", json.dumps(d.get("pagination"), ensure_ascii=False)[:800])
print("SEARCH_ID:", d.get("search_id"))
print("SEO:", json.dumps(d.get("seo_details"), ensure_ascii=False)[:900])
print("SEARCH_BAR:", json.dumps(d.get("search_bar"), ensure_ascii=False)[:600])
print("ACTION_LOG:", json.dumps(d.get("action_log"), ensure_ascii=False)[:500])
