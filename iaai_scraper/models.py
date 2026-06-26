"""Normalized auction-lot schema.

The IAAI search row (`RunList` item) carries ~60 raw fields. We map the
meaningful ones into a clean, typed `Lot` model with consistent names, parsed
numbers/dates, and a `raw` blob preserving the full original payload so nothing
is ever silently lost (completeness requirement).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class Lot(BaseModel):
    """A single auction lot, normalized for storage and the API."""

    # --- identity -------------------------------------------------------- #
    stock_number: str = Field(..., description="IAAI stock number (business key)")
    stock_id: Optional[int] = Field(None, description="Internal numeric id used by the site")
    vin: Optional[str] = Field(None, description="VIN; masked for anonymous users")
    detail_url: Optional[str] = None

    # --- vehicle --------------------------------------------------------- #
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    engine: Optional[str] = None
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    odometer: Optional[int] = None
    odometer_unit: Optional[str] = None          # "Km" / "Mi"
    odometer_source: Optional[str] = None         # "Actual" / "Not Actual" / ...

    # --- condition / title ---------------------------------------------- #
    primary_damage: Optional[str] = None
    secondary_damage: Optional[str] = None
    title_brand: Optional[str] = None             # e.g. "MB-SALVAGABLE"
    title_brand_type: Optional[str] = None         # e.g. "Repairable"
    damage_estimate: Optional[float] = None
    condition_text: Optional[str] = None           # e.g. "Stationary"
    runs: Optional[bool] = None                    # "Drives"
    starts: Optional[bool] = None
    has_keys: Optional[bool] = None

    # --- location / branch ---------------------------------------------- #
    branch_id: Optional[int] = None                # StockBranchId (Ontario classifier)
    branch_name: Optional[str] = None              # StockBranchDescription
    location: Optional[str] = None                 # VehicleLocation "City, PROV"
    province: Optional[str] = None                 # derived 2-letter code

    # --- auction / sale -------------------------------------------------- #
    auction_name: Optional[str] = None
    auction_id: Optional[int] = None
    auction_date: Optional[str] = None             # ISO date (local sale date)
    auction_datetime_display: Optional[str] = None
    auction_datetime_utc: Optional[datetime] = None
    auction_type: Optional[str] = None             # PUBLIC / ...
    lane: Optional[str] = None
    sequence: Optional[int] = None
    is_timed_auction: Optional[bool] = None
    buy_now_price: Optional[float] = None
    high_prebid: Optional[float] = None
    timed_high_bid: Optional[float] = None
    winning_bid: Optional[float] = None
    currency: str = "CAD"

    # --- lifecycle / sale outcome (from ItemStatusDesc etc.) ------------ #
    status: str = "active"                       # active | sold | if_bid | passed
    item_status_desc: Optional[str] = None       # Sold / IfBid / Pass / empty
    prebid_item_status_desc: Optional[str] = None
    prebid_item_status_id: Optional[int] = None
    final_price: Optional[float] = None          # best anonymous sale/bid signal
    bid_closes_at: Optional[datetime] = None
    last_price_at: Optional[datetime] = None     # when any tracked price last changed
    status_updated_at: Optional[datetime] = None
    delisted_at: Optional[datetime] = None

    # --- provenance / housekeeping -------------------------------------- #
    source: str = "ca.iaai.com"
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_changed: Optional[datetime] = None
    raw: dict[str, Any] = Field(default_factory=dict, description="full original row")
