# IAAI-Scraper

Data collection system for **IAA / IAAI Canada** vehicle auction listings
(`https://ca.iaai.com/`), scoped to **Ontario**. It collects structured auction
lot data, stores it in a searchable database, and exposes it through an internal
API for querying, filtering, and retrieval — optimised for completeness, speed,
reliability, and cost. The crawl stores image **URL pointers** only (no binaries);
on-demand thumbnail caching for the UI is specified in
[`docs/10-implementation-plan.md`](docs/10-implementation-plan.md).

> Status: **hardened staging baseline.** Captured-data tests, migrations, raw
> replay audits, and API tests pass. Production launch still requires the
> backup/restore, gateway, observability, and authorized-live-canary gates in
> [`docs/09-enterprise-hardening-plan.md`](docs/09-enterprise-hardening-plan.md).
> Next implementation work is sequenced in
> [`docs/10-implementation-plan.md`](docs/10-implementation-plan.md).

## Docker (recommended for deployment)

Isolated container: one HTTP port, auth required, data on a named volume.

```bash
cp .env.example .env
# Edit .env — set IAAI_API_TOKEN (openssl rand -hex 32)
docker compose up --build -d
```

- **Port:** binds `127.0.0.1:8000` only (not `0.0.0.0` on the host). For remote access use the sample TLS edge (`docs/ops-gateway.md`) or your own nginx/Caddy/Cloudflare Tunnel.
- **Auth:** container refuses to start without `IAAI_API_TOKEN`. All routes except `GET /healthz` require a token; set `IAAI_COMMAND_TOKEN` separately for crawl commands.
- **Network:** outbound HTTPS to `ca.iaai.com` happens only when you call `POST /commands/crawl` (Playwright crawl). Reads are local SQLite only.
- **Data:** persisted in Docker volume `iaai-data` at `/data`.

```bash
export TOKEN="your-token-from-.env"
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/stats
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/commands/crawl
```

Alternative header: `X-API-Key: $TOKEN`

## Quick start (local dev)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium

python -m iaai_scraper.cli serve            # start the unified API on :8000
```

**Everything runs through the API** — queries read the local DB; commands sync from IAAI:

```bash
# Discover commands
curl http://127.0.0.1:8000/commands

# Sync Ontario lots (background job, ~20–30s)
curl -X POST http://127.0.0.1:8000/commands/crawl
curl http://127.0.0.1:8000/commands/crawl/status

# Query with filters (local DB only, instant)
curl "http://127.0.0.1:8000/lots?make=toyota,honda&year_min=2018&high_prebid_min=1000"
curl http://127.0.0.1:8000/filters
```

### API authentication

| Env | Effect |
|-----|--------|
| `IAAI_REQUIRE_AUTH=false` | Open access (local dev default when no token set) |
| `IAAI_API_TOKEN=<secret>` | Enables auth (`auto` mode — enforced because token is set) |
| `IAAI_COMMAND_TOKEN=<secret>` | Optional separate credential for mutating crawl commands |
| `IAAI_REQUIRE_AUTH=true` | **Mandatory** token on all routes except `GET /healthz`; startup fails if token missing |
| `IAAI_MAX_DATA_AGE_HOURS=24` | `/readyz` fails when the last completed crawl is older than this |

Pass the token on every request:

```bash
curl -H "Authorization: Bearer $IAAI_API_TOKEN" http://127.0.0.1:8000/lots?limit=5
# or
curl -H "X-API-Key: $IAAI_API_TOKEN" http://127.0.0.1:8000/lots?limit=5
```

CLI shortcuts (same logic, for ops without HTTP):

```bash
python -m iaai_scraper.cli crawl            # sync via CLI (blocking)
python -m iaai_scraper.cli stats            # DB statistics
python -m iaai_scraper.cli backup           # atomic snapshot under data/backups/
python -m iaai_scraper.cli offsite-backup   # upload DB+raw + SHA-256 manifest
python -m iaai_scraper.cli restore-drill    # prove off-host snapshot restores

python scripts/verify_ontario_crawl.py     # verify last crawl invariants
python scripts/audit_raw_coverage.py data/raw/**/*.jsonl.gz  # offline field/completeness audit
scripts/scheduled_crawl.sh                 # cron entrypoint: crawl + one retry (see docs/ops-scheduling.md)
```

Example API calls:

```bash
curl -X POST http://127.0.0.1:8000/commands/crawl
curl http://127.0.0.1:8000/commands/crawl/status
curl "http://127.0.0.1:8000/lots?make=toyota&year_min=2018&limit=20"
curl "http://127.0.0.1:8000/lots?make=toyota,honda&status=active&high_prebid_min=500"
curl "http://127.0.0.1:8000/lots?primary_damage=front&has_keys=true&runs=true"
curl "http://127.0.0.1:8000/lots?branch_id=70,56&sort=final_price&descending=true"
curl "http://127.0.0.1:8000/lots?sort=stock_number&limit=100" # follow next_cursor
curl "http://127.0.0.1:8000/lots?status=sold&limit=5"   # opt-in status filter
curl "http://127.0.0.1:8000/lots/12033066"
curl "http://127.0.0.1:8000/lots/12033066/thumbnail"   # on-demand cached thumb
curl "http://127.0.0.1:8000/lots/12033066/price-history"
curl "http://127.0.0.1:8000/lots/12033066/status-history"
curl "http://127.0.0.1:8000/stats"
curl "http://127.0.0.1:8000/stats/freshness"
curl "http://127.0.0.1:8000/filters"
curl "http://127.0.0.1:8000/readyz"   # readiness (DB populated)
curl "http://127.0.0.1:8000/healthz"  # liveness only
```

## How it works (short version)

1. **Pass the anti-bot.** The site is behind Imperva/Incapsula. A real headless
   Chromium (Playwright) solves the JS challenge; all data requests are then made
   with `fetch()` *inside the page* so the TLS fingerprint, cookies, and Client
   Hints stay consistent with the solved session.
2. **Crawl completely.** By default, request **Ontario-only** lots at the source
   (`BranchIds` + `PageSize=1000`, ~2–3 requests). Legacy `--canada-wide` mode pages
   all of Canada and filters client-side. Sort is `STOCK ASC` for stable pagination.
3. **Store two ways.** Land every row before parsing in durable gzipped JSONL,
   route rejected rows/page windows to a companion DLQ, and upsert normalized
   rows into SQLite. Price and lifecycle changes append to separate history tables.
4. **Serve.** FastAPI exposes bounded, authenticated inventory queries, stable
   cursor pagination, price/status history, freshness readiness, and a separately
   credentialed crawl command.

### Lifecycle statuses

Each lot carries a single `status` column:

| Status | Meaning |
|---|---|
| `active` | On market / upcoming (empty `ItemStatusDesc`) |
| `sold` | Sold (`ItemStatusDesc=Sold`) |
| `if_bid` | Conditional sale (`ItemStatusDesc=IfBid`) |
| `passed` | Did not sell (`ItemStatusDesc=Pass`) |
| `removed` | Was `active` but vanished from a completed crawl snapshot |

Sale outcomes are derived at parse time from `ItemStatusDesc`. After a
**two consecutive completed** full crawls, lots that were `active` but not seen
are transitioned to `removed`; concluded lots (`sold`/`if_bid`/`passed`) keep
their outcome and get `delisted_at` stamped. Reappearance resets the miss counter.
Lots are never deleted.

`final_price` is populated only for concluded lots (best anonymous bid signal from
`HighPrebidValue` / `TimedAuctionHighestBidAmountValue`; `WinningbidAmount` is
usually empty for anonymous users).

**API filtering:** `GET /lots` default is unchanged — returns everything. Use
`?status=active|sold|if_bid|passed|removed` to filter opt-in; `?status=all` is
explicit no-filter. Use `/readyz` for readiness (DB reachable + lot count) vs
`/healthz` for liveness.

Best-practice anti-flagging, safeguards (loop/duplicate prevention, retries,
completeness checks), usage, results, and limitations are documented in
[`docs/05-implementation.md`](docs/05-implementation.md).

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/01-research.md`](docs/01-research.md) | Site profile, Imperva anti-bot findings, Ontario footprint, volume sizing. |
| [`docs/02-architecture-and-plan.md`](docs/02-architecture-and-plan.md) | Architecture, tech stack, phased roadmap. |
| [`docs/03-storage-decision.md`](docs/03-storage-decision.md) | JSON vs database decision. |
| [`docs/04-data-model.md`](docs/04-data-model.md) | Lot schema, SQLite DDL. |
| [`docs/05-implementation.md`](docs/05-implementation.md) | **How the built scraper works, anti-detection, safeguards, usage, results.** |
| [`docs/06-realtime-archive-infrastructure.md`](docs/06-realtime-archive-infrastructure.md) | **Real-time price + historical archive: free-infra research & recommendation.** |
| [`docs/07-live-verification.md`](docs/07-live-verification.md) | **Live E2E verification of Ontario-at-source crawl (2026-06-26).** |
| [`docs/09-enterprise-hardening-plan.md`](docs/09-enterprise-hardening-plan.md) | **Production gates, target architecture, testing, SLOs, and multi-source roadmap.** |
| [`docs/10-implementation-plan.md`](docs/10-implementation-plan.md) | **Actionable next plan: media pointers/cache, UI boundary, remaining P0/P1 tasks.** |
| [`docs/ops-scheduling.md`](docs/ops-scheduling.md) | Cron/systemd crawl schedule + `scheduled_crawl.sh` retry wrapper. |
| [`docs/ops-backup.md`](docs/ops-backup.md) | Off-host backup upload + restore drill. |
| [`docs/ops-gateway.md`](docs/ops-gateway.md) | TLS nginx edge, rate/body limits, secrets, command isolation. |
| [`docs/project-tracker.html`](docs/project-tracker.html) | **Living HTML status board** (phases, tasks, tags, agent comments, PR links). |
| [`docs/project-tracker.md`](docs/project-tracker.md) | How agents must update the tracker (short). |
| [`docs/handoffs/2026-07-16-phase0-phase2-handoff.md`](docs/handoffs/2026-07-16-phase0-phase2-handoff.md) | Evening handoff (PRs #8/#9); next after backups = TLS gateway. |

## Project layout

```
iaai_scraper/      # the package (config, session, crawler, storage, api, cli, ...)
spike/             # Phase 0 exploration scripts used to discover the data path
tests/             # offline unit tests (parser + storage)
docs/              # research, design, and implementation docs
requirements.txt
```

## Limitations

- VIN is **masked** for anonymous users (full VIN needs a logged-in buyer account).
- The crawl stores thumbnail **URL pointers** only (no image binaries on the hot
  path). The API exposes `thumbnail_href` and can cache thumbs on demand via
  `GET /lots/{stock}/thumbnail` (see
  [`docs/10-implementation-plan.md`](docs/10-implementation-plan.md)).
- Automated collection, retention, redistribution, and imagery require appropriate
  source authorization or a licensed data agreement before production use.
- SQLite is the hardened single-source baseline; PostgreSQL and composite
  `(source, external_lot_id)` identity are required before multi-site syndication.
