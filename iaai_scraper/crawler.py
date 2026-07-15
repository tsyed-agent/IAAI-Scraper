"""Orchestrates a full Ontario crawl with completeness + safety guarantees.

Approach (default, ``ontario_at_source=True``):
  * Request only Ontario branches via ``BranchIds`` + large ``PageSize`` (doc 06).
  * Page through the Ontario result set sorted by ``STOCK ASC``.

Legacy mode (``ontario_at_source=False``):
  * Page through the entire Canada result set, filter to Ontario by StockBranchId.

Both modes dedup by stock number and share the same safeguards.
"""
from __future__ import annotations

import logging
import os
import fcntl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from . import config, detail
from .parser import parse_row
from .search_client import SearchClient, SearchResponseError
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
    archived: int = 0
    status: str = "running"
    note: str = ""
    ontario_by_branch: dict[str, int] = field(default_factory=dict)
    recovery_attempts: int = 0
    empty_pages: int = 0
    short_pages: int = 0
    duplicate_pages: int = 0
    anomalies: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"status={self.status} pages={self.pages} canada_seen={self.canada_rows_seen}"
            f"/{self.total_canada} ontario={self.ontario_seen} "
            f"(ins={self.inserted} upd={self.updated} unch={self.unchanged} "
            f"bad={self.skipped_bad_rows} archived={self.archived} "
            f"recoveries={self.recovery_attempts})"
        )


class Crawler:
    def __init__(self, settings: Optional[config.CrawlSettings] = None):
        self.settings = settings or config.CrawlSettings()

    async def run(self) -> CrawlReport:
        report = CrawlReport(started_at=datetime.now(timezone.utc))
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        lock_path = config.DATA_DIR / "crawl.lock"
        lock_fd = self._acquire_lock(lock_path)
        store = None
        raw = None
        seen_stock: set[str] = set()           # dedup + loop guard across the whole run
        run_id = None

        scope = "Ontario (BranchIds)" if self.settings.ontario_at_source else "Canada-wide"
        log.info("Crawl scope: %s, page_size=%d", scope, self.settings.page_size)

        try:
            # Initialization is guarded so a failed DB/raw setup never leaks
            # the process-owned crawl lock.
            store = SqliteStore()
            raw = RawWriter()
            run_id = store.begin_run(report.started_at)
            async with IaaiSession(self.settings) as session:
                client = SearchClient(session, self.settings)
                page = 1
                while page <= self.settings.max_list_pages:
                    rows, total = await self._fetch_page_with_recovery(
                        client, session, page, seen_stock, report, raw, run_id
                    )
                    if report.total_canada is None and total is not None:
                        report.total_canada = total
                        log.info("Authoritative %s total: %d", scope, total)

                    if not rows:
                        log.info("Page %d returned 0 rows -> end of results", page)
                        break

                    new_on_page, parsed_on_page = await self._process_page(
                        rows, seen_stock, store, raw, report, session, page, run_id
                    )
                    report.pages = page
                    report.canada_rows_seen = len(seen_stock)
                    if report.total_canada is not None and report.canada_rows_seen > report.total_canada:
                        msg = (f"page {page}: collected {report.canada_rows_seen} unique rows, "
                               f"exceeding authoritative total {report.total_canada}")
                        report.anomalies.append(msg)
                        raise RuntimeError(msg)
                    store.commit()
                    raw.flush()
                    log.info("Page %d: %d rows, %d new, %d parsed (ontario=%d, seen=%d/%s)",
                             page, len(rows), new_on_page, parsed_on_page, report.ontario_seen,
                             len(seen_stock), report.total_canada)

                    # --- stop conditions -------------------------------- #
                    if parsed_on_page == 0:
                        msg = f"page {page}: {len(rows)} rows but none parsed (schema drift?)"
                        report.anomalies.append(msg)
                        raise RuntimeError(msg)
                    if new_on_page == 0:
                        # Loop guard for a repeating last page — but fail loudly if we
                        # Never treat overlap as complete unless the exact total
                        # has already been collected.
                        if report.total_canada and len(seen_stock) < report.total_canada:
                            msg = (f"page {page}: 0 new stock numbers but only "
                                   f"{len(seen_stock)}/{report.total_canada} collected "
                                   "— pagination overlap or server error?")
                            report.anomalies.append(msg)
                            raise RuntimeError(msg)
                        log.info(
                            "No new stock numbers on page %d -> stopping (loop guard)",
                            page,
                        )
                        break
                    if len(rows) < self.settings.page_size:
                        report.short_pages += 1
                        log.info("Short page (%d < %d) -> last page reached",
                                 len(rows), self.settings.page_size)
                        if report.total_canada and len(seen_stock) < report.total_canada:
                            report.anomalies.append(
                                f"page {page}: short page {len(rows)}/{self.settings.page_size}; "
                                f"collected {len(seen_stock)}/{report.total_canada}"
                            )
                        break
                    if report.total_canada and len(seen_stock) >= report.total_canada:
                        log.info("Collected all %d lots -> complete", report.total_canada)
                        break

                    await session.polite_delay()
                    page += 1
                else:
                    report.note = f"hit MAX_LIST_PAGES={self.settings.max_list_pages}"
                    log.warning(report.note)

            # A snapshot is not eligible to change lifecycle/archive state
            # until its replay artifact has been durably finalized.
            landing = raw
            raw = None
            try:
                landing.close()
            except Exception as exc:
                raise RuntimeError(f"raw archive finalization failed: {exc}") from exc
            report.status = self._final_status(report)
            if report.skipped_bad_rows:
                report.status = "failed"
                report.note = report.note or (
                    f"{report.skipped_bad_rows} rows failed parsing; snapshot is not archival-safe"
                )
            if report.status == "completed" and not report.anomalies:
                # Keep lifecycle mutations in the same transaction as the
                # completed crawl-run record. If finalization fails, close()
                # rolls the archival changes back instead of leaving an
                # unrecorded or partially recorded snapshot.
                report.archived = store.archive_missing(seen_stock, commit=False)
                log.info("Archived %d lots no longer listed", report.archived)
        except Exception as e:  # noqa: BLE001 - record failure, don't crash silently
            report.status = "failed"
            report.note = f"{type(e).__name__}: {e}"
            log.exception("Crawl failed")
        finally:
            report.finished_at = datetime.now(timezone.utc)
            try:
                if raw is not None:
                    try:
                        raw.close()
                    except Exception as exc:  # noqa: BLE001 - make durability loss visible
                        report.status = "failed"
                        report.note = self._append_note(
                            report.note,
                            f"raw archive finalization failed: {type(exc).__name__}: {exc}",
                        )
                        log.exception("Raw archive finalization failed")
                if store is not None and run_id is not None:
                    try:
                        store.finish_run(
                            run_id,
                            started_at=report.started_at, finished_at=report.finished_at,
                            total_canada=report.total_canada, ontario_seen=report.ontario_seen,
                            inserted=report.inserted, updated=report.updated,
                            unchanged=report.unchanged, skipped_bad_rows=report.skipped_bad_rows,
                            canada_rows_seen=report.canada_rows_seen, archived=report.archived,
                            pages=report.pages, status=report.status, note=report.note,
                        )
                    except Exception as exc:  # noqa: BLE001 - report operational failure
                        report.status = "failed"
                        report.note = self._append_note(
                            report.note,
                            f"crawl run finalization failed: {type(exc).__name__}: {exc}",
                        )
                        log.exception("Crawl run finalization failed")
                if store is not None:
                    try:
                        store.close()
                    except Exception as exc:  # noqa: BLE001 - lock must still be released
                        report.status = "failed"
                        report.note = self._append_note(
                            report.note,
                            f"database close failed: {type(exc).__name__}: {exc}",
                        )
                        log.exception("Database close failed")
            finally:
                self._release_lock(lock_fd, lock_path)
        log.info("Crawl done: %s", report.summary())
        return report

    @staticmethod
    def _acquire_lock(lock_path):
        """Acquire an OS-owned advisory lock and record its owning PID."""
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            os.close(fd)
            raise RuntimeError(f"Another crawl appears to be running (lock: {lock_path})") from exc
        os.ftruncate(fd, 0)
        os.write(fd, f"pid={os.getpid()}\nstarted={datetime.now(timezone.utc).isoformat()}\n".encode())
        os.fsync(fd)
        return fd

    @staticmethod
    def _release_lock(lock_fd, lock_path):
        # Keep the inode persistent. Unlinking an advisory-lock file after
        # unlock creates a handoff race where another process holds the old
        # inode while a third process locks a newly-created path.
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    async def _fetch_page_with_recovery(
        self, client, session, page, seen_stock, report, raw, run_id
    ):
        """Retry transient empty/short/duplicate windows before acting on them."""
        attempts = max(0, int(self.settings.page_recovery_retries))
        for recovery in range(attempts + 1):
            try:
                rows, total = await client.fetch_page(page)
            except SearchResponseError as exc:
                raw.write_dlq(
                    exc.payload,
                    exc,
                    phase="search_contract",
                    metadata={"run_id": run_id, "page": page, "attempt": recovery + 1},
                )
                raise
            if report.total_canada is None and total is not None:
                report.total_canada = total
            elif total is not None and report.total_canada is not None and total != report.total_canada:
                msg = (f"page {page}: authoritative total changed from "
                       f"{report.total_canada} to {total}")
                report.anomalies.append(msg)
                raise RuntimeError(msg)
            keys = {
                str(row.get("StockNum")) for row in rows
                if isinstance(row, dict) and row.get("StockNum") not in (None, "")
            }
            duplicate = bool(rows) and bool(keys) and keys.issubset(seen_stock)
            short = bool(rows) and len(rows) < self.settings.page_size
            incomplete = report.total_canada is not None and (
                len(seen_stock) + len(keys - seen_stock) < report.total_canada
            )
            empty = not rows
            anomaly = empty or duplicate or (short and incomplete)
            if not anomaly:
                return rows, total
            reason = "empty" if empty else "duplicate" if duplicate else "short_incomplete"
            raw.write_dlq(
                {"rows": rows, "total": total},
                f"{reason} page window",
                phase="page_anomaly",
                metadata={
                    "run_id": run_id,
                    "page": page,
                    "attempt": recovery + 1,
                    "unique_seen": len(seen_stock),
                },
            )
            report.empty_pages += int(empty)
            report.duplicate_pages += int(duplicate)
            if short:
                report.short_pages += 1
            if recovery >= attempts:
                if empty and report.total_canada and len(seen_stock) < report.total_canada:
                    raise RuntimeError(
                        f"page {page}: empty response after {attempts} recovery attempts; "
                        f"collected {len(seen_stock)}/{report.total_canada}"
                    )
                if duplicate and report.total_canada and len(seen_stock) < report.total_canada:
                    raise RuntimeError(
                        f"page {page}: duplicate page after {attempts} recovery attempts "
                        f"(pagination overlap); collected {len(seen_stock)}/{report.total_canada}"
                    )
                # A persistent short page is retained as a partial snapshot so
                # callers can inspect it; archival is blocked by the anomaly.
                return rows, total
            report.recovery_attempts += 1
            await session.polite_delay()

    async def _process_page(
        self, rows, seen_stock, store, raw, report, session, page, run_id
    ) -> tuple[int, int]:
        """Parse + filter + store one page. Returns (new_stock_count, parsed_ok_count)."""
        new_count = 0
        parsed_ok = 0
        for row in rows:
            # The source payload is evidence. Land it before any parser or
            # filtering decision so schema drift can be replayed and repaired.
            raw.persist_before_parse(row)
            try:
                lot = parse_row(row)
            except Exception as exc:  # noqa: BLE001 - isolate one malformed row
                raw.write_dlq(
                    row, exc, phase="parse_exception",
                    metadata={"run_id": run_id, "page": page},
                )
                report.skipped_bad_rows += 1
                continue
            if lot is None:
                raw.write_dlq(
                    row, "parse_row returned no lot", phase="parse_rejected",
                    metadata={"run_id": run_id, "page": page},
                )
                report.skipped_bad_rows += 1
                continue
            parsed_ok += 1

            # Dedup / loop guard: only act on stock numbers not yet seen this run.
            if lot.stock_number in seen_stock:
                continue
            seen_stock.add(lot.stock_number)
            new_count += 1

            if not self.settings.is_ontario(lot.branch_id, lot.branch_name):
                if self.settings.ontario_at_source:
                    msg = (f"non-Ontario row with BranchIds filter: stock={lot.stock_number} "
                           f"branch={lot.branch_name}")
                    report.anomalies.append(msg)
                    log.warning(msg)
                continue

            report.ontario_seen += 1
            report.ontario_by_branch[lot.branch_name or "?"] = (
                report.ontario_by_branch.get(lot.branch_name or "?", 0) + 1
            )

            # Optional per-lot enrichment (off by default).
            if self.settings.enrich_details:
                lot = await detail.enrich(session, lot)
                await session.polite_delay()

            outcome = store.upsert_lot(lot)
            if outcome == "inserted":
                report.inserted += 1
            elif outcome == "updated":
                report.updated += 1
            else:
                report.unchanged += 1
        return new_count, parsed_ok

    @staticmethod
    def _append_note(current: str, extra: str) -> str:
        return f"{current}; {extra}" if current else extra

    @staticmethod
    def _final_status(report: CrawlReport) -> str:
        """Flag completeness: did we page through (approximately) the whole set?"""
        if report.skipped_bad_rows:
            return "failed"
        # A persistent short final window is a partial result, while transport,
        # duplicate-window, and parse anomalies are hard failures.
        fatal_anomaly = any("short page" not in item.lower() for item in report.anomalies)
        if fatal_anomaly:
            return "failed"
        if report.total_canada is None:
            return "completed_unknown_total"
        # ``total_canada`` holds the authoritative API total for the crawl scope.
        if report.canada_rows_seen == report.total_canada:
            return "completed"
        return "completed_partial"
