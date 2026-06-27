"""MCP server exposing IAAI Ontario auction data and sync commands to LLMs."""
import json
import logging
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import IaaiApiClient
from .config import McpSettings
from .orchestrator import ApiOrchestrator

log = logging.getLogger("iaai_mcp.server")

_settings: McpSettings | None = None
_orchestrator: ApiOrchestrator | None = None
_client: IaaiApiClient | None = None


def _get_client() -> IaaiApiClient:
    if _client is None:
        raise RuntimeError("MCP server not initialized")
    return _client


@asynccontextmanager
async def _lifespan(server: FastMCP):
    del server
    try:
        yield
    finally:
        if _orchestrator is not None:
            await _orchestrator.close()


def create_mcp_server(
    settings: McpSettings | None = None,
    *,
    orchestrator: ApiOrchestrator | None = None,
    client: IaaiApiClient | None = None,
) -> FastMCP:
    """Build a configured FastMCP server (used by CLI and tests)."""
    global _settings, _orchestrator, _client

    _settings = settings or McpSettings.from_env()
    _settings.validate()
    _orchestrator = orchestrator or ApiOrchestrator(_settings)
    _client = client or IaaiApiClient(_orchestrator)

    mcp = FastMCP(
        "IAAI Ontario Auctions",
        host=_settings.host,
        port=_settings.port,
        lifespan=_lifespan,
    )

    # ------------------------------------------------------------------ #
    # Tools — read (local DB via API)
    # ------------------------------------------------------------------ #
    @mcp.tool(
        description=(
            "Search Ontario IAAI auction lots with filters. Returns paginated results "
            "from the local database (instant — no live crawl). Use status=active for "
            "upcoming auctions, sold/if_bid/passed for concluded lots."
        ),
    )
    async def search_lots(
        make: str | None = None,
        model: str | None = None,
        branch_id: str | None = None,
        province: str | None = None,
        primary_damage: str | None = None,
        secondary_damage: str | None = None,
        stock_number: str | None = None,
        vin: str | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        odometer_min: int | None = None,
        odometer_max: int | None = None,
        high_prebid_min: float | None = None,
        high_prebid_max: float | None = None,
        final_price_min: float | None = None,
        final_price_max: float | None = None,
        keyword: str | None = None,
        status: str = "all",
        sort: str = "auction_date",
        descending: bool = False,
        limit: int = 50,
        offset: int = 0,
        runs: bool | None = None,
        starts: bool | None = None,
        has_keys: bool | None = None,
    ) -> dict[str, Any]:
        return await _get_client().search_lots(
            make=make, model=model, branch_id=branch_id, province=province,
            primary_damage=primary_damage, secondary_damage=secondary_damage,
            stock_number=stock_number, vin=vin,
            year_min=year_min, year_max=year_max,
            odometer_min=odometer_min, odometer_max=odometer_max,
            high_prebid_min=high_prebid_min, high_prebid_max=high_prebid_max,
            final_price_min=final_price_min, final_price_max=final_price_max,
            keyword=keyword, status=status, sort=sort, descending=descending,
            limit=limit, offset=offset, runs=runs, starts=starts, has_keys=has_keys,
        )

    @mcp.tool(description="Get full details for a single lot by stock number.")
    async def get_lot(stock_number: str) -> dict[str, Any]:
        return await _get_client().get_lot_or_error(stock_number)

    @mcp.tool(
        description=(
            "Get prebid and status change history for a lot. "
            "Shows bid increases over time and lifecycle events."
        ),
    )
    async def get_price_history(stock_number: str) -> dict[str, Any]:
        return await _get_client().get_price_history(stock_number)

    @mcp.tool(description="Database statistics: lot counts by status, branch, make.")
    async def get_stats() -> dict[str, Any]:
        return await _get_client().get_stats()

    @mcp.tool(description="Crawl freshness: last sync time, inserts/updates from last run.")
    async def get_freshness() -> dict[str, Any]:
        return await _get_client().get_freshness()

    @mcp.tool(
        description=(
            "Distinct filter values available in the DB (makes, models, years, "
            "branches, statuses). Use before search_lots to discover valid filter values."
        ),
    )
    async def get_filters() -> dict[str, Any]:
        return await _get_client().get_filters()

    @mcp.tool(description="Ontario branch IDs and names (London, Hamilton, Toronto, etc.).")
    async def get_branches() -> dict[str, str]:
        return await _get_client().get_branches()

    @mcp.tool(description="Check whether the API and database are ready to serve queries.")
    async def check_ready() -> dict[str, Any]:
        return await _get_client().check_ready()

    # ------------------------------------------------------------------ #
    # Tools — commands (may trigger Playwright crawl)
    # ------------------------------------------------------------------ #
    @mcp.tool(description="List available API commands (currently: crawl).")
    async def list_commands() -> dict[str, Any]:
        return await _get_client().list_commands()

    @mcp.tool(
        description=(
            "Start a background sync from IAAI Ontario into the local database (~20–30s). "
            "Returns immediately; poll get_crawl_status for progress. "
            "Only one crawl runs at a time — if already running, returns the active job."
        ),
    )
    async def start_crawl(
        max_list_pages: int | None = None,
        page_size: int | None = None,
        canada_wide: bool = False,
        enrich: bool = False,
    ) -> dict[str, Any]:
        return await _get_client().start_crawl(
            max_list_pages=max_list_pages,
            page_size=page_size,
            canada_wide=canada_wide,
            enrich=enrich,
        )

    @mcp.tool(description="Poll the status of the current or most recent crawl job.")
    async def get_crawl_status() -> dict[str, Any]:
        return await _get_client().get_crawl_status()

    # ------------------------------------------------------------------ #
    # Resources — read-only snapshots for context injection
    # ------------------------------------------------------------------ #
    @mcp.resource("iaai://stats")
    async def resource_stats() -> str:
        data = await _get_client().get_stats()
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("iaai://freshness")
    async def resource_freshness() -> str:
        data = await _get_client().get_freshness()
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("iaai://filters")
    async def resource_filters() -> str:
        data = await _get_client().get_filters()
        return json.dumps(data, indent=2, default=str)

    @mcp.resource("iaai://branches")
    async def resource_branches() -> str:
        data = await _get_client().get_branches()
        return json.dumps(data, indent=2, default=str)

    # ------------------------------------------------------------------ #
    # Prompts — guided workflows for LLMs
    # ------------------------------------------------------------------ #
    @mcp.prompt(name="find_vehicles")
    def prompt_find_vehicles(
        make: str = "",
        budget_max: str = "",
        year_min: str = "",
    ) -> str:
        return (
            "Find Ontario IAAI auction lots matching these criteria:\n"
            f"- Make: {make or 'any'}\n"
            f"- Max prebid/budget: {budget_max or 'any'} CAD\n"
            f"- Minimum year: {year_min or 'any'}\n\n"
            "Steps:\n"
            "1. Call get_filters() to confirm make spelling.\n"
            "2. Call search_lots() with status=active and appropriate filters.\n"
            "3. For interesting lots, call get_lot() and get_price_history().\n"
            "4. Summarize: stock number, year/make/model, damage, prebid, auction date, branch."
        )

    @mcp.prompt(name="sync_and_report")
    def prompt_sync_and_report() -> str:
        return (
            "Sync the latest Ontario IAAI auction data and report changes:\n"
            "1. Call get_freshness() for current state.\n"
            "2. Call start_crawl() to sync from IAAI.\n"
            "3. Poll get_crawl_status() until completed or failed.\n"
            "4. Call get_freshness() again and summarize: new lots, updates, archived.\n"
            "5. Optionally search_lots with status=active and sort=auction_date for highlights."
        )

    return mcp
