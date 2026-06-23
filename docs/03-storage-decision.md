# Storage Decision: "Just JSON for now?" — or do we need a database?

> This document answers the specific question raised in the brief: *for simplicity,
> should the retrieved data just be stored as a JSON file for now, or is that too
> much data and we should start with a database?*

## TL;DR

**Use both, in layers — and don't build the product on a single flat JSON file.**

- A single `lots.json` is **fine for a throwaway first snapshot** (proving the
  scraper works). It is **not** fine as the system of record, because the stated
  goal is a **searchable, filterable** store behind an API.
- Recommended from day one:
  - **Raw layer = JSON Lines** (`.jsonl`, one lot per line, gzipped) — cheap,
    append-only, replayable. This is your "save it as JSON" instinct, done right.
  - **Serving layer = a database**, starting with **SQLite** (a single file, zero
    ops) and graduating to **PostgreSQL** when concurrency/scale demand it.

This costs only marginally more than a flat JSON file up front and avoids a
painful rewrite the moment you need real filtering.

## Why a single JSON file breaks down

The data volume itself is **not** the problem at Ontario scale:

- ~5,000 active Ontario lots × ~3 KB ≈ **~15 MB** for a full snapshot — trivially
  small for a JSON file.

The problems are everything *around* the data, which the brief explicitly asks for:

| Requirement | Flat JSON file | Database |
|---|---|---|
| **Query / filter** (make, year, branch, damage, price…) | Load entire file into memory and scan every time | Indexed `WHERE` — fast, partial reads |
| **Search speed** | O(n) per query, grows with file | O(log n) with indexes |
| **Incremental updates / upsert** | Rewrite whole file; risky | `INSERT … ON CONFLICT … UPDATE` per row |
| **Dedup by stock number** | Manual, full-file logic | Primary key constraint |
| **History** (`first_seen`/`last_seen`, sold price over time) | Hard; file grows unbounded | Natural with rows + timestamps |
| **Concurrent writers (scraper) + readers (API)** | File corruption / locking pain | Built-in transactions |
| **Crash safety / partial runs** | Easy to corrupt a big file | Transactional, resumable |
| **Growth over time** (100k+ rows/yr) | Multi-hundred-MB files re-read per query | Stays fast with indexes |

In short: JSON is a great **transport/landing** format and a poor **query engine**.

## The recommended layered approach

```
Scraper ──► Raw JSONL (.jsonl.gz, partitioned by branch/date)   [audit / replay]
                    │  parse + validate + dedup
                    ▼
              Serving DB (SQLite → PostgreSQL)                  [search / filter / API]
                    │
                    ▼
              Internal API (FastAPI)
```

- **Raw JSONL** keeps a faithful, append-only record of exactly what we fetched.
  If the parser improves or we find a bug, we **re-derive the DB from raw** with
  no re-scraping (saves anti-bot budget — the expensive part).
- **DB** is the normalized, indexed copy the API reads from.

### Why SQLite first (not Postgres immediately)
- Zero infrastructure — a single `.db` file, no server to run.
- Real SQL, indexes, and transactions; comfortably handles **millions** of rows.
- Perfect for the MVP and even modest production for a single-writer scraper.

### When to move to PostgreSQL
Migrate once any of these is true:
- Multiple concurrent writers or several API consumers / an SLA.
- You want **JSONB** to store the full raw payload alongside typed columns, with
  **GIN** indexes, or **full-text search** across descriptions.
- Dataset and history grow into the millions and you need robust concurrency.

Because both are accessed through the same loader/ORM layer (e.g. SQLAlchemy), the
SQLite→Postgres switch is a connection-string + migration change, not a rewrite.

## Concrete recommendation for *right now*

1. **Phase 0 spike:** yes, dump to a single JSON file just to prove the scrape +
   parse works end-to-end. This matches the "keep it simple" instinct.
2. **Phase 1 MVP (immediately after):** switch storage to **raw JSONL + SQLite**,
   put **FastAPI** in front. This is barely more work than the flat file and
   delivers the actual goal — a *searchable, filterable* API.

So: **start with JSON for the very first proof, but plan for the database now**;
adopting SQLite early is cheap insurance against an inevitable migration.
