import sqlite3

from iaai_scraper.storage import SqliteStore, backup_sqlite


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
        "prebid_item_status_id", "final_price", "timed_high_bid", "winning_bid",
        "bid_closes_at", "last_price_at", "status_updated_at", "delisted_at",
    ):
        assert required in cols
    hist_cols = {r[1] for r in store.conn.execute("PRAGMA table_info(price_history)").fetchall()}
    assert "price_type" in hist_cols
    lot_cols = {r[1] for r in store.conn.execute("PRAGMA table_info(lot_status_history)").fetchall()}
    assert {"old_status", "new_status", "final_price"}.issubset(lot_cols)
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    row = store.conn.execute("SELECT make FROM lots WHERE stock_number='old1'").fetchone()
    assert row["make"] == "FORD"
    backups = list((tmp_path / "backups").glob("old-pre-migration-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as pre_migration:
        assert pre_migration.execute(
            "SELECT make FROM lots WHERE stock_number='old1'"
        ).fetchone()[0] == "FORD"
    store.close()


def test_sqlite_backup_is_readable_and_distinct(tmp_path):
    db = tmp_path / "source.db"
    backup = tmp_path / "backups" / "snapshot.db"
    store = SqliteStore(db_path=db)
    store.record_run(status="completed")
    assert backup_sqlite(store, backup) == backup
    restored = SqliteStore(db_path=backup)
    assert restored.stats()["last_run"]["status"] == "completed"
    restored.close()
    store.close()


def test_sqlite_backup_rejects_missing_source(tmp_path):
    import pytest
    with pytest.raises(FileNotFoundError):
        backup_sqlite(tmp_path / "missing.db", tmp_path / "backup.db")


def test_store_backup_rejects_source_as_destination(tmp_path):
    import pytest
    db = tmp_path / "same.db"
    store = SqliteStore(db)
    with pytest.raises(ValueError, match="destination must differ"):
        store.backup(db)
    store.close()
