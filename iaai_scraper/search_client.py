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
        form = _build_form(page=page, page_size=self.settings.page_size, sort=self.settings.sort)
        payload = await self.session.post_json(config.SEARCH_RESULT_ENDPOINT, form)
        rows = payload.get("RunList") or []
        total = extract_total(payload)
        log.debug("page %d -> %d rows (total=%s)", page, len(rows), total)
        return rows, total
