"""Typed wrapper around POST /Search/GetSearchResult/.

Builds the (large) form payload the endpoint expects and exposes a clean
``fetch_page`` returning the rows plus the authoritative total count.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from . import config
from .session import IaaiSession

log = logging.getLogger("iaai.search")


class SearchResponseError(ValueError):
    """Raised when the search endpoint violates its JSON response contract."""

    def __init__(self, message: str, payload: Any = None):
        super().__init__(message)
        self.payload = payload


def _build_form(page: int, page_size: int, sort: str,
                province_ids: str = "", branch_ids: str = "") -> dict[str, str]:
    """Construct the GetSearchResult form body.

    Only a subset of fields actually drive the query (sort, paging, optional
    filters); the rest mirror what the site sends so the request looks normal.
    `page` and `RunlistPageIndex` must agree — both are set to the page number.
    """
    return {
        "Url": "", "Keyword": "", "PageLoading": "false",
        "PageSize": str(page_size),
        "IsKeywordSearch": "false",
        # IsNewSearch=true on page 1 resets server-side paging state.
        "IsNewSearch": "true" if page == 1 else "false",
        "RunlistSort": sort,
        "IgnoreKeywordYear": "false", "YearFrom": "", "YearTo": "", "VehicleAge": "",
        "AuctionToday": "false", "AuctionTomorrow": "false", "VehicleBrand": "",
        "StockFilter": "", "IsBuyNow": "false", "IsVehicleRemarketing": "false",
        "ShowOnlyPublicAuctions": "false",
        "VehicleTypeIds": "", "VehicleMakeIds": "", "VehicleModelIds": "",
        "VehicleBrandCodeIds": "", "BranchIds": branch_ids,
        "AuctionTypeIds": "", "AuctionIds": "", "RequestSource": "",
        "IsManagerPick": "false", "IsWatching": "false",
        "IsPreBidWinning": "false", "IsPreBidOutBid": "false",
        "AuctionMode": "", "IsSiteSale": "", "IsBuyNowOffer": "false",
        "VehicleTypeListPageIndex": "1", "BranchListPageIndex": "1",
        "MakeListPageIndex": "1", "ModelListPageIndex": "1",
        "VehicleProvinceIds": province_ids, "VehicleProvincePageIndex": "1",
        "RunlistPageIndex": str(page),
        "AttributePageSize": "100",
        "Caller": "getVehicleResults",
        "RunlistPageSize": str(page_size),
        "IsRepossession": "false",
        "page": str(page),
    }


def extract_total(payload: dict[str, Any]) -> Optional[int]:
    """The authoritative total lives in SummaryList where Description==TOTAL_COUNT."""
    for item in payload.get("SummaryList") or []:
        if item.get("Description") == "TOTAL_COUNT":
            raw = item.get("Count")
            try:
                if isinstance(raw, bool):
                    raise ValueError("boolean is not a count")
                if isinstance(raw, float) and not raw.is_integer():
                    raise ValueError("fractional count")
                return int(raw)
            except (TypeError, ValueError):
                log.warning("extract_total: non-numeric TOTAL_COUNT %r", raw)
                return None
    return None


class SearchClient:
    def __init__(self, session: IaaiSession, settings: Optional[config.CrawlSettings] = None):
        self.session = session
        self.settings = settings or session.settings

    async def fetch_page(self, page: int) -> tuple[list[dict[str, Any]], Optional[int]]:
        """Fetch one results page. Returns (rows, total_count)."""
        form = _build_form(
            page=page,
            page_size=self.settings.page_size,
            sort=self.settings.sort,
            branch_ids=self.settings.branch_ids,
        )
        payload = await self.session.post_json(config.SEARCH_RESULT_ENDPOINT, form)
        if not isinstance(payload, dict):
            raise SearchResponseError(
                f"page {page}: expected JSON object, got {type(payload).__name__}", payload,
            )
        if "RunList" not in payload or not isinstance(payload["RunList"], list):
            raise SearchResponseError(f"page {page}: RunList must be an array", payload)
        rows = payload["RunList"]
        if any(not isinstance(row, dict) for row in rows):
            raise SearchResponseError(f"page {page}: RunList contains non-object row", payload)
        if "SummaryList" not in payload or not isinstance(payload["SummaryList"], list):
            raise SearchResponseError(f"page {page}: SummaryList must be an array", payload)
        if any(not isinstance(item, dict) for item in payload["SummaryList"]):
            raise SearchResponseError(
                f"page {page}: SummaryList contains non-object item", payload,
            )
        total = extract_total(payload)
        has_total = any(item.get("Description") == "TOTAL_COUNT" for item in payload["SummaryList"])
        if has_total and total is None:
            raise SearchResponseError(f"page {page}: TOTAL_COUNT must be numeric", payload)
        if total is not None and total < 0:
            raise SearchResponseError(f"page {page}: TOTAL_COUNT must be non-negative", payload)
        log.debug("page %d -> %d rows (total=%s)", page, len(rows), total)
        return rows, total
