# divar-mcp

**A Model Context Protocol server for [divar.ir](https://divar.ir)** — Iran's largest classifieds marketplace (دیوار). Give any MCP-capable agent the ability to search live listings, read a post in full, and price an item against what is actually on the market right now.

Read-only, no account, no API key, no dependencies. Works from inside Iran and from anywhere else divar.ir's API is reachable.

```
search live listings   →  divar_search
read one post          →  divar_get_post
value an item          →  divar_price_analysis
check the competition  →  divar_similar_posts
```

> ۸ tool برای کار با آگهی‌های دیوار: جست‌وجوی زنده، خواندن کامل آگهی، تحلیل قیمت بازار و پیدا کردن آگهی‌های مشابه. فقط خواندنی، بدون کلید API.

---

## Why this exists

Divar has **no public MCP server**. What exists today:

| Option | Limitation |
| --- | --- |
| **Kenar (کنار دیوار)** — Divar's official API | Needs an approved app + API key + OAuth; that is for building *on* Divar as a business partner, not for reading listings |
| Random scrapers on GitHub | Playwright/Selenium page scraping, no MCP, no filters, no pagination, no price logic |
| This project | Speaks MCP over stdio, uses Divar's own JSON API (the one its web app calls), zero dependencies, 8 tools |

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

**Anything already installed:**

```bash
pip install git+https://github.com/shojaee76-cmyk/divar-mcp
# then:  "command": "divar-mcp"
```

## Tools

| Tool | What it does |
| --- | --- |
| `divar_search` | Live listing search: `query`, `city`, `category`, `price_min`, `price_max`, `has_photo`, `district_ids`, `brand_model`, `sort`, `pages`, `max_age_hours`. Returns normalized rows (token, title, price in Toman, city, district, relative age, image, url). |
| `divar_get_post` | Full post: description, price, structured attributes (برند و مدل, کارکرد, مدل سال…), image URLs, posted/updated dates in both Jalali text and ISO, city/district, category breadcrumb, and the `district_id` you can feed back into search. |
| `divar_price_analysis` | Price distribution of comparable live listings: min / p25 / p40 / median / p75 / max, suggested ask range, price bands, freshness split, cheapest & priciest samples. This is the "what should I ask for my item?" tool. |
| `divar_similar_posts` | Comparables for one post: same category + brand/model + city, excluding itself. |
| `divar_list_cities` | City id ↔ Persian name lookup (400+ ids harvested from the API). |
| `divar_list_categories` | Category slug lookup with Persian names and breadcrumb parents. |
| `divar_post_filters` | Which filters Divar currently exposes for a city/category (price range, districts, photo-only). |
| `divar_search_url` | A `divar.ir/s/...` URL a human can open in a browser for the same search. |

All prices are **Toman** (Divar displays Toman; its internal schema.org price is Rial, this server converts).

## Example agent use

> "My iPhone 13 is in good shape. What is it going for in Tehran right now, and what should I ask?"

The agent calls `divar_list_categories("موبایل")` → `mobile-phones`, then `divar_price_analysis(query="آیفون ۱۳", category="mobile-phones", city="تهران", pages=2)` and answers with the median, the active band, and the freshest comparables with links.

> "Find photo-only Peugeot 206 listings under 900 million Toman in Karaj posted this week."

`divar_search(query="پژو ۲۰۶", city="کرج", price_max=900000000, has_photo=true, max_age_hours=168)`.

## CLI (same engine, for humans and cron)

```bash
divar search "پژو ۲۰۶" --city تهران --price-max 900000000 --has-photo --sort price_asc
divar post https://divar.ir/v/gaxi5lYL
divar price "آیفون ۱۳" --category mobile-phones --pages 3
divar similar gaxi5lYL
divar categories موبایل
divar cities مشهد
divar url "پژو" --city کرج
divar search "لپ‌تاپ" --json | jq '.posts[].price_toman'
```

`divar-mcp --list-tools` prints the raw MCP tool schemas, and
`divar-mcp --call divar_search --args '{"query":"پژو","city":"1"}'` runs one tool without a client.

## How it works

The server talks to the same JSON endpoints `divar.ir` uses in the browser (all discovered from Divar's own web bundles and verified against the live API):

| Endpoint | Use |
| --- | --- |
| `POST /v8/postlist/w/search` | listing search + filters + cursor pagination |
| `POST /v8/postlist/w/filters` | filter schema for a city/category |
| `GET /v8/posts-v2/web/{token}` | single post view |

Details worth knowing if you fork this:

* **Filters are protobuf-Any encoded.** `category` is `{"str": {"value": "<slug>"}}`, `price` is `{"number_range": {"minimum": n, "maximum": n}}`, `districts` is `{"repeated_string": {"value": ["208"]}}` (numeric district ids), `has-photo` is `{"boolean": {}}` (presence = true), `brand_model` is a repeating string.
* **`page` is ignored by the API.** Real pagination is a cursor: echo back `pagination.data` (`last_post_date`, `pelle_max_score`, `filters_hash`, `cumulative_widgets_count`, `page`, `layer_page`) with an incremented `page`/`layer_page`. This server does that for you (`pages=3`).
* **Divar's own recency filter (`recent_ads`) does not filter** — verified: `3h` and `7d` return identical result sets. This server instead parses the Persian relative time per row ("۳ ساعت پیش") and filters client-side via `max_age_hours`.
* **District filter needs numeric ids**, which only appear inside post details (the district chip payload). `divar_get_post` exposes it as `district_id`.
* **Jalali dates are converted** (e.g. `۳۱ شهریور ۱۴۰۵` → `2026-09-22T00:06:00+03:30`) so agents can reason about age without a Hijri library.
* **Category slugs are harvested from Divar's own SEO breadcrumbs** (`mobile-phones` → `mobile-tablet` → `electronic-devices`), so the slug list and Persian names stay real instead of guessed.
* **Web links need a real slug.** `divar.ir/s/tehran` works, a Persian city name in the path does not, and divar.ir serves the same SPA shell for *any* slug — so a slug is published only when it came from Divar's own payload and its `city_id` matched the city asked for. `divar_search_url` returns `url: null` (with a reason) instead of a link that might 404, and `tools/harvest_city_slugs.py` grows the verified map.
* **Divar's own SEO headline is off by one** ("صفحه ۲" on the first page). Harmless, but don't read it as a page number.

## One-click launcher

`launchers/DIVAR search.bat` (double-click, or the Desktop shortcut) asks for a phrase, a city and an optional budget, prints the live listings with prices, and offers to open the matching divar.ir page in Chrome.

## Politeness and legality

* **Read-only.** This server never creates, edits, deletes, reports or messages anything on Divar.
* **No phone numbers.** Divar does not expose seller contact details to logged-out clients; this server does not try to acquire them (no auth bypass, no `GetContactWeb` calls, no PII harvesting).
* **Rate limited.** Default one request per 0.8s with an LRU response cache, far below Divar's ~30 requests/minute throttle. Tune with `DIVAR_MIN_INTERVAL`, `DIVAR_TIMEOUT`, `DIVAR_CACHE_TTL`.
* **Unofficial.** Not affiliated with or endorsed by Divar (Cafe Bazaar / Hezardastan). It reads public listings the same way a browser does. Respect Divar's terms and local law; if Divar ships an official MCP or you need write access (posting ads), use [Kenar](https://divar.ir/kenar).

## Development

```bash
git clone https://github.com/shojaee76-cmyk/divar-mcp && cd divar-mcp
uv venv && uv pip install -e ".[dev]"

pytest                      # offline: parsers, normalization, MCP protocol
DIVAR_LIVE=1 pytest tests/test_live.py -v   # hits the real API
```

The offline suite runs entirely on captured fixtures (`tests/fixtures/*.json`) — real payloads from a live search and two live post views, so the parsers are tested against reality rather than hand-written mocks. The protocol suite also boots the real server process and, when the official `mcp` SDK is installed, drives it with `mcp.client.stdio` to prove interop.

`tools/` holds the reverse-engineering and data-harvesting scripts (`probe*.py`, `harvest_cities.py`, `harvest_categories.py`, `build_data.py`) — run `python tools/build_data.py` after re-harvesting to refresh the bundled city and category data.

## Roadmap

* Saved-search diffing for cron jobs ("new listings since yesterday")
* Per-district price maps
* Optional `kenar` backend for authenticated workflows (my listings, contact details) when a user supplies their own key

## License

MIT. See [LICENSE](LICENSE).
