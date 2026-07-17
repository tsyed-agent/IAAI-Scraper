# Ops: TLS gateway, rate/body limits, secrets, audit logs

Production traffic must terminate TLS at a reverse proxy in front of the
container API (`127.0.0.1:8000`). The sample here is **nginx** + a Compose
overlay. Full external abuse testing is still an ops gate; this package is the
deployable edge baseline (doc 09 P0 / doc 10 · 2.3).

## Bring-up (lab)

```bash
cp .env.example .env   # set IAAI_API_TOKEN (+ IAAI_COMMAND_TOKEN)
scripts/gen_gateway_certs.sh
docker compose -f docker-compose.yml -f docker-compose.gateway.yml up --build -d
```

| Surface | Address | Purpose |
|---|---|---|
| Public HTTPS edge | `https://127.0.0.1:8443` | Read API (`/lots`, `/stats`, …) |
| HTTP → HTTPS | `:8080` → `:8443` | Redirect includes `IAAI_GATEWAY_HTTPS_PORT` (not bare 443) |
| Loopback API | `http://127.0.0.1:8000` | Ops, `POST /commands/*`, OpenAPI |

Override ports with `IAAI_GATEWAY_HTTPS_PORT` / `IAAI_GATEWAY_HTTP_PORT` /
`IAAI_API_PORT`. Host publish defaults to **loopback only**
(`IAAI_GATEWAY_BIND=127.0.0.1`); set `IAAI_GATEWAY_BIND=0.0.0.0` only when you
intentionally expose the edge on all interfaces. Keep the HTTPS env var in sync
with the compose port mapping — the gateway runs `envsubst` so HTTP redirects
land on the mapped TLS port.

```bash
# Public read (through TLS edge)
curl -k -H "Authorization: Bearer $IAAI_API_TOKEN" \
  https://127.0.0.1:8443/stats

# Commands stay off the edge — use loopback or CLI/cron
curl -H "Authorization: Bearer $IAAI_COMMAND_TOKEN" \
  -X POST http://127.0.0.1:8000/commands/crawl
# Edge returns 404 for /commands, /docs, /redoc, /openapi.json
```

## What the sample enforces

| Control | Where | Default |
|---|---|---|
| TLS 1.2/1.3 | `docker/gateway/nginx.conf.template` | self-signed lab certs via `scripts/gen_gateway_certs.sh` |
| Rate limit | `limit_req_zone` | 10 r/s per IP, burst 20 |
| Conn limit | `limit_conn` | 20 concurrent per IP |
| Body limit | `client_max_body_size` | 1 MiB |
| Command isolation | `location ^~ /commands` | **404** on public edge |
| Admin docs blocked | `/docs`, `/redoc`, `/openapi.json` | **404** on public edge |
| Access / audit log | nginx `iaai_audit` format | `/var/log/nginx/access.log` in volume `iaai-gateway-logs` |
| Token redaction | app `logging_utils` | `api_key` / `sig` query values → `REDACTED` on uvicorn access logs |

Optional IP allowlist is commented in the nginx config (`geo $iaai_allow`).

## Secrets

| Practice | Detail |
|---|---|
| Tokens in env only | `IAAI_API_TOKEN` / `IAAI_COMMAND_TOKEN` via `.env` (mode `0600`) or Docker/K8s secrets — never in nginx config or git |
| Separate command credential | Set `IAAI_COMMAND_TOKEN` ≠ read token; edge never exposes `/commands` |
| Lab certs | Self-signed under `docker/gateway/certs/` (gitignored). Replace with ACME / managed certs for prod |
| Rotation | Rotate API tokens by updating secret store + restarting `iaai-api`; rotate TLS by replacing PEMs + `docker compose … kill -s HUP iaai-gateway` (or recreate) |
| No secrets in logs | Do not enable Authorization header logging; rely on redaction for signed media query params |

## Audit logs

- **Edge:** nginx `iaai_audit` — client IP, method/path, status, timing, UA.
- **App:** uvicorn access logs with `AccessLogTokenRedactor` (already installed).
- Retain edge logs off-host with the same discipline as backups (`docs/ops-backup.md`).

## Production notes

1. Replace self-signed certs with a real certificate (Let’s Encrypt, cloud LB, or
   Cloudflare Tunnel terminating TLS).
2. Prefer publishing **only** the gateway ports on the host; keep
   `127.0.0.1:8000` for operators / scheduler, or drop the host publish and run
   crawl via `docker compose exec` / CLI on the host with a mounted data dir.
3. Tune `rate=` / `burst=` under expected BFF load; add WAF/CDN upstream if the
   edge is internet-facing.
4. External abuse / load tests: run offline CI check anytime; live edge when up:

```bash
scripts/gateway_abuse_check.sh                          # offline (CI)
IAAI_GATEWAY_BASE=https://127.0.0.1:8443 \
  IAAI_API_TOKEN=… scripts/gateway_abuse_check.sh       # live rate/404/body
```

## Files

| Path | Role |
|---|---|
| `docker/gateway/nginx.conf.template` | TLS, limits, route isolation, audit log format (`envsubst` at start) |
| `docker-compose.gateway.yml` | nginx service + shared internal network |
| `scripts/gen_gateway_certs.sh` | lab cert bootstrap |
| `tests/test_gateway_config.py` | offline assertions on the sample config |
