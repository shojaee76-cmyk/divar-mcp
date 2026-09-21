import json, urllib.request, re, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"
html = open("tools/divar_page.html", encoding="utf-8", errors="replace").read()
srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
allpaths, allhosts, js_blob = set(), set(), []
for s in srcs:
    url = s if s.startswith("http") else "https://divar.ir" + s
    try:
        req = urllib.request.Request(url); req.add_header("user-agent", UA)
        with urllib.request.urlopen(req, timeout=45) as r:
            js = r.read(9_000_000).decode("utf-8", "replace")
    except Exception as e:
        print("skip", url[-40:], e); continue
    allpaths |= set(re.findall(r'["\'`](/(?:v\d+|api)/[a-zA-Z0-9_\-/{}\.$:]*)["\'`]', js))
    allhosts |= set(re.findall(r'https://api\.divar\.ir[a-zA-Z0-9_\-/{}.]*', js))
    for m in re.finditer(r'categor', js, re.I):
        js_blob.append(js[max(0, m.start() - 90):m.start() + 90])

open("tools/api_paths.txt", "w", encoding="utf-8").write("\n".join(sorted(allpaths)))
print("total paths:", len(allpaths), "hosts:", len(allhosts))
kw = ("categor", "filter", "place", "suggest", "search", "city", "list")
for p in sorted(allpaths):
    if any(k in p.lower() for k in kw):
        print("  P", p)
print("HOSTS:")
for h in sorted(allhosts):
    print("  H", h[:140])

seen = set()
print("\n--- category context snippets ---")
for s in js_blob:
    k = s[:60]
    if k in seen: continue
    seen.add(k)
    print("   ", s.replace("\n", " ")[:170])
    if len(seen) > 45: break
