"""Unified IAAI Ontario API — queries and commands on one surface.

Read endpoints (local DB only):
  GET /healthz, /readyz, /stats, /stats/freshness, /filters, /branches
  GET /lots, /lots/{stock}, /lots/{stock}/price-history

Commands (same process, shared crawler logic):
  GET  /commands              — list available commands
  POST /commands/crawl        — start Ontario sync (background)
  GET  /commands/crawl/status — poll crawl job status

Authentication (when ``IAAI_REQUIRE_AUTH`` is enabled or ``IAAI_API_TOKEN`` is set):
  All routes except ``GET /healthz`` require ``Authorization: Bearer <token>``
  or ``X-API-Key: <token>``. Set ``IAAI_API_TOKEN`` in production / Docker.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from . import config
from .auth import require_api_auth, validate_startup_auth
from .storage import SqliteStore
from .sync_manager import sync_manager


@asynccontextmanager
async def _lifespan(app: FastAPI):
    validate_startup_auth()
    yield


app = FastAPI(
    title="IAAI Ontario API",
    version="0.4.0",
    description="Query scraped Ontario lots and run crawl commands through one API.",
    lifespan=_lifespan,
)

def _store() -> SqliteStore:
    return SqliteStore(db_path=config.DB_PATH)


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("raw"):
        try:
            row["raw"] = json.loads(row["raw"])
        except (json.JSONDecodeError, TypeError):
            pass
    for b in (
        "runs", "starts", "has_keys", "is_timed_auction",
        "is_auction_closed", "is_regular_auction", "auction_offsite",
        "prebid_allowed", "prebid_closed", "is_buy_now", "buy_now_allowed",
    ):
        if row.get(b) is not None:
            row[b] = bool(row[b])
    return row


class CrawlCommand(BaseModel):
    max_list_pages: int = Field(default=config.MAX_LIST_PAGES, ge=1)
    page_size: int = Field(
        default=config.ONTARIO_PAGE_SIZE if config.ONTARIO_AT_SOURCE else config.PAGE_SIZE,
        ge=1, le=1000,
    )
    canada_wide: bool = False
    enrich: bool = False


def _lot_filters(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


# ------------------------------------------------------------------ #
# Meta / health
# ------------------------------------------------------------------ #
@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe — no auth (Docker HEALTHCHECK only)."""
    return {"status": "ok"}


@app.get("/readyz", dependencies=[Depends(require_api_auth)])
def readyz(response: Response) -> dict[str, Any]:
    store = _store()
    try:
        n = store.conn.execute("SELECT COUNT(*) AS n FROM lots").fetchone()["n"]
        return {"status": "ready", "lots": n}
    except Exception as e:  # noqa: BLE001
        response.status_code = 503
        return {"status": "unavailable", "error": str(e)}
    finally:
        store.close()


@app.get("/stats", dependencies=[Depends(require_api_auth)])
def stats() -> dict[str, Any]:
    store = _store()
    try:
        return store.stats()
    finally:
        store.close()


@app.get("/stats/freshness", dependencies=[Depends(require_api_auth)])
def stats_freshness() -> dict[str, Any]:
    store = _store()
    try:
        return store.freshness()
    finally:
        store.close()


@app.get("/filters", dependencies=[Depends(require_api_auth)])
def list_filters() -> dict[str, Any]:
    store = _store()
    try:
        return store.filters()
    finally:
        store.close()


@app.get("/branches", dependencies=[Depends(require_api_auth)])
def branches() -> dict[str, str]:
    return {str(k): v for k, v in config.ONTARIO_BRANCH_IDS.items()}


# ------------------------------------------------------------------ #
# Commands
# ------------------------------------------------------------------ #
@app.get("/commands", dependencies=[Depends(require_api_auth)])
def list_commands() -> dict[str, Any]:
    return {
        "commands": [
            {
                "name": "crawl",
                "method": "POST",
                "path": "/commands/crawl",
                "description": "Sync Ontario lots from IAAI into the local DB (background job)",
                "status": "GET /commands/crawl/status",
            },
        ],
    }


@app.post("/commands/crawl", status_code=202, dependencies=[Depends(require_api_auth)])
async def command_crawl(body: CrawlCommand = CrawlCommand()) -> dict[str, Any]:
    """Start a crawl. Returns immediately; poll ``GET /commands/crawl/status``."""
    if sync_manager.is_running:
        raise HTTPException(status_code=409, detail="crawl already running")
    ontario_at_source = not body.canada_wide
    settings = config.CrawlSettings(
        page_size=body.page_size,
        max_list_pages=body.max_list_pages,
        enrich_details=body.enrich,
        ontario_at_source=ontario_at_source,
        branch_ids=config.ONTARIO_BRANCH_IDS_CSV if ontario_at_source else "",
    )
    try:
        job = await sync_manager.start_crawl(settings)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {
        "command": "crawl",
        "accepted": True,
        "job_id": job.job_id,
        "message": "Crawl started. Poll GET /commands/crawl/status for progress.",
    }


@app.get("/commands/crawl/status", dependencies=[Depends(require_api_auth)])
def command_crawl_status() -> dict[str, Any]:
    return sync_manager.status()


# ------------------------------------------------------------------ #
# Lots (read from local DB)
# ------------------------------------------------------------------ #
@app.get("/lots", dependencies=[Depends(require_api_auth)])
def list_lots(
    make: Optional[str] = Query(None, description="make or comma-separated makes"),
    model: Optional[str] = Query(None, description="model or comma-separated models"),
    branch_id: Optional[str] = Query(None, description="branch id or comma-separated ids"),
    province: Optional[str] = Query(None, description="2-letter code, e.g. ON"),
    title_brand_type: Optional[str] = None,
    auction_type: Optional[str] = None,
    primary_damage: Optional[str] = None,
    secondary_damage: Optional[str] = None,
    stock_number: Optional[str] = None,
    vin: Optional[str] = Query(None, description="partial VIN match"),
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    odometer_min: Optional[int] = None,
    odometer_max: Optional[int] = None,
    high_prebid_min: Optional[float] = None,
    high_prebid_max: Optional[float] = None,
    timed_high_bid_min: Optional[float] = None,
    timed_high_bid_max: Optional[float] = None,
    final_price_min: Optional[float] = None,
    final_price_max: Optional[float] = None,
    buy_now_price_min: Optional[float] = None,
    buy_now_price_max: Optional[float] = None,
    damage_estimate_min: Optional[float] = None,
    damage_estimate_max: Optional[float] = None,
    auction_date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    auction_date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    sold_from: Optional[str] = Query(None, description="ISO datetime — sold archive lower bound"),
    sold_to: Optional[str] = Query(None, description="ISO datetime — sold archive upper bound"),
    runs: Optional[bool] = None,
    starts: Optional[bool] = None,
    has_keys: Optional[bool] = None,
    is_timed_auction: Optional[bool] = None,
    is_auction_closed: Optional[bool] = None,
    keyword: Optional[str] = Query(None, description="match make/model/damage/stock"),
    status: str = Query("all", description="active | sold | if_bid | passed | removed | all"),
    sort: str = Query("auction_date"),
    descending: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    filters = _lot_filters(
        status=status,
        make=make, model=model, branch_id=branch_id, province=province,
        title_brand_type=title_brand_type, auction_type=auction_type,
        primary_damage=primary_damage, secondary_damage=secondary_damage,
        stock_number=stock_number, vin=vin,
        year_min=year_min, year_max=year_max,
        odometer_min=odometer_min, odometer_max=odometer_max,
        high_prebid_min=high_prebid_min, high_prebid_max=high_prebid_max,
        timed_high_bid_min=timed_high_bid_min, timed_high_bid_max=timed_high_bid_max,
        final_price_min=final_price_min, final_price_max=final_price_max,
        buy_now_price_min=buy_now_price_min, buy_now_price_max=buy_now_price_max,
        damage_estimate_min=damage_estimate_min, damage_estimate_max=damage_estimate_max,
        auction_date_from=auction_date_from, auction_date_to=auction_date_to,
        sold_from=sold_from, sold_to=sold_to,
        runs=runs, starts=starts, has_keys=has_keys,
        is_timed_auction=is_timed_auction, is_auction_closed=is_auction_closed,
        keyword=keyword,
    )
    store = _store()
    try:
        rows, total = store.query_lots(
            filters, limit=limit, offset=offset, sort=sort, descending=descending,
        )
        return {
            "total": total, "limit": limit, "offset": offset,
            "count": len(rows), "results": [_hydrate(r) for r in rows],
        }
    finally:
        store.close()


@app.get("/lots/{stock_number}", dependencies=[Depends(require_api_auth)])
def get_lot(stock_number: str) -> dict[str, Any]:
    store = _store()
    try:
        row = store.get_lot(stock_number)
        if not row:
            raise HTTPException(status_code=404, detail="lot not found")
        return _hydrate(row)
    finally:
        store.close()


@app.get("/lots/{stock_number}/price-history", dependencies=[Depends(require_api_auth)])
def get_price_history(stock_number: str) -> dict[str, Any]:
    store = _store()
    try:
        if not store.get_lot(stock_number):
            raise HTTPException(status_code=404, detail="lot not found")
        history = store.get_price_history(stock_number)
        return {"stock_number": stock_number, "count": len(history), "history": history}
    finally:
        store.close()
