# Ops: crawl scheduling

Production crawls must run from an external scheduler (cron / systemd timer),
**not** `POST /commands/crawl` (in-process, dies with the API).

Policy (doc 09 §6):

| Mode | Cadence | Notes |
|---|---|---|
| Inventory baseline | every 4–6 hours | Meets 12h freshness SLO; two-miss archive ≤ 12h at 6h cadence |
| Sale window (later) | every 15–30 minutes | Only while active lots are closing — Handoff Task F |
| Concurrent runs | never | OS lock in `crawler.py`; wrapper exits cleanly if busy |
| Whole-run retry | once after ~20 minutes | Then alert and stop |

## Wrapper

```bash
scripts/scheduled_crawl.sh
```

- Runs `python -m iaai_scraper.cli crawl` (override with `IAAI_CRAWL_CMD`).
- Lock busy → exit 0, no retry.
- Other failure → sleep `IAAI_SCHED_RETRY_DELAY_S` (default 1200) → one retry.
- Second failure → loud `ALERT:` log line, optional `IAAI_SCHED_ALERT_HOOK`, exit 1.

## Example crontab (6-hour baseline + jitter)

```cron
# Ontario inventory crawl — minute 17 of hours 0,6,12,18 UTC
17 0,6,12,18 * * * cd /srv/iaai && .venv/bin/python -c 'pass' && IAAI_PYTHON=/srv/iaai/.venv/bin/python /srv/iaai/scripts/scheduled_crawl.sh >>/var/log/iaai-crawl.log 2>&1
```

Prefer the project venv’s Python via `IAAI_PYTHON=…/.venv/bin/python`.

Optional alert hook (webhook / mailer):

```bash
export IAAI_SCHED_ALERT_HOOK='curl -fsS -X POST -d @- https://hooks.example/iaai-crawl'
```

## systemd timer (alternative)

`iaai-crawl.service` → `ExecStart=/srv/iaai/scripts/scheduled_crawl.sh`  
`iaai-crawl.timer` → `OnCalendar=*-*-* 00/6:17:00` (adjust to taste).

## Do not

- Point the schedule at `POST /commands/crawl`.
- Retry more than once automatically (anti-bot / Imperva risk).
- Run against production DB from ad-hoc live experiments.
