import json, collections

d = json.load(open("tools/search_raw.json", encoding="utf-8"))
print("TOP KEYS:", list(d.keys()))
counts = collections.Counter(w.get("widget_type") for w in d.get("list_widgets", []))
print("WIDGET TYPES:", dict(counts))
rows = [w for w in d.get("list_widgets", []) if w.get("widget_type") == "POST_ROW"]
print("rows:", len(rows))
r = rows[0]["data"]
print("ROW KEYS:", list(r.keys()))
print(json.dumps(r, ensure_ascii=False, indent=1)[:3000])
