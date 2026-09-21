import json, urllib.request, urllib.error, sys

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

def call(method, url, body=None, extra=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("user-agent", UA)
    req.add_header("accept", "application/json, text/plain, */*")
    req.add_header("origin", "https://divar.ir")
    req.add_header("referer", "https://divar.ir/")
    if data:
        req.add_header("content-type", "application/json")
    for k, v in (extra or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"

tests = [
    ("minimal city_ids", "POST", "https://api.divar.ir/v8/postlist/w/search", {"city_ids": ["1"]}),
    ("+pagination", "POST", "https://api.divar.ir/v8/postlist/w/search",
     {"city_ids": ["1"], "pagination_data": {"@type": "dfpaginationv5", "page": 1, "page_size": 2}}),
    ("+search_data category", "POST", "https://api.divar.ir/v8/postlist/w/search",
     {"city_ids": ["1"],
      "search_data": {"form_data": {"data": {"category": {"str": {"value": "buy-residential"}}}}},
      "pagination_data": {"@type": "dfpaginationv5", "page": 1, "page_size": 2}}),
    ("filters", "POST", "https://api.divar.ir/v8/postlist/w/filters", {"city_ids": ["1"]}),
    ("web api search", "POST", "https://api.divar.ir/v8/postlist/w/search",
     {"city_ids": ["1"], "pagination_data": {"@type": "dfpaginationv5", "page": 1, "page_size": 1},
      "search_data": {"form_data": {"data": {"category": {"str": {"value": "ROOT"}}}}}}),
]

for name, m, u, b in tests:
    st, raw = call(m, u, b)
    print("=" * 70)
    print(f"{name}: {st}  len={len(raw)}")
    print(raw[:700].replace("\n", " "))
    sys.stdout.flush()
