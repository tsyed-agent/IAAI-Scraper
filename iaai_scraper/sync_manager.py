"""Background crawl jobs triggered via the API.

Uses the same ``Crawler.run()`` path as the CLI so all safeguards,
price history, and archival logic apply identically.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from . import config
from .crawler import Crawler, CrawlReport

log = logging.getLogger("iaai.sync")


@dataclass
class CrawlJob:
    job_id: str
    status: str = "queued"          # queued | running | completed | partial | failed
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    report: Optional[CrawlReport] = None
    error: Optional[str] = None
    settings_summary: dict[str, Any] = field(default_factory=dict)


class SyncManager:
    """Tracks at most one background crawl at a time (matches crawl.lock)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._current: Optional[CrawlJob] = None
        self._last: Optional[CrawlJob] = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, Any]:
        job = self._current if self.is_running else self._last
        if job is None:
            return {"running": False, "job": None}
        return {"running": self.is_running, "job": _job_to_dict(job)}

    async def start_crawl(self, settings: Optional[config.CrawlSettings] = None) -> CrawlJob:
        async with self._lock:
            if self.is_running:
                raise RuntimeError("A crawl is already running")
            settings = settings or config.CrawlSettings()
            job = CrawlJob(
                job_id=uuid.uuid4().hex[:12],
                settings_summary={
                    "ontario_at_source": settings.ontario_at_source,
                    "page_size": settings.page_size,
                    "max_list_pages": settings.max_list_pages,
                    "enrich_details": settings.enrich_details,
                },
            )
            self._current = job
            self._task = asyncio.create_task(self._run(job, settings))
            return job

    async def _run(self, job: CrawlJob, settings: config.CrawlSettings) -> None:
        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        try:
            report = await Crawler(settings).run()
            job.report = report
            if report.status == "completed":
                job.status = "completed"
            elif report.status == "failed":
                job.status = "failed"
            else:
                job.status = "partial"
            if report.status == "failed":
                job.error = report.note
            elif job.status == "partial":
                job.error = report.note or f"crawl ended with status={report.status}"
        except Exception as e:  # noqa: BLE001
            job.status = "failed"
            job.error = f"{type(e).__name__}: {e}"
            log.exception("API-triggered crawl failed")
        finally:
            job.finished_at = datetime.now(timezone.utc)
            self._last = job
            self._current = None
            self._task = None


def _job_to_dict(job: CrawlJob) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job.job_id,
        "status": job.status,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "settings": job.settings_summary,
        "error": job.error,
    }
    if job.report:
        r = job.report
        out["report"] = {
            "crawl_status": r.status,
            "pages": r.pages,
            "total_seen": r.canada_rows_seen,
            "total_expected": r.total_canada,
            "ontario_seen": r.ontario_seen,
            "inserted": r.inserted,
            "updated": r.updated,
            "unchanged": r.unchanged,
            "archived": r.archived,
            "note": r.note,
            "ontario_by_branch": r.ontario_by_branch,
        }
    return out


# Process-wide singleton used by the API.
sync_manager = SyncManager()
