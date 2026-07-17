"""Storefront static mount smoke tests (offline)."""
import importlib

import pytest
from fastapi.testclient import TestClient

from iaai_scraper import config


@pytest.fixture
def client(monkeypatch, tmp_path):
    db = tmp_path / "ui.db"
    monkeypatch.setenv("IAAI_REQUIRE_AUTH", "false")
    monkeypatch.delenv("IAAI_API_TOKEN", raising=False)
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    import iaai_scraper.auth as auth_mod
    import iaai_scraper.api as api
    importlib.reload(auth_mod)
    importlib.reload(api)
    return TestClient(api.app)


def test_ui_index_served(client):
    res = client.get("/ui/")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    body = res.text.lower()
    assert "yardline" in body or "root" in body


def test_root_redirects_to_ui(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (307, 302)
    assert res.headers.get("location", "").endswith("/ui/")
