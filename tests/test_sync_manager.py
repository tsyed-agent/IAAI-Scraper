"""Unit tests for SyncManager (API crawl commands)."""
import asyncio
from datetime import datetime, timezone

import pytest

from iaai_scraper import config
from iaai_scraper.crawler import CrawlReport
from iaai_scraper.sync_manager import SyncManager


def test_rejects_concurrent_crawl(monkeypatch):
    async def _run():
        sm = SyncManager()
        started = asyncio.Event()

        async def _hang(self):
            started.set()
            await asyncio.sleep(10)

        monkeypatch.setattr("iaai_scraper.crawler.Crawler.run", _hang)

        await sm.start_crawl(config.CrawlSettings())
        await started.wait()
        assert sm.is_running

        with pytest.raises(RuntimeError, match="already running"):
            await sm.start_crawl(config.CrawlSettings())

    asyncio.run(_run())


def test_records_completed_report(monkeypatch):
    async def _run():
        sm = SyncManager()
        report = CrawlReport(
            started_at=datetime.now(timezone.utc),
            status="completed",
            ontario_seen=42,
            pages=2,
        )

        async def _done(self):
            return report

        monkeypatch.setattr("iaai_scraper.crawler.Crawler.run", _done)

        job = await sm.start_crawl(config.CrawlSettings())
        for _ in range(50):
            if not sm.is_running:
                break
            await asyncio.sleep(0.01)

        assert job.status == "completed"
        assert sm.status()["job"]["report"]["ontario_seen"] == 42

    asyncio.run(_run())
