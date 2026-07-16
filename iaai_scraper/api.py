"""Unified IAAI Ontario API — queries and commands on one surface.

Read endpoints (local DB only):
  GET /healthz, /readyz, /stats, /stats/freshness, /filters, /branches
  GET /lots, /lots/{stock}, /lots/{stock}/price-history
  GET /lots/{stock}/thumbnail — on-demand cached thumb (allowlisted source URL)

Commands (same process, shared crawler logic):
  GET  /commands              — list available commands
  POST /commands/crawl        — start Ontario sync (background)
  GET  /commands/crawl/status — poll crawl job status

Authentication (when ``IAAI_REQUIRE_AUTH`` is enabled or ``IAAI_API_TOKEN`` is set):
  All routes except ``GET /healthz`` require ``Authorization: Bearer <token>``
  or ``X-API-Key: <token>``. Thumbnail also accepts short-lived ``?expires=&sig=``
  (preferred for ``<img src>``) or deprecated ``?api_key=``.
  Set ``IAAI_API_TOKEN`` in production / Docker.
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field

from . import config
from .auth import (
    media_signed_href,
    require_api_auth,
    require_api_auth_flexible,
    require_command_auth,
    require_readyz_auth,
    validate_startup_auth,
)
from .images import (
    ImageCacheDisabled,
    ImageFetchError,
    ImageSourceRejected,
    ThumbnailCache,
)
from .storage import SqliteStore
from .sync_manager import sync_manager

log = logging.getLogger("iaai.api")


@asynccontextmanager
async def _lifespan(app: FastAPI):
    validate_startup_auth()
    # Ensure DDL/migrations run once here; request-path connections then skip
    # schema work entirely and stay write-free.
    store = SqliteStore(db_path=config.DB_PATH)
    try:
        removed = [
            r[0] for r in store.conn.execute(
                "SELECT stock_number FROM lots WHERE status = 'removed'"
            ).fetchall()
        ]
    finally:
        store.close()
    if config.IMAGE_CACHE_ENABLED:
        try:
            evicted = ThumbnailCache().sweep(
                ttl_s=config.IMAGE_CACHE_TTL_S or None, evict_stocks=removed,
            )
            if evicted:
                log.info("thumbnail cache sweep evicted %d entries", evicted)
        except Exception:  # noqa: BLE001 - cache hygiene must not block startup
            log.exception("thumbnail cache sweep failed")
    yield


app = FastAPI(
    title="IAAI Ontario API",
    version="0.5.0",
    description="Query scraped Ontario lots and run crawl commands through one API.",
    lifespan=_lifespan,
)

def _store() -> SqliteStore:
    return SqliteStore(db_path=config.DB_PATH)


def _hydrate(row: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Prepare a DB row for the API without exposing raw source data by default."""
    row = dict(row)
    if not include_raw:
        row.pop("raw", None)
    elif row.get("raw"):
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
    # Stable local pointer for the UI; crawl still only stores image_url text.
    # When auth is configured, embed a short-lived HMAC so <img> needs no header.
    stock = row.get("stock_number")
    if stock and row.get("image_url"):
        row["thumbnail_href"] = media_signed_href(f"/lots/{stock}/thumbnail")
    else:
        row["thumbnail_href"] = None
    return row


class CrawlCommand(BaseModel):
    max_list_pages: int = Field(default=config.MAX_LIST_PAGES, ge=1)
    # Default depends on canada_wide (1000 Ontario-at-source, 100 legacy), so
    # it is resolved in the endpoint rather than fixed here.
    page_size: Optional[int] = Field(default=None, ge=1, le=1000)
    canada_wide: bool = False
    enrich: bool = False

    def resolved_page_size(self) -> int:
        if self.page_size is not None:
            return self.page_size
        return config.PAGE_SIZE if self.canada_wide else config.ONTARIO_PAGE_SIZE


def _lot_filters(**kwargs: Any) -> dict[str, Any]:
    return {k: v for k, v in kwargs.items() if v is not None}


def _encode_cursor(sort: str, descending: bool, value: Any, stock_number: str) -> str:
    payload = json.dumps(
        {"v": 1, "sort": sort, "desc": descending, "value": value, "stock": stock_number},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str, sort: str, descending: bool) -> tuple[Any, str]:
    if len(cursor) > 2048:
        raise HTTPException(status_code=400, detail="invalid pagination cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        value = payload.get("value") if isinstance(payload, dict) else None
        text_sorts = {"auction_date", "make", "model", "last_seen", "stock_number"}
        numeric_sorts = {
            "year", "odometer", "damage_estimate", "high_prebid", "timed_high_bid",
            "final_price", "buy_now_price",
        }
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("sort") != sort
            or payload.get("desc") is not descending
            or not isinstance(payload.get("stock"), str)
            or "value" not in payload
            or (value is not None and sort in text_sorts and not isinstance(value, str))
            or (
                value is not None
                and sort in numeric_sorts
                and (isinstance(value, bool) or not isinstance(value, (int, float)))
            )
        ):
            raise ValueError("cursor contract mismatch")
        return value, payload["stock"]
    except (
        binascii.Error, KeyError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError,
    ) as exc:
        raise HTTPException(status_code=400, detail="invalid pagination cursor") from exc


# ------------------------------------------------------------------ #
# Meta / health
# ------------------------------------------------------------------ #
@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe — no auth (Docker HEALTHCHECK only)."""
    return {"status": "ok"}


@app.get("/readyz", dependencies=[Depends(require_readyz_auth)])
def readyz(response: Response) -> dict[str, Any]:
    store = _store()
    try:
        n = store.conn.execute("SELECT COUNT(*) AS n FROM lots").fetchone()["n"]
        last_run = store.conn.execute(
            "SELECT finished_at FROM crawl_runs WHERE status = 'completed' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if n <= 0:
            response.status_code = 503
            return {"status": "unavailable", "reason": "inventory_empty", "lots": 0}
        if not last_run or not last_run["finished_at"]:
            response.status_code = 503
            return {
                "status": "unavailable",
                "reason": "no_completed_crawl",
                "lots": n,
            }

        finished_at = datetime.fromisoformat(str(last_run["finished_at"]).replace("Z", "+00:00"))
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        age_seconds = max(0.0, (datetime.now(timezone.utc) - finished_at).total_seconds())
        max_age_hours = float(os.getenv("IAAI_MAX_DATA_AGE_HOURS", "24"))
        if age_seconds > max_age_hours * 3600:
            response.status_code = 503
            return {
                "status": "stale",
                "reason": "completed_crawl_too_old",
                "lots": n,
                "last_completed_at": finished_at.isoformat(),
                "age_seconds": round(age_seconds, 1),
                "max_age_hours": max_age_hours,
            }
        return {
            "status": "ready",
            "lots": n,
            "last_completed_at": finished_at.isoformat(),
            "age_seconds": round(age_seconds, 1),
        }
    except Exception:  # noqa: BLE001
        response.status_code = 503
        return {"status": "unavailable", "reason": "database_check_failed"}
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


@app.post("/commands/crawl", status_code=202, dependencies=[Depends(require_command_auth)])
async def command_crawl(body: CrawlCommand = CrawlCommand()) -> dict[str, Any]:
    """Start a crawl. Returns immediately; poll ``GET /commands/crawl/status``."""
    if sync_manager.is_running:
        raise HTTPException(status_code=409, detail="crawl already running")
    ontario_at_source = not body.canada_wide
    settings = config.CrawlSettings(
        page_size=body.resolved_page_size(),
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
    branch_id: Optional[str] = Query(
        None,
        pattern=r"^\d+(,\d+)*$",
        description="branch id or comma-separated ids",
    ),
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
    status: Literal["active", "sold", "if_bid", "passed", "removed", "all"] = "all",
    sort: Literal[
        "auction_date", "year", "make", "model", "odometer", "damage_estimate",
        "last_seen", "stock_number", "high_prebid", "timed_high_bid", "final_price",
        "buy_now_price",
    ] = "auction_date",
    descending: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    cursor: Optional[str] = Query(None, description="opaque continuation cursor"),
    include_raw: bool = Query(False, description="include preserved source payload"),
) -> dict[str, Any]:
    if include_raw and limit > 100:
        raise HTTPException(status_code=400, detail="include_raw requires limit <= 100")
    if cursor and offset:
        raise HTTPException(status_code=400, detail="cursor and non-zero offset are mutually exclusive")
    after = _decode_cursor(cursor, sort, descending) if cursor else None
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
            filters, limit=limit, offset=offset, sort=sort, descending=descending, after=after,
        )
        next_cursor = None
        if len(rows) == limit:
            next_cursor = _encode_cursor(
                sort, descending, rows[-1][sort], rows[-1]["stock_number"],
            )
        return {
            "total": total, "limit": limit, "offset": offset,
            "count": len(rows),
            "next_cursor": next_cursor,
            "next_offset": offset + len(rows) if not cursor and offset + len(rows) < total else None,
            "results": [_hydrate(r, include_raw=include_raw) for r in rows],
        }
    finally:
        store.close()


@app.get("/lots/{stock_number}", dependencies=[Depends(require_api_auth)])
def get_lot(
    stock_number: str,
    include_raw: bool = Query(False, description="include preserved source payload"),
) -> dict[str, Any]:
    store = _store()
    try:
        row = store.get_lot(stock_number)
        if not row:
            raise HTTPException(status_code=404, detail="lot not found")
        return _hydrate(row, include_raw=include_raw)
    finally:
        store.close()


@app.get("/lots/{stock_number}/thumbnail", dependencies=[Depends(require_api_auth_flexible)])
def get_lot_thumbnail(stock_number: str) -> Response:
    """Serve a cached thumbnail for a lot (fetch-on-miss from allowlisted ``image_url``)."""
    store = _store()
    try:
        row = store.get_lot(stock_number)
        if not row:
            raise HTTPException(status_code=404, detail="lot not found")
        source_url = row.get("image_url")
        if not source_url:
            raise HTTPException(status_code=404, detail="lot has no image_url")
    finally:
        store.close()

    cache = ThumbnailCache()
    try:
        thumb = cache.get_or_fetch(str(stock_number), str(source_url))
    except ImageCacheDisabled as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ImageSourceRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ImageFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    headers = {
        "Cache-Control": config.IMAGE_CACHE_CONTROL,
        "X-Image-Cache": "HIT" if thumb.from_cache else "MISS",
    }
    return Response(content=thumb.body, media_type=thumb.content_type, headers=headers)


@app.get("/lots/{stock_number}/price-history", dependencies=[Depends(require_api_auth)])
def get_price_history(
    stock_number: str,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    store = _store()
    try:
        if not store.get_lot(stock_number):
            raise HTTPException(status_code=404, detail="lot not found")
        total = store.count_price_history(stock_number)
        page = store.get_price_history(stock_number, limit=limit, offset=offset)
        return {
            "stock_number": stock_number,
            "total": total,
            "count": len(page),
            "limit": limit,
            "offset": offset,
            "history": page,
        }
    finally:
        store.close()


@app.get("/lots/{stock_number}/status-history", dependencies=[Depends(require_api_auth)])
def get_status_history(
    stock_number: str,
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    store = _store()
    try:
        if not store.get_lot(stock_number):
            raise HTTPException(status_code=404, detail="lot not found")
        total = store.count_status_history(stock_number)
        page = store.get_status_history(stock_number, limit=limit, offset=offset)
        return {
            "stock_number": stock_number,
            "total": total,
            "count": len(page),
            "limit": limit,
            "offset": offset,
            "history": page,
        }
    finally:
        store.close()
