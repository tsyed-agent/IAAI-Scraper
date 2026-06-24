"""Derive lot lifecycle status from raw IAAI RunList fields.

Confirmed against a live 500-row scan on 2026-06-24 (see docs/reference/).
Primary signal: ``ItemStatusDesc``. Secondary: ``PrebidItemStatusDesc``.

Observed values (Canada search, 500 rows):
  ItemStatusDesc: (empty)=upcoming/active, Sold, IfBid, Pass
  PrebidItemStatusDesc: (empty)=active window, BiddingComplete=auction ended

``WinningbidAmount`` is present on every row but usually empty for anonymous
users. For sold/if-bid lots, ``HighPrebidValue`` / ``HighPrebid`` carry the
best available final-bid signal. Timed auctions may use
``TimedAuctionHighestBidAmountValue``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

log = logging.getLogger("iaai.lifecycle")

# Normalized statuses stored on Lot.status
STATUS_ACTIVE = "active"
STATUS_SOLD = "sold"
STATUS_IF_BID = "if_bid"
STATUS_PASSED = "passed"

_ITEM_STATUS_MAP = {
    "sold": STATUS_SOLD,
    "ifbid": STATUS_IF_BID,
    "pass": STATUS_PASSED,
}


def _moneyish(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    s = str(value).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        f = float(s)
        return f if f > 0 else None
    except ValueError:
        return None


def best_final_price(row: dict[str, Any]) -> Optional[float]:
    """Best available sale/bid amount from the list row (anonymous tier)."""
    for key in (
        "WinningbidAmount",
        "WinningBidAmount",
        "TimedAuctionHighestBidAmountValue",
        "HighPrebidValue",
        "HighPrebid",
        "BuyNowOfferPrice",
    ):
        v = _moneyish(row.get(key))
        if v is not None:
            return v
    return None


def is_concluded(status: str) -> bool:
    return status in (STATUS_SOLD, STATUS_IF_BID, STATUS_PASSED)


def derive_lot_status(row: dict[str, Any]) -> tuple[str, str]:
    """Return (normalized_status, reason) from a raw RunList row.

    Default is ``active``. Only map to a non-active status when the source
    explicitly says so via ``ItemStatusDesc``.
    """
    item = (row.get("ItemStatusDesc") or "").strip()
    if not item:
        return STATUS_ACTIVE, "default_active"
    key = item.replace(" ", "").replace("-", "").lower()
    mapped = _ITEM_STATUS_MAP.get(key)
    if mapped:
        return mapped, f"item_status_desc={item}"
    log.warning("derive_lot_status: unknown ItemStatusDesc=%r -> active", item)
    return STATUS_ACTIVE, f"unknown_item_status={item}"
