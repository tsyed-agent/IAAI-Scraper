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
curl "http://127.0.0.1:8000/lots/12033066"
curl "http://127.0.0.1:8000/stats"
```

## How it works (short version)

1. **Pass the anti-bot.** The site is behind Imperva/Incapsula. A real headless
   Chromium (Playwright) solves the JS challenge; all data requests are then made
   with `fetch()` *inside the page* so the TLS fingerprint, cookies, and Client
   Hints stay consistent with the solved session.
2. **Crawl completely.** Page through the whole Canada result set sorted by
   `STOCK ASC` (immutable stock numbers → stable, non-overlapping pages), keep
   only Ontario-branch lots, dedup by stock number.
3. **Store two ways.** Append raw rows to gzipped JSON Lines (audit/replay) and
   upsert normalized rows into SQLite (`first_seen`/`last_seen`/`last_changed`).
4. **Serve.** A read-only FastAPI exposes query/filter/retrieve endpoints.

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
