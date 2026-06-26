# IAAI-Scraper

Data collection system for **IAA / IAAI Canada** vehicle auction listings
(`https://ca.iaai.com/`), scoped to **Ontario**. It collects structured auction
lot data, stores it in a searchable database, and exposes it through an internal
API for querying, filtering, and retrieval — optimised for completeness, speed,
reliability, and cost. Images are intentionally **not** collected.

> Status: **working end-to-end.** A full crawl retrieves all Ontario lots
> (~1,448 across 7 branches) into SQLite, served by a FastAPI read API.

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m playwright install --with-deps chromium

python -m iaai_scraper.cli crawl            # full Ontario crawl -> data/iaai_ontario.db
python -m iaai_scraper.cli crawl --max-pages 3 -v   # quick test crawl
python -m iaai_scraper.cli stats            # DB statistics
python -m iaai_scraper.cli serve            # internal API on :8000
```

Example API calls:

```bash
curl "http://127.0.0.1:8000/lots?make=toyota&year_min=2018&limit=20"
curl "http://127.0.0.1:8000/lots?branch_id=70&runs=true&sort=year&descending=true"
curl "http://127.0.0.1:8000/lots?status=sold&limit=5"   # opt-in status filter
curl "http://127.0.0.1:8000/lots/12033066"
curl "http://127.0.0.1:8000/lots/12033066/price-history"
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
3. **Store two ways.** Append every row to gzipped JSON Lines (audit/replay) and
   upsert normalized rows into SQLite. **Price changes** append to `price_history`.
4. **Serve.** A read-only FastAPI exposes query/filter/retrieve endpoints plus
   `GET /lots/{stock}/price-history` and `GET /stats/freshness`.

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
**completed** full crawl, lots that were `active` but not seen are transitioned
to `removed`; concluded lots (`sold`/`if_bid`/`passed`) keep their outcome and
get `delisted_at` stamped. Lots are never deleted.

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
- Images are skipped by design (URLs remain in the preserved `raw` payload).
- Sustained large-scale crawling should add a residential proxy (`IAAI_PROXY`) and
  is subject to IAAI's Terms of Use.
