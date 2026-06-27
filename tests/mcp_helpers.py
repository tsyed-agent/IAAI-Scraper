"""Shared MCP test helpers."""
from __future__ import annotations

from dataclasses import replace

import httpx

from iaai_mcp.config import McpSettings
from iaai_mcp.orchestrator import ApiOrchestrator


def make_settings(**overrides) -> McpSettings:
    base = McpSettings(
        api_base_url="http://test-api:8000",
        api_token="test-token",
        transport="stdio",
        host="127.0.0.1",
        port=8080,
        docker_wake=True,
        docker_compose_file=__import__("pathlib").Path("/tmp/docker-compose.yml"),
        docker_service="iaai-api",
        docker_wake_timeout_s=5.0,
        docker_poll_interval_s=0.05,
        max_concurrent_requests=16,
        request_timeout_s=5.0,
        max_retries=2,
        connection_pool_size=32,
    )
    return replace(base, **overrides) if overrides else base


def _with_probe_routes(handler):
    """Ensure health/ready probes succeed for orchestrator.ensure_ready()."""

    def wrapped(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready", "lots": 1})
        return handler(request)

    return wrapped


def mock_orchestrator(
    handler,
    settings: McpSettings | None = None,
) -> ApiOrchestrator:
    """Orchestrator with httpx MockTransport (skips real HTTP/Docker)."""
    settings = settings or make_settings(docker_wake=False)
    orch = ApiOrchestrator(settings)
    transport = httpx.MockTransport(_with_probe_routes(handler))
    orch._client = httpx.AsyncClient(
        base_url=settings.api_base_url,
        headers=orch._auth_headers(),
        transport=transport,
        timeout=httpx.Timeout(settings.request_timeout_s),
    )
    orch._ready.set()
    return orch
