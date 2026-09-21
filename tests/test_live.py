"""Live smoke tests against the real divar.ir API.

Skipped unless DIVAR_LIVE=1, because they need outbound access to divar.ir and
they consume the polite rate budget (a handful of requests only).

    DIVAR_LIVE=1 pytest tests/test_live.py -v

The local store is redirected to a temp file so a test run never mixes with the
observations the user's own searches have collected.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from divar_mcp.client import DivarClient, DivarError
from divar_mcp.store import reset_store
from divar_mcp.tools import (
    divar_appraise_post,
    divar_export,
    divar_find_deals,
    divar_help,
    divar_market_breakdown,
    divar_price_analysis,
    divar_price_trend,
    divar_search,
    divar_status,
    divar_watch_check,
    divar_watch_create,
    divar_watch_delete,
    divar_watch_list,
)

LIVE = os.environ.get("DIVAR_LIVE") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set DIVAR_LIVE=1 to hit the real API")


@pytest.fixture(scope="module")
def client():
    return DivarClient(min_interval=1.5, timeout=30.0, cache_ttl=30.0)


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DIVAR_STORE", str(Path(tmp_path) / "store.db"))
    reset_store()
    yield
    reset_store()


def test_live_search_returns_real_posts(client):
    result = client.search(city="1", query="پژو", page_size=10)
    assert result["count"] > 0
    first = result["posts"][0]
    assert first["token"] and first["url"].startswith("https://divar.ir/v/")
    assert first["title"]
    # query relevance: at least one sampled title mentions the term literally
    assert any("پژو" in (p["title"] or "") for p in result["posts"])
    # the raw client returns every parsed field; the tool layer applies brief
    assert "price_text" in first
    brief = divar_search(city="1", query="پژو", limit=5, client=client)
    assert "price_text" not in brief["posts"][0]
    assert brief["posts"][0]["url"].startswith("https://divar.ir/v/")
    # the local price history records the whole fetched page, not just the rows shown
    assert brief["price_points_recorded"] >= len(brief["posts"])


def test_live_cli_human_output(client):
    """Exercise the non-JSON CLI path, which is what a double-click runs.

    It must print a readable list, and it must NOT print Divar's decorative
    headline (which reads like our own summary: "all ads in <city> - page 2").
    """
    proc = subprocess.run(
        [sys.executable, "-m", "divar_mcp.cli", "search", "پژو", "--city", "tehran", "--limit", "2"],
        capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace",
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 0, proc.stderr[-500:]
    out = proc.stdout
    assert "listing" in out, out[-500:]
    assert "divar.ir/v/" in out
    assert "انواع آگهی" not in out and "صفحه" not in out


def test_live_pagination_pages_are_distinct(client):
    first = client.search(city="1", query="پژو", page=1, page_size=10)
    second = client.search(city="1", query="پژو", page=2, page_size=10)
    assert first["posts"] and second["posts"]
    assert {p["token"] for p in first["posts"]} != {p["token"] for p in second["posts"]}


def test_live_category_filter_is_applied(client):
    result = client.search(city="1", category="mobile-phones", page_size=10)
    assert result["count"] > 0
    assert all(p["token"] for p in result["posts"])


def test_live_price_range_filter(client):
    cheap = client.search(city="1", category="mobile-phones", price_max=5_000_000, page_size=10)
    values = [p["price_toman"] for p in cheap["posts"] if p["price_toman"]]
    assert values, "expected some priced rows"
    assert max(values) <= 5_000_000


def test_live_detail_roundtrip(client):
    found = client.search(city="1", category="mobile-phones", page_size=5)
    token = found["posts"][0]["token"]
    time.sleep(0.5)
    post = client.get_post(token)
    assert post["token"] == token
    assert post["title"]
    assert post["url"].endswith(token)
    assert isinstance(post["attributes"], dict)
    assert post["category"]


def test_live_district_filter_matches(client):
    post = client.get_post(client.search(city="1", category="jobs", page_size=3)["posts"][0]["token"])
    if not post.get("district_id"):
        pytest.skip("no district id on the sampled post")
    time.sleep(0.5)
    result = client.search(city="1", category="jobs", district_ids=[post["district_id"]], page_size=10)
    districts = {p["district"] for p in result["posts"] if p["district"]}
    assert len(districts) == 1, f"district filter leaked other districts: {districts}"


def test_live_price_analysis_shape(client):
    stats = divar_price_analysis(query="آیفون ۱۳", city="1", pages=1, client=client)
    assert stats["sampled_posts"] > 0
    assert stats["priced_posts"] >= 3
    assert stats["min"] <= stats["median"] <= stats["max"]
    assert stats["suggested_ask_range"][0] is not None
    assert "price_bands" in stats
    assert isinstance(stats.get("by_district"), list)


def test_live_invalid_category_error_is_clear(client):
    with pytest.raises(DivarError) as exc:
        client.search(city="1", category="definitely-not-a-category")
    # caught locally with suggestions, so no request was wasted
    assert exc.value.suggestions


# ------------------------------------------------------- v0.2 advanced features


def test_live_multi_city_search(client):
    result = divar_search(city="تهران", cities=["کرج"], query="پژو", pages=1, client=client)
    assert result["count"] > 0
    assert len(result["city_id_list"]) == 2
    cities = {p.get("city") for p in result["posts"] if p.get("city")}
    assert cities, "expected city names on rows"


def test_live_find_deals_scores_are_explainable(client):
    report = divar_find_deals(query="پژو ۲۰۶", city="1", category="light", pages=2,
                              min_discount=0.02, client=client)
    assert report["median_price"]
    assert report["deal_count"] >= 1
    top = report["deals"][0]
    assert 0 <= top["deal_score"] <= 100
    assert top["reasons"] and top["price_toman"] < report["median_price"]
    assert report["caveat"]


def test_live_appraise_verdict_and_confidence(client):
    found = divar_search(category="mobile-phones", city="1", limit=5, client=client)
    token = found["posts"][0]["token"]
    time.sleep(0.5)
    report = divar_appraise_post(token, client=client)
    assert report["verdict"] in ("below_market", "fair", "above_market", "not_enough_data")
    assert report["comparable_posts"] >= 1
    if report["verdict"] != "not_enough_data":
        assert report["confidence"] in ("low", "medium", "high")
        assert report["summary"]


def test_live_market_breakdown_has_districts(client):
    report = divar_market_breakdown(query="پژو", city="1", pages=2, client=client)
    assert report["sampled_posts"] > 5
    assert report["by_district"], "expected at least one district"
    district = report["by_district"][0]
    assert district["listings"] >= 1 and district["district"]


def test_live_watch_round_trip(client):
    created = divar_watch_create(name="live-watch", query="پژو ۲۰۶", city="1", client=client)
    assert created["baseline_listings"] > 0
    first_check = divar_watch_check("live-watch", client=client)
    # the baseline was seeded at create time, so an immediate re-check finds nothing new
    assert first_check["new_listings"] == 0, first_check["new"]
    assert first_check["checked_at_listings"] > 0
    listed = divar_watch_list()
    assert any(w["name"] == "live-watch" for w in listed["watches"])
    assert divar_watch_delete("live-watch")["deleted"] is True


def test_live_export_writes_real_rows(tmp_path, client):
    out = tmp_path / "divar.csv"
    report = divar_export(query="پژو", city="1", pages=2, path=str(out), client=client)
    assert report["rows"] > 10
    text = out.read_text(encoding="utf-8-sig")
    assert "token,title,price_toman" in text
    assert "https://divar.ir/v/" in text


def test_live_price_trend_records_observations(client):
    divar_search(query="پژو", city="1", pages=1, client=client)  # records today's prices
    report = divar_price_trend(query="پژو", city="1", client=client)
    assert report["status"] in ("ok", "collecting")
    if report["status"] == "collecting":
        assert report["days_tracked"] >= 1, "the search above should have recorded one day"
        assert report["note"]


def test_live_status_probe(client):
    report = divar_status(probe=True, client=client)
    assert report["api"]["reachable"] is True
    assert report["datasets"]["cities"] > 100
    assert report["server"]["version"]


def test_live_help_is_static(client):
    payload = divar_help()
    assert payload["recipes"] and payload["units"]["currency"]
