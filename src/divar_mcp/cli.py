"""Human-facing CLI: `divar search "پژو ۲۰۶" --city tehran --limit 10`.

Same code path as the MCP tools, so anything an agent can do you can do by hand.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys

from . import __version__
from .client import DivarError
from .store import get_store
from .tools import (
    divar_appraise_post,
    divar_export,
    divar_find_deals,
    divar_get_post,
    divar_help,
    divar_list_categories,
    divar_list_cities,
    divar_market_breakdown,
    divar_post_filters,
    divar_price_analysis,
    divar_price_trend,
    divar_search,
    divar_search_url,
    divar_similar_posts,
    divar_status,
    divar_watch_check,
    divar_watch_create,
    divar_watch_delete,
    divar_watch_list,
)


def _dump(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))


def _table(posts: list[dict]) -> None:
    print(f"{'price (Toman)':>22}  {'district':<20} {'when':<16} title")
    print("-" * 100)
    for post in posts:
        price = f"{post['price_toman']:,}" if post.get("price_toman") else (post.get("price_text") or "-")
        print(f"{price:>22}  {str(post.get('district') or '-'):<20} "
              f"{str(post.get('time_text') or '-'):<16} {post.get('title')}")
        print(f"{'':>22}  https://divar.ir/v/{post.get('token')}")
    print()


def _human_price_summary(stats: dict) -> None:
    print(f"  sampled           {stats.get('sampled_posts')} listings "
          f"({stats.get('priced_posts')} with a price)")
    for label, key in (("min", "min_human"), ("p25", "p25_human"), ("median", "median_human"),
                       ("p75", "p75_human"), ("max", "max_human"), ("mean", "mean_human")):
        value = stats.get(key)
        if value:
            print(f"  {label:<17} {value}")
    if stats.get("suggested_ask_human"):
        print(f"  ask between       {stats['suggested_ask_human']}")
    for row in (stats.get("by_district") or [])[:6]:
        median = f"{row['median']:,}" if row.get("median") else "-"
        print(f"  district {row['district']:<20} {row['listings']:>3} listings  median {median}")
    print()


def main(argv: list[str] | None = None) -> int:
    from .server import _force_utf8_streams

    _force_utf8_streams()
    parser = argparse.ArgumentParser(prog="divar", description="Read divar.ir listings from the terminal.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_json_flag(p):
        """Accept --json on every subcommand.

        Most commands already emit JSON, so the flag is a no-op there, but a
        uniform CLI is worth more than a strict one: `divar <cmd> --json` must
        never fail with "unrecognized arguments".
        """
        p.add_argument("--json", action="store_true", help="raw JSON (most commands default to it)")
        return p

    def add_search_args(p):
        # accept the search text either positionally or as -q/--query, so both
        # `divar search "پژو"` and `divar watch create --query "پژو"` work
        p.add_argument("query", nargs="?", help="search text (Persian works)")
        p.add_argument("-q", "--query", dest="query_opt", help="same as the positional query")
        p.add_argument("--city", default="تهران", help="id, Persian name or slug")
        p.add_argument("--city2", action="append", dest="cities", help="extra city (repeatable)")
        p.add_argument("--category", help="slug or Persian name, e.g. mobile-phones")
        p.add_argument("--price-min", type=int)
        p.add_argument("--price-max", type=int)
        p.add_argument("--has-photo", action="store_true")
        p.add_argument("--pages", type=int, default=1)
        p.add_argument("--limit", type=int, default=24)
        p.add_argument("--sort", choices=["newest", "price_asc", "price_desc"])
        p.add_argument("--max-age-hours", type=float)
        p.add_argument("--json", action="store_true", help="raw JSON instead of a table")
        p.add_argument("--full", action="store_true", help="every parsed field instead of brief rows")
        return p

    add_search_args(sub.add_parser("search", help="search live listings"))

    post = add_json_flag(sub.add_parser("post", help="show one post in full"))
    post.add_argument("token")

    price = add_search_args(sub.add_parser("price", help="price distribution for comparables"))
    price.set_defaults(pages=2)

    deals = add_search_args(sub.add_parser("deals", help="listings below the market"))
    deals.add_argument("--min-discount", type=float, default=0.05)
    deals.add_argument("--require-photo", action="store_true")
    deals.set_defaults(pages=3)

    appraise = add_json_flag(sub.add_parser("appraise", help="is this listing overpriced?"))
    appraise.add_argument("token")
    appraise.add_argument("--city")
    appraise.add_argument("--pages", type=int, default=2)

    breakdown = add_search_args(sub.add_parser("breakdown", help="price by district"))
    breakdown.set_defaults(pages=2)

    # local history is keyed by city + category + query only, so it takes a
    # narrower argument set than a search subcommand (nothing is silently dropped)
    trend = sub.add_parser("trend", help="local price history")
    trend.add_argument("query", nargs="?", help="search text (Persian works)")
    trend.add_argument("-q", "--query", dest="query_opt")
    trend.add_argument("--city", default="تهران")
    trend.add_argument("--category")
    trend.add_argument("--days", type=int, default=30)
    trend.add_argument("--json", action="store_true")

    similar = add_json_flag(sub.add_parser("similar", help="comparables for a post"))
    similar.add_argument("token")
    similar.add_argument("--city")
    similar.add_argument("--limit", type=int, default=12)

    watch = sub.add_parser("watch", help="saved searches")
    watch_sub = watch.add_subparsers(dest="watch_action", required=True)
    w_create = add_search_args(watch_sub.add_parser("create", help="save a watch"))
    w_create.add_argument("--name", required=True)
    w_check = add_json_flag(watch_sub.add_parser("check", help="new listings since the baseline"))
    w_check.add_argument("--name", required=True)
    w_check.add_argument("--pages", type=int, default=1)
    add_json_flag(watch_sub.add_parser("list", help="list saved watches"))
    w_delete = add_json_flag(watch_sub.add_parser("delete", help="forget a watch"))
    w_delete.add_argument("--name", required=True)

    export = add_search_args(sub.add_parser("export", help="write listings to CSV/JSONL"))
    export.add_argument("--path")
    export.add_argument("--format", choices=["csv", "jsonl"], default="csv")
    export.set_defaults(pages=3)

    cities = add_json_flag(sub.add_parser("cities", help="find city ids"))
    cities.add_argument("query", nargs="?")

    cats = add_json_flag(sub.add_parser("categories", help="find category slugs"))
    cats.add_argument("query", nargs="?")

    filters = add_json_flag(sub.add_parser("filters", help="filters Divar exposes for a city"))
    filters.add_argument("--city", default="تهران")
    filters.add_argument("--category")

    url = add_json_flag(sub.add_parser("url", help="print a divar.ir URL for a search"))
    url.add_argument("query", nargs="?")
    url.add_argument("--city", default="تهران")
    url.add_argument("--category")
    url.add_argument("--price-min", type=int)
    url.add_argument("--price-max", type=int)

    status = add_json_flag(sub.add_parser("status", help="health check"))
    status.add_argument("--no-probe", action="store_true")

    add_json_flag(sub.add_parser("help", help="capability map"))

    store_cmd = sub.add_parser("store", help="local store maintenance")
    store_sub = store_cmd.add_subparsers(dest="store_action", required=True)
    add_json_flag(store_sub.add_parser("stats", help="row counts and file size"))
    prune = add_json_flag(store_sub.add_parser("prune", help="drop old price points"))
    prune.add_argument("--days", type=int, default=180)

    args = parser.parse_args(argv)

    def search_kwargs(a) -> dict:
        """The options every search-shaped subcommand shares."""
        return dict(
            query=getattr(a, "query", None) or getattr(a, "query_opt", None),
            city=getattr(a, "city", "تهران"),
            cities=getattr(a, "cities", None),
            category=getattr(a, "category", None),
            price_min=getattr(a, "price_min", None),
            price_max=getattr(a, "price_max", None),
            has_photo=getattr(a, "has_photo", False),
            pages=getattr(a, "pages", 1),
        )

    def call_tool_fn(fn, **kwargs):
        """Call a tool with only the arguments it actually declares.

        Guards against the class of bug where a shared CLI flag (has_photo) is
        forwarded to a tool that does not take it: the CLI must never crash on
        its own wiring.
        """
        accepted = inspect.signature(fn).parameters
        if any(p.kind is p.VAR_KEYWORD for p in accepted.values()):
            return fn(**kwargs)
        return fn(**{k: v for k, v in kwargs.items() if k in accepted})

    try:
        if args.command == "search":
            result = call_tool_fn(
                divar_search,
                **search_kwargs(args),
                page_size=max(24, args.limit),
                sort=args.sort,
                max_age_hours=args.max_age_hours,
                limit=args.limit,
                brief=not args.full,
            )
            if args.json:
                _dump(result)
            else:
                # Divar's own decorative headline ("all ads in <city> - page 2")
                # is intentionally not printed: it reads like our summary and is
                # misleading. It stays in the structured payload for agents.
                _table(result.get("posts", []))
                print(f"{result.get('count')} listings - cities: {', '.join(result.get('cities') or [])}")
        elif args.command == "post":
            _dump(divar_get_post(args.token))
        elif args.command == "price":
            stats = call_tool_fn(divar_price_analysis, **search_kwargs(args))
            _dump(stats) if args.json else _human_price_summary(stats)
        elif args.command == "deals":
            report = call_tool_fn(
                divar_find_deals,
                **search_kwargs(args), min_discount=args.min_discount,
                require_photo=args.require_photo, limit=args.limit,
            )
            _dump(report)
        elif args.command == "appraise":
            _dump(divar_appraise_post(args.token, city=args.city, pages=args.pages))
        elif args.command == "breakdown":
            _dump(call_tool_fn(divar_market_breakdown, **search_kwargs(args)))
        elif args.command == "trend":
            _dump(call_tool_fn(divar_price_trend, **search_kwargs(args), days=args.days))
        elif args.command == "similar":
            _dump(divar_similar_posts(args.token, city=args.city, limit=args.limit))
        elif args.command == "watch":
            if args.watch_action == "create":
                _dump(call_tool_fn(divar_watch_create, name=args.name, **search_kwargs(args)))
            elif args.watch_action == "check":
                _dump(divar_watch_check(args.name, pages=args.pages))
            elif args.watch_action == "list":
                _dump(divar_watch_list())
            else:
                _dump(divar_watch_delete(args.name))
        elif args.command == "export":
            _dump(call_tool_fn(divar_export, **search_kwargs(args), path=args.path,
                               format=args.format, full=args.full))
        elif args.command == "cities":
            _dump(divar_list_cities(args.query))
        elif args.command == "categories":
            _dump(divar_list_categories(args.query))
        elif args.command == "filters":
            _dump(divar_post_filters(city=args.city, category=args.category))
        elif args.command == "url":
            _dump(divar_search_url(query=args.query, city=args.city, category=args.category,
                                   price_min=args.price_min, price_max=args.price_max))
        elif args.command == "status":
            _dump(divar_status(probe=not args.no_probe))
        elif args.command == "help":
            _dump(divar_help())
        elif args.command == "store":
            _dump(get_store().stats().as_dict() if args.store_action == "stats"
                  else get_store().prune(days=args.days))
        return 0
    except DivarError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False, indent=1), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
