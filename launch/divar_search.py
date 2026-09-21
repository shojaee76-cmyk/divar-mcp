"""Double-click search for divar.ir: prompts, live results table, optional browser open.

Used by launchers/DIVAR search.bat. Also runnable directly:
    python launch/divar_search.py "پژو ۲۰۶" --city تهران
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from divar_mcp.client import DivarError  # noqa: E402
from divar_mcp.tools import divar_search, divar_search_url  # noqa: E402

CITY_ALIASES = {"تهران": "1", "کرج": "2", "مشهد": "3", "اصفهان": "4", "تبریز": "5", "شیراز": "6"}

CHROME_CANDIDATES = [
    os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return value or default


def print_table(posts: list[dict]) -> None:
    print()
    print(f"{'price (Toman)':>22}  {'district':<20} {'when':<16} title")
    print("-" * 100)
    for post in posts:
        price = f"{post['price_toman']:,}" if post.get("price_toman") else (post.get("price_text") or "-")
        print(f"{price:>22}  {str(post.get('district') or '-'):<20} "
              f"{str(post.get('time_text') or '-'):<16} {post.get('title')}")
        print(f"{'':>22}  https://divar.ir/v/{post.get('token')}")
    print()


def open_in_browser(url: str) -> None:
    """Open in the running Chrome (no profile flag, so it lands in the open window)."""
    for chrome in CHROME_CANDIDATES:
        if os.path.exists(chrome):
            try:
                subprocess.Popen([chrome, url])
                return
            except Exception:
                break
    try:
        # never route through `cmd /c start &&`: a URL containing & would be
        # split into extra commands by cmd.exe
        os.startfile(url)  # noqa: S606 - Windows shell open
    except Exception as exc:  # pragma: no cover
        print(f"could not open a browser automatically ({exc}). Open this URL:\n{url}")


def main(argv: list[str]) -> int:
    print("=" * 60)
    print("  DIVAR  -  live search on divar.ir (prices in Toman)")
    print("=" * 60)
    print()

    query = argv[0] if argv and not argv[0].startswith("-") else ask("What are you looking for?")
    if not query:
        print("nothing to search for.")
        return 1

    city = ask("City (Enter = Tehran)", "تهران")
    city_input = CITY_ALIASES.get(city, city)

    budget = ask("Max price in Toman (Enter = no limit)", "")
    price_max = None
    if budget:
        digits = "".join(ch for ch in budget if ch.isdigit())
        if digits:
            price_max = int(digits)
        else:
            print("(could not read that price, ignoring it)")

    print(f"\nsearching divar.ir for '{query}' ...\n")
    try:
        result = divar_search(query=query, city=city_input, price_max=price_max, pages=1, page_size=24, limit=14)
    except DivarError as exc:
        print(f"divar said no: {exc}")
        print("Check the spelling, try a simpler phrase, or another city.")
        return 1

    posts = result.get("posts") or []
    if posts:
        print_table(posts)
        print(f"{len(posts)} listings  |  city: {result.get('city')}  |  newest first")
    else:
        print("no listings matched. Try fewer words, a wider price, or another city.")

    link = divar_search_url(query=query, city=city_input, price_max=price_max)
    if link.get("url"):
        print(f"\nfull results page: {link['url']}")
        if ask("Open that page in your browser? (y/n)", "y").lower().startswith("y"):
            open_in_browser(link["url"])
            print("opened in Chrome.")
    elif posts:
        print("\n(no verified divar.ir page link for this city - use the post links above.)")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
