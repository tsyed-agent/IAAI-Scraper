"""Contract tests for API schema versioning (task 2.7)."""
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from iaai_scraper import config
from iaai_scraper.api_schemas import (
    API_KEY_QUERY_SUNSET,
    API_SCHEMA_VERSION,
    LotResponseV1,
    LotsListResponseV1,
    StatsResponseV1,
    V1_LOT_REQUIRED_FIELDS,
    V1_LOTS_LIST_REQUIRED_FIELDS,
    V1_STATS_REQUIRED_FIELDS,
)
from iaai_scraper.parser import parse_row
from iaai_scraper.storage import SqliteStore
from tests.conftest import make_row


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = tmp_path / "schemas.db"
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
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    store.record_run(started_at=now, finished_at=now, status="completed")
    store.commit()
    store.close()
    import iaai_scraper.auth as auth_mod
    import iaai_scraper.api as api
    importlib.reload(auth_mod)
    importlib.reload(api)
    return TestClient(api.app)


def test_v1_required_fields_are_declared_on_models():
    """Renaming/removing a pinned v1 field from the model fails this test."""
    assert V1_LOT_REQUIRED_FIELDS <= set(LotResponseV1.model_fields)
    assert V1_LOTS_LIST_REQUIRED_FIELDS <= set(LotsListResponseV1.model_fields)
    assert V1_STATS_REQUIRED_FIELDS <= set(StatsResponseV1.model_fields)


def test_api_version_header_on_key_routes(client):
    for path in ("/healthz", "/lots", "/lots/100", "/stats", "/stats/freshness", "/filters"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("x-api-version") == API_SCHEMA_VERSION


def test_lot_detail_matches_v1_model_and_required_fields(client):
    r = client.get("/lots/100")
    assert r.status_code == 200
    body = r.json()
    assert V1_LOT_REQUIRED_FIELDS <= set(body.keys())
    parsed = LotResponseV1.model_validate(body)
    assert parsed.stock_number == "100"
    assert "raw" not in body


def test_lots_list_matches_v1_model(client):
    r = client.get("/lots")
    assert r.status_code == 200
    body = r.json()
    assert V1_LOTS_LIST_REQUIRED_FIELDS <= set(body.keys())
    parsed = LotsListResponseV1.model_validate(body)
    assert parsed.count == len(parsed.results)
    assert parsed.results
    assert V1_LOT_REQUIRED_FIELDS <= set(parsed.results[0].model_dump(exclude_unset=True))


def test_stats_matches_v1_model(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert V1_STATS_REQUIRED_FIELDS <= set(body.keys())
    StatsResponseV1.model_validate(body)


def test_openapi_documents_v1_schemas_and_deprecated_api_key(client):
    schema = client.get("/openapi.json").json()
    assert API_SCHEMA_VERSION in schema["info"]["description"]
    components = schema["components"]["schemas"]
    for name in (
        "LotResponseV1",
        "LotsListResponseV1",
        "StatsResponseV1",
        "FreshnessResponseV1",
        "FiltersResponseV1",
        "PriceHistoryResponseV1",
    ):
        assert name in components, name

    thumb = schema["paths"]["/lots/{stock_number}/thumbnail"]["get"]
    api_key_param = next(p for p in thumb["parameters"] if p["name"] == "api_key")
    assert api_key_param.get("deprecated") is True


def test_removing_v1_field_from_wire_would_fail_contract(client):
    """Simulate a breaking rename: response missing stock_number must not validate."""
    body = client.get("/lots/100").json()
    broken = {k: v for k, v in body.items() if k != "stock_number"}
    broken["lot_id"] = body["stock_number"]  # renamed
    with pytest.raises(Exception):
        LotResponseV1.model_validate(broken)
    assert "stock_number" in V1_LOT_REQUIRED_FIELDS
    assert not (V1_LOT_REQUIRED_FIELDS <= set(broken.keys()))


def test_api_key_query_sends_deprecation_headers(monkeypatch, tmp_path):
    token = "schema-test-token"
    db = tmp_path / "auth-schemas.db"
    monkeypatch.setenv("IAAI_REQUIRE_AUTH", "true")
    monkeypatch.setenv("IAAI_API_TOKEN", token)
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(config, "IMAGE_CACHE_DIR", tmp_path / "thumbs", raising=False)
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
    monkeypatch.setattr(
        "iaai_scraper.images._default_fetch",
        lambda url, timeout_s: (b"\xff\xd8\xffx", "image/jpeg"),
    )
    client = TestClient(api.app)
    r = client.get(f"/lots/100/thumbnail?api_key={token}")
    assert r.status_code == 200
    assert r.headers.get("deprecation") == "true"
    assert r.headers.get("sunset") == API_KEY_QUERY_SUNSET
    assert "api-versioning.md" in r.headers.get("link", "")
    assert r.headers.get("x-api-version") == API_SCHEMA_VERSION


def test_lot_response_v1_allows_string_raw():
    """Invalid stored JSON must remain a string — not a 500 from response_model."""
    lot = LotResponseV1.model_validate(
        {"stock_number": "999", "status": "active", "raw": "not-json{{{"}
    )
    assert lot.raw == "not-json{{{"
    parsed = LotResponseV1.model_validate(
        {"stock_number": "100", "raw": {"StockNum": "100"}}
    )
    assert parsed.raw == {"StockNum": "100"}
