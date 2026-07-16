"""Tests for API authentication."""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

from iaai_scraper import config
from iaai_scraper.storage import SqliteStore
from iaai_scraper.parser import parse_row
from tests.conftest import make_row

TOKEN = "test-secret-token"


def _make_client(monkeypatch, tmp_path, *, token: str | None = TOKEN, require: str = "true"):
    db = tmp_path / "auth.db"
    monkeypatch.setenv("IAAI_API_TOKEN", token or "")
    monkeypatch.setenv("IAAI_REQUIRE_AUTH", require)
    monkeypatch.delenv("IAAI_COMMAND_TOKEN", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(config, "IMAGE_CACHE_DIR", tmp_path / "image_cache", raising=False)
    monkeypatch.setattr(config, "IMAGE_CACHE_ENABLED", True, raising=False)
    monkeypatch.setattr(
        config, "IMAGE_ALLOWED_HOSTS", frozenset({"anvis.iaai.com"}), raising=False,
    )
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row(
        "100",
        ImageUrl="https://anvis.iaai.com/thumbnail?imageKeys=100",
    )))
    store.commit()
    store.close()
    import iaai_scraper.auth as auth_mod
    import iaai_scraper.api as api
    importlib.reload(auth_mod)
    importlib.reload(api)
    return TestClient(api.app)


@pytest.fixture
def authed_client(monkeypatch, tmp_path):
    return _make_client(monkeypatch, tmp_path)


def test_healthz_public_without_token(authed_client):
    assert authed_client.get("/healthz").status_code == 200


def test_lots_rejects_missing_token(authed_client):
    assert authed_client.get("/lots").status_code == 401


def test_lots_accepts_bearer_token(authed_client):
    r = authed_client.get("/lots", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    assert r.json()["count"] >= 1


def test_lots_accepts_x_api_key(authed_client):
    r = authed_client.get("/lots", headers={"X-API-Key": TOKEN})
    assert r.status_code == 200


def test_lots_rejects_invalid_token(authed_client):
    r = authed_client.get("/lots", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


def test_commands_crawl_requires_token(authed_client, monkeypatch):
    async def _noop_run(self):
        from iaai_scraper.crawler import CrawlReport
        from datetime import datetime, timezone
        return CrawlReport(started_at=datetime.now(timezone.utc), status="completed")

    monkeypatch.setattr("iaai_scraper.crawler.Crawler.run", _noop_run)
    assert authed_client.post("/commands/crawl", json={}).status_code == 401
    r = authed_client.post(
        "/commands/crawl",
        json={},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 202


def test_commands_can_require_separate_token(monkeypatch, tmp_path):
    async def _noop_run(self):
        from datetime import datetime, timezone
        from iaai_scraper.crawler import CrawlReport

        return CrawlReport(started_at=datetime.now(timezone.utc), status="completed")

    monkeypatch.setattr("iaai_scraper.crawler.Crawler.run", _noop_run)
    client = _make_client(monkeypatch, tmp_path)
    monkeypatch.setenv("IAAI_COMMAND_TOKEN", "command-only-secret")
    read_headers = {"Authorization": f"Bearer {TOKEN}"}
    command_headers = {"Authorization": "Bearer command-only-secret"}

    assert client.get("/lots", headers=read_headers).status_code == 200
    assert client.post("/commands/crawl", json={}, headers=read_headers).status_code == 403
    assert client.post("/commands/crawl", json={}, headers=command_headers).status_code == 202


def test_auth_disabled_when_require_false(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, token="", require="false")
    assert client.get("/lots").status_code == 200


def test_command_only_token_protects_crawl(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path, token="", require="auto")
    monkeypatch.setenv("IAAI_COMMAND_TOKEN", "command-only-secret")
    assert client.get("/lots").status_code == 200
    assert client.post("/commands/crawl", json={}).status_code == 401


def test_thumbnail_accepts_api_key_query(authed_client, monkeypatch):
    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    assert authed_client.get("/lots/100/thumbnail").status_code == 401
    r = authed_client.get(f"/lots/100/thumbnail?api_key={TOKEN}")
    assert r.status_code == 200
    assert r.headers["x-image-cache"] == "MISS"


def test_startup_fails_when_auth_required_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("IAAI_API_TOKEN", "")
    monkeypatch.setenv("IAAI_REQUIRE_AUTH", "true")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "x.db", raising=False)
    import iaai_scraper.auth as auth_mod
    import iaai_scraper.api as api
    importlib.reload(auth_mod)
    importlib.reload(api)
    with pytest.raises(RuntimeError, match="IAAI_API_TOKEN"):
        with TestClient(api.app):
            pass
