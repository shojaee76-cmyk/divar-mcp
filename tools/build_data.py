"""Merge the raw harvests into the packaged data files.

  tools/cities_raw.json      -> src/divar_mcp/data/cities.json
  tools/categories_raw.json  -> src/divar_mcp/data/categories.json

Run: python tools/build_data.py
"""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "src", "divar_mcp", "data")
os.makedirs(DATA, exist_ok=True)

cities_src = os.path.join(ROOT, "tools", "cities_raw.json")
if os.path.exists(cities_src):
    raw = json.load(open(cities_src, encoding="utf-8"))
    clean = {str(k): v.strip() for k, v in sorted(raw.items(), key=lambda kv: int(kv[0])) if v}
    with open(os.path.join(DATA, "cities.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(clean, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("cities.json:", len(clean), "entries")

cats_src = os.path.join(ROOT, "tools", "categories_raw.json")
if os.path.exists(cats_src):
    raw = json.load(open(cats_src, encoding="utf-8"))
    items = sorted(raw.values(), key=lambda c: (len(c.get("parents") or []), c["slug"]))
    with open(os.path.join(DATA, "categories.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("categories.json:", len(items), "entries")
else:
    print("no categories_raw.json yet; keeping placeholder", file=sys.stderr)
