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
