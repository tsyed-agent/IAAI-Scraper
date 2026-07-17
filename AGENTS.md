# AGENTS.md

## Project tracker (mandatory)

Living board: [`docs/project-tracker.html`](docs/project-tracker.html)  
How-to: [`docs/project-tracker.md`](docs/project-tracker.md)  
**Latest handoff:** [`docs/handoffs/2026-07-16-phase0-phase2-handoff.md`](docs/handoffs/2026-07-16-phase0-phase2-handoff.md)

After any task you finish, block, or test:

1. Update **only that task** in `docs/project-tracker.html` (status tag + checkbox).
2. Add **one** ≤2-line comment with your signature: `Cursor` / `Codex` / `Claude Code`.
3. Link PR/commit on that task when available; bump board `data-updated`.
4. Do not paste logs or rewrite plan docs into the HTML.

Plans (acceptance detail): `docs/09-…`, `docs/10-…`, `docs/ops-scheduling.md`,
and `docs/handoffs/`.

## Project

`IAAI-Scraper` — Python scraper for IAA/IAAI Canada (`https://ca.iaai.com/`)
Ontario auction lots. Playwright clears Imperva; data lands in SQLite + raw JSONL;
FastAPI serves a private read/command API. Single package `iaai_scraper`.

Code is on `main` (PRs #6–#10 merged). Prefer small task branches off updated
`main`. Phase 2 P0 board complete on `cursor/phase2-consolidate` (incl. 2.4b
durable worker). Next: Phase 3 UI / Phase 0 Task 12 in-container crawl verify;
0.11 in-container crawl still auth-gated.

**Crawler runs on a host you control** (CLI / Docker / cron). GitHub Actions is
CI (`pytest`) only — not the live scrape.

## Running things

Activate a local venv first (`.venv/` may be a broken Linux copy — use
`.venv311` or recreate). Then:

- Tests (offline): `python -m pytest`
- Crawl: `python -m iaai_scraper.cli crawl --max-pages 3 -v`
- Scheduled wrapper: `scripts/scheduled_crawl.sh` (see `docs/ops-scheduling.md`)
- Offsite backup: `scripts/offsite_backup.sh` (see `docs/ops-backup.md`)
- TLS gateway: `docker compose -f docker-compose.yml -f docker-compose.gateway.yml up -d` (see `docs/ops-gateway.md`)
- Stats: `python -m iaai_scraper.cli stats`
- API: `python -m iaai_scraper.cli serve` → `127.0.0.1:8000` (`/docs`)

## Non-obvious notes

- Live crawl can work without proxy from some environments; do **not** run
  casual live crawls — authorization + anti-bot risk (see doc 09).
- Outputs under `data/` are gitignored; never commit DB/raw.
- No ruff/black config; `python -m py_compile iaai_scraper/*.py tests/*.py` is fine.
- Ignore `.venv*/` and `graphify-out/` (otherwise Cursor shows ~900k fake diffs).
- Graphify: if `graphify-out/` exists, orient with `graphify query` before broad
  codebase exploration; run `graphify update .` after code edits.
