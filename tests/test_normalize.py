"""Normalization: Persian digits, prices, relative time, Jalali dates."""

import pytest

from divar_mcp.normalize import (
    extract_token,
    fa_to_en_digits,
    human_toman,
    jalali_to_gregorian,
    parse_age_hours,
    parse_int,
    parse_jalali_datetime,
    parse_price,
)


def test_fa_digits():
    assert fa_to_en_digits("۱۲۳۴۵۶۷۸۹۰") == "1234567890"
    assert fa_to_en_digits("٤٢") == "42"
    assert fa_to_en_digits("") == ""


def test_parse_int_with_separators():
    assert parse_int("۱,۵۰۰,۰۰۰") == 1500000
    assert parse_int("abc") is None


@pytest.mark.parametrize(
    "text,expected,note",
    [
        ("۷۳۰,۰۰۰,۰۰۰ تومان", 730000000, None),
        ("۱,۰۰۰ تومان", 1000, None),
        ("۵۰۰ میلیون تومان", 500000000, None),
        ("۲ میلیارد تومان", 2000000000, None),
        ("توافقی", None, "negotiable"),
        ("پرداخت توافقی", None, "negotiable"),
        ("رایگان", None, "free"),
        ("معاوضه با خودرو", None, "exchange"),
        ("", None, None),
        (None, None, None),
    ],
)
def test_parse_price(text, expected, note):
    assert parse_price(text) == (expected, note)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("دقایقی پیش", 0.0),
        ("لحظاتی پیش", 0.0),
        ("۳۰ دقیقه پیش", 0.5),
        ("۳ ساعت پیش", 3.0),
        ("۲ روز پیش", 48.0),
        ("۱ هفته پیش", 168.0),
        ("دیروز", 24.0),
        ("", None),
        ("چیزی نامعلوم", None),
    ],
)
def test_parse_age_hours(text, expected):
    assert parse_age_hours(text) == expected


def test_jalali_conversion_known_nowruz():
    # Nowruz anchors: 1403 -> 2024-03-20, 1404 -> 2025-03-21
    assert jalali_to_gregorian(1403, 1, 1) == (2024, 3, 20)
    assert jalali_to_gregorian(1404, 1, 1) == (2025, 3, 21)
    assert jalali_to_gregorian(1405, 6, 31) == (2026, 9, 22)


def test_parse_jalali_datetime_iso():
    iso = parse_jalali_datetime("انتشار آگهی: ۳۱ شهریور ۱۴۰۵، ۰۰:۰۶")
    assert iso == "2026-09-22T00:06:00+03:30"
    assert parse_jalali_datetime("nothing here") is None


def test_extract_token_from_urls():
    assert extract_token("gaxi5lYL") == "gaxi5lYL"
    assert extract_token("https://divar.ir/v/gaxi5lYL") == "gaxi5lYL"
    assert extract_token("https://divar.ir/v/آیفون-13-نرمال/gaxi5lYL") == "gaxi5lYL"
    assert extract_token("https://divar.ir/v/gaxi5lYL/?utm=x") == "gaxi5lYL"


def test_human_toman():
    assert human_toman(500000) == "500,000 تومان"
    assert human_toman(12_000_000) == "12.0 میلیون تومان"
    assert human_toman(2_500_000_000) == "2.50 میلیارد تومان"
    assert human_toman(None) is None
