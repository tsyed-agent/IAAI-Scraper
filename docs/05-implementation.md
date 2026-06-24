# Implementation & Operations Guide

> How the built scraper works, how to run it, the anti-detection practices it
> uses, the safeguards it ships with, and its known limitations. Companion to the
> research/design docs `01`–`04`.

## 1. What got built

A self-hosted (no paid scraping API — the cheaper option) pipeline:

```
Playwright (solves Incapsula)  ──►  in-page fetch of /Search/GetSearchResult
        │                                   │
        │                                   ▼
        │                         parse + normalize (Lot)
        │                                   │
        ▼                                   ▼
 optional VDP enrichment        raw JSONL (gzip)  +  SQLite (upsert)
                                                    │
                                                    ▼
                                          FastAPI internal API
```

Modules (all under `iaai_scraper/`):

| Module | Responsibility |
|---|---|
| `config.py` | Tunables, Ontario branch registry, politeness/safeguard knobs (env-overridable). |
| `session.py` | Launches Chromium, passes the Incapsula challenge, runs in-page `fetch()`, retries/backoff, block detection, jittered pacing, proxy-ready. |
| `search_client.py` | Builds the `GetSearchResult` form payload, paginates, extracts the authoritative total. |
| `parser.py` / `models.py` | Defensive normalization of a raw row into a typed `Lot` (raw payload always preserved). |
| `storage.py` | Gzip JSONL landing layer + SQLite serving DB with idempotent upserts and a per-run audit table. |
| `detail.py` | Optional VDP enrichment (off by default). |
| `crawler.py` | Orchestrates the full Ontario crawl with completeness + safety guarantees. |
| `api.py` | Read-only FastAPI (query/filter/retrieve/stats). |
| `cli.py` | `crawl` / `stats` / `serve` commands. |

## 2. How the data path was discovered (Phase 0)

The `spike/` scripts (throwaway exploration) established, against the live site:

- **Search data endpoint:** `POST /Search/GetSearchResult/` (form-encoded). Returns
  `RunList` (the lot rows) and `SummaryList` whose `TOTAL_COUNT` entry is the
  authoritative result count.
- **Stable pagination:** sorting by `RunlistSort=STOCK ASC` (the immutable stock
  number) yields **zero page overlap**, unlike the default time-based sort which
  reorders as live auctions churn (we measured ~24% duplicate rows with it).
  This is the single most important reliability decision.
- **Ontario classification:** by `StockBranchId` ∈ `{10,52,56,61,64,70,71}`
  (London, Hamilton, Toronto/Oshawa, Ottawa, Sudbury, Toronto North, Toronto West),
  validated against `StockBranchDescription`.
- **Detail page (VDP):** `/Vehicles/VehicleDetails?stockno={StockNum}`.

## 3. Anti-detection / best practices (how we avoid getting flagged)

Imperva/Incapsula scores TLS fingerprint, IP reputation, a JS challenge
(`reese84`/`utmvc`), cookie chain, and behaviour. Our practices:

1. **Real browser, not raw HTTP.** Chromium executes the JS challenge and passes
   the canvas/audio/`navigator.webdriver`/Client-Hints checks that `requests`/
   `curl` fail. Light stealth init-script removes the obvious headless tells.
2. **One coherent session.** All data requests are issued with `fetch()` *inside
   the page*, so TLS/JA3, the `incap_ses_*`/`visid_incap_*` cookie chain, and
   Client Hints all match the session that solved the challenge.
3. **Human-like pacing.** A random jittered delay (`DELAY_MIN..MAX`, default
   1.5–3.5 s) between requests, with low concurrency (single page), to avoid the
   machine-regular timing the behavioural layer flags.
4. **Session re-solve on soft block.** A non-JSON/interstitial response triggers a
   fresh challenge solve before retrying (see below).
5. **Proxy-ready.** Set `IAAI_PROXY` to route through a residential proxy —
   strongly recommended for sustained crawling, because datacenter IPs (incl.
   cloud VMs) get elevated scrutiny. The crawl works without one for modest runs.
6. **Minimise footprint.** Ontario-only, no image downloads, dedup + incremental
   upserts so re-runs are cheap and low-volume.

## 4. Safeguards, error handling, loop & duplicate prevention

- **Loop prevention:**
  - Hard page cap `MAX_LIST_PAGES` (default 200; the full set is ~53 pages).
  - "No new stock numbers on a page" → stop (defends against a server repeating
    the final page).
  - Short page (`< PAGE_SIZE`) → last page reached, stop.
  - Reached authoritative `TOTAL_COUNT` → stop.
- **Duplicate prevention:**
  - In-run dedup via a `seen_stock` set keyed by stock number.
  - In-DB dedup via the `stock_number` PRIMARY KEY and upsert (`inserted` /
    `updated` / `unchanged`), with `first_seen` preserved and `last_changed`
    bumped only when a tracked field changes.
- **Error handling:**
  - Retries with exponential backoff (`MAX_RETRIES`, `BACKOFF_BASE_S`) for
    transient failures and soft blocks.
  - Per-row parsing is defensive — a malformed row is counted (`bad`) and skipped,
    never fatal.
  - The crawl records a row in `crawl_runs` (status, counts, note) even on failure.
- **Completeness check:** final status is `completed` only when we paged through
  ≥ 98 % of the authoritative Canada total (slack for live churn); otherwise
  `completed_partial`.

## 5. Usage

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium

# Full Ontario crawl (writes data/iaai_ontario.db + data/raw/*.jsonl.gz)
python -m iaai_scraper.cli crawl

# Quick limited test crawl
python -m iaai_scraper.cli crawl --max-pages 3 -v

# With per-lot detail enrichment (slower, more requests)
python -m iaai_scraper.cli crawl --enrich

# Inspect DB stats
python -m iaai_scraper.cli stats

# Serve the internal API
python -m iaai_scraper.cli serve --port 8000
```

Useful env overrides: `IAAI_PROXY`, `IAAI_DELAY_MIN`/`IAAI_DELAY_MAX`,
`IAAI_HEADLESS`, `IAAI_DB_PATH`, `IAAI_DATA_DIR`, `IAAI_ENRICH_DETAILS`.

### API endpoints
- `GET /lots` — filter by `make, model, branch_id, province, year_min/max,
  auction_date_from/to, runs, title_brand_type, auction_type, keyword`; paginate
  (`limit`, `offset`); sort (`sort`, `descending`).
- `GET /lots/{stock_number}` — full record incl. preserved `raw`.
- `GET /branches`, `GET /stats`, `GET /healthz`.

## 6. Verified results (live run)

A full crawl on 2026-06-24 produced:

- **status=completed**, paged 5218/5218 Canada lots (53 pages), ~4m24s.
- **1,448 Ontario lots**, 0 duplicates, 0 bad rows. By branch: Toronto/Oshawa 386,
  London 301, Hamilton 222, Ottawa 165, Toronto North 161, Toronto West 134,
  Sudbury 79.
- Field completeness: VIN/year/make/model/branch 100 %; damage 99 %; transmission
  97 %; auction_date 97 %; engine 96 %; odometer 95 %; damage_estimate 90 %
  (gaps reflect fields genuinely absent on the source for some lots).
- Re-running the crawl reported all rows `unchanged` (idempotent, no duplicates).

## 7. Known limitations

- **VIN is masked** for anonymous users (e.g. `1C6RR7FT6HS******`). The full VIN
  requires an authenticated IAAI buyer account; enrichment can be extended to a
  logged-in session if/when that's in scope and permitted.
- **Images are intentionally skipped** (per the brief). Image *URLs* remain in the
  preserved `raw` payload if needed later.
- **Ontario = Ontario branch** (by `StockBranchId`). A few vehicles physically
  located out-of-province but auctioned through an Ontario branch are included by
  design (they are Ontario-branch sales).
- Sustained large-scale crawling should add a **residential proxy** and is subject
  to IAAI's Terms of Use (see `01-research.md`).
