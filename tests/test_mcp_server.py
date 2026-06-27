"""Tests for MCP server configuration and tool registration."""
from __future__ import annotations

import httpx
import pytest

from iaai_mcp.config import McpSettings
from iaai_mcp.server import create_mcp_server
from tests.mcp_helpers import make_settings, mock_orchestrator


def test_settings_validate_requires_token():
    s = make_settings(api_token="")
    with pytest.raises(RuntimeError, match="IAAI_API_TOKEN"):
        s.validate()


def test_create_server_registers_tools():
    settings = make_settings()
    server = create_mcp_server(settings)
    tool_names = set(server._tool_manager._tools.keys())  # noqa: SLF001
    expected = {
        "search_lots", "get_lot", "get_price_history", "get_stats",
        "get_freshness", "get_filters", "get_branches", "check_ready",
        "list_commands", "start_crawl", "get_crawl_status",
    }
    assert expected <= tool_names


@pytest.mark.asyncio
async def test_search_lots_tool_via_manager():
    from iaai_mcp.client import IaaiApiClient

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/lots":
            return httpx.Response(200, json={"total": 2, "count": 2, "results": []})
        return httpx.Response(404)

    settings = make_settings(docker_wake=False)
    orch = mock_orchestrator(handler, settings)
    api_client = IaaiApiClient(orch)
    mcp = create_mcp_server(settings, orchestrator=orch, client=api_client)
    tool = mcp._tool_manager._tools["search_lots"]  # noqa: SLF001
    result = await tool.fn(limit=5)
    assert result["total"] == 2
    await orch.close()
