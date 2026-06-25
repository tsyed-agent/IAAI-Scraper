import importlib

import pytest
from fastapi.testclient import TestClient

from iaai_scraper import config
from iaai_scraper.storage import SqliteStore
from iaai_scraper.parser import parse_row
from tests.conftest import make_row


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = tmp_path / "api.db"
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    store = SqliteStore(db_path=db)
    store.upsert_lot(parse_row(make_row("100", ItemStatusDesc="")))
    store.upsert_lot(parse_row(make_row("200", ItemStatusDesc="Sold", HighPrebidValue=1)))
    store.commit()
    store.close()
    import iaai_scraper.api as api
    importlib.reload(api)
    return TestClient(api.app)


def test_lots_default_returns_all(client):
    stocks = {r["stock_number"] for r in client.get("/lots").json()["results"]}
    assert {"100", "200"} <= stocks


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
