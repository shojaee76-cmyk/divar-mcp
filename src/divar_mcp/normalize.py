"""Normalization helpers for Divar data.

Everything here is deliberately dependency-free so the MCP server stays a
single-file-installable tool.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------- digits

_PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_DIGIT_MAP = {ord(c): str(i) for i, c in enumerate(_PERSIAN_DIGITS)}
_DIGIT_MAP.update({ord(c): str(i) for i, c in enumerate(_ARABIC_DIGITS)})

# separator variants Divar uses in prices: Persian comma, Arabic comma, thin space
_NUM_CLEAN = str.maketrans({",": "", "،": "", " ": "", "\u200c": "", "\u202f": "", "\u066c": ""})


def fa_to_en_digits(text: str) -> str:
    """Convert Persian/Arabic-Indic digits to ASCII digits."""
    if not text:
        return ""
    return str(text).translate(_DIGIT_MAP)


def parse_int(text: str) -> int | None:
    """Parse an integer from mixed Persian/ASCII text, ignoring separators."""
    if text is None:
        return None
    cleaned = fa_to_en_digits(str(text)).translate(_NUM_CLEAN)
    m = re.search(r"\d+", cleaned)
    return int(m.group()) if m else None


# ---------------------------------------------------------------- price

FREE_WORDS = ("رایگان", "مجانی", "مفت")
NEGOTIABLE_WORDS = ("توافقی", "توافق", "مذاکره", "قابل مذاکره", "تماس")


def parse_price(text: str | None) -> tuple[int | None, str | None]:
    """Parse a Divar price string.

    Returns (price_toman, note). ``note`` is set for non numeric prices:
    'negotiable', 'free', 'exchange' or None. Price is in Toman (not Rial),
    which is what Divar displays to users.
    """
    if not text:
        return None, None
    raw = fa_to_en_digits(str(text))
    low = raw.replace("\u200c", " ")
    if any(w in low for w in FREE_WORDS):
        return None, "free"
    if "معاوضه" in low or "تهاتر" in low:
        return None, "exchange"
    digits = raw.translate(_NUM_CLEAN)
    m = re.search(r"\d+", digits)
    if not m:
        if any(w in low for w in NEGOTIABLE_WORDS):
            return None, "negotiable"
        return None, None
    value = int(m.group())
    if "میلیارد" in low:
        value *= 1_000_000_000
    elif "میلیون" in low:
        value *= 1_000_000
    note = "negotiable" if any(w in low for w in NEGOTIABLE_WORDS) else None
    return value, note


# ---------------------------------------------------------------- time

_AGE_UNITS = (
    ("سال", 365 * 24),
    ("ماه", 30 * 24),
    ("هفته", 7 * 24),
    ("روز", 24),
    ("ساعت", 1),
)

_JUST_NOW = ("لحظاتی پیش", "دقایقی پیش", "همین حالا", "چند لحظه پیش", "کمتر از یک دقیقه پیش")


def parse_age_hours(text: str | None) -> float | None:
    """Best-effort 'how many hours ago' from Divar's relative time text."""
    if not text:
        return None
    t = fa_to_en_digits(str(text))
    if any(w in t for w in _JUST_NOW):
        return 0.0
    m = re.search(r"(\d+)\s*(دقیقه|ساعت|روز|هفته|ماه|سال)", t)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "دقیقه":
            return round(n / 60.0, 2)
        for name, hours in _AGE_UNITS:
            if unit == name:
                return float(n * hours)
    if "دیروز" in t:
        return 24.0
    if "پریروز" in t:
        return 48.0
    return None


# ---------------------------------------------------------------- Jalali

_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    """Convert a Jalali (Solar Hijri) date to Gregorian. Pure arithmetic."""
    jy -= 979
    jm -= 1
    jd -= 1
    j_day_no = 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4
    for i in range(jm):
        j_day_no += (31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29)[i]
    j_day_no += jd
    g_day_no = j_day_no + 79
    gy = 1600 + 400 * (g_day_no // 146097)
    g_day_no %= 146097
    leap = True
    if g_day_no >= 36525:
        g_day_no -= 1
        gy += 100 * (g_day_no // 36524)
        g_day_no %= 36524
        if g_day_no >= 365:
            g_day_no += 1
        else:
            leap = False
    gy += 4 * (g_day_no // 1461)
    g_day_no %= 1461
    if g_day_no >= 366:
        leap = False
        g_day_no -= 1
        gy += g_day_no // 365
        g_day_no %= 365
    months = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    gm = 0
    while gm < 12 and g_day_no >= months[gm]:
        g_day_no -= months[gm]
        gm += 1
    return gy, gm + 1, g_day_no + 1


_JALALI_RE = re.compile(r"(\d{1,2})\s+([^\s،,]+)\s+(\d{4})[،,]?\s*(?:(\d{1,2}):(\d{2}))?")


def parse_jalali_datetime(text: str | None, tz_offset_hours: float = 3.5) -> str | None:
    """Parse '۳۱ شهریور ۱۴۰۵، ۰۰:۲۰' into an ISO-8601 string (Asia/Tehran)."""
    if not text:
        return None
    t = fa_to_en_digits(str(text))
    m = _JALALI_RE.search(t)
    if not m:
        return None
    day, month_name, year = int(m.group(1)), m.group(2), int(m.group(3))
    month = _MONTHS.get(month_name)
    if not month:
        return None
    hour = int(m.group(4) or 0)
    minute = int(m.group(5) or 0)
    gy, gm, gd = jalali_to_gregorian(year, month, day)
    try:
        dt = datetime(gy, gm, gd, hour, minute, tzinfo=timezone(timedelta(hours=tz_offset_hours)))
    except ValueError:
        return None
    return dt.isoformat()


# ---------------------------------------------------------------- misc

def extract_token(value: str) -> str:
    """Accept a bare token or any divar.ir post URL and return the token."""
    if not value:
        return ""
    v = value.strip()
    if "divar.ir" not in v:
        return v.strip("/ ")
    v = v.split("?")[0].rstrip("/")
    parts = [p for p in v.split("/") if p]
    # formats: /v/<slug>/<token>, /v/<token>, /s/<city>/<token>
    return parts[-1] if parts else ""


def human_toman(value: int | None) -> str | None:
    """Format a Toman amount the way Iranians read it."""
    if value is None:
        return None
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} میلیارد تومان"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f} میلیون تومان"
    return f"{value:,} تومان"
