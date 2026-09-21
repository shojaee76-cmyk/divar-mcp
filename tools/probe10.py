import json, urllib.request, urllib.error, sys, time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
f = json.load(open("tools/filters_tehran.json", encoding="utf-8"))
print("=== filters widget data (full) ===")
for w in f["page"]["widget_list"]:
    print(json.dumps(w, ensure_ascii=False)[:900])
    print("-" * 40)

d = json.load(open("tools/detail_raw.json", encoding="utf-8"))
print("\n=== detail: top level ===")
for k, v in d.items():
    print(f"  {k}: {type(v).__name__}", json.dumps(v, ensure_ascii=False)[:220] if not isinstance(v, list) else f"list[{len(v)}]")
print("\n=== detail sections ===")
for i, s in enumerate(d.get("sections", [])):
    name = s.get("section_name")
    ws = s.get("widgets", [])
    print(f"  [{i}] {name} widgets={len(ws)} types={[w.get('widget_type') for w in ws][:12]}")
print("\n=== detail: full section 0/1 dump (trimmed) ===")
print(json.dumps(d.get("sections", [])[:2], ensure_ascii=False, indent=1)[:4000])
