"""Local state for divar-mcp: seen listings, price history, saved searches.

SQLite from the standard library, so the zero-dependency rule holds. The store
lives outside the package (user data, not code):

    Windows: %LOCALAPPDATA%\\divar-mcp\\store.db
    other:   ~/.divar-mcp/store.db
    override: DIVAR_STORE=/path/to/store.db  (or ":memory:" in tests)

Three tables:

  posts(token PK, price, city, category, query, title, district, url,
        first_seen, last_seen, seen_count)
  prices(token, day, price, PK(token, day))        -- one row per listing per day
  watch_seen(watch, token, seen_at, PK(watch, token))

Everything is written on a best-effort basis: if the disk is read-only the tools
must still work, so callers swallow store errors.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    token       TEXT PRIMARY KEY,
    price       INTEGER,
    city        TEXT,
    city_id     TEXT,
    category    TEXT,
    query       TEXT,
    title       TEXT,
    district    TEXT,
    url         TEXT,
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    seen_count  INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_posts_last_seen ON posts(last_seen);
CREATE INDEX IF NOT EXISTS idx_posts_category  ON posts(category);

CREATE TABLE IF NOT EXISTS prices (
    token    TEXT NOT NULL,
    day      TEXT NOT NULL,
    price    INTEGER NOT NULL,
    city     TEXT,
    category TEXT,
    query    TEXT,
    PRIMARY KEY (token, day)
);
CREATE INDEX IF NOT EXISTS idx_prices_day ON prices(day);

CREATE TABLE IF NOT EXISTS watches (
    name       TEXT PRIMARY KEY,
    params     TEXT NOT NULL,
    created    TEXT NOT NULL,
    last_check TEXT,
    checks     INTEGER NOT NULL DEFAULT 0,
    new_total  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS watch_seen (
    watch    TEXT NOT NULL,
    token    TEXT NOT NULL,
    seen_at  TEXT NOT NULL,
    PRIMARY KEY (watch, token)
);
"""


def default_store_path() -> Path:
    override = os.environ.get("DIVAR_STORE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_DATA_HOME")
    root = Path(base) / "divar-mcp" if base else Path.home() / ".divar-mcp"
    return root / "store.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    # Tehran is UTC+3:30 year round; day buckets follow the user's local market day
    return datetime.now(timezone(timedelta(hours=3, minutes=30))).date().isoformat()


@dataclass
class StoreStats:
    path: str
    posts: int
    price_days: int
    days_tracked: int
    watches: int
    first_seen: str | None
    last_seen: str | None
    size_bytes: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


class Store:
    def __init__(self, path: str | Path | None = None, *, enabled: bool = True):
        self.enabled = enabled
        self.path = str(path or default_store_path())
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------- plumbing
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if self.path != ":memory:":
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self.path, timeout=10.0)
            self._conn.row_factory = sqlite3.Row
            # WAL + NORMAL: writes stop paying a full fsync per row on Windows,
            # which is what made recording a page of listings slow. Safe here:
            # this is local, append-mostly state that is rebuilt from the API.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=10000")
            self._conn.executescript(SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------- writing
    def record_posts(
        self,
        posts: list[dict],
        *,
        city: str | None = None,
        city_id: str | None = None,
        category: str | None = None,
        query: str | None = None,
    ) -> int:
        """Upsert listings and add today's price point. Returns rows written."""
        if not self.enabled or not posts:
            return 0
        now, day = _now(), _today()
        written = 0
        try:
            cur = self.conn
            for post in posts:
                token = post.get("token")
                if not token:
                    continue
                price = post.get("price_toman")
                cur.execute(
                    """
                    INSERT INTO posts (token, price, city, city_id, category, query, title,
                                       district, url, first_seen, last_seen, seen_count)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,1)
                    ON CONFLICT(token) DO UPDATE SET
                        price      = COALESCE(excluded.price, posts.price),
                        last_seen  = excluded.last_seen,
                        seen_count = posts.seen_count + 1,
                        title      = COALESCE(excluded.title, posts.title)
                    """,
                    (token, price, city, city_id, category, query, post.get("title"),
                     post.get("district"), post.get("url"), now, now),
                )
                if price:
                    cur.execute(
                        "INSERT OR REPLACE INTO prices (token, day, price, city, category, query) VALUES (?,?,?,?,?,?)",
                        (token, day, price, city, category, query),
                    )
                written += 1
            self.conn.commit()
        except sqlite3.Error:
            return written
        return written

    def prune(self, days: int = 180) -> dict:
        """Drop price points (and posts with no points left) older than N days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        try:
            cur = self.conn
            removed = cur.execute("DELETE FROM prices WHERE day < ?", (cutoff,)).rowcount
            orphans = cur.execute(
                "DELETE FROM posts WHERE token NOT IN (SELECT DISTINCT token FROM prices)"
            ).rowcount
            self.conn.commit()
        except sqlite3.Error as exc:  # pragma: no cover
            return {"error": str(exc)}
        return {"price_points_removed": removed, "posts_removed": orphans, "older_than_days": days}

    # -------------------------------------------------------------- history
    def price_history(
        self,
        *,
        city: str | None = None,
        category: str | None = None,
        query: str | None = None,
        days: int = 30,
    ) -> list[dict]:
        """Per-day price stats from observed listings (oldest first)."""
        since = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).date().isoformat()
        sql = "SELECT day, price FROM prices WHERE day >= ?"
        args: list = [since]
        if city:
            sql += " AND city = ?"
            args.append(city)
        if category:
            sql += " AND category = ?"
            args.append(category)
        if query:
            sql += " AND query = ?"
            args.append(query)
        try:
            rows = self.conn.execute(sql + " ORDER BY day", args).fetchall()
        except sqlite3.Error:
            return []

        buckets: dict[str, list[int]] = {}
        for row in rows:
            buckets.setdefault(row["day"], []).append(row["price"])
        series = []
        for day in sorted(buckets):
            values = sorted(buckets[day])
            series.append(
                {
                    "day": day,
                    "listings": len(values),
                    "min": values[0],
                    "p25": _pct(values, 0.25),
                    "median": _pct(values, 0.5),
                    "p75": _pct(values, 0.75),
                    "max": values[-1],
                }
            )
        return series

    def observed_posts(
        self, *, city: str | None = None, category: str | None = None, limit: int = 20
    ) -> list[dict]:
        sql = "SELECT * FROM posts WHERE 1=1"
        args: list = []
        if city:
            sql += " AND city = ?"
            args.append(city)
        if category:
            sql += " AND category = ?"
            args.append(category)
        sql += " ORDER BY last_seen DESC LIMIT ?"
        args.append(max(1, limit))
        try:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]
        except sqlite3.Error:
            return []

    # -------------------------------------------------------------- watches
    def save_watch(self, name: str, params: dict) -> dict:
        try:
            cur = self.conn
            cur.execute(
                "INSERT INTO watches (name, params, created) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET params = excluded.params",
                (name, json.dumps(params, ensure_ascii=False), _now()),
            )
            self.conn.commit()
            existing = cur.execute("SELECT COUNT(*) AS n FROM watch_seen WHERE watch = ?", (name,)).fetchone()
        except sqlite3.Error as exc:
            return {"error": str(exc)}
        return {"name": name, "params": params, "seen_baseline": existing["n"]}

    def list_watches(self) -> list[dict]:
        try:
            rows = self.conn.execute("SELECT * FROM watches ORDER BY name").fetchall()
        except sqlite3.Error:
            return []
        out = []
        for row in rows:
            seen = self.conn.execute(
                "SELECT COUNT(*) AS n FROM watch_seen WHERE watch = ?", (row["name"],)
            ).fetchone()["n"]
            out.append(
                {
                    "name": row["name"],
                    "params": json.loads(row["params"]),
                    "created": row["created"],
                    "last_check": row["last_check"],
                    "checks": row["checks"],
                    "new_total": row["new_total"],
                    "seen": seen,
                }
            )
        return out

    def get_watch(self, name: str) -> dict | None:
        for watch in self.list_watches():
            if watch["name"] == name:
                return watch
        return None

    def delete_watch(self, name: str) -> bool:
        try:
            cur = self.conn
            cur.execute("DELETE FROM watch_seen WHERE watch = ?", (name,))
            removed = cur.execute("DELETE FROM watches WHERE name = ?", (name,)).rowcount
            self.conn.commit()
        except sqlite3.Error:
            return False
        return bool(removed)

    def diff_watch(self, name: str, tokens: list[str], *, update: bool = True) -> list[str]:
        """Tokens this watch has never reported before (marks them seen)."""
        tokens = [t for t in tokens if t]
        if not tokens:
            return []
        try:
            known = {
                row["token"]
                for row in self.conn.execute(
                    "SELECT token FROM watch_seen WHERE watch = ?", (name,)
                ).fetchall()
            }
        except sqlite3.Error:
            return []
        fresh = [t for t in tokens if t not in known]
        if update:
            now = _now()
            try:
                if fresh:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO watch_seen (watch, token, seen_at) VALUES (?,?,?)",
                        [(name, t, now) for t in fresh],
                    )
                # every check counts, even one that found nothing new
                self.conn.execute(
                    "UPDATE watches SET last_check = ?, checks = checks + 1, new_total = new_total + ? WHERE name = ?",
                    (now, len(fresh), name),
                )
                self.conn.commit()
            except sqlite3.Error:
                pass
        return fresh

    # ---------------------------------------------------------------- stats
    def stats(self) -> StoreStats:
        empty = StoreStats(self.path, 0, 0, 0, 0, None, None, 0)
        try:
            posts = self.conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
            points = self.conn.execute("SELECT COUNT(*) AS n FROM prices").fetchone()["n"]
            days = self.conn.execute("SELECT COUNT(DISTINCT day) AS n FROM prices").fetchone()["n"]
            watches = self.conn.execute("SELECT COUNT(*) AS n FROM watches").fetchone()["n"]
            span = self.conn.execute(
                "SELECT MIN(first_seen) AS a, MAX(last_seen) AS b FROM posts"
            ).fetchone()
        except sqlite3.Error:
            return empty
        size = 0
        if self.path != ":memory:" and Path(self.path).exists():
            size = Path(self.path).stat().st_size
        return StoreStats(self.path, posts, points, days, watches, span["a"], span["b"], size)


def _pct(values: list[int], p: float) -> int:
    if not values:
        return 0
    idx = min(len(values) - 1, max(0, int(round(p * (len(values) - 1)))))
    return values[idx]


_default: Store | None = None


def get_store() -> Store:
    """Process-wide store (one SQLite connection reused across tool calls)."""
    global _default
    if _default is None:
        disabled = os.environ.get("DIVAR_STORE_DISABLE", "").lower() in ("1", "true", "yes")
        _default = Store(enabled=not disabled)
    return _default


def reset_store() -> None:
    """Test helper: drop the cached store so the next call re-opens it."""
    global _default
    if _default is not None:
        _default.close()
    _default = None
