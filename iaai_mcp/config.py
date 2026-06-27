"""Configuration for the IAAI MCP server."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


@dataclass(frozen=True)
class McpSettings:
    """Environment-driven MCP server settings."""

    api_base_url: str
    api_token: str
    transport: str
    host: str
    port: int
    docker_wake: bool
    docker_compose_file: Path
    docker_service: str
    docker_wake_timeout_s: float
    docker_poll_interval_s: float
    max_concurrent_requests: int
    request_timeout_s: float
    max_retries: int
    connection_pool_size: int

    @classmethod
    def from_env(cls) -> McpSettings:
        repo_root = Path(os.getenv("IAAI_REPO_ROOT", Path.cwd()))
        compose = os.getenv("IAAI_DOCKER_COMPOSE_FILE", str(repo_root / "docker-compose.yml"))
        return cls(
            api_base_url=os.getenv("IAAI_API_BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
            api_token=os.getenv("IAAI_API_TOKEN", "").strip(),
            transport=os.getenv("IAAI_MCP_TRANSPORT", "stdio").strip().lower(),
            host=os.getenv("IAAI_MCP_HOST", "127.0.0.1"),
            port=_env_int("IAAI_MCP_PORT", 8080),
            docker_wake=_env_bool("IAAI_DOCKER_WAKE", True),
            docker_compose_file=Path(compose),
            docker_service=os.getenv("IAAI_DOCKER_SERVICE", "iaai-api"),
            docker_wake_timeout_s=float(os.getenv("IAAI_DOCKER_WAKE_TIMEOUT", "120")),
            docker_poll_interval_s=float(os.getenv("IAAI_DOCKER_POLL_INTERVAL", "2")),
            max_concurrent_requests=_env_int("IAAI_MCP_MAX_CONCURRENT", 32),
            request_timeout_s=float(os.getenv("IAAI_MCP_REQUEST_TIMEOUT", "60")),
            max_retries=_env_int("IAAI_MCP_MAX_RETRIES", 3),
            connection_pool_size=_env_int("IAAI_MCP_CONNECTION_POOL", 64),
        )

    def validate(self) -> None:
        if not self.api_token:
            raise RuntimeError(
                "IAAI_API_TOKEN is required for the MCP server "
                "(same token as the IAAI API Docker container)"
            )
        if self.transport not in ("stdio", "sse", "streamable-http"):
            raise RuntimeError(
                f"Unsupported IAAI_MCP_TRANSPORT={self.transport!r}; "
                "use stdio, sse, or streamable-http"
            )
