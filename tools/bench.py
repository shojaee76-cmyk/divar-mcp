"""Latency benchmark for divar-mcp, measured against the real divar.ir API.

Three modes, so every claim is measured rather than asserted:

  --mode before     fresh client per call, urllib transport, no cache
                    (this is the pre-optimization behaviour)
  --mode transport  shared client with keep-alive sockets, cache disabled
                    (isolates the TLS/TCP handshake saving)
  --mode warm       shared client with keep-alive sockets and the cache on,
                    reporting the first call and the repeat call separately

Usage:
  python tools/bench.py --mode before
  python tools/bench.py --mode transport --json

Reported per tool: cold (first call, network) and warm (repeat call) seconds,
plus the HTTP requests each one cost.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from divar_mcp import tools as T  # noqa: E402
from divar_mcp.client import DivarClient  # noqa: E402

CASES: list[tuple[str, dict]] = [
    ("divar_search", {"query": "پژو ۲۰۶", "city": "tehran", "limit": 10}),
    ("divar_price_analysis", {"query": "پژو ۲۰۶", "city": "tehran", "pages": 2}),
    ("divar_market_breakdown", {"query": "پژو ۲۰۶", "city": "tehran", "pages": 2}),
    ("divar_find_deals", {"query": "پژو ۲۰۶", "city": "tehran", "pages": 3}),
    ("divar_appraise_post", {"token": "gaxKbqa_", "pages": 2}),
    ("divar_get_post", {"token": "gaxKbqa_"}),
]


def configure(mode: str) -> None:
    os.environ["DIVAR_STORE"] = str(Path(os.environ.get("TEMP", ".")) / "divar-bench.db")
    os.environ.pop("DIVAR_NO_KEEPALIVE", None)
    if mode == "before":
        os.environ["DIVAR_NO_KEEPALIVE"] = "1"
        os.environ["DIVAR_CACHE_TTL"] = "0"
    elif mode == "transport":
        os.environ["DIVAR_CACHE_TTL"] = "0"
    else:
        os.environ["DIVAR_CACHE_TTL"] = "300"
    T.reset_client()


def timed(name: str, args: dict, *, mode: str) -> tuple[float, int]:
    """Run one tool once; return (seconds, http requests)."""
    fn = T.REGISTRY[name]
    client = DivarClient() if mode == "before" else T.build_client()
    before = client.request_count
    start = time.perf_counter()
    fn(client=client, **args)
    return time.perf_counter() - start, client.request_count - before


def cold_median(name: str, args: dict, mode: str, repeat: int) -> tuple[float, int]:
    """Median of N genuine cold calls.

    This line is noisy enough that one sample proves nothing, so every cold
    figure reported in the README is a median. In "warm" mode the cached repeats
    would be ~0s, so the median is only meaningful for before/transport.
    """
    times: list[float] = []
    requests = 0
    for _ in range(max(1, repeat)):
        seconds, count = timed(name, args, mode=mode)
        times.append(seconds)
        requests = max(requests, count)
    return statistics.median(times), requests


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["before", "transport", "warm"], default="warm")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repeat", type=int, default=3, help="cold samples per tool (median reported)")
    a = ap.parse_args()

    configure(a.mode)
    rows = []
    for name, args in CASES:
        cold, cold_req = cold_median(name, args, a.mode, a.repeat)
        # the second identical call should be served from the cache
        warm, warm_req = timed(name, args, mode=a.mode)
        rows.append({
            "tool": name,
            "cold_s": round(cold, 2),  # median of --repeat samples
            "samples": a.repeat,
            "warm_s": round(warm, 2),
            "cold_http": cold_req,
            "warm_http": warm_req,
        })
        if not a.json:
            print(f"{name:<26} cold {cold:>5.2f}s ({cold_req} http)   warm {warm:>5.2f}s ({warm_req} http)")

    total_cold = round(sum(r["cold_s"] for r in rows), 2)
    total_warm = round(sum(r["warm_s"] for r in rows), 2)
    payload = {"mode": a.mode, "total_cold_s": total_cold, "total_warm_s": total_warm, "cases": rows}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
    else:
        print(f"{'TOTAL':<26} cold {total_cold:>5.2f}s           warm {total_warm:>5.2f}s   [{a.mode}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
