"""Orchestrates a full Ontario crawl with completeness + safety guarantees.

Approach (validated in Phase 0):
  * Page through the *entire* Canada result set sorted by ``STOCK ASC``. That sort
    is on the immutable stock number, so pages never overlap or shift as live
    auctions churn — this is what makes the crawl complete.
  * Classify each lot as Ontario by StockBranchId (validated by branch name) and
    keep only those.
  * Dedup by stock number across the whole run.

Safeguards:
  * Hard page cap (MAX_LIST_PAGES) so a pagination bug can never loop forever.
  * "No new rows" detection: if a page contributes zero unseen stock numbers we
    stop (defends against a server that repeats the last page at the end).
  * Completeness check against the authoritative TOTAL_COUNT, logged + recorded.
  * Per-row parsing is defensive; one bad row is skipped, not fatal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import config, detail
from .parser import parse_row
from .search_client import SearchClient
from .session import IaaiSession
from .storage import RawWriter, SqliteStore

log = logging.getLogger("iaai.crawler")


@dataclass
class CrawlReport:
    started_at: datetime
    finished_at: Optional[datetime] = None
    total_canada: Optional[int] = None
    pages: int = 0
    canada_rows_seen: int = 0
    ontario_seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_bad_rows: int = 0
    status: str = "running"
    note: str = ""
    ontario_by_branch: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"status={self.status} pages={self.pages} canada_seen={self.canada_rows_seen}"
            f"/{self.total_canada} ontario={self.ontario_seen} "
            f"(ins={self.inserted} upd={self.updated} unch={self.unchanged} "
            f"bad={self.skipped_bad_rows})"
        )


class Crawler:
    def __init__(self, settings: Optional[config.CrawlSettings] = None):
        self.settings = settings or config.CrawlSettings()

    async def run(self) -> CrawlReport:
        report = CrawlReport(started_at=datetime.now(timezone.utc))
        store = SqliteStore()
        raw = RawWriter()
        seen_stock: set[str] = set()           # dedup + loop guard across the whole run

        try:
            async with IaaiSession(self.settings) as session:
                client = SearchClient(session, self.settings)
                page = 1
                while page <= self.settings.max_list_pages:
                    rows, total = await client.fetch_page(page)
                    if report.total_canada is None and total is not None:
                        report.total_canada = total
                        log.info("Authoritative Canada total: %d", total)

                    if not rows:
                        log.info("Page %d returned 0 rows -> end of results", page)
                        break

                    new_on_page = await self._process_page(
                        rows, seen_stock, store, raw, report, session
                    )
                    report.pages = page
                    report.canada_rows_seen = len(seen_stock)
                    store.commit()
                    log.info("Page %d: %d rows, %d new (running ontario=%d, canada=%d/%s)",
                             page, len(rows), new_on_page, report.ontario_seen,
                             len(seen_stock), report.total_canada)

                    # --- stop conditions -------------------------------- #
                    if new_on_page == 0:
                        log.info("No new stock numbers on page %d -> stopping (loop guard)", page)
                        break
                    if len(rows) < self.settings.page_size:
                        log.info("Short page (%d < %d) -> last page reached",
                                 len(rows), self.settings.page_size)
                        break
                    if report.total_canada and len(seen_stock) >= report.total_canada:
                        log.info("Collected all %d Canada lots -> complete", report.total_canada)
                        break

                    await session.polite_delay()
                    page += 1
                else:
                    report.note = f"hit MAX_LIST_PAGES={self.settings.max_list_pages}"
                    log.warning(report.note)

            report.status = self._final_status(report)
        except Exception as e:  # noqa: BLE001 - record failure, don't crash silently
            report.status = "failed"
            report.note = f"{type(e).__name__}: {e}"
            log.exception("Crawl failed")
        finally:
            report.finished_at = datetime.now(timezone.utc)
            raw.close()
            store.record_run(
                started_at=report.started_at, finished_at=report.finished_at,
                total_canada=report.total_canada, ontario_seen=report.ontario_seen,
                inserted=report.inserted, updated=report.updated, pages=report.pages,
                status=report.status, note=report.note,
            )
            store.close()
        log.info("Crawl done: %s", report.summary())
        return report

    async def _process_page(self, rows, seen_stock, store, raw, report, session) -> int:
        """Parse + filter + store one page. Returns count of new stock numbers seen."""
        new_count = 0
        for row in rows:
            lot = parse_row(row)
            if lot is None:
                report.skipped_bad_rows += 1
                continue

            # Dedup / loop guard: only act on stock numbers not yet seen this run.
            if lot.stock_number in seen_stock:
                continue
            seen_stock.add(lot.stock_number)
            new_count += 1

            # Keep only Ontario lots.
            if not self.settings.is_ontario(lot.branch_id, lot.branch_name):
                continue

            report.ontario_seen += 1
            report.ontario_by_branch[lot.branch_name or "?"] = (
                report.ontario_by_branch.get(lot.branch_name or "?", 0) + 1
            )

            # Optional per-lot enrichment (off by default).
            if self.settings.enrich_details:
                lot = await detail.enrich(session, lot)
                await session.polite_delay()

            raw.write(row)
            outcome = store.upsert_lot(lot)
            if outcome == "inserted":
                report.inserted += 1
            elif outcome == "updated":
                report.updated += 1
            else:
                report.unchanged += 1
        return new_count

    @staticmethod
    def _final_status(report: CrawlReport) -> str:
        """Flag completeness: did we page through (approximately) the whole set?"""
        if report.total_canada is None:
            return "completed_unknown_total"
        # Allow small slack for live inventory churn during the crawl.
        if report.canada_rows_seen >= report.total_canada * 0.98:
            return "completed"
        return "completed_partial"
