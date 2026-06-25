"""Internal read API over the scraped Ontario lots (FastAPI).

Endpoints:
  GET /healthz          - liveness
  GET /stats            - totals, per-branch counts, last crawl info
  GET /lots             - filter + paginate lots
  GET /lots/{stock}     - full lot record
  GET /branches         - known Ontario branch registry

This is a read-only serving layer; the crawler is the only writer.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, Response

from . import config
from .storage import SqliteStore

app = FastAPI(title="IAAI Ontario Lots API", version="0.1.0")


def _store() -> SqliteStore:
    # Resolve DB path at call time so env overrides and tests see the right file.
    return SqliteStore(db_path=config.DB_PATH)


def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
    """Decode the stored raw JSON blob and normalize bool-ish integer columns."""
    if row.get("raw"):
        try:
            row["raw"] = json.loads(row["raw"])
        except (json.JSONDecodeError, TypeError):
            pass
    for b in ("runs", "starts", "has_keys", "is_timed_auction"):
        if row.get(b) is not None:
            row[b] = bool(row[b])
    return row


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
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


@app.get("/stats")
def stats() -> dict[str, Any]:
    store = _store()
    try:
        return store.stats()
    finally:
        store.close()


@app.get("/branches")
def branches() -> dict[str, str]:
    return {str(k): v for k, v in config.ONTARIO_BRANCH_IDS.items()}


@app.get("/lots")
def list_lots(
    make: Optional[str] = None,
    model: Optional[str] = None,
    branch_id: Optional[int] = None,
    province: Optional[str] = Query(None, description="2-letter code, e.g. ON"),
    title_brand_type: Optional[str] = None,
    auction_type: Optional[str] = None,
    year_min: Optional[int] = None,
    year_max: Optional[int] = None,
    auction_date_from: Optional[str] = Query(None, description="YYYY-MM-DD"),
    auction_date_to: Optional[str] = Query(None, description="YYYY-MM-DD"),
    runs: Optional[bool] = None,
    keyword: Optional[str] = Query(None, description="match make/model/damage"),
    status: str = Query("all", description="active | sold | if_bid | passed | removed | all"),
    sort: str = Query("auction_date"),
    descending: bool = False,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    filters = {
        "status": status,
        "make": make, "model": model, "branch_id": branch_id, "province": province,
        "title_brand_type": title_brand_type, "auction_type": auction_type,
        "year_min": year_min, "year_max": year_max,
        "auction_date_from": auction_date_from, "auction_date_to": auction_date_to,
        "runs": runs, "keyword": keyword,
    }
    store = _store()
    try:
        rows, total = store.query_lots(filters, limit=limit, offset=offset,
                                       sort=sort, descending=descending)
        return {
            "total": total, "limit": limit, "offset": offset,
            "count": len(rows), "results": [_hydrate(r) for r in rows],
        }
    finally:
        store.close()


@app.get("/lots/{stock_number}")
def get_lot(stock_number: str) -> dict[str, Any]:
    store = _store()
    try:
        row = store.get_lot(stock_number)
        if not row:
            raise HTTPException(status_code=404, detail="lot not found")
        return _hydrate(row)
    finally:
        store.close()
