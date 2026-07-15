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
import tempfile
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
    """Durable raw landing writer with a companion parse-error DLQ.

    ``persist_before_parse`` is intentionally exposed for callers that need to
    land a source row before attempting to parse it.  ``write_dlq`` records a
    durable envelope for malformed rows without making the crawler depend on a
    particular parser error type.
    """

    def __init__(self, base_dir: Path = config.RAW_DIR):
        day = _now().strftime("%Y-%m-%d")
        # Include microseconds so two writers started in one second cannot
        # silently append to one another's run file.
        ts = _now().strftime("%Y%m%dT%H%M%S%fZ")
        self.dir = base_dir / day
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"run-{ts}.jsonl.gz"
        self._staging = self.path.with_suffix("")  # plain JSONL until close gzips it
        self._fh = open(self._staging, "a", encoding="utf-8")
        self.dlq_path = self.dir / f"run-{ts}.dlq.jsonl.gz"
        self._dlq_staging = self.dlq_path.with_suffix("")
        self._dlq_fh = None
        self.count = 0
        self.dlq_count = 0
        self._closed = False
        log.info("Raw landing file: %s", self.path)

    def write(self, raw_row: dict[str, Any]) -> None:
        """Append a successfully handled source row to the raw landing file."""
        self._ensure_open()
        self._fh.write(json.dumps(raw_row, ensure_ascii=False, default=str) + "\n")
        self.count += 1

    def persist_before_parse(self, raw_row: dict[str, Any]) -> None:
        """Land a source row before parsing it (alias kept explicit for callers)."""
        self.write(raw_row)

    def write_dlq(
        self,
        raw_row: Any,
        error: Any,
        *,
        phase: str = "parse",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Append a malformed row and parser error to the durable DLQ."""
        self._ensure_open()
        if self._dlq_fh is None:
            self._dlq_fh = open(self._dlq_staging, "a", encoding="utf-8")
        envelope = {
            "recorded_at": _now().isoformat(),
            "phase": phase,
            "error": str(error),
            "metadata": metadata or {},
            "row": raw_row,
        }
        self._dlq_fh.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
        self.dlq_count += 1

    # Convenient companion-writer spelling for integrations that keep a
    # separate malformed-row path.
    write_malformed = write_dlq

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RawWriter is already closed")

    def flush(self) -> None:
        """Flush and fsync all open landing files; durability failures propagate."""
        self._ensure_open()
        self._fh.flush()
        os.fsync(self._fh.fileno())
        if self._dlq_fh is not None:
            self._dlq_fh.flush()
            os.fsync(self._dlq_fh.fileno())

    def close(self) -> None:
        if self._closed:
            return
        try:
            self.flush()
            self._fh.close()
            if self._dlq_fh is not None:
                self._dlq_fh.close()
            self._compress_durable(self._staging, self.path)
            self._staging.unlink(missing_ok=True)
            if self._dlq_fh is not None:
                self._compress_durable(self._dlq_staging, self.dlq_path)
                self._dlq_staging.unlink(missing_ok=True)
            self._closed = True
        except Exception:
            # Keep the writer open/marked failed so callers cannot mistake a
            # failed close for a successful durable archive.
            log.exception("RawWriter.close failed for %s", self.path)
            raise

    @staticmethod
    def _compress_durable(staging: Path, destination: Path) -> None:
        with open(staging, "rb") as src, gzip.open(destination, "wb") as dst:
            shutil.copyfileobj(src, dst)
        # The gzip trailer is written when the context exits; fsync afterwards
        # so the complete compressed artifact is durable.
        with open(destination, "rb") as dst:
            os.fsync(dst.fileno())


class DeadLetterWriter:
    """Small companion API for integrations that only need a malformed-row DLQ."""

    def __init__(self, raw_writer: RawWriter):
        self.raw_writer = raw_writer

    def write(
        self,
        raw_row: Any,
        error: Any,
        *,
        phase: str = "parse",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        self.raw_writer.write_dlq(raw_row, error, phase=phase, metadata=metadata)

    def flush(self) -> None:
        self.raw_writer.flush()

    def close(self) -> None:
        self.raw_writer.close()


def backup_sqlite(source: Path | str | sqlite3.Connection | "SqliteStore",
                  destination: Path | str) -> Path:
    """Create an atomic SQLite backup using SQLite's online backup API.

    The destination is written beside the requested path and atomically
    replaced only after the backup has committed and been fsynced. This is safe
    while the crawler/API have the source database open (including WAL mode).
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(source, (str, Path)):
        source_path = Path(source)
        if not source_path.is_file():
            raise FileNotFoundError(f"SQLite database does not exist: {source_path}")
        if source_path.resolve() == destination.resolve():
            raise ValueError("backup destination must differ from source")
        src_conn = sqlite3.connect(source_path)
        close_source = True
    elif isinstance(source, sqlite3.Connection):
        src_conn = source
        close_source = False
    else:
        src_conn = source.conn
        close_source = False

    main_db = next(
        (row[2] for row in src_conn.execute("PRAGMA database_list").fetchall() if row[1] == "main"),
        "",
    )
    if main_db and Path(main_db).resolve() == destination.resolve():
        if close_source:
            src_conn.close()
        raise ValueError("backup destination must differ from source")

    tmp_name: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent,
            delete=False,
        ) as tmp:
            tmp_name = tmp.name
        dst_conn = sqlite3.connect(tmp_name)
        try:
            src_conn.backup(dst_conn)
            dst_conn.commit()
            dst_conn.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            dst_conn.close()
        with open(tmp_name, "rb") as fh:
            os.fsync(fh.fileno())
        os.replace(tmp_name, destination)
        # Persist the directory entry as well as the database bytes.
        dir_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return destination
    finally:
        if close_source:
            src_conn.close()
        if tmp_name:
            Path(tmp_name).unlink(missing_ok=True)


# Friendly aliases used by maintenance scripts.
backup_database = backup_sqlite


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
    "first_seen", "last_seen", "last_changed", "raw", "missing_run_count",
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
    raw                   TEXT,
    missing_run_count    INTEGER NOT NULL DEFAULT 0
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
    UNIQUE(stock_number, observed_at, price_type),
    FOREIGN KEY (run_id) REFERENCES crawl_runs(id)
);

CREATE TABLE IF NOT EXISTS lot_status_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_number  TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    old_status    TEXT,
    new_status    TEXT NOT NULL,
    final_price   REAL,
    run_id        INTEGER,
    UNIQUE(stock_number, observed_at, new_status),
    FOREIGN KEY (run_id) REFERENCES crawl_runs(id)
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
CREATE INDEX IF NOT EXISTS idx_lot_status_hist_stock ON lot_status_history (stock_number, observed_at);
"""

# Housekeeping/derived columns excluded from change comparison.
_NO_COMPARE = {
    "stock_number", "first_seen", "last_seen", "last_changed", "source", "raw",
    "status_updated_at", "delisted_at", "last_price_at", "server_observed_at",
    "missing_run_count",
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
        "missing_run_count": "INTEGER NOT NULL DEFAULT 0",
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
        db_path = Path(db_path)
        had_database = db_path.is_file() and db_path.stat().st_size > 0
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        # Enforce relationships for newly-created history rows. This must be
        # enabled before any schema-changing statement.
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        if had_database and self._requires_migration():
            stamp = _now().strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = db_path.parent / "backups" / f"{db_path.stem}-pre-migration-{stamp}.db"
            backup_sqlite(self.conn, backup_path)
            log.warning("Created pre-migration backup: %s", backup_path)
        # WAL improves concurrent read (API) + write (crawler) behaviour. Set
        # it after any required snapshot so the backup precedes persistent DB
        # configuration/schema changes.
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.executescript(_DDL)
        self._migrate()
        self.conn.executescript(_INDEX_DDL)
        self.conn.commit()
        self._current_run_id: Optional[int] = None

    def _requires_migration(self) -> bool:
        tables = {
            row[0] for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table, columns in _MIGRATIONS.items():
            if table not in tables:
                return True
            existing = {
                row[1] for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
            if not set(columns).issubset(existing):
                return True
        return "lot_status_history" not in tables

    def _migrate(self) -> None:
        for table, cols in _MIGRATIONS.items():
            existing = {r[1] for r in self.conn.execute(
                f"PRAGMA table_info({table})").fetchall()}
            for name, decl in cols.items():
                if name not in existing:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    log.info("migrated: added %s.%s", table, name)
        # Existing databases encoded lifecycle transitions in the compatibility
        # price_history table. Preserve those rows in the normalized table.
        self.conn.execute(
            "INSERT OR IGNORE INTO lot_status_history "
            "(stock_number, observed_at, old_status, new_status, final_price, run_id) "
            "SELECT stock_number, observed_at, NULL, substr(price_type, 8), amount, "
            "CASE WHEN run_id IS NOT NULL AND EXISTS "
            "(SELECT 1 FROM crawl_runs cr WHERE cr.id = price_history.run_id) "
            "THEN run_id ELSE NULL END "
            "FROM price_history WHERE price_type LIKE 'status_%'"
        )
        self.conn.execute("UPDATE lots SET missing_run_count = 0 WHERE missing_run_count IS NULL")

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
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = [_to_db_value(v) for v in fields.values()]
        values.append(run_id)
        self.conn.execute(f"UPDATE crawl_runs SET {assignments} WHERE id = ?", values)
        self.conn.commit()
        self._current_run_id = None

    def backup(self, destination: Path | str) -> Path:
        """Create a durable snapshot of this open store."""
        self.conn.commit()
        return backup_sqlite(self.conn, destination)

    # -- writes --------------------------------------------------------- #
    def upsert_lot(self, lot: Lot) -> str:
        """Insert or update a lot. Returns 'inserted' | 'updated' | 'unchanged'."""
        now = _now()
        existing = self.conn.execute(
            "SELECT * FROM lots WHERE stock_number = ?", (lot.stock_number,)
        ).fetchone()

        # Support both Pydantic v2 (production) and v1 (older maintenance
        # environments used for offline replay/tests).
        data = lot.model_dump() if hasattr(lot, "model_dump") else lot.dict()
        data["last_seen"] = now
        data["source"] = lot.source or "ca.iaai.com"

        if existing is None:
            data["first_seen"] = now
            data["last_changed"] = now
            data["status_updated_at"] = now
            data["delisted_at"] = None
            data["missing_run_count"] = 0
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
        # Any observed row breaks a consecutive-miss streak.
        data["missing_run_count"] = 0
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
        """Append a lifecycle transition to normalized and legacy history."""
        new_status = data.get("status")
        old_status = existing.get("status") if existing else None
        if existing is not None and new_status == old_status:
            return
        ts = now.isoformat()
        old_status = existing.get("status") if existing else None
        try:
            self.conn.execute(
                "INSERT INTO lot_status_history "
                "(stock_number, observed_at, old_status, new_status, final_price, run_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    data["stock_number"], ts, old_status, new_status,
                    _normalize_price(data.get("final_price")), self._current_run_id,
                ),
            )
        except sqlite3.IntegrityError:
            log.debug(
                "status history duplicate skipped stock=%s status=%s",
                data["stock_number"], new_status,
            )
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

    def archive_missing(
        self,
        seen_stock: set[str],
        now: Optional[datetime] = None,
        *,
        commit: bool = True,
    ) -> int:
        """Archive lots after two consecutive complete-run misses.

        A first miss is only recorded. Reappearance through ``upsert_lot`` (or
        an explicitly seen row) resets the streak. Concluded statuses are
        never downgraded to ``removed``.
        """
        now = now or _now()
        ts = now.isoformat()
        self.conn.execute("SAVEPOINT archive_missing")
        try:
            rows = self.conn.execute(
                "SELECT stock_number, status, missing_run_count FROM lots "
                "WHERE delisted_at IS NULL"
            ).fetchall()
            removed = 0
            for row in rows:
                if row["stock_number"] in seen_stock:
                    self.conn.execute(
                        "UPDATE lots SET missing_run_count = 0 WHERE stock_number = ?",
                        (row["stock_number"],),
                    )
                    continue
                misses = int(row["missing_run_count"] or 0) + 1
                if misses < 2:
                    self.conn.execute(
                        "UPDATE lots SET missing_run_count = ? WHERE stock_number = ?",
                        (misses, row["stock_number"]),
                    )
                    continue
                if row["status"] == "active":
                    self.conn.execute(
                        "UPDATE lots SET status = 'removed', status_updated_at = ?, "
                        "delisted_at = ?, last_changed = ?, missing_run_count = ? "
                        "WHERE stock_number = ?",
                        (ts, ts, ts, misses, row["stock_number"]),
                    )
                    self._record_status_change(
                        dict(row),
                        {"stock_number": row["stock_number"], "status": "removed", "final_price": None},
                        now,
                    )
                    removed += 1
                else:
                    self.conn.execute(
                        "UPDATE lots SET delisted_at = ?, last_changed = ?, missing_run_count = ? "
                        "WHERE stock_number = ?",
                        (ts, ts, misses, row["stock_number"]),
                    )
            self.conn.execute("RELEASE SAVEPOINT archive_missing")
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT archive_missing")
            self.conn.execute("RELEASE SAVEPOINT archive_missing")
            raise
        if commit:
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
                   sort: str = "auction_date", descending: bool = False,
                   after: Optional[tuple[Any, str]] = None) -> tuple[list[dict], int]:
        """Filtered query with offset or stable keyset pagination.

        ``after`` is ``(sort_value, stock_number)`` from the final row of the
        previous page. NULL values are always ordered after non-NULL values and
        continue by stock number, so every allowed sort is traversable.
        """
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
        page_where = where
        page_params = list(params)
        if after is not None:
            if len(after) != 2 or after[1] is None:
                raise ValueError("keyset cursor requires (sort_value, stock_number)")
            op = "<" if descending else ">"
            prefix = f"{where} AND " if where else "WHERE "
            if after[0] is None:
                page_where = prefix + f"({sort_col} IS NULL AND stock_number {op} ?)"
                page_params.append(after[1])
            else:
                page_where = prefix + (
                    f"({sort_col} {op} ? OR "
                    f"({sort_col} = ? AND stock_number {op} ?) OR {sort_col} IS NULL)"
                )
                page_params.extend([after[0], after[0], after[1]])
        rows = self.conn.execute(
            f"SELECT * FROM lots {page_where} ORDER BY ({sort_col} IS NULL) ASC, "
            f"{sort_col} {direction}, "
            f"stock_number {direction} LIMIT ? OFFSET ?",
            page_params + [limit, offset],
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

    def get_price_history(
        self,
        stock_number: str,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        page_sql = ""
        params: list[Any] = [stock_number]
        if limit is not None:
            page_sql = " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self.conn.execute(
            "SELECT id, stock_number, observed_at, price_type, amount, run_id "
            "FROM price_history WHERE stock_number = ? "
            "ORDER BY observed_at, price_type, id" + page_sql,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def count_price_history(self, stock_number: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM price_history WHERE stock_number = ?",
            (stock_number,),
        ).fetchone()[0])

    def get_status_history(
        self,
        stock_number: str,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        page_sql = ""
        params: list[Any] = [stock_number]
        if limit is not None:
            page_sql = " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        rows = self.conn.execute(
            "SELECT id, stock_number, observed_at, old_status, new_status, final_price, run_id "
            "FROM lot_status_history WHERE stock_number = ? "
            "ORDER BY observed_at, id" + page_sql,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def count_status_history(self, stock_number: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM lot_status_history WHERE stock_number = ?",
            (stock_number,),
        ).fetchone()[0])

    # Explicit domain spelling; keep get_status_history as the concise API.
    get_lot_status_history = get_status_history

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
