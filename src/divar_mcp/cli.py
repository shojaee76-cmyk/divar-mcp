"""Human-facing CLI: `divar search "پژو ۲۰۶" --city tehran --limit 10`.

Same code path as the MCP tools, so anything the agent can do you can do by hand.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .client import DivarError
from .tools import (
    build_client,
    divar_get_post,
    divar_list_categories,
    divar_list_cities,
    divar_post_filters,
    divar_price_analysis,
    divar_search,
    divar_search_url,
    divar_similar_posts,
)


def _dump(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=1, default=str))


def _table(posts: list[dict]) -> None:
    print(f"{'price (Toman)':>16}  {'district':<18} {'when':<14} title")
    print("-" * 96)
    for post in posts:
        price = f"{post['price_toman']:,}" if post.get("price_toman") else (post.get("price_text") or "-")
        print(
            f"{price:>16}  {str(post.get('district') or '-'):<18} "
            f"{str(post.get('time_text') or '-'):<14} {post.get('title')}"
        )
        print(f"{'':>16}  https://divar.ir/v/{post.get('token')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="divar", description="Read divar.ir listings from the terminal.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="search live listings")
    search.add_argument("query", nargs="?", help="free text (Persian)")
    search.add_argument("--city", default="تهران")
    search.add_argument("--category", help="slug, e.g. mobile-phones")
    search.add_argument("--price-min", type=int)
    search.add_argument("--price-max", type=int)
    search.add_argument("--has-photo", action="store_true")
    search.add_argument("--district", action="append", dest="district_ids", help="numeric district id (repeatable)")
    search.add_argument("--pages", type=int, default=1)
    search.add_argument("--limit", type=int, default=24)
    search.add_argument("--sort", choices=["newest", "price_asc", "price_desc"])
    search.add_argument("--json", action="store_true", help="raw JSON instead of a table")

    post = sub.add_parser("post", help="show one post in full")
    post.add_argument("token")

    price = sub.add_parser("price", help="price distribution for comparables")
    price.add_argument("query", nargs="?")
    price.add_argument("--category")
    price.add_argument("--city", default="تهران")
    price.add_argument("--pages", type=int, default=2)

    similar = sub.add_parser("similar", help="comparables for a post")
    similar.add_argument("token")
    similar.add_argument("--city")
    similar.add_argument("--limit", type=int, default=12)

    cities = sub.add_parser("cities", help="find city ids")
    cities.add_argument("query", nargs="?")

    cats = sub.add_parser("categories", help="find category slugs")
    cats.add_argument("query", nargs="?")

    filters = sub.add_parser("filters", help="filters Divar exposes for a city")
    filters.add_argument("--city", default="تهران")
    filters.add_argument("--category")

    url = sub.add_parser("url", help="print a divar.ir URL for a search")
    url.add_argument("query", nargs="?")
    url.add_argument("--city", default="تهران")
    url.add_argument("--category")
    url.add_argument("--price-min", type=int)
    url.add_argument("--price-max", type=int)

    args = parser.parse_args(argv)

    try:
        if args.command == "search":
            result = divar_search(
                query=args.query,
                city=args.city,
                category=args.category,
                price_min=args.price_min,
                price_max=args.price_max,
                has_photo=args.has_photo,
                district_ids=args.district_ids,
                pages=args.pages,
                page_size=max(24, args.limit),
                sort=args.sort,
                limit=args.limit,
            )
            if args.json:
                _dump(result)
            else:
                print(f"{result.get('headline', '')}")
                _table(result.get("posts", []))
                print(f"\n{result.get('count')} listings - city {result.get('city')} ({result.get('city_id')})")
        elif args.command == "post":
            _dump(divar_get_post(args.token))
        elif args.command == "price":
            _dump(divar_price_analysis(query=args.query, category=args.category, city=args.city, pages=args.pages))
        elif args.command == "similar":
            _dump(divar_similar_posts(args.token, city=args.city, limit=args.limit))
        elif args.command == "cities":
            _dump(divar_list_cities(args.query))
        elif args.command == "categories":
            _dump(divar_list_categories(args.query))
        elif args.command == "filters":
            _dump(divar_post_filters(city=args.city, category=args.category))
        elif args.command == "url":
            _dump(
                divar_search_url(
                    query=args.query, city=args.city, category=args.category,
                    price_min=args.price_min, price_max=args.price_max,
                )
            )
        return 0
    except DivarError as exc:
        print(f"divar error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
