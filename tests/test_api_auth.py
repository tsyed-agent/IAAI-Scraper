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


def test_lots_thumbnail_href_is_signed(authed_client):
    r = authed_client.get("/lots/100", headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 200
    href = r.json()["thumbnail_href"]
    assert href.startswith("/lots/100/thumbnail?expires=")
    assert "&sig=" in href


def test_thumbnail_accepts_valid_signed_url(authed_client, monkeypatch):
    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    lot = authed_client.get("/lots/100", headers={"Authorization": f"Bearer {TOKEN}"})
    href = lot.json()["thumbnail_href"]
    r = authed_client.get(href)  # no Authorization header
    assert r.status_code == 200
    assert r.headers["cache-control"].startswith("public, max-age=")
    directives = {
        name: value
        for directive in r.headers["cache-control"].split(",")
        if "=" in directive
        for name, value in (directive.strip().split("=", 1),)
    }
    assert 0 < int(directives["max-age"]) <= config.MEDIA_URL_TTL_S
    assert 0 < int(directives["s-maxage"]) <= config.MEDIA_URL_TTL_S


def test_thumbnail_header_auth_keeps_configured_cache_control(authed_client, monkeypatch):
    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    r = authed_client.get(
        "/lots/100/thumbnail",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
    assert r.headers["cache-control"] == config.IMAGE_CACHE_CONTROL


def test_thumbnail_signed_url_with_header_still_caps_cache(authed_client, monkeypatch):
    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    lot = authed_client.get("/lots/100", headers={"Authorization": f"Bearer {TOKEN}"})
    r = authed_client.get(
        lot.json()["thumbnail_href"],
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
    assert r.headers["cache-control"].startswith("public, max-age=")
    directives = {
        name: value
        for directive in r.headers["cache-control"].split(",")
        if "=" in directive
        for name, value in (directive.strip().split("=", 1),)
    }
    assert 0 < int(directives["max-age"]) <= config.MEDIA_URL_TTL_S
    assert 0 < int(directives["s-maxage"]) <= config.MEDIA_URL_TTL_S


@pytest.mark.parametrize(
    "query",
    (
        "expires=2000000000",
        "sig=not-a-signature",
        "expires=not-a-timestamp&sig=not-a-signature",
    ),
)
def test_thumbnail_header_auth_with_invalid_signed_params_is_no_store(
    authed_client, monkeypatch, query,
):
    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    r = authed_client.get(
        f"/lots/100/thumbnail?{query}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"


def test_signed_media_cache_control_caps_expiry_and_removes_stale_windows():
    from iaai_scraper.auth import signed_media_cache_control

    base = (
        "public, max-age=86400, s-maxage=86400, "
        "stale-while-revalidate=60, stale-if-error=120"
    )
    assert signed_media_cache_control(1_000 + 900, now=1_000, base=base) == (
        "public, max-age=900, s-maxage=900"
    )
    assert signed_media_cache_control(1_001, now=1_000, base=base) == (
        "public, max-age=1, s-maxage=1"
    )
    assert signed_media_cache_control(
        1_900, now=1_000, base="public, s-maxage=86400",
    ) == "public, s-maxage=900, max-age=900"
    assert signed_media_cache_control(
        1_900, now=1_000, base="private, max-age=86400",
    ) == "private, max-age=900, s-maxage=900"


def test_thumbnail_signed_cache_control_near_expiry(authed_client, monkeypatch):
    import iaai_scraper.auth as auth_mod
    from iaai_scraper.auth import sign_media_path

    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    monkeypatch.setattr(auth_mod.time, "time", lambda: 2_000)
    path = "/lots/100/thumbnail"
    expires = 2_001
    sig = sign_media_path(path, expires, key=TOKEN)
    r = authed_client.get(f"{path}?expires={expires}&sig={sig}")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=1, s-maxage=1"


def test_thumbnail_rejects_expired_signature(authed_client, monkeypatch):
    from iaai_scraper.auth import sign_media_path

    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    path = "/lots/100/thumbnail"
    expires = 1_700_000_000  # firmly in the past relative to 2026
    sig = sign_media_path(path, expires, key=TOKEN)
    r = authed_client.get(f"{path}?expires={expires}&sig={sig}")
    assert r.status_code == 401


def test_thumbnail_rejects_tampered_signature(authed_client, monkeypatch):
    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    lot = authed_client.get("/lots/100", headers={"Authorization": f"Bearer {TOKEN}"})
    href = lot.json()["thumbnail_href"]
    # Flip last hex nibble of the signature
    bad = href[:-1] + ("0" if href[-1] != "0" else "1")
    assert authed_client.get(bad).status_code == 401


def test_sign_media_path_round_trip():
    from iaai_scraper.auth import sign_media_path, verify_media_signature

    path = "/lots/100/thumbnail"
    expires = 2_000_000_000
    sig = sign_media_path(path, expires, key="sekrit")
    assert verify_media_signature(path, expires, sig, key="sekrit", now=expires - 10)
    assert not verify_media_signature(path, expires, sig, key="sekrit", now=expires + 1)
    assert not verify_media_signature(path, expires, "deadbeef", key="sekrit", now=expires - 10)
    assert not verify_media_signature("/lots/999/thumbnail", expires, sig, key="sekrit", now=expires - 10)


def test_readyz_public_env_allows_unauthenticated_probe(monkeypatch, tmp_path):
    client = _make_client(monkeypatch, tmp_path)
    assert client.get("/readyz").status_code == 401
    monkeypatch.setenv("IAAI_READYZ_PUBLIC", "true")
    response = client.get("/readyz")
    # Auth no longer blocks the probe; status reflects data readiness only.
    assert response.status_code in (200, 503)
    assert "reason" in response.json() or response.json().get("status") == "ready"


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
