"""Tests for lifecycle status + price derivation (offline, uses live-captured rows)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iaai_scraper.lifecycle import (
    STATUS_ACTIVE,
    STATUS_IF_BID,
    STATUS_PASSED,
    STATUS_SOLD,
    best_final_price,
    derive_lot_status,
)
from iaai_scraper.parser import parse_row

REF = Path(__file__).resolve().parents[1] / "docs/reference/lot_samples_reference.json"

_BASE_ROW = {
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


def make_row(stock_number, branch_id=70, branch_desc="Toronto North", **overrides):
    row = dict(_BASE_ROW)
    row["StockNum"] = str(stock_number)
    row["StockBranchId"] = branch_id
    row["StockBranchDescription"] = branch_desc
    row.update(overrides)
    return row


def _load_samples():
    data = json.loads(REF.read_text(encoding="utf-8"))
    return {s["stock_number"]: s["runlist_row_full"] for s in data["samples"]}


def test_derive_active_when_item_status_empty():
    row = {"ItemStatusDesc": "", "PrebidItemStatusDesc": ""}
    assert derive_lot_status(row) == (STATUS_ACTIVE, "default_active")


def test_derive_sold_if_bid_pass():
    assert derive_lot_status({"ItemStatusDesc": "Sold"})[0] == STATUS_SOLD
    assert derive_lot_status({"ItemStatusDesc": "IfBid"})[0] == STATUS_IF_BID
    assert derive_lot_status({"ItemStatusDesc": "Pass"})[0] == STATUS_PASSED


def test_bidding_complete_alone_stays_active():
    row = {"ItemStatusDesc": "", "PrebidItemStatusDesc": "BiddingComplete"}
    assert derive_lot_status(row) == (STATUS_ACTIVE, "default_active")


def test_best_final_price_prefers_high_prebid():
    row = {"WinningbidAmount": "", "HighPrebidValue": 1850, "HighPrebid": "$1,850.00"}
    assert best_final_price(row) == 1850.0


def test_unknown_item_status_defaults_active_with_reason():
    status, reason = derive_lot_status({"ItemStatusDesc": "WeirdNewValue"})
    assert status == STATUS_ACTIVE
    assert "unknown" in reason


def test_active_lot_has_no_final_price():
    lot = parse_row(make_row("7007", HighPrebidValue=175, ItemStatusDesc=""))
    assert lot.status == "active"
    assert lot.high_prebid == 175.0
    assert lot.final_price is None


def test_concluded_lot_has_final_price():
    lot = parse_row(make_row("7008", HighPrebidValue=1850, ItemStatusDesc="Sold"))
    assert lot.status == "sold"
    assert lot.final_price == 1850.0


def test_parse_row_lifecycle_from_live_samples():
    samples = _load_samples()
    # Sold with price signal
    sold = parse_row(samples["12143176"])
    assert sold.status == STATUS_SOLD
    assert sold.item_status_desc == "Sold"
    assert sold.prebid_item_status_desc == "BiddingComplete"
    assert sold.final_price == 1850.0
    assert sold.high_prebid == 1850.0

    # IfBid
    ifbid = parse_row(samples["12249600"])
    assert ifbid.status == STATUS_IF_BID
    assert ifbid.final_price == 750.0

    # Pass
    passed = parse_row(samples["12088838"])
    assert passed.status == STATUS_PASSED

    # Active (empty ItemStatusDesc)
    active = parse_row(samples["12033066"])
    assert active.status == STATUS_ACTIVE
    assert active.item_status_desc is None
    assert active.final_price is None
