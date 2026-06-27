"""Tests for IaaiApiClient tool methods."""
from __future__ import annotations

import httpx
import pytest

from iaai_mcp.client import IaaiApiClient
from tests.mcp_helpers import _with_probe_routes, mock_orchestrator


def _full_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/lots":
        return httpx.Response(200, json={"total": 1, "count": 1, "results": [{"stock_number": "100"}]})
    if path == "/lots/100":
        return httpx.Response(200, json={"stock_number": "100", "make": "TOYOTA"})
    if path == "/lots/missing":
        return httpx.Response(404, json={"detail": "lot not found"})
    if path == "/lots/100/price-history":
        return httpx.Response(200, json={"stock_number": "100", "count": 0, "history": []})
    if path == "/stats":
        return httpx.Response(200, json={"total_lots": 5})
    if path == "/commands/crawl/status":
        return httpx.Response(200, json={"running": False, "job": None})
    if path == "/commands/crawl" and request.method == "POST":
        if request.headers.get("X-Test-Conflict"):
            return httpx.Response(409, json={"detail": "crawl already running"})
        return httpx.Response(202, json={"job_id": "abc", "accepted": True})
    return httpx.Response(200, json={})


@pytest.fixture
def client():
    return IaaiApiClient(mock_orchestrator(_full_handler))


@pytest.mark.asyncio
async def test_search_lots(client):
    body = await client.search_lots(make="toyota", limit=10)
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_get_lot_or_error_not_found(client):
    orch = client._orch
    orch._client = httpx.AsyncClient(
        base_url=orch.settings.api_base_url,
        transport=httpx.MockTransport(_with_probe_routes(_full_handler)),
    )
    orch._ready.set()
    body = await client.get_lot_or_error("missing")
    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_start_crawl_conflict_returns_status(client):
    orch = client._orch

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/commands/crawl" and request.method == "POST":
            return httpx.Response(409, json={"detail": "crawl already running"})
        return _full_handler(request)

    orch._client = httpx.AsyncClient(
        base_url=orch.settings.api_base_url,
        transport=httpx.MockTransport(_with_probe_routes(handler)),
    )
    orch._ready.set()
    body = await client.start_crawl()
    assert body["accepted"] is False
    assert body["reason"] == "crawl_already_running"
