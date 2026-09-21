# PROJECT.md — divar-mcp

**What it is.** An open-source MCP (Model Context Protocol) server for [divar.ir](https://divar.ir), Iran's largest classifieds marketplace. It gives any MCP-capable agent read access to live Divar listings: search with real filters, read a post in full, value an item against current market prices, and list the competition for an existing ad.

**Why it exists.** No Divar MCP server existed (checked GitHub + web search: the `divar-ir/*` repos are Divar's own internal code-search tooling, not marketplace tools). Divar's official API ("Kenar", کنار دیوار) needs an approved app, an API key and OAuth, which is a partner programme, not a read path. Everything else on GitHub is Playwright/Selenium page scraping with no MCP surface, no filters and no pagination. So: built one, tested it against the live API, published it as public MIT.

**Status: v0.1.0, working and verified against the live API — 2026-09-21.**

## Verified facts (all checked against the live API, not assumed)

* Public read endpoints, no key, no account: `POST /v8/postlist/w/search`, `POST /v8/postlist/w/filters`, `GET /v8/posts-v2/web/{token}`. Discovered by harvesting Divar's own JS bundles for API paths (`tools/probe8.py`) and verified by probing.
* Filters are protobuf-`Any` values: `{"category": {"str": {"value": "<slug>"}}}`, `{"price": {"number_range": {...}}}`, `{"districts": {"repeated_string": {"value": ["208"]}}}` (numeric ids), `{"has-photo": {"boolean": {}}}` (presence = true), `{"brand_model": {"repeated_string": {"value": [...]}}}`.
* `page` in `pagination_data` is **ignored**; real pagination is a cursor (echo back `pagination.data` with `page`/`layer_page` incremented). Verified: page 1 vs 2 vs 3 return disjoint token sets only when the cursor is echoed.
* Divar's `recent_ads` filter does **not** filter (3h and 7d returned identical token sets) → implemented client-side `max_age_hours` from the parsed Persian relative time instead.
* Category slugs and Persian names harvested from Divar's own SEO breadcrumbs (`mobile-phones` → `mobile-tablet` → `electronic-devices`), not guessed. Persian digits, Jalali→Gregorian conversion and Toman parsing verified against known Nowruz dates (1403/01/01 = 2024-03-20, 1405/06/31 = 2026-09-22).
* City id ↔ Persian name map harvested: **378 ids** (`tools/harvest_cities.py`).

## Test state

| Suite | Result |
| --- | --- |
| `pytest` (offline: normalization, parsers on real captured fixtures, MCP protocol, server subprocess, official SDK interop) | 56 passed, 0 failed |
| `DIVAR_LIVE=1 pytest tests/test_live.py` | **9 passed in 38.7s** against the real divar.ir API |
| `uvx --from git+… divar-mcp` + Claude/Hermes-style client config | documented in README, stdio verified by subprocess test |

Bugs the tests caught during the build (worth remembering): `brand_model` lives at `action_log.server_side_info.info.field/value`, not inside `jli`; Divar appends brand/model chips to the post breadcrumb, so the leaf category must be de-duplicated; stdio must use `readline()` on the **binary** buffer (pipe iteration buffers and Windows ANSI code page would mangle Persian).

## Layout

```
src/divar_mcp/client.py      HTTP + politeness + caching + all response parsing
src/divar_mcp/normalize.py   Persian digits, prices, relative time, Jalali dates
src/divar_mcp/tools.py       the 8 tools + JSON schemas (shared by MCP and CLI)
src/divar_mcp/server.py      zero-dependency MCP stdio server (JSON-RPC 2.0)
src/divar_mcp/cli.py         human CLI (`divar search ...`)
src/divar_mcp/data/          harvested cities.json + categories.json
tests/                       offline suite + fixtures (real payloads) + live suite
tools/                       reverse-engineering probes and data harvesters (provenance)
```

## Rebuild / maintain

```bash
python tools/harvest_cities.py        # ~400 probes, polite 2s spacing
python tools/harvest_categories.py    # category tree from SEO breadcrumbs
python tools/build_data.py            # -> src/divar_mcp/data/*.json
pytest && DIVAR_LIVE=1 pytest tests/test_live.py
```

## Ethics / constraints baked in

Read-only (no posting, editing, messaging), no phone numbers (no `GetContactWeb`, no auth bypass, no PII), rate-limited well under Divar's throttle, unofficial and clearly labelled as such in the README. Not affiliated with Divar.

## Next steps

* Saved-search diffing for cron ("new listings since yesterday")
* Per-district price maps
* Optional `kenar` backend for authenticated workflows (my listings) when a user brings their own key
