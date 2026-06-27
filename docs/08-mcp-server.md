# IAAI Ontario MCP Server

The **IAAI MCP server** exposes Ontario auction lot data and sync commands to LLMs
via the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). It wraps
the existing FastAPI backend with typed tools, resources, and prompts.

## Architecture

```
┌─────────────┐     MCP (stdio / HTTP)      ┌──────────────┐
│  LLM Client │ ◄──────────────────────────►│  iaai_mcp    │
│  (Cursor,   │                             │  MCP Server  │
│   Claude,   │                             └──────┬───────┘
│   etc.)     │                                    │ HTTP + Bearer token
└─────────────┘                                    ▼
                                          ┌──────────────────┐
                                          │  iaai-api        │
                                          │  (FastAPI + DB)  │
                                          └────────┬─────────┘
                                                   │ Playwright (on crawl only)
                                                   ▼
                                          https://ca.iaai.com
```

### Wake-on-demand

When the API Docker container is stopped, the MCP server can start it automatically:

1. Detect connection failure to `IAAI_API_BASE_URL`
2. Run `docker compose up -d iaai-api` (single-flight — parallel callers wait)
3. Poll `GET /readyz` until the database is ready
4. Retry the original request

Disable with `IAAI_DOCKER_WAKE=false` when the API is always running (e.g. inside Docker Compose).

### Concurrency

| Mechanism | Purpose |
|-----------|---------|
| `asyncio.Semaphore` (`IAAI_MCP_MAX_CONCURRENT`, default 32) | Limits in-flight API requests |
| `httpx` connection pool (`IAAI_MCP_CONNECTION_POOL`, default 64) | Reuses TCP connections |
| Single-flight Docker wake lock | One `docker compose up` for parallel cold starts |
| API crawl lock | Only one crawl at a time (409 if duplicate — MCP returns active job) |

Parallel read requests (search, get lot, stats) are safe and fully supported.

---

## Installation

### Local dev (same venv as scraper)

```bash
. .venv/bin/activate
pip install -r requirements-mcp.txt
export IAAI_API_TOKEN="your-secret"
export IAAI_API_BASE_URL="http://127.0.0.1:8000"
```

### MCP-only sidecar (no Playwright)

```bash
pip install -r requirements-mcp-lite.txt
```

---

## Running

### Stdio (Cursor / Claude Desktop — local)

```bash
export IAAI_API_TOKEN="..."
python -m iaai_mcp
```

Default transport is `stdio`. Cursor spawns the process and communicates over stdin/stdout.

### Streamable HTTP (future public URL)

```bash
export IAAI_MCP_TRANSPORT=streamable-http
export IAAI_MCP_HOST=127.0.0.1
export IAAI_MCP_PORT=8080
export IAAI_API_TOKEN="..."
python -m iaai_mcp
```

Endpoint: `http://127.0.0.1:8080/mcp` (bind localhost until you add a reverse proxy).

### Docker Compose (API + MCP)

```bash
cp .env.example .env   # set IAAI_API_TOKEN
docker compose up --build -d
```

Services:

| Service | Port (host) | Role |
|---------|-------------|------|
| `iaai-api` | `127.0.0.1:8000` | FastAPI + SQLite + crawler |
| `iaai-mcp` | `127.0.0.1:8080` | MCP server (streamable-http) |

MCP connects to `http://iaai-api:8000` on the internal network. Docker wake is disabled inside Compose (`depends_on` starts the API).

---

## Cursor configuration

Copy `docs/mcp-config.example.json` into your Cursor MCP settings:

```json
{
  "mcpServers": {
    "iaai-ontario": {
      "command": "python",
      "args": ["-m", "iaai_mcp"],
      "env": {
        "IAAI_API_TOKEN": "your-token-here",
        "IAAI_API_BASE_URL": "http://127.0.0.1:8000",
        "IAAI_DOCKER_WAKE": "true",
        "IAAI_REPO_ROOT": "/path/to/IAAI-Scraper"
      }
    }
  }
}
```

Ensure the API is running (`docker compose up -d`) or let wake-on-demand start it.

---

## Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IAAI_API_TOKEN` | *(required)* | Same token as the IAAI API |
| `IAAI_API_BASE_URL` | `http://127.0.0.1:8000` | FastAPI base URL |
| `IAAI_MCP_TRANSPORT` | `stdio` | `stdio`, `sse`, or `streamable-http` |
| `IAAI_MCP_HOST` | `127.0.0.1` | Bind address for HTTP transports |
| `IAAI_MCP_PORT` | `8080` | Port for HTTP transports |
| `IAAI_DOCKER_WAKE` | `true` | Start API container when unreachable |
| `IAAI_DOCKER_COMPOSE_FILE` | `./docker-compose.yml` | Compose file for wake |
| `IAAI_DOCKER_SERVICE` | `iaai-api` | Service name to start |
| `IAAI_DOCKER_WAKE_TIMEOUT` | `120` | Seconds to wait for readiness |
| `IAAI_DOCKER_POLL_INTERVAL` | `2` | Seconds between readiness polls |
| `IAAI_MCP_MAX_CONCURRENT` | `32` | Max parallel API requests |
| `IAAI_MCP_CONNECTION_POOL` | `64` | httpx connection pool size |
| `IAAI_MCP_REQUEST_TIMEOUT` | `60` | Per-request timeout (seconds) |
| `IAAI_MCP_MAX_RETRIES` | `3` | Retries after connection errors |
| `IAAI_REPO_ROOT` | `.` | Repo root for default compose path |

---

## MCP tools

| Tool | Description |
|------|-------------|
| `search_lots` | Filter/paginate lots (make, model, year, price, damage, status, …) |
| `get_lot` | Full detail for one stock number |
| `get_price_history` | Prebid and status change timeline |
| `get_stats` | DB statistics |
| `get_freshness` | Last crawl run summary |
| `get_filters` | Distinct makes, models, years, branches |
| `get_branches` | Ontario branch ID map |
| `check_ready` | API/database readiness |
| `list_commands` | Available commands |
| `start_crawl` | Background sync from IAAI (~20–30s) |
| `get_crawl_status` | Poll crawl job progress |

## MCP resources

| URI | Content |
|-----|---------|
| `iaai://stats` | JSON snapshot of DB stats |
| `iaai://freshness` | JSON snapshot of crawl freshness |
| `iaai://filters` | JSON snapshot of filter values |
| `iaai://branches` | JSON branch map |

## MCP prompts

| Prompt | Use case |
|--------|----------|
| `find_vehicles` | Guided vehicle search workflow |
| `sync_and_report` | Sync from IAAI and summarize changes |

---

## Exposing publicly (later)

Not enabled by default. Recommended path:

1. Keep MCP on `127.0.0.1:8080` inside the VPC
2. Put **Cloudflare Tunnel**, nginx, or Caddy in front with TLS
3. Add MCP-layer auth (OAuth / API key) when the MCP SDK transport supports your provider
4. Rate-limit at the reverse proxy

The IAAI API stays on the internal Docker network; only the MCP port is published through the proxy.

---

## Testing

```bash
python -m pytest tests/test_mcp_*.py -v
python -m pytest   # full suite including MCP
```

Tests use `httpx.MockTransport` — no live API or Docker required.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `IAAI_API_TOKEN is required` | Set token in env or `.env` |
| `docker CLI not found` | Install Docker or set `IAAI_DOCKER_WAKE=false` and start API manually |
| `crawl already running` | Poll `get_crawl_status` — only one crawl at a time |
| 401 from API | Token mismatch between MCP and API containers |
| Slow first request | Cold start — Docker wake + API boot (~10–30s) |
