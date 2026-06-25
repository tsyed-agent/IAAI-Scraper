"""Offline unit tests for parsing + storage (no network)."""
from iaai_scraper.parser import parse_row, _money, _price, _bool, _dotnet_date, _province
from iaai_scraper.models import Lot
from iaai_scraper.storage import SqliteStore
from iaai_scraper import config
from tests.conftest import SAMPLE_ROW


def test_money():
    assert _money("$26,044.00") == 26044.0
    assert _money("$0.00") == 0.0
    assert _money("") is None


def test_bool_and_date_and_province():
    assert _bool("True") is True and _bool("False") is False and _bool(True) is True
    assert _dotnet_date("/Date(1782313200000)/").year == 2026
    assert _dotnet_date("/Date(-62135575200000)/") is None  # null sentinel
    assert _province("Stouffville, ON") == "ON"
    assert _province("nope") is None


def test_parse_row():
    lot = parse_row(SAMPLE_ROW)
    assert isinstance(lot, Lot)
    assert lot.stock_number == "12637338"
    assert lot.year == 2026 and lot.make == "MITSUBISHI"
    assert lot.damage_estimate == 26044.0
    assert lot.runs is False and lot.has_keys is True
    assert lot.branch_id == 70 and lot.province == "ON"
    assert lot.buy_now_price is None  # $0.00 -> None
    assert lot.status == "active"
    assert lot.detail_url.endswith("stockno=12637338")
    assert lot.raw["Make"] == "MITSUBISHI"  # raw preserved


def test_lot_model_has_lifecycle_housekeeping_fields():
    assert "status_updated_at" in Lot.model_fields
    assert "delisted_at" in Lot.model_fields


def test_parse_row_missing_stock():
    assert parse_row({"Make": "X"}) is None  # no stock number => skipped


def test_parse_row_non_dict_returns_none():
    assert parse_row(None) is None
    assert parse_row("not a dict") is None
    assert parse_row(12345) is None


def test_parse_row_bad_year_does_not_raise():
    from tests.conftest import make_row
    row = make_row("99999999", Year="garbage")
    lot = parse_row(row)
    assert lot is not None
    assert lot.year is None


def test_ontario_classifier():
    s = config.CrawlSettings()
    assert s.is_ontario(70, "Toronto North") is True
    assert s.is_ontario(999, "London") is True      # by name fallback
    assert s.is_ontario(53, "Edmonton") is False


def test_upsert_lifecycle(tmp_path):
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    lot = parse_row(SAMPLE_ROW)

    assert store.upsert_lot(lot) == "inserted"
    assert store.upsert_lot(lot) == "unchanged"     # idempotent re-scrape

    lot2 = parse_row({**SAMPLE_ROW, "BuyNowPrice": "$5,000.00"})
    assert store.upsert_lot(lot2) == "updated"      # tracked field changed

    store.commit()
    rows, total = store.query_lots({"province": "ON"}, limit=10, offset=0)
    assert total == 1 and rows[0]["stock_number"] == "12637338"
    assert store.get_lot("12637338")["make"] == "MITSUBISHI"
    # first_seen preserved across updates
    assert store.get_lot("12637338")["first_seen"] is not None
    store.close()


def test_upsert_persists_status_active_then_sold(tmp_path):
    from tests.conftest import make_row
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    assert store.upsert_lot(parse_row(make_row("3003", ItemStatusDesc=""))) == "inserted"
    assert store.get_lot("3003")["status"] == "active"

    out = store.upsert_lot(parse_row(make_row("3003", ItemStatusDesc="Sold", HighPrebidValue=4000)))
    rec = store.get_lot("3003")
    assert out == "updated"
    assert rec["status"] == "sold"
    assert rec["final_price"] == 4000.0
    assert rec["status_updated_at"] is not None
    store.close()


def test_change_detection_on_untracked_field(tmp_path):
    from tests.conftest import make_row
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row("4004", Model="RVR ES AWC")))
    out = store.upsert_lot(parse_row(make_row("4004", Model="OUTLANDER")))
    assert out == "updated"
    store.close()


def test_archive_missing_only_touches_active(tmp_path):
    from tests.conftest import make_row
    db = tmp_path / "t.db"
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row("5005", ItemStatusDesc="")))
    store.upsert_lot(parse_row(make_row("5006", ItemStatusDesc="")))
    store.upsert_lot(parse_row(make_row("5007", ItemStatusDesc="Sold", HighPrebidValue=900)))

    n = store.archive_missing({"5005"})
    assert n == 1
    assert store.get_lot("5005")["status"] == "active"
    rec6 = store.get_lot("5006")
    assert rec6["status"] == "removed"
    assert rec6["delisted_at"] is not None
    rec7 = store.get_lot("5007")
    assert rec7["status"] == "sold"
    assert rec7["delisted_at"] is not None
    store.close()
