"""Two-layer storage.

* RawWriter  - append every parsed lot to a gzipped JSON Lines file, partitioned
               by crawl date. Cheap, append-only audit trail that lets us re-derive
               the DB without re-scraping if the parser changes.
* SqliteStore - normalized, indexed serving layer with idempotent upserts keyed by
               stock_number. Tracks first_seen / last_seen / last_changed so we get
               history-awareness and natural duplicate prevention.
"""
from __future__ import annotations

import gzip
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config
from .models import Lot

log = logging.getLogger("iaai.storage")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Raw landing layer
# --------------------------------------------------------------------------- #
class RawWriter:
    """Appends raw rows as gzipped JSONL: data/raw/YYYY-MM-DD/run-<ts>.jsonl.gz."""

    def __init__(self, base_dir: Path = config.RAW_DIR):
        day = _now().strftime("%Y-%m-%d")
        ts = _now().strftime("%Y%m%dT%H%M%SZ")
        self.dir = base_dir / day
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"run-{ts}.jsonl.gz"
        self._fh = gzip.open(self.path, "wt", encoding="utf-8")
        self.count = 0
        log.info("Raw landing file: %s", self.path)

    def write(self, raw_row: dict[str, Any]) -> None:
        self._fh.write(json.dumps(raw_row, ensure_ascii=False, default=str) + "\n")
        self.count += 1

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# SQLite serving layer
# --------------------------------------------------------------------------- #
# Columns persisted to the lots table (order matters for upsert SQL generation).
_COLUMNS = [
    "stock_number", "stock_id", "vin", "detail_url",
    "year", "make", "model", "engine", "fuel_type", "transmission",
    "odometer", "odometer_unit", "odometer_source",
    "primary_damage", "secondary_damage", "title_brand", "title_brand_type",
    "damage_estimate", "condition_text", "runs", "starts", "has_keys",
    "branch_id", "branch_name", "location", "province",
    "auction_name", "auction_id", "auction_date", "auction_datetime_display",
    "auction_datetime_utc", "auction_type", "lane", "sequence",
    "is_timed_auction", "buy_now_price", "high_prebid", "currency",
    "source", "first_seen", "last_seen", "last_changed", "raw",
]

_DDL = """
CREATE TABLE IF NOT EXISTS lots (
    stock_number          TEXT PRIMARY KEY,
    stock_id              INTEGER,
    vin                   TEXT,
    detail_url            TEXT,
    year                  INTEGER,
    make                  TEXT,
    model                 TEXT,
    engine                TEXT,
    fuel_type             TEXT,
    transmission          TEXT,
    odometer              INTEGER,
    odometer_unit         TEXT,
    odometer_source       TEXT,
    primary_damage        TEXT,
    secondary_damage      TEXT,
    title_brand           TEXT,
    title_brand_type      TEXT,
    damage_estimate       REAL,
    condition_text        TEXT,
    runs                  INTEGER,
    starts                INTEGER,
    has_keys              INTEGER,
    branch_id             INTEGER,
    branch_name           TEXT,
    location              TEXT,
    province              TEXT,
    auction_name          TEXT,
    auction_id            INTEGER,
    auction_date          TEXT,
    auction_datetime_display TEXT,
    auction_datetime_utc  TEXT,
    auction_type          TEXT,
    lane                  TEXT,
    sequence              INTEGER,
    is_timed_auction      INTEGER,
    buy_now_price         REAL,
    high_prebid           REAL,
    currency              TEXT DEFAULT 'CAD',
    source                TEXT DEFAULT 'ca.iaai.com',
    first_seen            TEXT,
    last_seen             TEXT,
    last_changed          TEXT,
    raw                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_lots_make_model_year ON lots (make, model, year);
CREATE INDEX IF NOT EXISTS idx_lots_branch          ON lots (branch_id);
CREATE INDEX IF NOT EXISTS idx_lots_auction_date    ON lots (auction_date);
CREATE INDEX IF NOT EXISTS idx_lots_province        ON lots (province);
CREATE INDEX IF NOT EXISTS idx_lots_vin             ON lots (vin);

-- Per-run audit so we can see crawl health/freshness over time.
CREATE TABLE IF NOT EXISTS crawl_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    finished_at   TEXT,
    total_canada  INTEGER,
    ontario_seen  INTEGER,
    inserted      INTEGER,
    updated       INTEGER,
    pages         INTEGER,
    status        TEXT,
    note          TEXT
);
"""

# Change-tracked columns: if any differ on re-scrape, bump last_changed.
_CHANGE_COLS = ("auction_date", "buy_now_price", "high_prebid", "auction_name", "lane",
                "primary_damage", "secondary_damage")


def _to_db_value(v: Any) -> Any:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


class SqliteStore:
    def __init__(self, db_path: Path = config.DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # WAL improves concurrent read (API) + write (crawler) behaviour.
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(_DDL)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- writes --------------------------------------------------------- #
    def upsert_lot(self, lot: Lot) -> str:
        """Insert or update a lot. Returns 'inserted' | 'updated' | 'unchanged'."""
        now = _now()
        existing = self.conn.execute(
            "SELECT * FROM lots WHERE stock_number = ?", (lot.stock_number,)
        ).fetchone()

        data = lot.model_dump()
        data["last_seen"] = now
        data["source"] = lot.source or "ca.iaai.com"

        if existing is None:
            data["first_seen"] = now
            data["last_changed"] = now
            self._insert(data)
            return "inserted"

        # Determine whether any tracked field changed.
        changed = any(_to_db_value(data.get(c)) != existing[c] for c in _CHANGE_COLS)
        data["first_seen"] = existing["first_seen"]  # preserve original
        data["last_changed"] = now if changed else existing["last_changed"]
        self._update(data)
        return "updated" if changed else "unchanged"

    def _insert(self, data: dict[str, Any]) -> None:
        cols = ", ".join(_COLUMNS)
        placeholders = ", ".join(["?"] * len(_COLUMNS))
        values = [_to_db_value(data.get(c)) for c in _COLUMNS]
        self.conn.execute(f"INSERT INTO lots ({cols}) VALUES ({placeholders})", values)

    def _update(self, data: dict[str, Any]) -> None:
        assignments = ", ".join(f"{c} = ?" for c in _COLUMNS if c != "stock_number")
        values = [_to_db_value(data.get(c)) for c in _COLUMNS if c != "stock_number"]
        values.append(data["stock_number"])
        self.conn.execute(f"UPDATE lots SET {assignments} WHERE stock_number = ?", values)

    def commit(self) -> None:
        self.conn.commit()

    def record_run(self, **fields: Any) -> None:
        cols = ", ".join(fields.keys())
        ph = ", ".join(["?"] * len(fields))
        self.conn.execute(f"INSERT INTO crawl_runs ({cols}) VALUES ({ph})",
                          [_to_db_value(v) for v in fields.values()])
        self.conn.commit()

    # -- reads (used by the API) ---------------------------------------- #
    def get_lot(self, stock_number: str) -> Optional[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM lots WHERE stock_number = ?", (stock_number,)
        ).fetchone()
        return dict(row) if row else None

    def query_lots(self, filters: dict[str, Any], limit: int, offset: int,
                   sort: str = "auction_date", descending: bool = False) -> tuple[list[dict], int]:
        """Filtered, paginated query. Returns (rows, total_matching)."""
        where, params = self._build_where(filters)
        allowed_sort = {
            "auction_date", "year", "make", "model", "odometer",
            "damage_estimate", "last_seen", "stock_number",
        }
        sort_col = sort if sort in allowed_sort else "auction_date"
        direction = "DESC" if descending else "ASC"

        total = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM lots {where}", params
        ).fetchone()["n"]
        rows = self.conn.execute(
            f"SELECT * FROM lots {where} ORDER BY {sort_col} {direction} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [dict(r) for r in rows], total

    @staticmethod
    def _build_where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        eq = {"make": "make", "model": "model", "branch_id": "branch_id",
              "province": "province", "title_brand_type": "title_brand_type",
              "auction_type": "auction_type"}
        for key, col in eq.items():
            if filters.get(key) is not None:
                # Case-insensitive match for text-ish fields.
                clauses.append(f"LOWER({col}) = LOWER(?)")
                params.append(str(filters[key]))
        if filters.get("year_min") is not None:
            clauses.append("year >= ?"); params.append(filters["year_min"])
        if filters.get("year_max") is not None:
            clauses.append("year <= ?"); params.append(filters["year_max"])
        if filters.get("auction_date_from"):
            clauses.append("auction_date >= ?"); params.append(filters["auction_date_from"])
        if filters.get("auction_date_to"):
            clauses.append("auction_date <= ?"); params.append(filters["auction_date_to"])
        if filters.get("runs") is not None:
            clauses.append("runs = ?"); params.append(1 if filters["runs"] else 0)
        if filters.get("keyword"):
            kw = f"%{filters['keyword']}%"
            clauses.append("(make LIKE ? OR model LIKE ? OR primary_damage LIKE ?)")
            params += [kw, kw, kw]
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def stats(self) -> dict[str, Any]:
        cur = self.conn
        total = cur.execute("SELECT COUNT(*) AS n FROM lots").fetchone()["n"]
        by_branch = [dict(r) for r in cur.execute(
            "SELECT branch_name, branch_id, COUNT(*) AS n FROM lots "
            "GROUP BY branch_id ORDER BY n DESC"
        ).fetchall()]
        last_run = cur.execute(
            "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "total_lots": total,
            "by_branch": by_branch,
            "last_run": dict(last_run) if last_run else None,
        }
