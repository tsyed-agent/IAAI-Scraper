"""Tests for API commands (crawl via POST /commands/crawl)."""
import importlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from iaai_scraper import config
from iaai_scraper.crawler import CrawlReport
from iaai_scraper.sync_manager import SyncManager


@pytest.fixture
def api_client(monkeypatch, tmp_path):
    db = tmp_path / "cmd.db"
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    fresh = SyncManager()
    monkeypatch.setattr("iaai_scraper.sync_manager.sync_manager", fresh)
    import iaai_scraper.api as api
    importlib.reload(api)
    return TestClient(api.app)


def test_list_commands(api_client):
    body = api_client.get("/commands").json()
    assert any(c["name"] == "crawl" for c in body["commands"])


def test_crawl_command_accepts_and_completes(api_client, monkeypatch):
    report = CrawlReport(
        started_at=datetime.now(timezone.utc),
        status="completed",
        ontario_seen=10,
        canada_rows_seen=10,
        total_canada=10,
        pages=2,
    )

    async def _instant_run(self):
        return report

    monkeypatch.setattr("iaai_scraper.crawler.Crawler.run", _instant_run)

    r = api_client.post("/commands/crawl", json={})
    assert r.status_code == 202
    job_id = r.json()["job_id"]

    status = api_client.get("/commands/crawl/status").json()
    assert status["job"]["job_id"] == job_id
    assert status["job"]["status"] in ("completed", "running")

    # Poll until done (instant in test).
    for _ in range(20):
        status = api_client.get("/commands/crawl/status").json()
        if not status["running"]:
            break
    assert status["job"]["status"] == "completed"
    assert status["job"]["report"]["ontario_seen"] == 10
