import importlib
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from iaai_scraper import config
from iaai_scraper.storage import SqliteStore
from iaai_scraper.parser import parse_row
from tests.conftest import make_row


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = tmp_path / "api.db"
    monkeypatch.setenv("IAAI_REQUIRE_AUTH", "false")
    monkeypatch.delenv("IAAI_API_TOKEN", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(config, "IMAGE_CACHE_DIR", tmp_path / "image_cache", raising=False)
    monkeypatch.setattr(config, "IMAGE_CACHE_ENABLED", True, raising=False)
    monkeypatch.setattr(
        config, "IMAGE_ALLOWED_HOSTS", frozenset({"anvis.iaai.com"}), raising=False,
    )
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row(
        "100",
        ItemStatusDesc="",
        ImageUrl="https://anvis.iaai.com/thumbnail?imageKeys=100",
    )))
    store.upsert_lot(parse_row(make_row("200", ItemStatusDesc="Sold", HighPrebidValue=1)))
    now = datetime.now(timezone.utc)
    store.record_run(started_at=now, finished_at=now, status="completed")
    store.commit()
    store.close()
    import iaai_scraper.auth as auth_mod
    import iaai_scraper.api as api
    importlib.reload(auth_mod)
    importlib.reload(api)
    return TestClient(api.app)


def test_lots_default_returns_all(client):
    results = client.get("/lots").json()["results"]
    stocks = {r["stock_number"] for r in results}
    assert {"100", "200"} <= stocks
    assert all("raw" not in row for row in results)
    by_stock = {r["stock_number"]: r for r in results}
    assert by_stock["100"]["thumbnail_href"] == "/lots/100/thumbnail"
    assert by_stock["100"]["image_url"].startswith("https://anvis.iaai.com/")
    assert by_stock["200"]["thumbnail_href"] is None


def test_thumbnail_miss_then_hit(client, monkeypatch):
    calls: list[str] = []

    def fake_fetch(url: str, timeout_s: float):
        calls.append(url)
        return b"\xff\xd8\xffthumb", "image/jpeg"

    monkeypatch.setattr("iaai_scraper.images._default_fetch", fake_fetch)
    first = client.get("/lots/100/thumbnail")
    second = client.get("/lots/100/thumbnail")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content == second.content == b"\xff\xd8\xffthumb"
    assert first.headers["content-type"].startswith("image/jpeg")
    assert first.headers["x-image-cache"] == "MISS"
    assert second.headers["x-image-cache"] == "HIT"
    assert len(calls) == 1


def test_thumbnail_missing_lot_or_url(client):
    assert client.get("/lots/missing/thumbnail").status_code == 404
    assert client.get("/lots/200/thumbnail").status_code == 404


def test_thumbnail_rejects_non_allowlisted_host(client):
    store = SqliteStore(db_path=config.DB_PATH)
    store.upsert_lot(parse_row(make_row(
        "300",
        ImageUrl="https://evil.example/x.png",
    )))
    store.commit()
    store.close()
    r = client.get("/lots/300/thumbnail")
    assert r.status_code == 422


def test_thumbnail_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(config, "IMAGE_CACHE_ENABLED", False, raising=False)
    import iaai_scraper.api as api
    importlib.reload(api)
    r = TestClient(api.app).get("/lots/100/thumbnail")
    assert r.status_code == 503


def test_lots_cursor_pagination_is_stable(client):
    first = client.get("/lots?sort=stock_number&limit=1").json()
    assert first["next_cursor"]
    second = client.get(
        "/lots",
        params={"sort": "stock_number", "limit": 1, "cursor": first["next_cursor"]},
    ).json()
    assert first["results"][0]["stock_number"] != second["results"][0]["stock_number"]


def test_lots_rejects_invalid_or_mismatched_cursor(client):
    first = client.get("/lots?sort=stock_number&limit=1").json()
    assert client.get("/lots?cursor=not-base64").status_code == 400
    assert client.get(
        "/lots", params={"sort": "year", "cursor": first["next_cursor"]},
    ).status_code == 400
    assert client.get(
        "/lots", params={"sort": "stock_number", "offset": 1, "cursor": first["next_cursor"]},
    ).status_code == 400
    for payload in (
        {"v": 1, "sort": "year", "desc": False, "stock": "100"},
        {"v": 1, "sort": "year", "desc": False, "stock": "100", "value": []},
    ):
        malformed = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        assert client.get(
            "/lots", params={"sort": "year", "cursor": malformed},
        ).status_code == 400


def test_raw_payload_is_explicit_opt_in(client):
    row = client.get("/lots/100").json()
    assert "raw" not in row
    raw_row = client.get("/lots/100?include_raw=true").json()
    assert raw_row["raw"]["StockNum"] == "100"


def test_raw_list_payload_has_safe_limit(client):
    assert client.get("/lots?include_raw=true&limit=101").status_code == 400


@pytest.mark.parametrize(
    "query",
    ["branch_id=70,abc", "status=unknown", "sort=raw"],
)
def test_invalid_query_contract_returns_422(client, query):
    assert client.get(f"/lots?{query}").status_code == 422


def test_lots_status_active_opt_in(client):
    stocks = {r["stock_number"] for r in client.get("/lots?status=active").json()["results"]}
    assert stocks == {"100"}


def test_lots_status_sold_opt_in(client):
    stocks = {r["stock_number"] for r in client.get("/lots?status=sold").json()["results"]}
    assert stocks == {"200"}


def test_lots_status_all_explicit(client):
    stocks = {r["stock_number"] for r in client.get("/lots?status=all").json()["results"]}
    assert {"100", "200"} <= stocks


def test_healthz_is_liveness(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_ok_when_db_ready(client):
    body = client.get("/readyz").json()
    assert body["status"] == "ready"
    assert body["lots"] >= 1


def test_readyz_rejects_empty_database(monkeypatch, tmp_path):
    monkeypatch.setenv("IAAI_REQUIRE_AUTH", "false")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "empty.db", raising=False)
    import iaai_scraper.api as api

    importlib.reload(api)
    test_client = TestClient(api.app)
    response = test_client.get("/readyz")
    assert response.status_code == 503
    assert response.json()["reason"] == "inventory_empty"


def test_readyz_rejects_stale_completed_crawl(monkeypatch, tmp_path):
    db = tmp_path / "stale.db"
    monkeypatch.setenv("IAAI_REQUIRE_AUTH", "false")
    monkeypatch.setenv("IAAI_MAX_DATA_AGE_HOURS", "1")
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    store = SqliteStore(db)
    store.upsert_lot(parse_row(make_row("stale-1")))
    old = datetime.now(timezone.utc) - timedelta(hours=2)
    store.record_run(started_at=old, finished_at=old, status="completed")
    store.commit()
    store.close()
    import iaai_scraper.api as api

    importlib.reload(api)
    response = TestClient(api.app).get("/readyz")
    assert response.status_code == 503
    assert response.json()["status"] == "stale"


def test_price_history_endpoint(client):
    r = client.get("/lots/200/price-history")
    assert r.status_code == 200
    body = r.json()
    assert body["stock_number"] == "200"
    assert "history" in body


def test_status_history_endpoint(client):
    r = client.get("/lots/200/status-history")
    assert r.status_code == 200
    assert r.json()["history"][-1]["new_status"] == "sold"


def test_price_history_404(client):
    assert client.get("/lots/missing/price-history").status_code == 404


def test_stats_freshness(client):
    body = client.get("/stats/freshness").json()
    assert "last_crawl" in body
    assert "lots_with_price_history" in body


def test_filters_endpoint(client):
    body = client.get("/filters").json()
    assert "makes" in body
    assert "models" in body
    assert "years" in body
    assert "statuses" in body
    assert "branches" in body
    assert isinstance(body["statuses"], list)
    assert any(s["status"] == "sold" for s in body["statuses"])
