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
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from . import config
from .lifecycle import is_concluded
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
        self._staging = self.path.with_suffix("")  # plain JSONL until close gzips it
        self._fh = open(self._staging, "a", encoding="utf-8")
        self.count = 0
        log.info("Raw landing file: %s", self.path)

    def write(self, raw_row: dict[str, Any]) -> None:
        self._fh.write(json.dumps(raw_row, ensure_ascii=False, default=str) + "\n")
        self.count += 1

    def flush(self) -> None:
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except Exception as e:  # pragma: no cover
            log.warning("RawWriter.flush failed: %s", e)

    def close(self) -> None:
        try:
            self._fh.close()
            with open(self._staging, "rb") as src, gzip.open(self.path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            self._staging.unlink(missing_ok=True)
        except Exception as e:
            log.warning("RawWriter.close failed for %s: %s", self.path, e)


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
    "branch_id", "branch_name", "location", "location_name", "province",
    "auction_name", "auction_id", "auction_date", "auction_datetime_display",
    "auction_datetime_utc", "auction_type", "auction_type_id", "auction_type_desc",
    "auction_branch_id", "auction_branch_name", "auction_status_id",
    "is_auction_closed", "is_regular_auction", "auction_offsite",
    "lane", "sequence", "is_timed_auction", "timed_auction_status",
    "buy_now_price", "buy_now_offer_price", "timed_buy_now_price",
    "high_prebid", "timed_high_bid", "winning_bid", "currency",
    "source",
    "status", "item_status_desc", "prebid_item_status_desc", "prebid_item_status_id",
    "prebid_allowed", "prebid_closed", "buy_now_status", "is_buy_now", "buy_now_allowed",
    "final_price", "bid_closes_at", "server_observed_at", "last_price_at",
    "status_updated_at", "delisted_at", "image_url",
    "first_seen", "last_seen", "last_changed", "raw",
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
    location_name         TEXT,
    province              TEXT,
    auction_name          TEXT,
    auction_id            INTEGER,
    auction_date          TEXT,
    auction_datetime_display TEXT,
    auction_datetime_utc  TEXT,
    auction_type          TEXT,
    auction_type_id       INTEGER,
    auction_type_desc     TEXT,
    auction_branch_id     INTEGER,
    auction_branch_name   TEXT,
    auction_status_id     INTEGER,
    is_auction_closed     INTEGER,
    is_regular_auction    INTEGER,
    auction_offsite       INTEGER,
    lane                  TEXT,
    sequence              INTEGER,
    is_timed_auction      INTEGER,
    timed_auction_status  TEXT,
    buy_now_price         REAL,
    buy_now_offer_price   REAL,
    timed_buy_now_price   REAL,
    high_prebid           REAL,
    timed_high_bid        REAL,
    winning_bid           REAL,
    currency              TEXT DEFAULT 'CAD',
    source                TEXT DEFAULT 'ca.iaai.com',
    status                TEXT DEFAULT 'active',
    item_status_desc      TEXT,
    prebid_item_status_desc TEXT,
    prebid_item_status_id INTEGER,
    prebid_allowed        INTEGER,
    prebid_closed         INTEGER,
    buy_now_status        TEXT,
    is_buy_now            INTEGER,
    buy_now_allowed       INTEGER,
    final_price           REAL,
    bid_closes_at         TEXT,
    server_observed_at    TEXT,
    last_price_at         TEXT,
    status_updated_at     TEXT,
    delisted_at           TEXT,
    image_url             TEXT,
    first_seen            TEXT,
    last_seen             TEXT,
    last_changed          TEXT,
    raw                   TEXT
);

-- Per-run audit so we can see crawl health/freshness over time.
CREATE TABLE IF NOT EXISTS crawl_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT,
    finished_at   TEXT,
    total_canada  INTEGER,
    ontario_seen  INTEGER,
    inserted      INTEGER,
    updated       INTEGER,
    unchanged     INTEGER,
    skipped_bad_rows INTEGER,
    canada_rows_seen INTEGER,
    archived      INTEGER,
    run_type      TEXT DEFAULT 'full',
    pages         INTEGER,
    status        TEXT,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS price_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_number  TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    price_type    TEXT NOT NULL,
    amount        REAL,
    run_id        INTEGER,
    UNIQUE(stock_number, observed_at, price_type)
);
"""

_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_lots_make_model_year ON lots (make, model, year);
CREATE INDEX IF NOT EXISTS idx_lots_branch          ON lots (branch_id);
CREATE INDEX IF NOT EXISTS idx_lots_auction_date    ON lots (auction_date);
CREATE INDEX IF NOT EXISTS idx_lots_province        ON lots (province);
CREATE INDEX IF NOT EXISTS idx_lots_vin             ON lots (vin);
CREATE INDEX IF NOT EXISTS idx_lots_status          ON lots (status);
CREATE INDEX IF NOT EXISTS idx_price_hist_stock     ON price_history (stock_number, observed_at);
"""

# Housekeeping/derived columns excluded from change comparison.
_NO_COMPARE = {
    "stock_number", "first_seen", "last_seen", "last_changed", "source", "raw",
    "status_updated_at", "delisted_at", "last_price_at", "server_observed_at",
}
_COMPARE_COLS = [c for c in _COLUMNS if c not in _NO_COMPARE]

# Tracked price columns -> price_history.price_type
_PRICE_TRACKED: dict[str, str] = {
    "high_prebid": "prebid",
    "timed_high_bid": "timed_bid",
    "buy_now_price": "buy_now",
    "buy_now_offer_price": "buy_now_offer",
    "timed_buy_now_price": "timed_buy_now",
    "winning_bid": "winning",
    "final_price": "final",
}

_MIGRATIONS = {
    "lots": {
        "stock_id": "INTEGER",
        "vin": "TEXT",
        "detail_url": "TEXT",
        "year": "INTEGER",
        "make": "TEXT",
        "model": "TEXT",
        "engine": "TEXT",
        "fuel_type": "TEXT",
        "transmission": "TEXT",
        "odometer": "INTEGER",
        "odometer_unit": "TEXT",
        "odometer_source": "TEXT",
        "primary_damage": "TEXT",
        "secondary_damage": "TEXT",
        "title_brand": "TEXT",
        "title_brand_type": "TEXT",
        "damage_estimate": "REAL",
        "condition_text": "TEXT",
        "runs": "INTEGER",
        "starts": "INTEGER",
        "has_keys": "INTEGER",
        "branch_id": "INTEGER",
        "branch_name": "TEXT",
        "location": "TEXT",
        "location_name": "TEXT",
        "province": "TEXT",
        "auction_name": "TEXT",
        "auction_id": "INTEGER",
        "auction_date": "TEXT",
        "auction_datetime_display": "TEXT",
        "auction_datetime_utc": "TEXT",
        "auction_type": "TEXT",
        "auction_type_id": "INTEGER",
        "auction_type_desc": "TEXT",
        "auction_branch_id": "INTEGER",
        "auction_branch_name": "TEXT",
        "auction_status_id": "INTEGER",
        "is_auction_closed": "INTEGER",
        "is_regular_auction": "INTEGER",
        "auction_offsite": "INTEGER",
        "lane": "TEXT",
        "sequence": "INTEGER",
        "is_timed_auction": "INTEGER",
        "timed_auction_status": "TEXT",
        "buy_now_price": "REAL",
        "buy_now_offer_price": "REAL",
        "timed_buy_now_price": "REAL",
        "high_prebid": "REAL",
        "timed_high_bid": "REAL",
        "winning_bid": "REAL",
        "currency": "TEXT DEFAULT 'CAD'",
        "source": "TEXT DEFAULT 'ca.iaai.com'",
        "status": "TEXT DEFAULT 'active'",
        "item_status_desc": "TEXT",
        "prebid_item_status_desc": "TEXT",
        "prebid_item_status_id": "INTEGER",
        "prebid_allowed": "INTEGER",
        "prebid_closed": "INTEGER",
        "buy_now_status": "TEXT",
        "is_buy_now": "INTEGER",
        "buy_now_allowed": "INTEGER",
        "final_price": "REAL",
        "bid_closes_at": "TEXT",
        "server_observed_at": "TEXT",
        "last_price_at": "TEXT",
        "status_updated_at": "TEXT",
        "delisted_at": "TEXT",
        "image_url": "TEXT",
        "first_seen": "TEXT",
        "last_seen": "TEXT",
        "last_changed": "TEXT",
        "raw": "TEXT",
    },
    "crawl_runs": {
        "unchanged": "INTEGER",
        "skipped_bad_rows": "INTEGER",
        "canada_rows_seen": "INTEGER",
        "archived": "INTEGER",
        "run_type": "TEXT DEFAULT 'full'",
    },
}


def _to_db_value(v: Any) -> Any:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def _normalize_price(v: Any) -> Optional[float]:
    """Normalize a price for stable equality checks (cent precision)."""
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _prices_equal(a: Any, b: Any) -> bool:
    return _normalize_price(a) == _normalize_price(b)


def _apply_lifecycle_preservation(
    existing: dict[str, Any], data: dict[str, Any]
) -> bool:
    """Keep concluded sale outcomes when the source row regresses to active.

    Returns True if a concluded status was preserved (no downgrade applied).
    """
    old_status = existing.get("status")
    new_status = data.get("status")
    if not is_concluded(old_status):
        return False
    if is_concluded(new_status):
        # Still concluded — allow price/status field updates but never clear final_price.
        if data.get("final_price") is None and existing.get("final_price") is not None:
            data["final_price"] = existing["final_price"]
        return False
    # Source row looks active again; keep the stored sale outcome.
    log.info(
        "Preserving concluded status=%s for stock=%s (source row was active)",
        old_status,
        data.get("stock_number"),
    )
    data["status"] = old_status
    data["item_status_desc"] = existing.get("item_status_desc")
    if data.get("final_price") is None:
        data["final_price"] = existing.get("final_price")
    return True


class SqliteStore:
    def __init__(self, db_path: Path = config.DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # WAL improves concurrent read (API) + write (crawler) behaviour.
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.executescript(_DDL)
        self._migrate()
        self.conn.executescript(_INDEX_DDL)
        self.conn.commit()
        self._current_run_id: Optional[int] = None

    def _migrate(self) -> None:
        for table, cols in _MIGRATIONS.items():
            existing = {r[1] for r in self.conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
            for name, decl in cols.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    log.info("migrated: added %s.%s", table, name)

    def close(self) -> None:
        self.conn.close()

    def begin_run(self, started_at: Optional[datetime] = None) -> int:
        """Start a crawl run row and expose its id for price_history linkage."""
        started_at = started_at or _now()
        cur = self.conn.execute(
            "INSERT INTO crawl_runs (started_at, status) VALUES (?, 'running')",
            (started_at.isoformat(),),
        )
        self.conn.commit()
        self._current_run_id = cur.lastrowid
        return self._current_run_id

    def finish_run(self, run_id: int, **fields: Any) -> None:
        """Update the crawl run row with final stats."""
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = [_to_db_value(v) for v in fields.values()]
        values.append(run_id)
        self.conn.execute(f"UPDATE crawl_runs SET {assignments} WHERE id = ?", values)
        self.conn.commit()
        self._current_run_id = None

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
            data["status_updated_at"] = now
            data["delisted_at"] = None
            data["last_price_at"] = self._initial_price_at(data, now)
            self._insert(data)
            self._record_price_changes(None, data, now)
            self._record_status_change(None, data, now)
            return "inserted"

        _apply_lifecycle_preservation(dict(existing), data)
        changed = any(
            _to_db_value(data.get(c)) != existing[c]
            for c in _COMPARE_COLS
            if c not in _PRICE_TRACKED
        ) or any(
            not _prices_equal(data.get(c), existing[c])
            for c in _PRICE_TRACKED
        )
        status_changed = data.get("status") != existing["status"]
        price_changed = self._record_price_changes(dict(existing), data, now)
        if status_changed:
            self._record_status_change(dict(existing), data, now)
        data["first_seen"] = existing["first_seen"]
        data["last_changed"] = now if (changed or price_changed or status_changed) else existing["last_changed"]
        data["status_updated_at"] = now if status_changed else existing["status_updated_at"]
        # Lot reappeared in search — clear delisted marker.
        data["delisted_at"] = None
        data["last_price_at"] = (
            now.isoformat() if price_changed else existing["last_price_at"]
        )
        self._update(data)
        if changed or price_changed or status_changed:
            return "updated"
        return "unchanged"

    def _initial_price_at(self, data: dict[str, Any], now: datetime) -> Optional[str]:
        """Set last_price_at on insert when any price field is present."""
        for col in _PRICE_TRACKED:
            if data.get(col) is not None:
                return now.isoformat()
        return None

    def _record_price_changes(
        self,
        existing: Optional[dict[str, Any]],
        data: dict[str, Any],
        now: datetime,
    ) -> bool:
        """Append price_history rows when tracked price columns change."""
        changed = False
        ts = now.isoformat()
        for col, price_type in _PRICE_TRACKED.items():
            new_val = data.get(col)
            old_val = existing.get(col) if existing else None
            if _prices_equal(new_val, old_val):
                continue
            changed = True
            try:
                self.conn.execute(
                    "INSERT INTO price_history "
                    "(stock_number, observed_at, price_type, amount, run_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        data["stock_number"],
                        ts,
                        price_type,
                        _normalize_price(new_val),
                        self._current_run_id,
                    ),
                )
            except sqlite3.IntegrityError:
                log.debug(
                    "price_history duplicate skipped stock=%s type=%s at %s",
                    data["stock_number"],
                    price_type,
                    ts,
                )
        return changed

    def _record_status_change(
        self,
        existing: Optional[dict[str, Any]],
        data: dict[str, Any],
        now: datetime,
    ) -> None:
        """Append a lifecycle transition to price_history (price_type=status_*)."""
        new_status = data.get("status")
        old_status = existing.get("status") if existing else None
        if existing is not None and new_status == old_status:
            return
        ts = now.isoformat()
        try:
            self.conn.execute(
                "INSERT INTO price_history "
                "(stock_number, observed_at, price_type, amount, run_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    data["stock_number"],
                    ts,
                    f"status_{new_status}",
                    _normalize_price(data.get("final_price")),
                    self._current_run_id,
                ),
            )
        except sqlite3.IntegrityError:
            log.debug(
                "status history duplicate skipped stock=%s status=%s",
                data["stock_number"],
                new_status,
            )

    def archive_missing(self, seen_stock: set[str], now: Optional[datetime] = None) -> int:
        """Mark lots absent from a completed crawl without erasing sale outcomes."""
        now = now or _now()
        ts = now.isoformat()
        rows = self.conn.execute(
            "SELECT stock_number, status FROM lots WHERE delisted_at IS NULL"
        ).fetchall()
        removed = 0
        for row in rows:
            if row["stock_number"] in seen_stock:
                continue
            if row["status"] == "active":
                self.conn.execute(
                    "UPDATE lots SET status = 'removed', status_updated_at = ?, "
                    "delisted_at = ? WHERE stock_number = ?",
                    (ts, ts, row["stock_number"]),
                )
                self._record_status_change(
                    dict(row),
                    {"stock_number": row["stock_number"], "status": "removed", "final_price": None},
                    now,
                )
                removed += 1
            else:
                self.conn.execute(
                    "UPDATE lots SET delisted_at = ? WHERE stock_number = ?",
                    (ts, row["stock_number"]),
                )
        self.conn.commit()
        return removed

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
        """Legacy helper: insert a completed run in one shot (tests)."""
        cols = ", ".join(fields.keys())
        ph = ", ".join(["?"] * len(fields))
        self.conn.execute(
            f"INSERT INTO crawl_runs ({cols}) VALUES ({ph})",
            [_to_db_value(v) for v in fields.values()],
        )
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
            "high_prebid", "timed_high_bid", "final_price", "buy_now_price",
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
    def _csv_lower_in(values: str, column: str, clauses: list[str], params: list[Any]) -> None:
        """Match any of comma-separated values (case-insensitive)."""
        parts = [p.strip() for p in values.split(",") if p.strip()]
        if not parts:
            return
        placeholders = ", ".join(["?"] * len(parts))
        clauses.append(f"LOWER({column}) IN ({placeholders})")
        params.extend(p.lower() for p in parts)

    @staticmethod
    def _build_where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        status = filters.get("status")
        if status and str(status).lower() != "all":
            clauses.append("LOWER(status) = LOWER(?)")
            params.append(str(status))

        # Single-value equality filters.
        eq = {
            "province": "province",
            "title_brand_type": "title_brand_type",
            "auction_type": "auction_type",
            "primary_damage": "primary_damage",
            "secondary_damage": "secondary_damage",
            "stock_number": "stock_number",
        }
        for key, col in eq.items():
            if filters.get(key) is not None:
                clauses.append(f"LOWER({col}) = LOWER(?)")
                params.append(str(filters[key]))

        # Comma-separated multi-value filters (make=toyota,honda).
        if filters.get("make"):
            SqliteStore._csv_lower_in(str(filters["make"]), "make", clauses, params)
        if filters.get("model"):
            SqliteStore._csv_lower_in(str(filters["model"]), "model", clauses, params)

        if filters.get("branch_id") is not None:
            parts = [p.strip() for p in str(filters["branch_id"]).split(",") if p.strip()]
            if parts:
                placeholders = ", ".join(["?"] * len(parts))
                clauses.append(f"branch_id IN ({placeholders})")
                params.extend(int(p) for p in parts)

        if filters.get("year_min") is not None:
            clauses.append("year >= ?"); params.append(filters["year_min"])
        if filters.get("year_max") is not None:
            clauses.append("year <= ?"); params.append(filters["year_max"])
        if filters.get("auction_date_from"):
            clauses.append("auction_date >= ?"); params.append(filters["auction_date_from"])
        if filters.get("auction_date_to"):
            clauses.append("auction_date <= ?"); params.append(filters["auction_date_to"])
        if filters.get("sold_from"):
            clauses.append("status_updated_at >= ?"); params.append(filters["sold_from"])
        if filters.get("sold_to"):
            clauses.append("status_updated_at <= ?"); params.append(filters["sold_to"])

        for col, lo_key, hi_key in (
            ("high_prebid", "high_prebid_min", "high_prebid_max"),
            ("timed_high_bid", "timed_high_bid_min", "timed_high_bid_max"),
            ("final_price", "final_price_min", "final_price_max"),
            ("buy_now_price", "buy_now_price_min", "buy_now_price_max"),
            ("damage_estimate", "damage_estimate_min", "damage_estimate_max"),
            ("odometer", "odometer_min", "odometer_max"),
        ):
            if filters.get(lo_key) is not None:
                clauses.append(f"{col} >= ?"); params.append(filters[lo_key])
            if filters.get(hi_key) is not None:
                clauses.append(f"{col} <= ?"); params.append(filters[hi_key])

        for key, col in (
            ("runs", "runs"),
            ("starts", "starts"),
            ("has_keys", "has_keys"),
            ("is_timed_auction", "is_timed_auction"),
            ("is_auction_closed", "is_auction_closed"),
        ):
            if filters.get(key) is not None:
                clauses.append(f"{col} = ?")
                params.append(1 if filters[key] else 0)

        if filters.get("vin"):
            clauses.append("UPPER(vin) LIKE UPPER(?)")
            params.append(f"%{filters['vin']}%")

        if filters.get("keyword"):
            kw = f"%{filters['keyword']}%"
            clauses.append(
                "(make LIKE ? OR model LIKE ? OR primary_damage LIKE ? "
                "OR secondary_damage LIKE ? OR stock_number LIKE ?)"
            )
            params += [kw, kw, kw, kw, kw]

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return where, params

    def get_price_history(self, stock_number: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT id, stock_number, observed_at, price_type, amount, run_id "
            "FROM price_history WHERE stock_number = ? ORDER BY observed_at, price_type",
            (stock_number,),
        ).fetchall()
        return [dict(r) for r in rows]

    def freshness(self) -> dict[str, Any]:
        cur = self.conn
        last_run = cur.execute(
            "SELECT * FROM crawl_runs WHERE status != 'running' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        last_price = cur.execute(
            "SELECT MAX(observed_at) AS ts FROM price_history"
        ).fetchone()["ts"]
        lots_with_history = cur.execute(
            "SELECT COUNT(DISTINCT stock_number) AS n FROM price_history"
        ).fetchone()["n"]
        return {
            "last_crawl": dict(last_run) if last_run else None,
            "last_price_change_at": last_price,
            "lots_with_price_history": lots_with_history,
        }

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

    def filters(self) -> dict[str, Any]:
        """Distinct facet values present in the DB for API filter dropdowns."""
        cur = self.conn

        def _distinct_text(col: str) -> list[str]:
            rows = cur.execute(
                f"SELECT DISTINCT {col} FROM lots "
                f"WHERE {col} IS NOT NULL AND TRIM({col}) != '' "
                f"ORDER BY LOWER({col})"
            ).fetchall()
            return [str(r[0]) for r in rows]

        years = [
            int(r[0])
            for r in cur.execute(
                "SELECT DISTINCT year FROM lots WHERE year IS NOT NULL ORDER BY year"
            ).fetchall()
        ]
        statuses = [
            dict(r)
            for r in cur.execute(
                "SELECT status, COUNT(*) AS count FROM lots "
                "GROUP BY status ORDER BY count DESC"
            ).fetchall()
        ]
        branches = [
            dict(r)
            for r in cur.execute(
                "SELECT branch_id, branch_name, COUNT(*) AS count FROM lots "
                "WHERE branch_id IS NOT NULL "
                "GROUP BY branch_id ORDER BY count DESC"
            ).fetchall()
        ]
        return {
            "makes": _distinct_text("make"),
            "models": _distinct_text("model"),
            "years": years,
            "provinces": _distinct_text("province"),
            "title_brand_types": _distinct_text("title_brand_type"),
            "auction_types": _distinct_text("auction_type"),
            "primary_damages": _distinct_text("primary_damage"),
            "secondary_damages": _distinct_text("secondary_damage"),
            "statuses": statuses,
            "branches": branches,
        }
