import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A realistic raw RunList row captured from the live API (Ontario lot).
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
    "ItemStatusDesc": "", "PrebidItemStatusDesc": "",
}


@pytest.fixture
def sample_row():
    return dict(SAMPLE_ROW)


def make_row(stock_number, branch_id=70, branch_desc="Toronto North", **overrides):
    """Build a RunList row variant for a given stock number / branch."""
    row = dict(SAMPLE_ROW)
    row["StockNum"] = str(stock_number)
    row["StockBranchId"] = branch_id
    row["StockBranchDescription"] = branch_desc
    row.update(overrides)
    return row
