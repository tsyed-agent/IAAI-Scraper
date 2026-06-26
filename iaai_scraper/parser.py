"""Convert a raw IAAI `RunList` row into a normalized `Lot`.

All parsing is defensive: a malformed/missing field yields ``None`` rather than
raising, so one bad row never aborts a crawl. The complete raw row is always
preserved on ``Lot.raw``.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .config import DETAIL_URL_TEMPLATE
from .lifecycle import best_final_price, derive_lot_status, is_concluded
from .models import Lot

log = logging.getLogger("iaai.parser")

# ".NET" JSON date format: "/Date(1782313200000)/" (ms since epoch, may be negative)
_DOTNET_DATE_RE = re.compile(r"/Date\((-?\d+)\)/")
# Province code at the end of "City, ON"
_PROVINCE_RE = re.compile(r",\s*([A-Za-z]{2})\s*$")
# Sentinel used by the site for "no date" (year 0001) — treat as None.
_NULL_DATE_MS = -62135575200000


def _money(value: Any) -> Optional[float]:
    """'$26,044.00' -> 26044.0, '$0.00' -> 0.0, '' -> None (faithful parse)."""
    if value is None:
        return None
    s = str(value).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _price(value: Any) -> Optional[float]:
    """Like _money but treats 0 as None — for *offer* fields where $0.00 means
    'no buy-now price / no pre-bid placed' rather than a real zero price."""
    f = _money(value)
    return f if f else None


def _int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(float(str(value).replace(",", "")))
    except (ValueError, TypeError):
        return None


def _bool(value: Any) -> Optional[bool]:
    """Handle real bools and the site's "True"/"False" strings."""
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in ("true", "yes", "1"):
        return True
    if s in ("false", "no", "0"):
        return False
    return None


def _str(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _dotnet_date(value: Any) -> Optional[datetime]:
    """Parse '/Date(ms)/' to an aware UTC datetime; None for the null sentinel."""
    if not value:
        return None
    m = _DOTNET_DATE_RE.search(str(value))
    if not m:
        return None
    ms = int(m.group(1))
    if ms == _NULL_DATE_MS:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def _province(location: Optional[str]) -> Optional[str]:
    if not location:
        return None
    m = _PROVINCE_RE.search(location)
    return m.group(1).upper() if m else None


def parse_row(row: dict[str, Any]) -> Optional[Lot]:
    """Map one raw RunList dict to a Lot. Returns None if it lacks a stock number."""
    if not isinstance(row, dict):
        return None

    stock_number = _str(row.get("StockNum"))
    if not stock_number:
        # Without the business key we cannot dedup/store reliably; skip it.
        return None

    location = _str(row.get("VehicleLocation"))
    try:
        status, _status_reason = derive_lot_status(row)
        final_price = best_final_price(row) if is_concluded(status) else None
        return Lot(
            stock_number=stock_number,
            stock_id=_int(row.get("StockId")),
            vin=_str(row.get("Vin")),
            detail_url=DETAIL_URL_TEMPLATE.format(stock_num=stock_number),
            year=_int(row.get("Year")),
            make=_str(row.get("Make")),
            model=_str(row.get("Model")),
            engine=_str(row.get("Engine")),
            fuel_type=_str(row.get("FuelType")),
            transmission=_str(row.get("Transmission")),
            odometer=_int(row.get("OdometerReading")),
            odometer_unit=_str(row.get("OdometerUnit")),
            odometer_source=_str(row.get("OdometerSource")),
            primary_damage=_str(row.get("PrimaryDamage")),
            secondary_damage=_str(row.get("SecondaryDamage")),
            title_brand=_str(row.get("Brand")),
            title_brand_type=_str(row.get("BrandCodeType")),
            damage_estimate=_money(row.get("DamageEstimate")),
            condition_text=_str(row.get("ConditionText")),
            runs=_bool(row.get("Drives")),
            starts=_bool(row.get("Starts")),
            has_keys=_bool(row.get("Keys")),
            branch_id=_int(row.get("StockBranchId")),
            branch_name=_str(row.get("StockBranchDescription")),
            location=location,
            location_name=_str(row.get("LocationName")),
            province=_province(location),
            auction_name=_str(row.get("Auction")),
            auction_id=_int(row.get("AuctionId")),
            auction_date=_str(row.get("AuctionDate")),
            auction_datetime_display=_str(row.get("AuctionDateTimeDisplay")),
            auction_datetime_utc=_dotnet_date(row.get("AuctionDateUTC")),
            auction_type=_str(row.get("AuctionType")),
            auction_type_id=_int(row.get("AuctionTypeId")),
            auction_type_desc=_str(row.get("AuctionTypeDesc")),
            auction_branch_id=_int(row.get("AuctionBranchId")),
            auction_branch_name=_str(row.get("AuctionBranchDescription")),
            auction_status_id=_int(row.get("AuctionStatusID")),
            is_auction_closed=_bool(row.get("IsAuctionClosed")),
            is_regular_auction=_bool(row.get("IsRegularAuction")),
            auction_offsite=_bool(row.get("AuctionOffsite")),
            lane=_str(row.get("AuctionLaneNum")),
            sequence=_int(row.get("AuctionSequenceNum")),
            is_timed_auction=_bool(row.get("IsTimedAuction")),
            timed_auction_status=_str(row.get("TimedAuctionStatusDesc")),
            buy_now_price=_price(row.get("BuyNowPrice")),
            buy_now_offer_price=_price(row.get("BuyNowOfferPrice")),
            timed_buy_now_price=_price(row.get("TimedAuctionBuyNowPrice")),
            high_prebid=_price(row.get("HighPrebidValue")),
            timed_high_bid=_price(row.get("TimedAuctionHighestBidAmountValue")),
            winning_bid=_price(row.get("WinningbidAmount")),
            status=status,
            item_status_desc=_str(row.get("ItemStatusDesc")),
            prebid_item_status_desc=_str(row.get("PrebidItemStatusDesc")),
            prebid_item_status_id=_int(row.get("PrebidItemStatusID")),
            prebid_allowed=_bool(row.get("PrebidAllowed")),
            prebid_closed=_bool(row.get("PrebidClosed")),
            buy_now_status=_str(row.get("BuyNowStatus")),
            is_buy_now=_bool(row.get("IsBuyNow")),
            buy_now_allowed=_bool(row.get("BuyNowAllowed")),
            final_price=final_price,
            bid_closes_at=_dotnet_date(row.get("BidItemClosingDateUTC")),
            server_observed_at=_dotnet_date(row.get("ServerCurrentDateUTC")),
            image_url=_str(row.get("ImageUrl")),
            raw=row,
        )
    except Exception:  # noqa: BLE001 - never let one row abort the crawl
        log.warning("parse_row: dropping row stock=%s (parse/validation failed)", stock_number)
        return None
