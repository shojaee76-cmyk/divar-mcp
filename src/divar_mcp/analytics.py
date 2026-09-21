"""Market math for divar-mcp: percentiles, deal scoring, appraisal, breakdowns, trends.

Pure functions over already-fetched rows, so every one is unit-testable offline
and reusable from the CLI, the MCP tools and any future UI.
"""

from __future__ import annotations

import bisect
from typing import Iterable

TOMANS_PER_MILLION = 1_000_000

BANDS = [
    (1 * TOMANS_PER_MILLION, "زیر ۱ میلیون"),
    (5 * TOMANS_PER_MILLION, "۱-۵ میلیون"),
    (20 * TOMANS_PER_MILLION, "۵-۲۰ میلیون"),
    (100 * TOMANS_PER_MILLION, "۲۰-۱۰۰ میلیون"),
    (500 * TOMANS_PER_MILLION, "۱۰۰-۵۰۰ میلیون"),
    (2_000 * TOMANS_PER_MILLION, "۵۰۰ میلیون - ۲ میلیارد"),
]
BANDS_OVERFLOW = "بیش از ۲ میلیارد"


def band_label(price: int) -> str:
    for ceiling, label in BANDS:
        if price < ceiling:
            return label
    return BANDS_OVERFLOW


def percentile(values: list[int], p: float) -> int | None:
    """Nearest-rank percentile (same convention as the rest of the package)."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(p * (len(ordered) - 1)))))
    return ordered[idx]


def priced(posts: Iterable[dict]) -> list[dict]:
    return [p for p in posts if p.get("price_toman") and p["price_toman"] > 0]


def price_summary(posts: list[dict]) -> dict:
    """Distribution over the priced subset, with human-readable mirrors."""
    from .normalize import human_toman

    rows = priced(posts)
    values = sorted(p["price_toman"] for p in rows)
    stats = {
        "sampled_posts": len(posts),
        "priced_posts": len(values),
        "unpriced_posts": len(posts) - len(values),
        "negotiable_posts": sum(1 for p in posts if p.get("price_note") == "negotiable"),
        "currency": "تومان (Toman)",
        "min": values[0] if values else None,
        "p25": percentile(values, 0.25),
        "p40": percentile(values, 0.40),
        "median": percentile(values, 0.50),
        "p75": percentile(values, 0.75),
        "max": values[-1] if values else None,
        "mean": int(sum(values) / len(values)) if values else None,
    }
    stats["suggested_ask_range"] = (
        [stats["p40"], stats["p75"]] if values and stats["p40"] is not None else None
    )
    stats["suggested_ask_human"] = (
        f"{human_toman(stats['suggested_ask_range'][0])} - {human_toman(stats['suggested_ask_range'][1])}"
        if stats["suggested_ask_range"]
        else None
    )
    for key in ("min", "p25", "p40", "median", "p75", "max", "mean"):
        stats[f"{key}_human"] = human_toman(stats[key])
    if values:
        buckets: dict[str, int] = {}
        for value in values:
            label = band_label(value)
            buckets[label] = buckets.get(label, 0) + 1
        stats["price_bands"] = buckets
    return stats


def freshness(posts: list[dict]) -> dict:
    ages = [p["age_hours"] for p in posts if p.get("age_hours") is not None]
    return {
        "sampled_with_time": len(ages),
        "under_24h": sum(1 for a in ages if a < 24),
        "under_3d": sum(1 for a in ages if a < 24 * 3),
        "under_7d": sum(1 for a in ages if a < 24 * 7),
    }


# ------------------------------------------------------------------ deals


def deal_score(post: dict, median: int | None) -> tuple[float, list[str], dict]:
    """Explainable 0-100 score for "this looks cheap". Higher is better."""
    price = post.get("price_toman")
    factors: dict = {}
    reasons: list[str] = []
    score = 0.0

    if price and median:
        below = (median - price) / median
        advantage = max(0.0, min(1.0, below / 0.35))  # 35% under the median = full marks
        points = 60 * advantage
        score += points
        factors["price_vs_median_pct"] = round(-below * 100, 1)
        factors["price_points"] = round(points, 1)
        if below > 0.02:
            reasons.append(f"{abs(round(below * 100))}% below the market median")

    age = post.get("age_hours")
    if age is not None:
        fresh_points = 20 if age < 24 else 12 if age < 24 * 3 else 6 if age < 24 * 7 else 0
        score += fresh_points
        factors["age_hours"] = age
        factors["freshness_points"] = fresh_points
        if fresh_points == 20:
            reasons.append("posted within 24h")

    images = post.get("image_count") or 0
    image_points = 10 if images >= 3 else 6 if images >= 1 else 0
    score += image_points
    factors["image_count"] = images
    factors["photo_points"] = image_points
    if images >= 3:
        reasons.append(f"{images} photos")

    if price:
        score += 10
        factors["has_price"] = True
        reasons.append("price stated")
    else:
        factors["has_price"] = False

    return round(min(100.0, score), 1), reasons, factors


def rank_deals(
    posts: list[dict],
    *,
    limit: int = 10,
    min_discount: float = 0.05,
    require_photo: bool = False,
    median: int | None = None,
) -> dict:
    """Rank listings by how far below the market they sit.

    Honest guardrails: an unusually low price can mean a broken item, a very old
    model, a typo, or a scam, so every row carries its score factors and the
    caller gets the caveat verbatim.
    """
    rows = priced(posts)
    if median is None:
        median = percentile([p["price_toman"] for p in rows], 0.5)
    candidates = []
    for post in rows:
        if require_photo and not post.get("image_count"):
            continue
        if median:
            discount = (median - post["price_toman"]) / median
            if discount < min_discount:
                continue
        score, reasons, factors = deal_score(post, median)
        entry = dict(post)
        entry["deal_score"] = score
        entry["reasons"] = reasons
        entry["factors"] = factors
        candidates.append(entry)
    candidates.sort(key=lambda p: (-p["deal_score"], p["price_toman"]))
    out = {
        "sampled_posts": len(posts),
        "priced_posts": len(rows),
        "median_price": median,
        "min_discount": min_discount,
        "deal_count": len(candidates),
        "deals": candidates[: max(1, limit)],
        "caveat": (
            "A low price is a signal to check, not a verdict: verify the item, its condition and the "
            "seller. This score only uses price, age and photo count from public listing data."
        ),
    }
    return out


# ------------------------------------------------------------- appraisal


def appraise(price: int | None, comps: list[dict], *, brand_model: str | None = None) -> dict:
    """Judge one asking price against live comparables."""
    rows = priced(comps)
    values = sorted(p["price_toman"] for p in rows)
    result: dict = {
        "asking_price": price,
        "comparable_posts": len(rows),
        "brand_model": brand_model,
    }
    if price is None or not values:
        result["verdict"] = "not_enough_data"
        result["note"] = (
            "This listing has no usable price, or no live comparables were found. Widen the search "
            "(more pages, broader category, nearby cities) and try again."
        )
        return result

    below = bisect.bisect_left(values, price)
    result["percentile"] = round(100.0 * below / len(values), 1)
    result["median_price"] = percentile(values, 0.5)
    result["p25"] = percentile(values, 0.25)
    result["p75"] = percentile(values, 0.75)
    result["min"] = values[0]
    result["max"] = values[-1]
    median = result["median_price"] or price
    result["delta_vs_median_pct"] = round((price - median) / median * 100, 1)

    if price <= (result["p25"] or price):
        verdict = "below_market"
    elif price >= (result["p75"] or price):
        verdict = "above_market"
    else:
        verdict = "fair"
    result["verdict"] = verdict
    result["confidence"] = (
        "high" if len(values) >= 12 else "medium" if len(values) >= 5 else "low"
    )
    result["suggested_price_range"] = [percentile(values, 0.40), percentile(values, 0.75)]
    result["cheaper_alternatives"] = [
        {"token": p["token"], "title": p["title"], "price_toman": p["price_toman"], "url": p.get("url")}
        for p in sorted(rows, key=lambda p: p["price_toman"])[:5]
        if p["price_toman"] < price
    ]
    verdict_text = {
        "below_market": "asking below the live market for these comparables",
        "fair": "asking inside the live market band",
        "above_market": "asking above the live market band",
    }[verdict]
    result["summary"] = (
        f"{verdict_text}: {result['delta_vs_median_pct']:+.1f}% versus the median of "
        f"{len(values)} comparable live listings"
    )
    if result["confidence"] == "low":
        result["note"] = "Few comparables: treat this as a hint, not a valuation."
    return result


# ------------------------------------------------------------- breakdown


def district_breakdown(posts: list[dict], *, min_listings: int = 1) -> list[dict]:
    """Median price per district, busiest district first."""
    groups: dict[str, list[dict]] = {}
    for post in posts:
        district = (post.get("district") or "").strip()
        if not district:
            continue
        groups.setdefault(district, []).append(post)
    rows = []
    total = sum(len(v) for v in groups.values()) or 1
    for district, items in groups.items():
        values = sorted(p["price_toman"] for p in priced(items))
        if len(items) < min_listings:
            continue
        rows.append(
            {
                "district": district,
                "listings": len(items),
                "share_pct": round(100.0 * len(items) / total, 1),
                "priced": len(values),
                "min": values[0] if values else None,
                "median": percentile(values, 0.5),
                "max": values[-1] if values else None,
            }
        )
    rows.sort(key=lambda r: (-r["listings"], r["median"] if r["median"] is not None else 0))
    return rows


# ---------------------------------------------------------------- trends


def trend_report(series: list[dict], *, min_days: int = 2) -> dict:
    """Turn a per-day price series into a direction, honestly."""
    if not series:
        return {
            "status": "collecting",
            "days_tracked": 0,
            "note": (
                "No local price history yet for this filter. Every search this server performs records "
                "today's prices, so run the same query on later days and a real trend will appear."
            ),
        }
    if len(series) < min_days:
        return {
            "status": "collecting",
            "days_tracked": len(series),
            "days_needed": min_days,
            "first_day": series[0]["day"],
            "series": series,
            "note": "Only one day of observations so far; come back tomorrow for a direction.",
        }

    first, last = series[0], series[-1]
    change = None
    if first["median"] and last["median"]:
        change = round((last["median"] - first["median"]) / first["median"] * 100, 1)
    thin = min(first["listings"], last["listings"]) < 3
    if change is None:
        direction = "unknown"
    elif change > 2:
        direction = "rising"
    elif change < -2:
        direction = "falling"
    else:
        direction = "flat"
    return {
        "status": "ok",
        "days_tracked": len(series),
        "first_day": first["day"],
        "last_day": last["day"],
        "change_pct": change,
        "direction": direction,
        "low_confidence": thin,
        "series": series,
        "note": (
            "Thin sample (fewer than 3 priced listings on a day): the direction can be noise."
            if thin
            else "Medians per day over the observed listings for this exact filter."
        ),
    }
