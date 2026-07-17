"""Post-crawl thumbnail warmer (optional Phase 1.5 / tracker 3.4).

Prefetches allowlisted ``image_url`` thumbs for the first page of active lots so
the default UI list does not cold-miss. Never runs on the crawl hot path unless
explicitly invoked (CLI ``warm-thumbs`` or ``IAAI_WARM_THUMBS=1`` after crawl).
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from . import config
from .images import (
    ImageCacheDisabled,
    ImageCacheError,
    ImageFetchError,
    ImageSourceRejected,
    ThumbnailCache,
)
from .storage import SqliteStore

log = logging.getLogger("iaai.thumb_warmer")


@dataclass
class WarmReport:
    requested: int = 0
    warmed: int = 0
    cache_hits: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def warm_active_thumbs(
    *,
    limit: int = 50,
    db_path: Optional[Any] = None,
    cache: Optional[ThumbnailCache] = None,
    workers: int = 4,
) -> WarmReport:
    """Warm thumbs for up to ``limit`` active lots (default GET /lots order)."""
    limit = max(0, int(limit))
    report = WarmReport()
    if limit == 0:
        return report

    store = SqliteStore(db_path=db_path) if db_path is not None else SqliteStore()
    cache = cache or ThumbnailCache()
    try:
        # Match list_lots defaults: auction_date ascending (first UI page).
        rows, _total = store.query_lots(
            {"status": "active"},
            limit=limit,
            offset=0,
            sort="auction_date",
            descending=False,
        )
    finally:
        store.close()

    targets: list[tuple[str, str]] = []
    for row in rows:
        stock = row.get("stock_number")
        url = row.get("image_url")
        if not stock or not url:
            report.skipped += 1
            continue
        targets.append((str(stock), str(url)))
    report.requested = len(targets)

    def _one(item: tuple[str, str]) -> tuple[str, str, Optional[str]]:
        stock, url = item
        try:
            thumb = cache.get_or_fetch(stock, url)
            return stock, ("hit" if thumb.from_cache else "miss"), None
        except ImageCacheDisabled as exc:
            return stock, "skipped", str(exc)
        except ImageSourceRejected as exc:
            return stock, "skipped", str(exc)
        except (ImageFetchError, ImageCacheError, OSError) as exc:
            return stock, "failed", f"{type(exc).__name__}: {exc}"

    if not targets:
        return report

    workers = max(1, min(int(workers), len(targets)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, t) for t in targets]
        for fut in as_completed(futures):
            stock, outcome, err = fut.result()
            if outcome == "hit":
                report.cache_hits += 1
                report.warmed += 1
            elif outcome == "miss":
                report.warmed += 1
            elif outcome == "skipped":
                report.skipped += 1
            else:
                report.failed += 1
                if err and len(report.errors) < 20:
                    report.errors.append(f"{stock}: {err}")
            if err and outcome == "failed":
                log.warning("thumb warm failed stock=%s: %s", stock, err)

    log.info(
        "thumb warm done requested=%s warmed=%s hits=%s skipped=%s failed=%s",
        report.requested,
        report.warmed,
        report.cache_hits,
        report.skipped,
        report.failed,
    )
    return report
