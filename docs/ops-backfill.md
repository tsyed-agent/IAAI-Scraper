# Ops: raw baseline backfill

Rebuild (or seed) SQLite from surviving raw landing archives **without** a live
crawl. Offline only — never contacts `ca.iaai.com`.

## Command

```bash
# Default: data/raw → data/iaai_ontario.db (or IAAI_DATA_DIR / IAAI_DB_PATH)
python -m iaai_scraper.cli backfill-raw -v

# Explicit paths
python -m iaai_scraper.cli backfill-raw \
  --raw-dir /path/to/raw \
  --db-path /path/to/iaai_ontario.db

# Abort on first bad row (default: DLQ and continue)
python -m iaai_scraper.cli backfill-raw --strict
```

## Behaviour

| Step | Detail |
|---|---|
| Discover | Recurse under `--raw-dir` for `*.jsonl` / `*.jsonl.gz`; skip `*.dlq.*` |
| Order | Oldest file first (mtime) so later snapshots win on upsert |
| Parse | Same `parse_row` + Ontario filter as the crawler |
| Store | Idempotent `SqliteStore.upsert_lot` (safe to re-run) |
| Audit | Inserts/updates a `crawl_runs` row with `run_type=backfill` |
| Bad rows | Written to `raw/<day>/backfill-*.dlq.jsonl.gz` unless `--strict` |

Does **not** call `archive_missing` — a partial archive set must not delist lots.
