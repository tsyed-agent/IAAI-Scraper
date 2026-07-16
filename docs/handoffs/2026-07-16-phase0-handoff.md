# Handoff — 2026-07-16 — PR #6 review, Plan v2, Phase 0 fixes

> **Audience:** the next agent (or human) picking this project up cold.
> Read this file top to bottom before touching anything. Every next task below
> is self-contained: it says what to do, in which files, and how to prove it
> works. The authoritative plan is
> [`docs/09-enterprise-hardening-plan.md`](../09-enterprise-hardening-plan.md)
> (release gates, phases) and
> [`docs/10-implementation-plan.md`](../10-implementation-plan.md) (media/UI
> checklists). This handoff tells you where we are inside those plans.
>
> **Status board:** [`docs/project-tracker.html`](../project-tracker.html) —
> update the task you touch (see [`project-tracker.md`](../project-tracker.md)).

---

## 1. What this project is (30-second version)

A scraper + private API for IAA/IAAI Canada (`ca.iaai.com`) vehicle auction
lots, scoped to **Ontario**. Playwright/Chromium solves the Imperva challenge,
then in-page `fetch()` pulls the search JSON (~1,453 lots in 2–3 requests).
Data lands three ways: raw gzipped JSONL (audit/replay + DLQ), normalized rows
in SQLite (`data/iaai_ontario.db`), and append-only `price_history` /
`lot_status_history` tables. A FastAPI app serves queries, price/status
history, and on-demand cached thumbnails. **The UI (future) only ever talks to
our API — never to IAAI.** Prices fluctuate (it's an auction): every crawl is a
sampling observation; a changed price appends a history row.

Hard rules that must never regress:

- **Fail-closed:** an incomplete/anomalous crawl must never be marked
  `completed` and must never archive (delist) inventory.
- **Lots are never deleted.** Sold/removed lots are the historical archive.
- **Concluded outcomes are never downgraded** (a `sold` lot reappearing as
  active stays `sold`).
- **The crawl hot path never downloads images** — pointers only.
- **Tests are offline** — no network except loopback; deterministic fakes.
- **Do not run live crawls against ca.iaai.com casually** — anti-bot exposure
  and authorization are real constraints (doc 09 §2, §10).

## 2. Current state (verified 2026-07-16)

### Branch / PR topology

```text
main (a3e8a93)
  └── codex/enterprise-history-hardening   ← PR #6 → main   (MERGEABLE, CI green)
        └── codex/plan-v2-pr6-followups    ← PR #7 → PR#6 branch (MERGEABLE, CI green)
              ├── 7ccf451 docs: plan v2 (Phase 0, cadence, retention, retry/fallback)
              └── 1c08f7c fix: Phase 0 hardening (code fixes, 123 tests)
```

- **PR #6** "Enterprise history hardening (doc 09 Phases A–C)" — the big
  staging baseline (~4.3k lines). Reviewed 2026-07-16; verdict: sound.
- **PR #7** "Phase 0: PR #6 review fixes + plan v2" — targets the **PR #6
  branch**, not main, because it is stacked on PR #6's head. Merging it into
  main directly would drag PR #6's entire diff with it and leave PR #6 dangling.
- **Merge order:** merge **PR #7 → PR #6 branch** first, then **PR #6 → main**.
  Both are conflict-free and CI-green right now:

  ```bash
  gh pr merge 7 --merge        # into codex/enterprise-history-hardening
  gh pr merge 6 --merge        # into main (re-check CI after #7 lands)
  ```

- GitHub auth gotcha on this machine: two accounts are configured. Pushing
  needs `gh auth switch -u tsyed-agent && gh auth setup-git` (the
  `hyperexpert575` account has no write access).

### Test / tooling state

- **123 tests pass** (`python -m pytest -q`), Python 3.11 and 3.13, CI matrix green.
- The repo's committed `.venv/` is a **broken Linux copy** (`/home/ubuntu`
  shebangs) — do not use it. Create a fresh env:

  ```bash
  /opt/homebrew/bin/python3.11 -m venv .venv311   # macOS; any 3.11/3.13 works
  .venv311/bin/pip install -r requirements-dev.txt
  .venv311/bin/python -m pytest -q                # expect: 123 passed
  ```

  (Python 3.14 fails: no wheels for pinned pydantic-core/greenlet.)

### What was done this sprint

1. **Full review of PR #6** (storage/crawler/API/auth/images/Docker/tests).
   Findings are recorded as **doc 09 §5 Phase 0** — a 12-item table, each with
   status, file locations, and acceptance criteria.
2. **Plan v2** (`docs/09`): added Phase 0; §6 refresh-cadence policy (4–6 h
   baseline, 15–30 min sale-window tightening keyed on `bid_closes_at`; cron,
   not the API; scheduler owns whole-run retry); §7 retention rules (lots =
   forever; raw = 90 d hot / ≥12 mo off-host; thumbs = evict removed + 30 d
   TTL); §8 error/retry/fallback matrix; §9 caching strategy; media
   architecture decision record (§3).
3. **Phase 0 code fixes implemented** (9 of 12 items ✅ — see doc 09 Phase 0
   table for the per-item map):
   - 0.1 schema/migrations run **once** per DB path per process
     (`storage._SCHEMA_READY` memo + `api._lifespan`); API reads are write-free.
   - 0.2 thumbnail fetcher **refuses redirects** (`images._RefuseRedirects`).
   - 0.3 `price_history` gained `currency` + `source_observed_at` columns.
   - 0.4 lifecycle rows live only in `lot_status_history`; legacy `status_*`
     rows filtered from price-history reads.
   - 0.5 thumbnail cache: single-flight, negative cache
     (`IAAI_IMAGE_NEGATIVE_TTL`), stale-on-error, `evict()`/`sweep()` +
     removed-lot/TTL sweep at API startup (`IAAI_IMAGE_CACHE_TTL`).
   - 0.7 `IAAI_READYZ_PUBLIC=true` opens `/readyz` to probes.
   - 0.8 `sold_from/sold_to` require a concluded status.
   - 0.9 `--canada-wide` page-size default = 100.
   - 0.10 duplicate-page detection normalizes `StockNum` like the parser.
   - 0.11 *(partial)* `/dev/shm` tmpfs added to docker-compose; **manual
     in-container crawl verification still open** (Task B below).

## 3. Key files map

| File | What it is |
|---|---|
| `iaai_scraper/crawler.py` | Orchestrates a crawl: OS lock, page recovery/anomaly handling, fail-closed status, archival gate. |
| `iaai_scraper/storage.py` | SQLite store: DDL + idempotent migrations (`_MIGRATIONS`), `upsert_lot`, price/status history, two-miss `archive_missing`, online `backup_sqlite`, keyset `query_lots`. |
| `iaai_scraper/search_client.py` / `session.py` | Search POST contract validation / Playwright session, transport retries + backoff, in-page fetch timeout. |
| `iaai_scraper/parser.py` / `models.py` / `lifecycle.py` | RunList row → `Lot`; status derivation (`sold`/`if_bid`/`passed`/`active`), `final_price` signal. |
| `iaai_scraper/api.py` | FastAPI app: lots/filters/stats/history/thumbnail/commands; lifespan does schema-once init + cache sweep. |
| `iaai_scraper/auth.py` | Bearer/X-API-Key auth, separate command token, `require_readyz_auth`, `require_api_auth_flexible` (`?api_key=` for `<img>`). |
| `iaai_scraper/images.py` | Thumbnail cache (see 0.2/0.5 above). |
| `iaai_scraper/sync_manager.py` | In-process background crawl job for `POST /commands/crawl` (not durable — by design until the P0 worker exists). |
| `scripts/verify_ontario_crawl.py`, `scripts/audit_raw_coverage.py` | Post-crawl invariants; offline raw-snapshot schema/completeness audit. |
| `tests/` | 123 offline tests; `tests/conftest.py:make_row` builds realistic RunList rows. |

## 4. Next tasks — in order, fully specified

> Work each task as its own small branch + PR (after the merges in §2).
> Run `python -m pytest -q` before and after; all 123+ tests must pass.
> Never add a test that touches the network (loopback servers are OK).

### Task A — Access-log token redaction (doc 09 Phase 0 item 0.6a) ✅

**Status:** implemented on branch `codex/phase0-access-log-redaction`.
`iaai_scraper/logging_utils.py` provides `AccessLogTokenRedactor`;
`cli.serve` installs it on `uvicorn.access` before `uvicorn.run`. Offline
tests in `tests/test_logging_utils.py`.

### Task B — Verify the in-container crawl (doc 09 Phase 0 item 0.11)

**Do this** (requires Docker; this is a *manual/ops* check, no code expected):
```bash
cp .env.example .env   # set IAAI_API_TOKEN=$(openssl rand -hex 32)
docker compose up --build -d
TOKEN=<the token>
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/healthz
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/commands/crawl
watch curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/commands/crawl/status
```
**Only do this if a live crawl is authorized** (doc 09 §2). If Chromium
crashes with shm/sandbox errors despite the `/dev/shm` tmpfs, add
`--disable-dev-shm-usage` to the Chromium launch args in
`iaai_scraper/session.py` (look for the `playwright` browser launch call) and
re-test. Record the result in doc 09 Phase 0 row 0.11 (flip ◐ to ✅).

### Task C — Signed short-lived media URLs (doc 09 Phase 0 item 0.6b) ✅

**Status:** implemented on branch `codex/phase0-signed-media-urls`.
`auth.sign_media_path` / `verify_media_signature` / `media_signed_href`;
`_hydrate` embeds `?expires=&sig=`; thumbnail auth accepts header, signed query,
or deprecated `?api_key=`. Offline tests in `tests/test_api_auth.py`.

### Task D — Scheduler + whole-run retry (doc 09 §6, P0 "durable worker" start)

**Do this** (ops/docs first, code second):
1. Add `docs/ops-scheduling.md` (or a README section) with the recommended
   crontab, e.g. every 6 hours with jitter:
   `17 */6 * * * cd /srv/iaai && .venv/bin/python -m iaai_scraper.cli crawl || <alert-hook>`
2. Add a thin wrapper script `scripts/scheduled_crawl.sh`: run the CLI crawl;
   on non-zero exit, retry **once** after 20 minutes; on second failure, emit a
   loud log line / webhook call and exit non-zero. Keep it POSIX sh, no deps.
3. Do **not** build the full queue/worker yet — that's the P0 package in doc 09
   §15; this task just makes today's cron path safe.

**Acceptance:** script is idempotent under the crawl lock (a concurrent run
exits cleanly with "another crawl running"), and a simulated failure
(`IAAI_MAX_LIST_PAGES=0`-style or a fake) triggers exactly one retry.

### Task E — Off-host backups (doc 09 §7 + P0 package)

`python -m iaai_scraper.cli backup` already produces atomic snapshots under
`data/backups/`. Build the off-host leg: a script that uploads the latest
snapshot + newest raw `*.jsonl.gz` files to versioned object storage
(S3-compatible; bucket/creds via env), writes a SHA-256 manifest, prunes local
copies per doc 09 §7, and a **restore drill** script that downloads the latest
snapshot, opens it with `SqliteStore`, and asserts `PRAGMA integrity_check`
plus a lot-count floor. Backups don't count until the restore drill passes.

### Task F — (P1, after the above) Adaptive sale-window scheduling

Doc 09 §15 P1 row. Extend the Task D wrapper: query the DB for
`MIN(bid_closes_at)` among active lots; if any lot closes within N hours,
schedule 15–30 min runs until the window passes. Design first, keep it in the
scheduler layer — do not put timing logic inside the crawler.

### Deferred / do not do without a reason

- 0.12 unchanged-row minimal UPDATE (only matters at multi-source scale).
- PostgreSQL / second source (doc 09 Phase D) — blocked on P0 gates.
- Full-gallery image fetching — explicitly out of scope (doc 10 §2).

## 5. Gotchas the next agent must know

- `SqliteStore.__init__` skips schema work when the resolved DB path is in
  `storage._SCHEMA_READY` (per-process memo). Tests get isolation from unique
  `tmp_path` DBs. If you ever delete and recreate the *same* DB file inside one
  process, pass `ensure_schema=True`.
- `ThumbnailCache` is constructed per request; its single-flight locks are
  module-level (`images._KEY_LOCKS`) keyed by `cache_dir::stock`.
- The API's `/commands/crawl` job dies with the process — that's known and
  accepted until the durable worker (Task D is the interim mitigation).
- `crawl_runs.total_canada` holds the authoritative total *for the crawl
  scope* (Ontario by default) — the column name is historical.
- `final_price` is a **bid signal**, not a confirmed sale price (doc 09 §4);
  `winning_bid` is stored separately.
- Legacy DBs may still contain `status_*` rows in `price_history`; they're
  migrated into `lot_status_history` once and *filtered* from reads — don't
  delete them.
- E2E live scripts (`scripts/e2e_live_3runs.sh`) write only to a temp dir —
  keep it that way; never point tests at `data/`.
