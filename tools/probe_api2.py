import json, urllib.request, urllib.error, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("user-agent", UA)
    req.add_header("accept", "application/json, text/plain, */*")
    req.add_header("origin", "https://divar.ir")
    req.add_header("referer", "https://divar.ir/")
    if data:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"

st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/filters", {"city_ids": ["1"]})
print("filters:", st, len(raw))
open("tools/filters_tehran.json", "w", encoding="utf-8").write(raw)
try:
    d = json.loads(raw)
    print("top keys:", list(d.keys()))
    print(json.dumps(d, ensure_ascii=False)[:200])
except Exception as e:
    print("parse fail", e)

candidates = [
    "type.googleapis.com/post_list.PaginationData",
    "type.googleapis.com/post_list.Pagination",
    "type.googleapis.com/post_list.v5.PaginationData",
    "type.googleapis.com/PostListPaginationData",
    "type.googleapis.com/post_list.PaginationV5",
]
for t in candidates:
    body = {"city_ids": ["1"], "pagination_data": {"@type": t, "page": 1, "page_size": 2}}
    st, raw = call("POST", "https://api.divar.ir/v8/postlist/w/search", body)
    print("=" * 60)
    print(t, "->", st, "len", len(raw))
    print(raw[:300].replace("\n", " "))
    sys.stdout.flush()
    if st == 200:
        open("tools/search_raw.json", "w", encoding="utf-8").write(raw)
        break
