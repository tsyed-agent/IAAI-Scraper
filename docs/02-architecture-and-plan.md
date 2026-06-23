# IAAI Ontario Scraper — Architecture & Build Plan

> Status: Proposed design. Companion to [`01-research.md`](./01-research.md) and the
> storage decision in [`03-storage-decision.md`](./03-storage-decision.md).

## 1. Design principles (from the brief)

- **Completeness** — capture every available lot field; nothing silently dropped.
- **Speed** — solve the anti-bot challenge once per session, then fetch fast.
- **Reliability** — idempotent, resumable, observable; survives blocks/restarts.
- **Cost efficiency** — Ontario-only, no images, incremental updates, dedup.

## 2. High-level architecture

```
                 ┌─────────────────────────────────────────────────┐
                 │                 Orchestrator                     │
                 │  (schedule, per-branch jobs, retries, backoff)   │
                 └───────────────┬─────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                   ▼
     ┌─────────────────┐ ┌──────────────────┐ ┌──────────────────┐
     │  Session Manager │ │  List Crawler     │ │  Detail Crawler   │
     │  (Imperva solve, │ │  (paginate Search │ │  (VehicleDetails/ │
     │  cookies, proxy, │→│  for ON, collect  │→│  {id}, full lot   │
     │  CSRF token)     │ │  stock numbers)   │ │  fields)          │
     └─────────────────┘ └──────────────────┘ └─────────┬────────┘
                                                         │ raw HTML/JSON
                                                         ▼
                                              ┌──────────────────────┐
                                              │   Raw landing layer   │
                                              │  JSONL (.jsonl.gz)    │  ← audit / replay
                                              └──────────┬───────────┘
                                                         │ parse + normalize
                                                         ▼
                                              ┌──────────────────────┐
                                              │  Normalizer / Loader  │
                                              │  (validate, dedup,    │
                                              │   upsert)             │
                                              └──────────┬───────────┘
                                                         ▼
                                              ┌──────────────────────┐
                                              │   Serving DB          │
                                              │  SQLite → PostgreSQL  │
                                              └──────────┬───────────┘
                                                         ▼
                                              ┌──────────────────────┐
                                              │   Internal API        │
                                              │  FastAPI (query/      │
                                              │  filter/retrieve)     │
                                              └──────────────────────┘
```

### Why a two-layer store (raw JSONL + DB)

We **keep both**, each doing what it's good at:

- **Raw JSONL landing layer** — every fetched lot is appended as one line of
  newline-delimited JSON (gzipped, partitioned by `branch/date`). Cheap,
  append-only, and lets us **re-parse history** if the parser improves or a bug
  is found, without re-scraping. This is the audit trail.
- **Serving database** — the normalized, deduplicated, indexed copy that powers
  the API's search/filter. Start **SQLite**, graduate to **PostgreSQL**.

Full rationale and the answer to "just JSON for now?" is in
[`03-storage-decision.md`](./03-storage-decision.md). Short version: **a flat JSON
file is fine only for a throwaway first snapshot; the actual product needs a DB.**
The recommended path uses JSONL as the raw layer and SQLite (then Postgres) as the
searchable serving layer — minimal extra effort, no painful migration later.

## 3. Components

### 3.1 Session Manager (the hard part)
- Uses a **headless browser (Playwright)** to load `ca.iaai.com/Search`, execute
  the Incapsula JS challenge, and obtain valid cookies (`visid_incap_*`,
  `incap_ses_*`, `ASP.NET_SessionId`, `IAAIT`) + the `__RequestVerificationToken`.
- **Harvests** cookies + token and hands them to a fast HTTP client (httpx) for
  the bulk list/detail requests — browser only for the handshake/renewal.
- Bound to a **single residential proxy IP** per session; rotates the whole
  session (IP + cookies together) on block. Honors `renewInSec`-style refresh.
- Fallback adapter: **managed scraping API** (Scrapfly/ZenRows/etc.) behind the
  same interface, toggled by config, for when self-hosted solving degrades.

### 3.2 List Crawler
- Drives the **Search** endpoint filtered to **Province = Ontario** (and/or the
  7 Ontario branch IDs), paginating with max page size.
- Emits the set of `(stockNumber, branch, list-level fields, last_seen)`.
- Reads the **total result count** to verify completeness per run.

### 3.3 Detail Crawler
- For each stock number, fetches `…/VehicleDetails/{stockNumber}` and parses the
  **full lot record** (all attributes). Concurrency-limited + jittered.
- Skips images entirely (don't fetch image binaries; we may keep image *URLs* as
  cheap text if useful, configurable).

### 3.4 Normalizer / Loader
- Validates against a schema (Pydantic), normalizes units/enums (damage codes,
  title types, run-and-drive, currency), and **upserts** into the DB keyed by
  stock number, tracking `first_seen` / `last_seen` / `sale_status` history.

### 3.5 Internal API (FastAPI)
- `GET /lots` — filter by make, model, year range, branch, damage, title type,
  sale date range, run/drive, price range; cursor pagination; sort.
- `GET /lots/{stock_number}` — full lot record.
- `GET /branches`, `GET /filters` — facet/enum values.
- `GET /healthz`, `/stats` — last run, counts, freshness.

## 4. Proposed tech stack

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | Best scraping + data ecosystem |
| Browser/anti-bot | **Playwright** (+ stealth), optional managed API fallback | Reliable Incapsula solve |
| HTTP (bulk) | **httpx** | Fast replay of harvested session |
| Parsing | **selectolax / BeautifulSoup** | Fast HTML extraction |
| Validation | **Pydantic v2** | Schema + normalization |
| Raw store | **gzip JSONL**, partitioned `data/raw/{branch}/{date}.jsonl.gz` | Cheap, replayable |
| Serving DB | **SQLite (MVP) → PostgreSQL** | Indexed search; JSONB for raw payload in PG |
| API | **FastAPI + Uvicorn** | Typed, fast, auto OpenAPI docs |
| Scheduling | cron / APScheduler (MVP) → a queue/worker later | Periodic refresh |
| Proxies | residential pool (rotated per session) | Beat IP reputation checks |

## 5. Proposed lot data model

See [`04-data-model.md`](./04-data-model.md) for the full field list and the
SQL DDL. Core fields: `stock_number` (PK), `vin`, `year/make/model/trim`,
`body_style`, `vehicle_type`, `branch`, `province`, `auction_date`,
`sale_status`, `current_bid`, `loss_type`, `primary/secondary_damage`,
`title_type/state`, `odometer` + units, `run_and_drive`, `keys`, `engine`,
`cylinders`, `fuel_type`, `drivetrain`, `transmission`, `color`, `seller`,
`acv`, `est_retail_value`, `repair_cost`, `lane/run number`, `detail_url`,
`first_seen`, `last_seen`, `raw_json`.

## 6. Roadmap (phased; no time estimates per project policy)

**Phase 0 — Spike (de-risk the anti-bot).** Prove we can pass Incapsula and pull
one Ontario Search page + a few lot detail pages. Dump results to a single JSON
file. Decide self-hosted Playwright vs managed API based on success rate/cost.
*Exit:* a reproducible script that reliably returns parsed Ontario lots.

**Phase 1 — MVP pipeline.** Session manager + list + detail crawlers writing raw
JSONL; normalizer loading **SQLite**; FastAPI read API with the core filters.
Ontario-only, no images, incremental upserts with `first_seen/last_seen`.
*Exit:* full Ontario inventory queryable via API, refreshable on a schedule.

**Phase 2 — Reliability & scale.** Proxy rotation + session pool, retries/backoff,
block detection + alerting, run metrics, completeness checks vs. total-result
count, resumability. Migrate SQLite → **PostgreSQL** (JSONB + GIN/btree indexes,
optional full-text). Sale-price history retention.

**Phase 3 — Hardening & ops.** Containerize, secrets management, observability
(structured logs, dashboards), cost monitoring of proxy/scraping-API spend,
backfill tooling, optional build-vs-buy review against licensed data providers.

## 7. Open questions to confirm

- Refresh cadence required (hourly? a few times/day? per branch sale day?).
- Do we need **sold price / sale history** retained over time, or only current
  open inventory?
- Acceptable monthly budget for proxies / managed scraping API (drives Phase 0
  build-vs-buy).
- Any downstream consumers/SLA for the internal API (affects SQLite→Postgres
  timing and concurrency needs).
