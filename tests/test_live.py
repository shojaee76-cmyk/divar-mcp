"""Live smoke tests against the real divar.ir API.

Skipped unless DIVAR_LIVE=1, because they need outbound access to divar.ir and
they consume the polite rate budget (a handful of requests only).

    DIVAR_LIVE=1 pytest tests/test_live.py -v
"""

import os
import time

import pytest

from divar_mcp.client import DivarClient, DivarError
from divar_mcp.tools import divar_price_analysis, divar_search

LIVE = os.environ.get("DIVAR_LIVE") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set DIVAR_LIVE=1 to hit the real API")


@pytest.fixture(scope="module")
def client():
    return DivarClient(min_interval=1.5, timeout=30.0, cache_ttl=30.0)


def test_live_search_returns_real_posts(client):
    result = client.search(city="1", query="پژو", page_size=10)
    assert result["count"] > 0
    first = result["posts"][0]
    assert first["token"] and first["url"].startswith("https://divar.ir/v/")
    assert first["title"]
    # query relevance: at least one sampled title mentions the term literally
    assert any("پژو" in (p["title"] or "") for p in result["posts"])


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


def test_live_invalid_category_error_is_clear(client):
    with pytest.raises(DivarError) as exc:
        client.search(city="1", category="definitely-not-a-category")
    assert "invalid category" in str(exc.value)


def test_live_search_url_is_openable(client):
    from divar_mcp.tools import divar_search_url

    payload = divar_search_url(query="پژو ۲۰۶", city="تهران")
    assert payload["url"].startswith("https://divar.ir/s/")
