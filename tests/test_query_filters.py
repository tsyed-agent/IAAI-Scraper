"""Tests for extended lot query filters."""
from iaai_scraper.parser import parse_row
from iaai_scraper.storage import SqliteStore
from tests.conftest import make_row


def _seed(tmp_path):
    db = tmp_path / "q.db"
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row(
        "1001", Make="TOYOTA", Model="COROLLA", Year=2018,
        PrimaryDamage="Front", HighPrebidValue="$2,000.00", ItemStatusDesc="",
        Drives="True", Keys="True",
    )))
    store.upsert_lot(parse_row(make_row(
        "1002", Make="HONDA", Model="CIVIC", Year=2015,
        PrimaryDamage="Rear", HighPrebidValue="$500.00", ItemStatusDesc="Sold",
    )))
    store.upsert_lot(parse_row(make_row(
        "1003", Make="TOYOTA", Model="RAV4", Year=2020,
        PrimaryDamage="Side", HighPrebidValue="$8,000.00", ItemStatusDesc="",
    )))
    store.commit()
    return store


def test_multi_make_filter(tmp_path):
    store = _seed(tmp_path)
    rows, total = store.query_lots({"make": "toyota,honda"}, limit=50, offset=0)
    assert total == 3
    store.close()


def test_price_range_filter(tmp_path):
    store = _seed(tmp_path)
    rows, total = store.query_lots({"high_prebid_min": 1000, "high_prebid_max": 5000}, limit=50, offset=0)
    assert total == 1
    assert rows[0]["stock_number"] == "1001"
    store.close()


def test_primary_damage_filter(tmp_path):
    store = _seed(tmp_path)
    rows, total = store.query_lots({"primary_damage": "rear"}, limit=50, offset=0)
    assert total == 1
    assert rows[0]["make"] == "HONDA"
    store.close()


def test_has_keys_and_runs(tmp_path):
    store = _seed(tmp_path)
    rows, total = store.query_lots({"has_keys": True, "runs": True}, limit=50, offset=0)
    assert total == 1
    assert rows[0]["stock_number"] == "1001"
    store.close()
