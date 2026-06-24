"""Offline unit tests for parsing + storage (no network)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iaai_scraper.parser import parse_row, _money, _price, _bool, _dotnet_date, _province
from iaai_scraper.models import Lot
from iaai_scraper.storage import SqliteStore
from iaai_scraper import config

# A realistic raw row captured from the live API during Phase 0.
SAMPLE_ROW = {
    "StockNum": "12637338", "StockId": 3038001, "Vin": "JA4AJUAU9TU******",
    "Year": 2026, "Make": "MITSUBISHI", "Model": "RVR ES AWC",
    "Engine": "2.0L I-4 DOHC, VVT, 148HP", "FuelType": "", "Transmission": "Auto",
    "OdometerReading": 9701, "OdometerUnit": "Km", "OdometerSource": "Actual",
    "PrimaryDamage": "Front", "SecondaryDamage": "Left Side",
    "Brand": "MB-SALVAGABLE", "BrandCodeType": "Repairable",
    "DamageEstimate": "$26,044.00", "ConditionText": "Stationary",
    "Drives": "False", "Starts": "False", "Keys": "True",
    "StockBranchId": 70, "StockBranchDescription": "Toronto North",
    "VehicleLocation": "Stouffville, ON",
    "Auction": "IAA Ontario Regional Sale", "AuctionId": 17887,
    "AuctionDate": "2026-06-24", "AuctionDateTimeDisplay": "Wed, Jun 24, 11:00 AM EDT",
    "AuctionDateUTC": "/Date(1782313200000)/", "AuctionType": "PUBLIC",
    "AuctionLaneNum": 2, "AuctionSequenceNum": 48, "IsTimedAuction": False,
    "BuyNowPrice": "$0.00", "HighPrebidValue": 0,
}


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


def test_parse_row_missing_stock():
    assert parse_row({"Make": "X"}) is None  # no stock number => skipped


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
