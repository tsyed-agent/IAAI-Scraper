"""Tests for MCP configuration."""
from iaai_mcp.config import McpSettings


def test_from_env_defaults(monkeypatch):
    monkeypatch.setenv("IAAI_API_TOKEN", "secret")
    monkeypatch.setenv("IAAI_API_BASE_URL", "http://127.0.0.1:9000")
    s = McpSettings.from_env()
    assert s.api_token == "secret"
    assert s.api_base_url == "http://127.0.0.1:9000"
    assert s.transport == "stdio"


def test_validate_accepts_token():
    s = McpSettings(
        api_base_url="http://x",
        api_token="tok",
        transport="stdio",
        host="127.0.0.1",
        port=8080,
        docker_wake=False,
        docker_compose_file=__import__("pathlib").Path("."),
        docker_service="iaai-api",
        docker_wake_timeout_s=60,
        docker_poll_interval_s=1,
        max_concurrent_requests=8,
        request_timeout_s=30,
        max_retries=2,
        connection_pool_size=16,
    )
    s.validate()
