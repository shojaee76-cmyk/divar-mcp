"""Advanced features: local store, analytics, resolution, export, resources.

Everything here runs offline. The store is redirected to a temp file so the
real user store is never touched.
"""

import csv
import json
from pathlib import Path

import pytest

from divar_mcp import analytics
from divar_mcp.client import DivarClient, DivarError
from divar_mcp.resolve import (
    resolve_category_input,
    resolve_city_input,
    suggest_categories,
    suggest_cities,
)
from divar_mcp.store import Store, reset_store


# ------------------------------------------------------------------ fixtures


def make_posts(specs):
    """specs: list of (token, price, age_hours, images, district)"""
    out = []
    for token, price, age, images, district in specs:
        out.append(
            {
                "token": token,
                "title": f"listing {token}",
                "price_toman": price,
                "price_text": f"{price:,} تومان" if price else "توافقی",
                "price_human": None,
                "price_note": None if price else "negotiable",
                "city": "تهران",
                "district": district,
                "time_text": "دقایقی پیش",
                "age_hours": age,
                "image_count": images,
                "url": f"https://divar.ir/v/{token}",
            }
        )
    return out


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "store.db")
    yield s
    s.close()


# ------------------------------------------------------------------ analytics


def test_price_summary_percentiles():
    posts = make_posts([(f"t{i}", p, 1, 3, "x") for i, p in enumerate([10, 20, 30, 40, 50, 60, 70, 80, 90, 100])])
    stats = analytics.price_summary(posts)
    assert stats["priced_posts"] == 10
    assert stats["min"] == 10 and stats["max"] == 100
    assert stats["median"] == 50
    assert stats["suggested_ask_range"] == [stats["p40"], stats["p75"]]
    assert stats["suggested_ask_human"]
    assert sum(stats["price_bands"].values()) == 10


def test_deal_score_prefers_cheap_fresh_photographed():
    median = 100_000_000
    good, reasons, factors = analytics.deal_score(
        make_posts([("a", 60_000_000, 2, 5, "x")])[0], median
    )
    weak, _, _ = analytics.deal_score(make_posts([("b", 98_000_000, 24 * 30, 0, "x")])[0], median)
    assert good > weak
    assert good >= 85
    assert factors["price_vs_median_pct"] == -40.0
    assert any("below the market median" in r for r in reasons)


def test_rank_deals_filters_and_sorts():
    posts = make_posts([
        ("cheap", 50, 1, 4, "a"),
        ("mid", 95, 1, 4, "b"),
        ("pricey", 150, 1, 4, "c"),
        ("nophoto", 40, 1, 0, "d"),
    ])
    report = analytics.rank_deals(posts, median=100, min_discount=0.05, limit=5)
    tokens = [d["token"] for d in report["deals"]]
    assert tokens[0] == "cheap" and "nophoto" in tokens and "pricey" not in tokens
    assert report["caveat"]

    strict = analytics.rank_deals(posts, median=100, require_photo=True)
    assert "nophoto" not in [d["token"] for d in strict["deals"]]


def test_rank_deals_quarantines_implausible_prices():
    """A 1,000 Toman placeholder must not top the deal ranking (median 100 here)."""
    posts = make_posts([
        ("placeholder", 1, 1, 9, "a"),
        ("real-deal", 70, 2, 4, "b"),
        ("normal", 99, 2, 4, "c"),
    ])
    report = analytics.rank_deals(posts, median=100, min_discount=0.05)
    assert [d["token"] for d in report["deals"]] == ["real-deal"]
    assert report["suspicious_count"] == 1
    assert report["suspicious"][0]["token"] == "placeholder"
    assert "placeholder" in report["suspicious"][0]["insight"]
    assert "suspicious" in report["caveat"]


@pytest.mark.parametrize(
    "price,expected",
    [
        (10, "below_market"),
        (80, "fair"),
        (500, "above_market"),
    ],
)
def test_appraise_verdicts(price, expected):
    comps = make_posts([(f"c{i}", p, 1, 2, "x") for i, p in enumerate([20, 40, 60, 80, 100, 120, 140])])
    report = analytics.appraise(price, comps)
    assert report["verdict"] == expected
    assert report["median_price"] == 80
    assert report["confidence"] in ("low", "medium", "high")
    assert report["summary"]


def test_appraise_without_price_or_comps():
    report = analytics.appraise(None, make_posts([("a", 100, 1, 1, "x")]))
    assert report["verdict"] == "not_enough_data"
    report2 = analytics.appraise(100, [])
    assert report2["verdict"] == "not_enough_data"


def test_district_breakdown_sorted_by_stock():
    posts = make_posts([
        ("a", 10, 1, 1, "ونک"), ("b", 20, 1, 1, "ونک"), ("c", 30, 1, 1, "ونک"),
        ("d", 1000, 1, 1, "نیاوران"),
    ])
    rows = analytics.district_breakdown(posts)
    assert rows[0]["district"] == "ونک"
    assert rows[0]["listings"] == 3 and rows[0]["median"] == 20
    assert rows[0]["share_pct"] == 75.0


def test_trend_report_states():
    assert analytics.trend_report([])["status"] == "collecting"
    one_day = [{"day": "2026-09-20", "listings": 5, "min": 1, "p25": 2, "median": 3, "p75": 4, "max": 5}]
    assert analytics.trend_report(one_day)["status"] == "collecting"
    rising = [
        {"day": "2026-09-19", "listings": 5, "min": 1, "p25": 2, "median": 100, "p75": 4, "max": 5},
        {"day": "2026-09-20", "listings": 5, "min": 1, "p25": 2, "median": 150, "p75": 4, "max": 5},
    ]
    report = analytics.trend_report(rising)
    assert report["status"] == "ok" and report["direction"] == "rising" and report["change_pct"] == 50.0
    thin = [
        {"day": "2026-09-19", "listings": 1, "min": 1, "p25": 2, "median": 100, "p75": 4, "max": 5},
        {"day": "2026-09-20", "listings": 1, "min": 1, "p25": 2, "median": 110, "p75": 4, "max": 5},
    ]
    assert analytics.trend_report(thin)["low_confidence"] is True


# ---------------------------------------------------------------------- store


def test_store_records_and_reports_history(store):
    posts = make_posts([("t1", 100, 1, 2, "x"), ("t2", 300, 1, 2, "y")])
    assert store.record_posts(posts, city="تهران", category="mobile-phones", query="iphone") == 2
    # second observation of the same token updates, not duplicates
    store.record_posts([dict(posts[0], price_toman=120)], city="تهران", category="mobile-phones", query="iphone")
    stats = store.stats()
    assert stats.posts == 2
    assert stats.price_days == 2
    series = store.price_history(city="تهران", category="mobile-phones", query="iphone")
    assert len(series) == 1
    # nearest-rank median of two values takes the lower one
    assert series[0]["median"] == 120
    assert store.price_history(query="nothing-matches") == []


def test_store_watch_lifecycle(store):
    store.save_watch("iphone", {"query": "iphone", "city": "تهران"})
    assert [w["name"] for w in store.list_watches()] == ["iphone"]
    assert store.diff_watch("iphone", ["a", "b"], update=True) == ["a", "b"]
    # second call reports only the new token
    assert store.diff_watch("iphone", ["a", "b", "c"], update=True) == ["c"]
    assert store.diff_watch("iphone", ["a", "b", "c"], update=True) == []
    watch = store.get_watch("iphone")
    assert watch["checks"] == 3 and watch["new_total"] == 3 and watch["seen"] == 3
    assert store.delete_watch("iphone") is True
    assert store.list_watches() == []
    assert store.delete_watch("iphone") is False


def test_store_prune_removes_old_points(store):
    store.record_posts(make_posts([("t1", 100, 1, 1, "x")]), city="تهران")
    store.conn.execute("UPDATE prices SET day = '2000-01-01'")
    store.conn.commit()
    result = store.prune(days=30)
    assert result["price_points_removed"] == 1
    assert store.stats().posts == 0


def test_store_disabled_is_silent(tmp_path):
    off = Store(tmp_path / "x.db", enabled=False)
    assert off.record_posts(make_posts([("t", 1, 1, 1, "x")])) == 0
    assert off.price_history() == []
    off.close()


# ------------------------------------------------------------------- resolve


def test_city_resolution_accepts_id_name_and_slug():
    assert resolve_city_input("1")[0] == "1"
    assert resolve_city_input("تهران")[0] == "1"
    assert resolve_city_input("tehran")[0] == "1"
    assert resolve_city_input("MASHHAD")[0] == "3"
    assert resolve_city_input(None)[0] == "1"
    assert resolve_city_input("atlantis") is None


def test_city_suggestions_for_typo():
    suggestions = suggest_cities("tehram")
    assert suggestions and suggestions[0]["name"] == "تهران"
    assert suggest_cities("") == []


def test_category_resolution_and_suggestions():
    assert resolve_category_input("mobile-phones")[0] == "mobile-phones"
    assert resolve_category_input("موبایل")[0] == "mobile-phones"
    assert resolve_category_input("ROOT")[0] is None
    slug, suggestions = resolve_category_input("mobil-phones")
    assert slug == "mobile-phones" or suggestions
    assert suggest_categories("موبایل")


# -------------------------------------------------------------------- export


def test_export_writes_csv_and_jsonl(tmp_path):
    client = DivarClient(min_interval=0.0)
    rows = make_posts([("t1", 100, 1, 2, "ونک"), ("t2", 200, 2, 1, "ونک")])
    client._walk_pages = lambda **kwargs: iter([{  # type: ignore[assignment]
        "page": 1, "rows": rows, "headline": "h", "has_next_page": False,
        "cursor": None, "search_id": "s",
    }])
    csv_path = tmp_path / "out.csv"
    report = client.export_rows(city="1", path=str(csv_path), pages=1)
    assert report["rows"] == 2 and Path(report["path"]).exists()
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "token,title,price_toman" in text
    assert "t1" in text and "t2" in text
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 2

    jsonl_path = tmp_path / "out.jsonl"
    report2 = client.export_rows(city="1", path=str(jsonl_path), pages=1, fmt="jsonl")
    assert report2["format"] == "jsonl"
    lines = [json.loads(l) for l in jsonl_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 2 and lines[0]["token"] == "t1"


# ----------------------------------------------------- client-side filtering


def test_search_client_side_filters(tmp_path, monkeypatch):
    monkeypatch.setenv("DIVAR_STORE", str(tmp_path / "s.db"))
    reset_store()
    client = DivarClient(min_interval=0.0)
    rows = make_posts([("a", 10, 1, 1, "x"), ("b", 20, 100, 1, "x"), ("c", 30, 2, 1, "x")])
    monkeypatch.setattr(client, "_walk_pages", lambda **kwargs: iter([{
        "page": 1, "rows": rows, "headline": "h", "has_next_page": False,
        "cursor": None, "search_id": "s",
    }]))
    from divar_mcp.tools import divar_search

    fresh = divar_search(city="1", max_age_hours=24, client=client)
    assert [p["token"] for p in fresh["posts"]] == ["a", "c"]
    assert fresh["filtered_out_by_age"] == 1

    excluded = divar_search(city="1", exclude_terms=["listing b"], client=client)
    assert "b" not in [p["token"] for p in excluded["posts"]]

    titled = divar_search(city="1", title_contains="listing c", client=client)
    assert [p["token"] for p in titled["posts"]] == ["c"]

    brief = divar_search(city="1", client=client)
    assert "price_text" not in brief["posts"][0]  # brief rows stay compact
    full = divar_search(city="1", brief=False, client=client)
    assert "price_text" in full["posts"][0]
    reset_store()
