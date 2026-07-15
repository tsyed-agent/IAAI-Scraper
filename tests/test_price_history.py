"""Tests for price_history recording and related storage reads."""
from iaai_scraper.parser import parse_row
from iaai_scraper.storage import SqliteStore
from tests.conftest import SAMPLE_ROW, make_row


def test_price_history_on_insert_and_update(tmp_path):
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.begin_run()
    row = make_row("12637338", HighPrebidValue="$1,500.00")
    lot = parse_row(row)
    store.upsert_lot(lot)
    store.commit()

    hist = store.get_price_history(lot.stock_number)
    assert len(hist) >= 1
    assert any(h["price_type"] == "prebid" for h in hist)

    lot2 = parse_row({**row, "HighPrebidValue": "$9,999.00"})
    store.upsert_lot(lot2)
    store.commit()
    hist2 = store.get_price_history(lot.stock_number)
    assert len(hist2) > len(hist)
    amounts = [h["amount"] for h in hist2 if h["price_type"] == "prebid"]
    assert 9999.0 in amounts


def test_winning_bid_parsed_and_stored(tmp_path):
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    row = make_row("555", ItemStatusDesc="Sold", WinningbidAmount="$12,500.00")
    lot = parse_row(row)
    assert lot.winning_bid == 12500.0
    store.upsert_lot(lot)
    stored = store.get_lot("555")
    assert stored["winning_bid"] == 12500.0
    store.close()


def test_bid_increments_recorded_separately(tmp_path):
    """Each bid increase should append a new prebid history row."""
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.begin_run()
    base = make_row("900", HighPrebidValue="$1,000.00")
    store.upsert_lot(parse_row(base))
    for price in ("$2,500.00", "$4,000.00", "$4,000.00"):
        store.upsert_lot(parse_row({**base, "HighPrebidValue": price}))
    store.commit()
    hist = store.get_price_history("900")
    prebid = [h for h in hist if h["price_type"] == "prebid"]
    assert len(prebid) == 3  # 1000, 2500, 4000 — duplicate crawl is no-op
    assert [h["amount"] for h in prebid] == [1000.0, 2500.0, 4000.0]
    assert store.get_lot("900")["high_prebid"] == 4000.0
    store.close()


def test_identical_re_crawl_is_unchanged_no_extra_history(tmp_path):
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    row = make_row("901", HighPrebidValue="$500.00")
    lot = parse_row(row)
    store.upsert_lot(lot)
    store.upsert_lot(lot)
    store.upsert_lot(lot)
    hist = store.get_price_history("901")
    assert len([h for h in hist if h["price_type"] == "prebid"]) == 1
    assert store.upsert_lot(lot) == "unchanged"
    store.close()


def test_concluded_status_not_regressed_on_active_row(tmp_path):
    """Sold outcome must survive a later crawl where ItemStatusDesc is empty."""
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    sold = parse_row(make_row("902", ItemStatusDesc="Sold", HighPrebidValue="$3,200.00"))
    store.upsert_lot(sold)
    active = parse_row(make_row("902", ItemStatusDesc="", HighPrebidValue="$3,200.00"))
    out = store.upsert_lot(active)
    rec = store.get_lot("902")
    assert rec["status"] == "sold"
    assert rec["final_price"] == 3200.0
    assert out == "unchanged"
    store.close()


def test_active_to_sold_records_status_and_final_price(tmp_path):
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.begin_run()
    store.upsert_lot(parse_row(make_row("903", ItemStatusDesc="", HighPrebidValue="$800.00")))
    store.upsert_lot(parse_row(make_row("903", ItemStatusDesc="Sold", HighPrebidValue="$800.00")))
    hist = store.get_price_history("903")
    types = {h["price_type"] for h in hist}
    assert "status_sold" in types
    assert "final" in types
    assert store.get_status_history("903")[-1]["new_status"] == "sold"
    store.close()


def test_removed_lot_reappearing_becomes_active(tmp_path):
    from datetime import datetime, timezone
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row("904", ItemStatusDesc="")))
    store.archive_missing(set(), now=datetime.now(timezone.utc))
    store.archive_missing(set(), now=datetime.now(timezone.utc))
    assert store.get_lot("904")["status"] == "removed"
    store.upsert_lot(parse_row(make_row("904", ItemStatusDesc="")))
    rec = store.get_lot("904")
    assert rec["status"] == "active"
    assert rec["delisted_at"] is None
    store.close()


def test_freshness_and_sold_archive_filters(tmp_path):
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.record_run(
        started_at="2026-06-24T10:00:00+00:00",
        finished_at="2026-06-24T10:05:00+00:00",
        status="completed",
        ontario_seen=1,
    )
    sold = parse_row(make_row("301", ItemStatusDesc="Sold", HighPrebidValue="$3,000.00"))
    store.upsert_lot(sold)
    store.commit()

    fresh = store.freshness()
    assert fresh["last_crawl"]["status"] == "completed"

    rows, total = store.query_lots(
        {"status": "sold", "sold_from": "2026-01-01T00:00:00+00:00"}, limit=10, offset=0,
    )
    assert total == 1 and rows[0]["stock_number"] == "301"
    store.close()


def test_missing_lot_requires_two_complete_misses_and_reappearance_resets(tmp_path):
    from datetime import datetime, timezone
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row("5008", ItemStatusDesc="")))
    now = datetime.now(timezone.utc)
    assert store.archive_missing(set(), now=now) == 0
    assert store.get_lot("5008")["missing_run_count"] == 1
    store.upsert_lot(parse_row(make_row("5008", ItemStatusDesc="")))
    assert store.get_lot("5008")["missing_run_count"] == 0
    assert store.archive_missing(set(), now=now) == 0
    assert store.archive_missing(set(), now=now) == 1
    assert store.get_lot("5008")["status"] == "removed"
    store.close()


def test_archive_missing_is_atomic_on_database_error(tmp_path):
    import pytest

    store = SqliteStore(db_path=tmp_path / "t.db")
    store.upsert_lot(parse_row(make_row("atomic-1", ItemStatusDesc="")))
    store.upsert_lot(parse_row(make_row("atomic-2", ItemStatusDesc="")))
    store.commit()
    store.conn.execute(
        "CREATE TRIGGER fail_archive BEFORE UPDATE ON lots "
        "WHEN NEW.stock_number = 'atomic-2' "
        "BEGIN SELECT RAISE(ABORT, 'simulated archive failure'); END"
    )
    with pytest.raises(Exception, match="simulated archive failure"):
        store.archive_missing(set())
    assert store.get_lot("atomic-1")["missing_run_count"] == 0
    assert store.get_lot("atomic-2")["missing_run_count"] == 0
    store.close()


def test_keyset_query_tie_breaks_and_traverses_null_cursor(tmp_path):
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    for stock in ("b", "a", "c"):
        store.upsert_lot(parse_row(make_row(stock, Model="SAME")))
    first, total = store.query_lots({}, limit=2, offset=0, sort="model")
    assert total == 3
    second, _ = store.query_lots(
        {}, limit=2, offset=0, sort="model",
        after=(first[-1]["model"], first[-1]["stock_number"]),
    )
    assert [r["stock_number"] for r in first + second] == ["a", "b", "c"]
    null_page, _ = store.query_lots({}, limit=2, offset=0, sort="final_price")
    assert null_page[-1]["final_price"] is None
    after_null, _ = store.query_lots(
        {}, limit=2, offset=0, sort="final_price",
        after=(null_page[-1]["final_price"], null_page[-1]["stock_number"]),
    )
    assert all(row["stock_number"] > null_page[-1]["stock_number"] for row in after_null)
    store.close()
