# PROJECT.md - divar-mcp

**What it is.** An open-source, agent-grade MCP (Model Context Protocol) server for [divar.ir](https://divar.ir), Iran's largest classifieds marketplace. 19 tools, 5 resources and 4 prompts over Divar's public web API: search live listings, read a post in full, value an item, appraise someone else's listing, hunt underpriced stock, watch a search over time, and export bulk rows.

**Why it exists.** No Divar MCP server existed (checked GitHub and web search: the `divar-ir/*` repos are Divar's own internal code-search tooling, not marketplace tools). Divar's official API ("Kenar", کنار دیوار) needs an approved app, an API key and OAuth, which is a partner programme rather than a read path. Everything else on GitHub is Playwright/Selenium page scraping with no MCP surface, no filters and no pagination.

**Status: v0.2.0, working and verified against the live API - 2026-09-21.**

## Verified facts (all checked against the live API, not assumed)

* Public read endpoints, no key, no account: `POST /v8/postlist/w/search`, `POST /v8/postlist/w/filters`, `GET /v8/posts-v2/web/{token}`. Discovered by harvesting Divar's own JS bundles for API paths (`tools/probe8.py`).
* Filters are protobuf-`Any` values: `{"category": {"str": {"value": "<slug>"}}}`, `{"price": {"number_range": {...}}}`, `{"districts": {"repeated_string": {"value": ["208"]}}}` (numeric ids), `{"has-photo": {"boolean": {}}}` (presence = true), `{"brand_model": {"repeated_string": {"value": [...]}}}`.
* `page` in `pagination_data` is **ignored**; real pagination is a cursor (echo back `pagination.data` with `page`/`layer_page` incremented). Verified: pages 1/2/3 return disjoint token sets only when the cursor is echoed.
* Divar's `recent_ads` filter does **not** filter (3h and 7d returned identical token sets) so recency is filtered client-side (`max_age_hours`) from the parsed Persian relative time.
* `category: ROOT` means "everything" in Divar's own payloads but is rejected as a filter value; the client treats ROOT as "no category filter" (verified live: same unfiltered set).
* Category slugs and Persian names harvested from Divar's SEO breadcrumbs; city id map has **378 ids**; **39 city page slugs** validated (a slug is accepted only when the post's `city_id` matches the id queried, so Tehran neighbourhoods and `کل ایران` are rejected).
* Persian digits, Jalali→Gregorian conversion and Toman parsing verified against known Nowruz dates (1403/01/01 = 2024-03-20, 1405/06/31 = 2026-09-22).

## Test state (v0.2.0)

| Suite | Result |
| --- | --- |
| `pytest` (offline: normalization, parsers on real captured fixtures, store, analytics, resolution, export, MCP protocol, structured output, resources, prompts, server subprocess, official SDK interop) | all green on Linux, macOS and Windows, Python 3.10 and 3.12 |
| `DIVAR_LIVE=1 pytest tests/test_live.py` | green against the real divar.ir API (search, pagination, filters, detail, district isolation, price analysis, multi-city, deals, appraisal, breakdown, watch round trip, export, trend, status) |
| `python tools/smoke.py` | 19 tools, 5 resources, 4 prompts; every tool has an outputSchema and annotations; Persian survives the CLI redirect |
| Hermes registration | `hermes mcp test divar` connected, all tools discovered |
| Desktop launcher | `DIVAR search.bat` prints real listings and opens the page in Chrome |

Bugs the tests caught during the build (worth remembering):

* `brand_model` lives at `action_log.server_side_info.info.field/value`, not inside `jli`.
* Divar appends brand/model chips to a post breadcrumb, so the leaf category must be de-duplicated.
* stdio must use `readline()` on the **binary** buffer (pipe iteration buffers, and the Windows ANSI code page mangles Persian).
* `build_data.py` wrote CRLF on Windows, so published JSON differed from a clean checkout (fixed with `newline="\n"` plus `.gitattributes`).
* A search URL containing `&` broke `cmd /c start` (open Chrome directly instead).
* CI on Windows used PowerShell, whose `>` redirection writes UTF-16, so the smoke test lives in `tools/smoke.py` rather than a shell pipeline.
* Two harvesters writing the same output file interleaved; stop the running one first.

## Layout

```
src/divar_mcp/client.py      HTTP, politeness, caching, all response parsing
src/divar_mcp/normalize.py   Persian digits, prices, relative time, Jalali dates
src/divar_mcp/datasets.py    bundled cities / categories / city page slugs
src/divar_mcp/resolve.py     forgiving input resolution with did-you-mean suggestions
src/divar_mcp/analytics.py   percentiles, deal scoring, appraisal, district breakdown, trends
src/divar_mcp/store.py       SQLite: listing observations, price history, watches
src/divar_mcp/tools.py       the 19 tools + input/output schemas + MCP annotations
src/divar_mcp/server.py      zero-dependency MCP stdio server (JSON-RPC 2.0, resources, prompts)
src/divar_mcp/cli.py         human CLI (`divar search|price|deals|appraise|watch|export|status`)
src/divar_mcp/data/          harvested cities.json, categories.json, city_slugs.json
launch/divar_search.py       interactive search (what the .bat runs)
launchers/DIVAR search.bat   one-click launcher (Desktop copy too)
tests/                       offline suite + real-payload fixtures + live suite
tools/                       probes, harvesters, smoke test, publish verifier
```

## Rebuild / maintain

```bash
python tools/harvest_cities.py                   # ~400 probes, 2s spacing
python tools/harvest_categories.py               # category tree from SEO breadcrumbs
python tools/harvest_city_slugs.py --max-id 60   # validates city_id before accepting a slug
python tools/build_data.py                       # -> src/divar_mcp/data/*.json (LF)
pytest && DIVAR_LIVE=1 pytest tests/test_live.py
python tools/smoke.py && uv tool install --force --from . divar-mcp
python tools/verify_publish.py                   # git blob SHAs vs the published repo
```

## Ethics / constraints baked in

Read-only for Divar (no posting, editing, messaging or reporting; the only writes are local files). No phone numbers or contact data (no `GetContactWeb`, no auth bypass, no PII harvesting). Rate-limited well under Divar's throttle. Unofficial and labelled as such. Not affiliated with Divar.

## Performance work (2026-09-21)

Latency to divar.ir is dominated by a TCP+TLS handshake per request plus the
polite 0.8s gap between requests. Changes, all measured with `tools/bench.py`:

1. **Keep-alive sockets.** One HTTP connection per thread, held across requests;
   a closed idle socket is detected and reconnected transparently.
2. **Shared client.** `build_client()` returns a singleton for the long-lived MCP
   process, so sockets and cache survive between tool calls instead of a new
   client (and a new handshake) per call.
3. **Response cache** with a request-start-stamped TTL, disabled by
   `DIVAR_CACHE_TTL=0`.
4. **Store in WAL mode** with `synchronous=NORMAL` and one commit per page
   instead of a per-row fsync (Windows fsync was the slow part).
5. **Retry backoff trimmed** from `min(6s, 1.2*2^n)` to `min(3s, 0.6*2^n)`, so a
   stalled line fails over in seconds instead of tens of seconds.
6. `brief_posts` made static: a whole client was being constructed just to
   re-project rows.

Benchmark modes: `--mode before` (fresh client, urllib, no cache),
`--mode transport` (keep-alive, cache off), `--mode warm` (keep-alive + cache).
Cold figures are medians of `--repeat` samples because this line is noisy.

Bugs found while measuring:

- **Cache TTL was stamped at completion**, so `cache_ttl=0` still served hits for
  as long as the request itself had taken (measured: a 0 TTL entry lived 1.5s).
  TTL is now stamped when the request starts, and `0` means no caching at all.
- The test fixture did not drain request bodies, so leftovers corrupted the next
  request on a reused socket. A real server consumes the body; the fixture does now.
- The first benchmark blended a network call with a cache hit into a single
  median, which hid the caching entirely. Cold and warm are now separate columns.
- `divar <cmd> --json` was accepted by the search-shaped commands and rejected by
  the rest, although the rest already print JSON. Now uniform.

## Next steps

* Optional `kenar` backend for authenticated workflows (a user's own listings) when they bring a key
* District-level price history once the store has a few weeks of observations
* A scheduler that pushes watch diffs, built on the existing watch storage
