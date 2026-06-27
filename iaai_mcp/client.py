"""HTTP client facade over the IAAI FastAPI surface."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from .orchestrator import ApiOrchestrator


class IaaiApiClient:
    """Typed wrapper for IAAI API endpoints used by MCP tools."""

    def __init__(self, orchestrator: ApiOrchestrator) -> None:
        self._orch = orchestrator

    async def search_lots(
        self,
        *,
        make: Optional[str] = None,
        model: Optional[str] = None,
        branch_id: Optional[str] = None,
        province: Optional[str] = None,
        primary_damage: Optional[str] = None,
        secondary_damage: Optional[str] = None,
        stock_number: Optional[str] = None,
        vin: Optional[str] = None,
        year_min: Optional[int] = None,
        year_max: Optional[int] = None,
        odometer_min: Optional[int] = None,
        odometer_max: Optional[int] = None,
        high_prebid_min: Optional[float] = None,
        high_prebid_max: Optional[float] = None,
        final_price_min: Optional[float] = None,
        final_price_max: Optional[float] = None,
        keyword: Optional[str] = None,
        status: str = "all",
        sort: str = "auction_date",
        descending: bool = False,
        limit: int = 50,
        offset: int = 0,
        runs: Optional[bool] = None,
        starts: Optional[bool] = None,
        has_keys: Optional[bool] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "status": status,
            "sort": sort,
            "descending": descending,
            "limit": limit,
            "offset": offset,
        }
        optional = {
            "make": make, "model": model, "branch_id": branch_id, "province": province,
            "primary_damage": primary_damage, "secondary_damage": secondary_damage,
            "stock_number": stock_number, "vin": vin,
            "year_min": year_min, "year_max": year_max,
            "odometer_min": odometer_min, "odometer_max": odometer_max,
            "high_prebid_min": high_prebid_min, "high_prebid_max": high_prebid_max,
            "final_price_min": final_price_min, "final_price_max": final_price_max,
            "keyword": keyword, "runs": runs, "starts": starts, "has_keys": has_keys,
        }
        for key, val in optional.items():
            if val is not None:
                params[key] = val
        return await self._orch.get_json("/lots", params=params)

    async def get_lot(self, stock_number: str) -> dict[str, Any]:
        return await self._orch.get_json(f"/lots/{stock_number}")

    async def get_price_history(self, stock_number: str) -> dict[str, Any]:
        return await self._orch.get_json(f"/lots/{stock_number}/price-history")

    async def get_stats(self) -> dict[str, Any]:
        return await self._orch.get_json("/stats")

    async def get_freshness(self) -> dict[str, Any]:
        return await self._orch.get_json("/stats/freshness")

    async def get_filters(self) -> dict[str, Any]:
        return await self._orch.get_json("/filters")

    async def get_branches(self) -> dict[str, str]:
        return await self._orch.get_json("/branches")

    async def check_ready(self) -> dict[str, Any]:
        return await self._orch.get_json("/readyz")

    async def list_commands(self) -> dict[str, Any]:
        return await self._orch.get_json("/commands")

    async def start_crawl(
        self,
        *,
        max_list_pages: Optional[int] = None,
        page_size: Optional[int] = None,
        canada_wide: bool = False,
        enrich: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"canada_wide": canada_wide, "enrich": enrich}
        if max_list_pages is not None:
            body["max_list_pages"] = max_list_pages
        if page_size is not None:
            body["page_size"] = page_size
        r = await self._orch.request("POST", "/commands/crawl", json_body=body)
        if r.status_code == 409:
            status = await self.get_crawl_status()
            return {
                "accepted": False,
                "reason": "crawl_already_running",
                "detail": r.json().get("detail", "crawl already running"),
                "current_job": status.get("job"),
            }
        r.raise_for_status()
        data = r.json()
        data["accepted"] = True
        return data

    async def get_crawl_status(self) -> dict[str, Any]:
        return await self._orch.get_json("/commands/crawl/status")

    async def get_lot_or_error(self, stock_number: str) -> dict[str, Any]:
        try:
            return await self.get_lot(stock_number)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {"error": "not_found", "stock_number": stock_number}
            raise
