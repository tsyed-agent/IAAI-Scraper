"""Tests for extended RunList field normalization."""
import json
from pathlib import Path

from iaai_scraper.parser import parse_row

REF = Path(__file__).resolve().parents[1] / "docs/reference/lot_samples_reference.json"


def _sample_row(stock: str) -> dict:
    data = json.loads(REF.read_text(encoding="utf-8"))
    for s in data["samples"]:
        if s["stock_number"] == stock:
            return s["runlist_row_full"]
    raise KeyError(stock)


def test_extended_fields_from_live_sample():
    row = _sample_row("11997575")
    lot = parse_row(row)
    assert lot is not None
    assert lot.auction_branch_id == 56
    assert lot.auction_branch_name == "Toronto (Oshawa)"
    assert lot.auction_type_id == 17
    assert lot.auction_type_desc == "IAA Ontario Regional Sale"
    assert lot.is_auction_closed is True
    assert lot.is_regular_auction is True
    assert lot.location_name == "IAA Toronto"
    assert lot.image_url.startswith("https://")
    assert lot.server_observed_at is not None
    assert lot.status == "sold"
    assert lot.prebid_item_status_desc == "BiddingComplete"


def test_active_sample_extended_pricing_fields():
    row = _sample_row("12033066")
    lot = parse_row(row)
    assert lot.status == "active"
    assert lot.final_price is None
    assert lot.location_name is not None
    assert lot.server_observed_at is not None
