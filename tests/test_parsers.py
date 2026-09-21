"""Parsers run against real captured divar.ir payloads (tests/fixtures)."""

import json
from pathlib import Path

import pytest

from divar_mcp.client import DivarClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def search_payload():
    return json.loads((FIXTURES / "search_response.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail_jobs():
    return json.loads((FIXTURES / "detail_jobs.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def detail_sale():
    return json.loads((FIXTURES / "detail_sale.json").read_text(encoding="utf-8"))


def test_parse_search_rows(search_payload):
    rows = [
        DivarClient._parse_row(w["data"])
        for w in search_payload["list_widgets"]
        if w["widget_type"] == "POST_ROW"
    ]
    assert len(rows) == 24
    first = rows[0]
    assert first["token"]
    assert first["title"]
    assert first["url"].startswith("https://divar.ir/v/")
    assert first["city"] == "تهران"
    assert first["district"]
    assert first["price_text"]
    # every row must carry a token so downstream tools can fetch it
    assert all(r["token"] for r in rows)
    # time text never contains the district part
    assert not any(" در " in (r["time_text"] or "") for r in rows)


def test_parse_detail_jobs(detail_jobs):
    post = DivarClient._parse_detail(detail_jobs)
    assert post["token"] == "gaxWZCEz"
    assert post["title"] == "استخدام منشی  و دستیار خانم"
    assert post["category"] == "administration-and-hr"
    assert post["city"] == "تهران"
    assert post["city_id"] == "1"
    assert post["posted_at"] == "2026-09-22T00:06:00+03:30"
    assert post["updated_at"] == "2026-09-22T00:06:00+03:30"
    assert post["relative_time"] in ("دقایقی پیش", "لحظاتی پیش")
    assert post["attributes"]["عنوان شغلی"] == "منشی | دستیار"
    assert post["images"] and post["images"][0]["url"].startswith("https://")
    assert post["district_id"] == "208"
    assert post["category_path"][0]["slug"] == "jobs"
    assert post["category_path"][1]["slug"] == "administration-and-hr"
    assert post["chat_enabled"] is True


def test_parse_detail_sale(detail_sale):
    post = DivarClient._parse_detail(detail_sale)
    assert post["token"] == "gaxi5lYL"
    assert post["category"] == "mobile-phones"
    assert post["price_toman"] == 12000000
    assert post["price_human"] == "12.0 میلیون تومان"
    assert post["brand_model"] == "samsung galaxy s21 5g"
    assert post["status"] == "used"
    assert len(post["images"]) == 2
    assert post["attributes"]["برند و مدل"] == "سامسونگ Galaxy S21 5G"
    # breadcrumb stops at the leaf category (brand chips repeat the leaf slug)
    assert [c["slug"] for c in post["category_path"]] == [
        "electronic-devices",
        "mobile-tablet",
        "mobile-phones",
    ]
    # the public endpoint never leaks seller contact details
    import re

    blob = json.dumps(post, ensure_ascii=False)
    assert not re.search(r"0?9\d{9}|09\d{2}[\s-]?\d{3}", blob)
    assert "contact_uuid" not in blob


def test_search_body_shape():
    body = DivarClient._build_search_body(
        "1",
        query="پژو",
        category="light",
        price_min=100000,
        price_max=500000,
        district_ids=[208, "209"],
        has_photo=True,
        brand_model="samsung galaxy s21 5g",
        page=2,
        page_size=30,
    )
    data = body["search_data"]["form_data"]["data"]
    assert body["city_ids"] == ["1"]
    assert body["search_data"]["query"] == "پژو"
    assert data["category"] == {"str": {"value": "light"}}
    assert data["price"] == {"number_range": {"minimum": 100000, "maximum": 500000}}
    assert data["districts"] == {"repeated_string": {"value": ["208", "209"]}}
    assert data["has-photo"] == {"boolean": {}}
    assert data["brand_model"] == {"repeated_string": {"value": ["samsung galaxy s21 5g"]}}
    assert body["pagination_data"]["page"] == 2
    assert body["pagination_data"]["page_size"] == 30


def test_search_body_omits_empty_filters():
    body = DivarClient._build_search_body("1")
    assert "search_data" not in body
    assert body["pagination_data"]["page"] == 1


def test_city_resolution():
    client = DivarClient()
    assert client.resolve_city("1") == ("1", "تهران")
    assert client.resolve_city("تهران") == ("1", "تهران")
    assert client.resolve_city(None)[0] == "1"
    assert client.resolve_city("مشهد")[0] == "3"
    with pytest.raises(Exception):
        client.resolve_city("Atlantis")


def test_sort_posts_client_side():
    posts = [
        {"token": "a", "price_toman": 300, "age_hours": 5},
        {"token": "b", "price_toman": None, "age_hours": 1},
        {"token": "c", "price_toman": 100, "age_hours": 9},
    ]
    assert [p["token"] for p in DivarClient._sort_posts(posts, "price_asc")] == ["c", "a", "b"]
    assert [p["token"] for p in DivarClient._sort_posts(posts, "price_desc")] == ["a", "c", "b"]
    assert [p["token"] for p in DivarClient._sort_posts(posts, "newest")] == ["b", "a", "c"]


def test_caching_avoids_second_network_call():
    client = DivarClient()
    calls = {"n": 0}

    def fake_attempt(method, url, payload):
        calls["n"] += 1
        return {"list_widgets": [], "pagination": {"has_next_page": False}}

    client._attempt = fake_attempt  # type: ignore[assignment]
    client.search(city="1", query="x")
    client.search(city="1", query="x")
    assert calls["n"] == 1
    # a different query must not be served from the cache
    client.search(city="1", query="y")
    assert calls["n"] == 2


def test_client_errors_fail_fast_without_retry():
    client = DivarClient(max_retries=3, min_interval=0.0)
    calls = {"n": 0}

    def fake_attempt(method, url, payload):
        calls["n"] += 1
        from divar_mcp.client import DivarError

        raise DivarError("invalid category: nope", status=400, code=3)

    client._attempt = fake_attempt  # type: ignore[assignment]
    with pytest.raises(Exception) as exc:
        client.search(city="1", category="nope")
    assert "invalid category" in str(exc.value)
    assert calls["n"] == 1  # no pointless retries on a 4xx


def test_search_url_uses_ascii_path_segment():
    """divar.ir 404s on a Persian city name in the path; it wants a real slug."""
    from divar_mcp.tools import divar_search_url

    payload = divar_search_url(query="پژو ۲۰۶", city="تهران", category="light")
    assert payload["verified"] is True
    path = payload["url"].split("divar.ir")[1].split("?")[0]
    assert path.isascii(), f"non-ascii city segment in {path!r}"
    assert path.startswith("/s/tehran")
    assert "%" in payload["url"]  # the query itself is percent-encoded
    assert divar_search_url(query="x", city="3")["city_path_segment"] == "mashhad"


def test_search_url_refuses_unverified_city():
    """No guessed links: a city without a validated slug returns url=None."""
    from divar_mcp.tools import divar_search_url

    payload = divar_search_url(query="x", city="9999")
    # 9999 is not in the harvested slug map, but the client resolves unknown ids
    # to themselves, so this must not produce a fabricated link.
    assert payload["url"] is None
    assert payload["verified"] is False
    assert "no verified" in payload["note"]


def test_price_stats_math():
    client = DivarClient()
    rows = [
        {"token": f"t{i}", "title": f"p{i}", "price_toman": value, "price_human": None,
         "district": "x", "time_text": "دقایقی پیش", "age_hours": 0, "url": "u"}
        for i, value in enumerate([100, 200, 300, 400, 500])
    ]
    client.search_many = lambda **kwargs: {"posts": rows, "city": "تهران", "city_id": "1"}  # type: ignore
    stats = client.price_stats(query="x", pages=1)
    assert stats["priced_posts"] == 5
    assert stats["min"] == 100 and stats["max"] == 500
    assert stats["median"] in (200, 300)
    assert stats["suggested_ask_range"][0] <= stats["suggested_ask_range"][1]
