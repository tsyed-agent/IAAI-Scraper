# Handoff — 2026-07-16 (evening) — Phase 0 media auth + Phase 2 scheduler

> **Audience:** the next agent (or human) picking this project up cold.  
> Read this top to bottom before coding. Update
> [`docs/project-tracker.html`](../project-tracker.html) for every task you
> touch (see [`docs/project-tracker.md`](../project-tracker.md)).
>
> **Plans (acceptance detail):**  
> [`docs/09-enterprise-hardening-plan.md`](../09-enterprise-hardening-plan.md) ·
> [`docs/10-implementation-plan.md`](../10-implementation-plan.md) ·
> [`docs/ops-scheduling.md`](../ops-scheduling.md)  
> **Prior handoff (morning):**  
> [`2026-07-16-phase0-handoff.md`](./2026-07-16-phase0-handoff.md) — still useful
> for Phase 0 item background; this file supersedes it for *current* merge order
> and next tasks.

---

## 1. What this project is (30 seconds)

Ontario-scoped IAA/IAAI Canada (`ca.iaai.com`) scraper + private FastAPI.

- Playwright clears Imperva → in-page `fetch()` pulls search JSON (~2–3 requests).
- Storage: raw gzipped JSONL + DLQ, SQLite lots, append-only `price_history` /
  `lot_status_history`.
- API serves inventory, history, on-demand cached thumbnails.
- **Future UI talks only to our API — never to IAAI.**

Hard rules (do not regress):

1. Fail-closed crawls never archive inventory.
2. Lots are never deleted; concluded statuses are never downgraded.
3. Crawl hot path never downloads image binaries (URL pointers only).
4. Tests stay offline (loopback OK; no live IAAI in CI).
5. Do **not** run casual live crawls — authorization + anti-bot risk.

**Where things run**

| Workload | Runs on |
|---|---|
| Live crawl | Your machine / Docker / a host you control (`cli crawl` or `scheduled_crawl.sh`) |
| GitHub Actions | **CI only** (`pytest`) — not the scraper |
| GitHub Actions as crawl host | Researched as 🥉 fallback in doc 06; **not** the current plan |

---

## 2. Current state (verified 2026-07-16 ~21:15 UTC)

### Branch / PR topology

```text
main (709eb2d)   ← PRs #6 + #7 already merged
  ├── codex/phase0-signed-media-urls     ← PR #8  MERGEABLE, CI green
  │     0.6a access-log redaction + project tracker
  │     0.6b signed thumbnail URLs (?expires=&sig=)
  └── codex/phase2-scheduled-crawl       ← PR #9  MERGEABLE, CI green
        scheduled_crawl.sh + ops-scheduling.md + tracker copy
        .gitignore for .venv*/graphify-out
```

| PR | Title | Tests (branch) | URL |
|---|---|---|---|
| [#8](https://github.com/tsyed-agent/IAAI-Scraper/pull/8) | Phase 0: access-log redaction + signed media URLs | 133 offline | open |
| [#9](https://github.com/tsyed-agent/IAAI-Scraper/pull/9) | Phase 2: scheduled crawl wrapper + ops docs | 127 offline* | open |

\*PR #9 was cut from `main` before #8 landed, so its suite count is lower until #8 merges.

### Merge order (important)

Both PRs touch `docs/project-tracker.html`. Merge **#8 first**, then **#9**, and
resolve tracker conflicts in favor of the newer board (keep Phase 0 tasks 6–7
completed **and** Phase 2 task 4 completed + PR links).

```bash
gh pr merge 8 --merge
# wait for main CI
gh pr merge 9 --merge   # fix project-tracker.html if GitHub reports conflicts
```

After both land on `main`, expect roughly **133 + 4 scheduled tests − overlap ≈ 137**
(exact count: run `pytest -q` on updated `main`).

### What shipped this session (not yet on main until #8/#9 merge)

**Phase 0**

| Task | ID | Status | Where |
|---|---|---|---|
| Access-log token redaction | 0.6a / Handoff A | ✅ in PR #8 | `iaai_scraper/logging_utils.py`, `cli.serve` |
| Signed media URLs | 0.6b / Handoff C | ✅ in PR #8 | `auth.sign_media_path`, `media_signed_href`, `api._hydrate` |
| Project tracker HTML | — | ✅ in both PRs | `docs/project-tracker.html` + `.md` |
| In-container live crawl | 0.11 / Handoff B | ◐ / open | `/dev/shm` in compose; **manual verify still open** |
| Minimal unchanged-row UPDATE | 0.12 | deferred | skip |

**Phase 2**

| Task | ID | Status | Where |
|---|---|---|---|
| Thin scheduler + one retry | 2.4a / Handoff D | ✅ in PR #9 | `scripts/scheduled_crawl.sh`, `docs/ops-scheduling.md`, `tests/test_scheduled_crawl.py` |
| Full durable queue worker | 2.4b | open | later |
| Off-host backups + restore | 2.2 / Handoff E | open | **next coding task** |
| Legal / gateway / canary | 2.1, 2.3, 2.5 | open | mostly ops/legal |

### Explicitly not done this session

- No live crawl against `ca.iaai.com`.
- No end-to-end review of real lot results.
- No GitHub Actions crawl workflow.
- No Docker in-container crawl verification (Task 12 / 0.11).

---

## 3. Key files map

| File | Role |
|---|---|
| `iaai_scraper/crawler.py` | Fail-closed crawl; OS `fcntl` lock; archival gate |
| `iaai_scraper/storage.py` | SQLite, migrations-once, history, `backup_sqlite` |
| `iaai_scraper/auth.py` | Tokens + **signed media** (`sign_media_path`, `require_api_auth_flexible`) |
| `iaai_scraper/logging_utils.py` | Access-log redaction for `api_key=` / `sig=` |
| `iaai_scraper/images.py` | On-demand thumbnail cache (redirects refused, single-flight, etc.) |
| `iaai_scraper/api.py` | FastAPI; `thumbnail_href` via `media_signed_href` |
| `iaai_scraper/sync_manager.py` | In-process crawl job for API — not for production schedule |
| `scripts/scheduled_crawl.sh` | Cron entrypoint: CLI crawl + one retry |
| `docs/ops-scheduling.md` | Cadence + crontab examples |
| `docs/project-tracker.html` | Living status board (agents must update) |
| `tests/test_scheduled_crawl.py` | Offline fakes for the shell wrapper |
| `tests/test_api_auth.py` | Auth + signed URL + deprecated `?api_key=` |
| `tests/test_logging_utils.py` | Access-log redactor |

---

## 4. Next tasks — in order

> One small branch + PR per task off updated `main` after #8/#9 merge.  
> Run `python -m pytest -q` before/after. Never add networked live-crawl tests.  
> Update **only the task you touched** in `docs/project-tracker.html`.

### Task 1 — Merge #8 then #9 (human / agent with merge rights)

**Do this first** so later work is not based on stale `main`.

1. Merge PR #8.
2. Merge PR #9; resolve `project-tracker.html` if needed (union of statuses).
3. Pull `main`, run full pytest, confirm CI green.
4. Mark merge comments on tracker tasks 0.6a/0.6b/2.4 with merge SHAs.

### Task 2 — In-container crawl verify (Phase 0 · 0.11 · Handoff B)

**Ops / manual. Only if live crawl is authorized.**

```bash
cp .env.example .env   # IAAI_API_TOKEN=$(openssl rand -hex 32)
docker compose up --build -d
TOKEN=…
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/healthz
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8000/commands/crawl
# poll /commands/crawl/status until completed or failed
```

If Chromium dies on shm despite `/dev/shm` tmpfs, add
`--disable-dev-shm-usage` in `iaai_scraper/session.py` launch args and re-test.
Flip doc 09 Phase 0 row 0.11 and tracker Task 12 to ✅ when done.

If live crawl is **not** authorized: leave open, note “blocked on authorization”
on the tracker, and proceed to Task 3.

### Task 3 — Off-host backups + restore drill (Phase 2 · 2.2 · Handoff E) ★ next code

`python -m iaai_scraper.cli backup` already writes atomic snapshots under
`data/backups/`. Build the off-host leg:

1. Script (e.g. `scripts/offsite_backup.sh` or small Python module) that:
   - Takes latest SQLite backup + newest raw `*.jsonl.gz`
   - Uploads to **versioned S3-compatible** object storage (bucket/creds via env)
   - Writes a **SHA-256 manifest**
   - Prunes local copies per doc 09 §7 (keep 5 local DB backups; raw 90d hot)
2. **Restore drill** script that:
   - Downloads latest snapshot to a temp dir
   - Opens with `SqliteStore`
   - Asserts `PRAGMA integrity_check=ok` and a configurable lot-count floor
3. Offline tests with a fake/local “S3” (e.g. filesystem backend or moto) — **no
   real cloud required in CI**.
4. Document env vars in `.env.example` + a short `docs/ops-backup.md` (or section
   in ops-scheduling).

**Acceptance:** restore drill passes in CI against a fixture DB; backups are not
considered done without that proof (doc 09 §2.4).

### Task 4 — TLS gateway / secrets / audit (Phase 2 · 2.3)

Mostly platform: reverse proxy in front of Docker’s `127.0.0.1:8000`, rate/body
limits, managed secrets, access logs. Keep command routes off the public edge.
Can be docs + compose/nginx sample first; full abuse tests later.

### Task 5 — Legal / image display policy (Phase 2 · 2.1)

Non-code. Blocks public image display. Tracker stays `blocking` until product/legal.

### Task 6 — Authorized 3-run live canary (Phase 2 · 2.5)

Only after authorization. Use `scripts/e2e_live_3runs.sh` into an **isolated**
temp data dir — never production `data/`. Compare totals/branches/prices to source.

### Task 7 — (later) Adaptive sale-window scheduling (Handoff F / Phase 5)

After Task 3’s scheduler is in use: tighten to 15–30 min when `bid_closes_at` is
near. Scheduler layer only — not inside `crawler.py`.

### Deferred (do not pick up casually)

- 0.12 unchanged-row minimal UPDATE
- PostgreSQL / second source (Phase 4) — after P0 gates
- Full-gallery image fetch during crawl
- GitHub Actions as primary crawl host (doc 06 🥉 only)

---

## 5. How to work in this repo

### Venv

Committed `.venv/` may be a broken Linux copy. Prefer:

```bash
/opt/homebrew/bin/python3.11 -m venv .venv311
.venv311/bin/pip install -r requirements-dev.txt
.venv311/bin/python -m pytest -q
```

`.gitignore` on PR #9 ignores `.venv*/` and `graphify-out/` — without that,
Cursor shows ~900k “changes” from site-packages.

### Project tracker (mandatory)

Open [`docs/project-tracker.html`](../project-tracker.html).

After any task:

1. Set `data-status` + visible tag on **that task only**.
2. One ≤2-line comment with signature:
   - `<span class="sig cursor">Cursor</span>`
   - `<span class="sig codex">Codex</span>`
   - `<span class="sig claude">Claude Code</span>`
3. Link PR/commit; bump `data-updated` on `<html>`.
4. Do not paste logs or rewrite plan docs into the HTML.

### Graphify

If `graphify-out/graph.json` exists: `graphify query "…"` before broad exploration;
`graphify update .` after code edits (needs full FS permissions in some sandboxes).

---

## 6. Gotchas

- `SqliteStore` schema runs **once per DB path per process** (`_SCHEMA_READY`).
  Recreating the same path in one process → `ensure_schema=True`.
- Thumbnail cache locks are module-level (`images._KEY_LOCKS`).
- `POST /commands/crawl` dies with the API process — production schedule uses
  `scripts/scheduled_crawl.sh` → CLI.
- Lock busy message must stay recognizable:
  `Another crawl appears to be running` (wrapper greps this; exit 0, no retry).
- `thumbnail_href` is signed only when auth is enabled **and** `IAAI_API_TOKEN`
  is set; bare path when auth is off (local open API).
- `?api_key=` on thumbnails is **deprecated** but still accepted (one release).
- Access logs must never show raw `api_key=` or `sig=` (redactor on `uvicorn.access`).
- E2E live scripts write only to temp dirs — never point them at `data/`.
- `crawl_runs.total_canada` name is historical; value is scoped total (Ontario default).
- `final_price` is a bid signal, not a confirmed sale price.

---

## 7. Suggested first commands for the next agent

```bash
gh pr view 8 --json state,mergeable,statusCheckRollup
gh pr view 9 --json state,mergeable,statusCheckRollup
# merge #8 then #9 (see §4 Task 1)

git checkout main && git pull --ff-only
python3.11 -m venv .venv311 && .venv311/bin/pip install -r requirements-dev.txt
.venv311/bin/python -m pytest -q

# open tracker
open docs/project-tracker.html   # or xdg-open

# next coding work (after merges): off-host backup + restore drill
git checkout -b codex/phase2-offsite-backup
```

---

## 8. Success snapshot for this handoff

| Gate | State |
|---|---|
| Staging baseline (PR #6/#7) | ✅ on `main` |
| Phase 0 code (0.6a/0.6b) | ✅ PR #8 (merge pending) |
| Phase 0 0.11 container crawl | open (auth-gated) |
| Phase 1 Media API | ✅ on `main` |
| Phase 2 thin scheduler (2.4a) | ✅ PR #9 (merge pending) |
| Phase 2 off-host backup (2.2) | **next code** |
| Production P0 (legal, TLS, canary) | open |
| Live crawl this session | **not run** |

When you finish a task, leave a short signed comment on the tracker and a
1–2 sentence note in a new handoff only if the topology or next-task order
changes materially.
