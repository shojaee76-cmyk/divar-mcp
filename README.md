# divar-mcp

[![tests](https://github.com/shojaee76-cmyk/divar-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/shojaee76-cmyk/divar-mcp/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-lightgrey.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-lightgrey.svg)](pyproject.toml)
[![MCP: stdio](https://img.shields.io/badge/MCP-stdio-lightgrey.svg)](https://modelcontextprotocol.io)
[![M8ven Score](https://m8ven.ai/badge/mcp/shojaee76-cmyk/divar-mcp)](https://m8ven.ai/mcp/shojaee76-cmyk/divar-mcp)
[![dependencies: none](https://img.shields.io/badge/dependencies-none-lightgrey.svg)](pyproject.toml)

**An agent-grade MCP server for [divar.ir](https://divar.ir)**, Iran's largest classifieds marketplace (دیوار). Search live listings, read a post in full, price an item, appraise someone else's listing, hunt underpriced stock, watch a search over time, and export bulk rows, all from any MCP client.

Read-only, no account, no API key, no dependencies. Works from inside Iran and from anywhere else divar.ir's API is reachable.

| | |
| --- | --- |
| **19 tools** | find, read, value, appraise, hunt, watch, export, plus self-diagnosis and a capability map |
| **5 resources** | cities, categories, verified slugs, status, help as readable MCP resources |
| **4 prompts** | price-an-item, appraise-listing, find-deals, watch-market |
| **structured output** | every tool declares an `outputSchema` and returns `structuredContent` |
| **self-repairing errors** | a wrong city or category comes back with the closest real values and a hint |
| **conservative by design** | read-only, no contact details, polite rate limits, no fabricated links |

> یک MCP سرور کامل برای کار با آگهی‌های دیوار: جست‌وجو، خواندن آگهی، قیمت‌گذاری، ارزیابی آگهی دیگران، پیدا کردن زیرقیمت‌ها، دیده‌بانی جست‌وجو و خروجی CSV. فقط خواندنی، بدون کلید API، بدون وابستگی.

---

## Why this exists

Divar has **no public MCP server**. The `divar-ir/*` GitHub org is Divar's own internal code-search tooling, not marketplace access. The official API (**Kenar / کنار دیوار**) needs an approved app, an API key and OAuth, which is a partner programme, not a read path. Everything else out there is Playwright page-scraping with no MCP surface, no filters and no pagination.

This server talks to the same JSON endpoints divar.ir's own web app uses, and wraps them in the shape an agent actually needs.

## Install

```bash
# run straight from git, no install
uvx --from git+https://github.com/shojaee76-cmyk/divar-mcp divar-mcp

# or install it
pip install git+https://github.com/shojaee76-cmyk/divar-mcp
```

Requires Python 3.10+. There are no third-party dependencies.

## Connect a client

**Claude Desktop / Claude Code** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "divar": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/shojaee76-cmyk/divar-mcp", "divar-mcp"]
    }
  }
}
```

**Cursor / Cline / Windsurf** — same shape, `command` + `args`.

**Hermes Agent** (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  divar:
    command: uvx
    args: ["--from", "git+https://github.com/shojaee76-cmyk/divar-mcp", "divar-mcp"]
```

## Tools

### Find and read

| Tool | What it gives an agent |
| --- | --- |
| `divar_search` | Live listings: `query`, one or many `cities`, `category`, Toman `price_min`/`price_max`, `has_photo`, numeric `district_ids`, `brand_model`, `sort`, plus client-side `max_age_hours`, `exclude_terms`, `title_contains` that Divar itself does not offer. Compact rows by default, `brief: false` for every parsed field. |
| `divar_get_post` | One listing in full: description, structured attributes (brand/model, year, mileage...), image URLs, Jalali text and ISO 8601 dates, city/district, district id, category breadcrumb. |
| `divar_similar_posts` | Comparables for an existing listing: same category, brand/model and city, excluding itself. |
| `divar_search_url` | A `divar.ir/s/...` URL a human can open. Returns `url: null` with a reason when no **verified** city slug exists, rather than a link that may 404. |

### Value and decide

| Tool | What it gives an agent |
| --- | --- |
| `divar_price_analysis` | Price distribution of live comparables: min, p25, p40, median, p75, max, mean, suggested ask range, price bands, freshness split, and a per-district median table. The "what should I ask?" tool. |
| `divar_appraise_post` | Judges one listing: `below_market` / `fair` / `above_market`, its percentile, delta versus median, confidence from the sample size, and cheaper alternatives. |
| `divar_find_deals` | Ranks listings below the live market with an explainable `deal_score` (price advantage, freshness, photo count, price stated) plus per-deal `reasons`. Ships with the caveat that a low price is a signal to verify, not a verdict. |
| `divar_market_breakdown` | Where the stock sits: median price per district (busiest first), price bands, freshness. |

### Watch and export

| Tool | What it gives an agent |
| --- | --- |
| `divar_watch_create` | Save a named search; today's listings become the quiet baseline. |
| `divar_watch_check` | Returns only listings a watch has never reported before. Cron-friendly "anything new?" |
| `divar_watch_list` / `divar_watch_delete` | Manage saved watches. |
| `divar_price_trend` | Local day-by-day price history for an exact filter. Every search records a price point, so the series grows with use. Says `status: collecting` (with a reason) until two days exist. |
| `divar_export` | Walks pages and writes CSV (utf-8-sig, Excel-safe Persian) or JSONL to disk, returning the absolute path. Bulk work stays out of the model's context. |

### Meta

| Tool | What it gives an agent |
| --- | --- |
| `divar_status` | Is divar.ir reachable right now (latency, or a clear hint when it is not), dataset sizes, store size, cache counters. First call when something looks broken. |
| `divar_help` | Capability map: tool index, units, dataset sizes and recipes for the common jobs. No network call. |
| `divar_list_cities` | Cities by Persian name, ASCII slug or id; a miss returns the closest names. |
| `divar_list_categories` | Category slugs by Persian or English text with breadcrumb parents; a miss returns the closest slugs. |
| `divar_post_filters` | Which filter widgets Divar currently exposes for a city/category. |

All prices are **Toman** (Divar displays Toman; its internal schema.org price is Rial, 10x larger). Dates come back as both the Jalali text and ISO 8601 with `+03:30`.

## Resources and prompts

MCP clients that support them get read-only resources instead of tool calls:

```
divar://cities          city id, Persian name and ASCII slug for every harvested city
divar://categories      category slugs with Persian names and parents
divar://cities/slugs    the validated city page slug map
divar://status          dataset + store + cache stats, no network probe
divar://help            the capability map
```

And four prompts that encode the workflows: `price-an-item`, `appraise-listing`, `find-deals`, `watch-market`.

## Built for agents, not just for humans

* **Every tool declares an `outputSchema` and returns `structuredContent`**, so a client can parse results instead of regexing prose.
* **Errors repair themselves.** A wrong category returns `suggestions` with the closest real slugs and a `hint`; a wrong city returns the closest real names. Unknown tools return the list of valid ones. Nothing fails with a bare 400.
* **Inputs are forgiving.** Cities accept an id (`1`), a Persian name (`تهران`), or an ASCII slug (`tehran`), case-insensitively. Categories accept a slug or a Persian name. `ROOT` is understood as "everything".
* **Every result carries `meta`**: requests made, cached responses, elapsed seconds, rate-limit and cache settings, so an agent can tell a free call from an expensive one.
* **Annotations on every tool** (`readOnlyHint`, `idempotentHint`, `openWorldHint`) so a client can gate the two tools that write local files or local state.
* **Brief by default.** Search returns 10 compact fields per row; `brief: false` returns everything.

## Example agent use

> "My iPhone 13 is in good shape. What should I ask in Tehran right now?"

The agent calls `divar_list_categories("موبایل")` → `mobile-phones`, then `divar_price_analysis(query="آیفون ۱۳", category="mobile-phones", city="تهران", pages=2)`, and answers with the median, the active band, the district table, and fresh comparables with links.

> "Is this listing overpriced? https://divar.ir/v/gaxi5lYL"

`divar_appraise_post` → verdict, percentile, confidence, cheaper alternatives.

> "Find a Peugeot 206 under 700 million in Karaj or Tehran, photo-only, posted this week."

`divar_search(query="پژو ۲۰۶", cities=["کرج","تهران"], price_max=700000000, has_photo=true, max_age_hours=168)`.

> "Tell me whenever a cheap 206 shows up."

`divar_watch_create(name="206-karaj", query="پژو ۲۰۶", city="کرج")`, then `divar_watch_check("206-karaj")` on a schedule.

## CLI (same engine, for humans and cron)

```bash
divar search "پژو ۲۰۶" --city tehran --price-max 900000000 --has-photo --sort price_asc
divar price "آیفون ۱۳" --category موبایل --pages 3        # printed as a readable summary
divar deals "پژو ۲۰۶" --category light --min-discount 0.1
divar appraise https://divar.ir/v/gaxi5lYL
divar breakdown "پژو" --city تهران                       # median price per district
divar trend "آیفون ۱۳" --days 60
divar watch create --name 206 --query "پژو ۲۰۶" --city کرج
divar watch check --name 206
divar export "لپ‌تاپ" --pages 5 --format csv
divar status
divar help
```

`divar-mcp --list-tools` prints the raw tool schemas, `--resources` and `--prompts` list those, and `divar-mcp --call divar_search --args '{"query":"پژو","city":"1"}'` runs one tool without a client.

## Performance

Latency to divar.ir is dominated by two things: a TCP+TLS handshake per request,
and the polite delay between requests. Both are handled:

- **Keep-alive sockets.** The client holds one HTTP connection per thread, so a
  multi-request tool pays for the handshake once instead of once per page. Idle
  sockets that Divar has closed are detected and reconnected transparently.
- **A shared client.** The MCP server is long lived, so the same client (and its
  sockets and cache) is reused across tool calls instead of rebuilt per call.
- **A response cache.** Identical requests inside `DIVAR_CACHE_TTL` (default 180
  seconds) are served locally. Set `DIVAR_CACHE_TTL=0` to disable it entirely.
- **Batched local writes.** The SQLite store runs in WAL mode with relaxed fsync
  and one commit per page of listings instead of one per row.

Every result's `meta` block reports `connections_reused`, `requests_made` and
`elapsed_seconds`, so the effect is visible from a client:

```bash
python tools/bench.py --mode before      # fresh client + urllib + no cache
python tools/bench.py --mode transport   # keep-alive, cache off
python tools/bench.py --mode warm        # keep-alive + cache
```

Measured against the real API from an Iranian line (`--repeat 3`, medians, cache
disabled so only the transport is compared):

| Tool | before | keep-alive | speedup | repeat call |
|---|---|---|---|---|
| `divar_search` | 1.59s | 1.11s | 1.43x | 0.00s |
| `divar_price_analysis` | 2.78s | 1.40s | 1.99x | 0.00s |
| `divar_market_breakdown` | 2.46s | 1.72s | 1.43x | 0.00s |
| `divar_find_deals` | 3.92s | 2.59s | 1.51x | 0.00s |
| `divar_appraise_post` | 4.07s | 2.44s | 1.67x | 0.00s |
| `divar_get_post` | 1.23s | 0.75s | 1.64x | 0.00s |
| **total** | **16.05s** | **10.01s** | **1.60x** | **0.00s** |

Tools that make several requests gain most (a handshake saved per request); a
repeated identical call inside the cache TTL costs nothing. Numbers vary with
the line: treat them as a median of the hour they were taken, not a promise.

Tuning knobs, all optional environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `DIVAR_CACHE_TTL` | `180` | seconds a response stays cached; `0` disables caching |
| `DIVAR_POOL` | `1` | `0` builds a fresh client per call (isolation, benchmarks) |
| `DIVAR_NO_KEEPALIVE` | unset | `1` reverts to a connection per request |
| `DIVAR_MIN_INTERVAL` | `0.8` | minimum seconds between requests (be polite) |
| `DIVAR_TIMEOUT` | `25` | per-request timeout in seconds |
| `DIVAR_STORE` | platform data dir | path to the local SQLite store |
| `DIVAR_STORE_DISABLE` | unset | `1` runs fully stateless (no history, no watches) |

## How it works

The server talks to the same JSON endpoints the divar.ir web app uses (discovered from Divar's own JS bundles and verified against the live API):

| Endpoint | Use |
| --- | --- |
| `POST /v8/postlist/w/search` | listing search, filters, cursor pagination |
| `POST /v8/postlist/w/filters` | filter schema for a city/category |
| `GET /v8/posts-v2/web/{token}` | single post view |

Things worth knowing if you fork this:

* **Filters are protobuf-`Any` encoded.** `category` is `{"str": {"value": "<slug>"}}`, `price` is `{"number_range": {"minimum": n, "maximum": n}}`, `districts` is `{"repeated_string": {"value": ["208"]}}` (numeric district ids), `has-photo` is `{"boolean": {}}` (presence = true), `brand_model` is a repeating string.
* **`page` is ignored by the API.** Real pagination is a cursor: echo back `pagination.data` (`last_post_date`, `pelle_max_score`, `filters_hash`, `cumulative_widgets_count`, `page`, `layer_page`) with `page`/`layer_page` incremented. This server does that for you (`pages=3`).
* **Divar's own recency filter (`recent_ads`) does not filter.** Verified: `3h` and `7d` return identical result sets, so recency filtering is done client-side from the parsed Persian relative time (`max_age_hours`).
* **District filtering needs numeric ids**, which only appear inside a post's TAGS payload. `divar_get_post` exposes it as `district_id`.
* **Jalali dates are converted** (`۳۱ شهریور ۱۴۰۵` → `2026-09-22T00:06:00+03:30`) so an agent can reason about age without a Hijri library.
* **Category slugs come from Divar's own SEO breadcrumbs** (`mobile-phones` → `mobile-tablet` → `electronic-devices`), so the vocabulary and Persian names are real, not guessed.
* **Web links need a real slug.** A Persian city name in the path is wrong, and divar.ir answers with the same SPA shell for *any* slug, so a slug is published only when it came from Divar's payload and its `city_id` matched the query. `tools/harvest_city_slugs.py` grows that verified map.
* **Divar's own SEO headline is off by one** ("صفحه ۲" on the first page). Harmless, but do not read it as a page number.

Local state (listing observations, price history, watches) lives in SQLite outside the package: `%LOCALAPPDATA%\divar-mcp\store.db` on Windows, `~/.divar-mcp/store.db` elsewhere, overridable with `DIVAR_STORE`. Set `DIVAR_STORE_DISABLE=1` for a fully stateless run.

## One-click launcher

`launchers/DIVAR search.bat` (double-click, or the Desktop shortcut) asks for a phrase, a city and an optional budget, prints live listings with prices, and offers to open the matching divar.ir page in Chrome.

## Politeness and legality

* **Read-only for Divar.** This server never creates, edits, deletes, reports or messages anything there. The only writes are local files (`divar_export`, the SQLite store, watch definitions).
* **No phone numbers or contact data.** Divar does not expose seller contact details to logged-out clients and this server never tries to obtain them (no auth bypass, no `GetContactWeb`, no PII harvesting).
* **Rate limited.** Default one request per 0.8s with an LRU response cache, well under Divar's ~30 requests/minute throttle. Tune with `DIVAR_MIN_INTERVAL`, `DIVAR_TIMEOUT`, `DIVAR_CACHE_TTL`.
* **Unofficial.** Not affiliated with or endorsed by Divar (Hezardastan / Cafe Bazaar). It reads public listings the way a browser does. Respect Divar's terms and local law; if you need write access or official data, use [Kenar](https://divar.ir/kenar).

## Development

```bash
git clone https://github.com/shojaee76-cmyk/divar-mcp && cd divar-mcp
uv venv && uv pip install -e ".[dev]"

pytest                                       # offline: parsers, store, analytics, MCP protocol, SDK interop
DIVAR_LIVE=1 pytest tests/test_live.py -v    # hits the real API (needs an Iran-reachable line)
python tools/smoke.py                        # CLI -> MCP end to end, no network
python tools/verify_publish.py               # repo blobs vs this working tree
```

The offline suite runs entirely on captured fixtures (`tests/fixtures/*.json`, real payloads from a live search and two live post views) plus temp-file stores, so parsers, analytics and watch logic are all tested without a network. The protocol suite boots the real server process and, when the official `mcp` SDK is installed, drives it with `mcp.client.stdio` to prove interop.

`tools/` holds the reverse-engineering and harvesting scripts (`probe*.py`, `harvest_cities.py`, `harvest_categories.py`, `harvest_city_slugs.py`, `build_data.py`). Run `python tools/build_data.py` after re-harvesting to refresh the bundled data.

## Roadmap

* Optional `kenar` backend for authenticated workflows (your own listings) when the user supplies a key
* District-level price history once the store has a few weeks of observations
* Saved-search diffing pushed by a scheduler (the watch tools are the storage side of this)

## License

MIT. See [LICENSE](LICENSE).
