import json, urllib.request, re, sys, os

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0 Safari/537.36"

def get(url, headers=None, binary=False, cap=6_000_000):
    req = urllib.request.Request(url)
    req.add_header("user-agent", UA)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            b = r.read(cap)
            return r.status, b if binary else b.decode("utf-8", "replace")
    except Exception as e:
        return -1, (b"" if binary else str(e))

tok = None
envp = os.path.expandvars(r"%LOCALAPPDATA%\hermes\.env")
if os.path.exists(envp):
    for line in open(envp, encoding="utf-8", errors="replace"):
        m = re.match(r'\s*(?:export\s+)?GITHUB_TOKEN\s*=\s*"?([^"\n]+)"?', line)
        if m:
            tok = m.group(1).strip()
print("gh token:", bool(tok))

for repo in ("afsharsharifi/DivarCrawler", "hecaning/divar-telegram-bot", "rezaxd/divar-crawler"):
    st, txt = get(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1",
                  {"Accept": "application/vnd.github+json", "Authorization": f"Bearer {tok}"} if tok else {"Accept": "application/vnd.github+json"})
    if st == 200:
        print(f"{repo}:", [x["path"] for x in json.loads(txt).get("tree", []) if x["type"] == "blob"][:35])
    else:
        print(f"{repo}: {st} {txt[:150]}")

print("\n=== divar JS bundles ===")
html = open("tools/divar_page.html", encoding="utf-8", errors="replace").read()
srcs = re.findall(r'<script[^>]+src="([^"]+)"', html)
print("scripts:", srcs[:12])
paths = set()
for s in srcs[:10]:
    url = s if s.startswith("http") else "https://divar.ir" + s
    st, js = get(url, binary=False)
    if st != 200:
        print(f"  {url[:90]}: {st} {str(js)[:80]}")
        continue
    found = set(re.findall(r'["\'`](/(?:v\d+|api)[a-zA-Z0-9_\-/{}\.$:]*)["\'`]', js))
    host = set(re.findall(r'https://api\.divar\.ir[a-zA-Z0-9_\-/{}.]*(?:/[a-zA-Z0-9_\-/{}.]*)*', js))
    cat = set(re.findall(r'["\'`]([a-zA-Z0-9_\-]*categor[a-zA-Z0-9_\-/]*)["\'`]', js, re.I))
    print(f"  {url.split('/')[-1][:40]}: {len(js)}B paths={len(found)} hosts={len(host)} cat={sorted(cat)[:12]}")
    paths |= found
    if host:
        print("     hosts:", sorted(host)[:15])
print("\nAPI-ish paths sample:", sorted(p for p in paths if "post" in p or "place" in p or "categor" in p or "suggest" in p)[:40])
