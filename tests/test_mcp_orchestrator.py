"""Tests for ApiOrchestrator wake-on-demand and concurrency."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from iaai_mcp.orchestrator import ApiOrchestrator, ApiUnavailableError
from tests.mcp_helpers import make_settings, mock_orchestrator


def _api_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/healthz":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/readyz":
        return httpx.Response(200, json={"status": "ready", "lots": 10})
    if request.url.path == "/stats":
        return httpx.Response(200, json={"total_lots": 10})
    return httpx.Response(404, json={"detail": "not found"})


@pytest.mark.asyncio
async def test_get_json_when_ready():
    orch = mock_orchestrator(_api_handler)
    data = await orch.get_json("/stats")
    assert data["total_lots"] == 10
    await orch.close()


@pytest.mark.asyncio
async def test_parallel_requests_share_pool():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"n": calls})

    orch = mock_orchestrator(handler, make_settings(max_concurrent_requests=8))
    results = await asyncio.gather(*[orch.get_json("/stats") for _ in range(20)])
    assert len(results) == 20
    assert calls == 20
    await orch.close()


@pytest.mark.asyncio
async def test_wake_docker_single_flight():
    """Parallel ensure_ready calls must trigger only one docker compose up."""
    ping_count = 0
    compose_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal ping_count
        ping_count += 1
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            if ping_count < 4:
                return httpx.Response(503, json={"status": "unavailable"})
            return httpx.Response(200, json={"status": "ready", "lots": 1})
        return httpx.Response(200, json={})

    settings = make_settings(docker_wake=True, docker_poll_interval_s=0.01)
    orch = ApiOrchestrator(settings)
    transport = httpx.MockTransport(handler)
    orch._client = httpx.AsyncClient(
        base_url=settings.api_base_url,
        headers=orch._auth_headers(),
        transport=transport,
    )

    async def fake_compose(*args):
        nonlocal compose_calls
        compose_calls += 1
        return __import__("subprocess").CompletedProcess(args, 0, "", "")

    with patch.object(orch, "_start_docker_service", new=AsyncMock(side_effect=fake_compose)):
        await asyncio.gather(
            orch.ensure_ready(),
            orch.ensure_ready(),
            orch.ensure_ready(),
        )

    assert compose_calls == 1
    await orch.close()


@pytest.mark.asyncio
async def test_unavailable_when_wake_disabled():
    settings = make_settings(docker_wake=False)
    orch = ApiOrchestrator(settings)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    orch._client = httpx.AsyncClient(
        base_url=settings.api_base_url,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ApiUnavailableError):
        await orch.ensure_ready()
    await orch.close()


@pytest.mark.asyncio
async def test_retry_on_connect_error():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 2:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, json={"ok": True})

    orch = mock_orchestrator(handler, make_settings(max_retries=3, docker_wake=False))
    orch._ready.clear()
    data = await orch.get_json("/stats")
    assert data["ok"] is True
    assert attempts >= 2
    await orch.close()
