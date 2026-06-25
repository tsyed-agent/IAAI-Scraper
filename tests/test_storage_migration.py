import sqlite3

from iaai_scraper.storage import SqliteStore


def _old_schema_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE lots (stock_number TEXT PRIMARY KEY, make TEXT, "
        "first_seen TEXT, last_seen TEXT, last_changed TEXT, raw TEXT)"
    )
    conn.execute("INSERT INTO lots (stock_number, make) VALUES ('old1', 'FORD')")
    conn.commit()
    conn.close()


def test_migration_adds_columns(tmp_path):
    db = tmp_path / "old.db"
    _old_schema_db(db)
    store = SqliteStore(db_path=db)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(lots)").fetchall()}
    for required in (
        "status", "item_status_desc", "prebid_item_status_desc",
        "prebid_item_status_id", "final_price", "timed_high_bid",
        "bid_closes_at", "status_updated_at", "delisted_at",
    ):
        assert required in cols
    row = store.conn.execute("SELECT make FROM lots WHERE stock_number='old1'").fetchone()
    assert row["make"] == "FORD"
    store.close()
