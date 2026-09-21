import json, urllib.request, re, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def get(url, headers=None):
    req = urllib.request.Request(url)
    req.add_header("user-agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)

st, txt = get("https://divar.ir/robots.txt")
print("robots:", st, repr(txt[:400]))

html = open("tools/divar_page.html", encoding="utf-8", errors="replace").read()
print("html len", len(html))
for pat in ("category", "categories", "buy-residential", "light-vehicle", "پژو", "__NEXT", "window.__"):
    idx = html.find(pat)
    print(f"  find {pat!r}: {idx}", html[max(0, idx - 120):idx + 200].replace("\n", " ") if idx >= 0 else "")

print("\n=== repo file lists ===")
for repo in ("afsharsharifi/DivarCrawler", "hecaning/divar-telegram-bot", "milad-azami/rjs-divar-api"):
    st, txt = get(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1",
                  {"Accept": "application/vnd.github+json"})
    if st == 200:
        t = json.loads(txt)
        files = [x["path"] for x in t.get("tree", []) if x["type"] == "blob"]
        print(f"{repo}: {files[:40]}")
    else:
        print(f"{repo}: {st} {txt[:120]}")
