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
| Startup jitter | uniform 0–5 minutes by default | Set `IAAI_SCHED_JITTER_S=0` to disable |

## Wrapper

```bash
scripts/scheduled_crawl.sh
```

- Runs `python -m iaai_scraper.cli crawl` (override with `IAAI_CRAWL_CMD`).
- Lock busy → exit 0, no retry.
- Before the first crawl, sleeps a random whole number of seconds from
  `0..IAAI_SCHED_JITTER_S` (default 300). This is real start-time jitter, not
  a fixed cron minute; the wrapper uses `/dev/urandom` and POSIX `awk`.
- `IAAI_SCHED_RETRY_DELAY_S` must be a nonnegative integer. Other failure →
  sleep that many seconds (default 1200) → one retry.
- `IAAI_SCHED_JITTER_S` must also be a nonnegative integer. Invalid scheduler
  settings are rejected before a crawl and emit an `ALERT:` line.
- Second failure → loud `ALERT:` log line, optional `IAAI_SCHED_ALERT_HOOK`, exit 1.

## Example crontab (6-hour baseline + wrapper jitter)

```cron
# Ontario inventory crawl — exact hour is randomized by the wrapper (0–5 min)
0 0,6,12,18 * * * cd /srv/iaai && IAAI_PYTHON=/srv/iaai/.venv/bin/python IAAI_SCHED_JITTER_S=300 /srv/iaai/scripts/scheduled_crawl.sh >>/var/log/iaai-crawl.log 2>&1
```

Prefer the project venv’s Python via `IAAI_PYTHON=…/.venv/bin/python`.
For deterministic maintenance or tests, set `IAAI_SCHED_JITTER_S=0`.

Optional alert hook (webhook / mailer):

```bash
export IAAI_SCHED_ALERT_HOOK='curl -fsS -X POST -d @- https://hooks.example/iaai-crawl'
```

## systemd timer (alternative)

`iaai-crawl.service` → `ExecStart=/srv/iaai/scripts/scheduled_crawl.sh`  
`iaai-crawl.timer`:

```ini
[Timer]
OnCalendar=*-*-* 00/6:00:00
RandomizedDelaySec=5min
Persistent=true
```

Use either systemd's `RandomizedDelaySec` or the wrapper's
`IAAI_SCHED_JITTER_S`, not both. If systemd owns the jitter, set
`IAAI_SCHED_JITTER_S=0` in the service environment; the wrapper setting is
useful when the same entrypoint is run from cron and systemd.

## Do not

- Point the schedule at `POST /commands/crawl`.
- Retry more than once automatically (anti-bot / Imperva risk).
- Run against production DB from ad-hoc live experiments.
