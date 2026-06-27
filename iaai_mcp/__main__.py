"""CLI entrypoint: python -m iaai_mcp"""
from __future__ import annotations

import logging
import sys

from .config import McpSettings
from .server import create_mcp_server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    settings = McpSettings.from_env()
    settings.validate()
    server = create_mcp_server(settings)
    logging.getLogger("iaai_mcp").info(
        "Starting MCP server transport=%s api=%s",
        settings.transport,
        settings.api_base_url,
    )
    server.run(transport=settings.transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
