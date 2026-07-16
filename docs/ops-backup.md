# Ops: off-host backups + restore drill

Production durability requires **versioned off-host copies** of the SQLite
snapshot and newest raw JSONL, plus a **restore drill** that proves the copy
opens cleanly. Local `cli backup` alone is not enough (doc 09 §2.4 / §7).

## Commands

```bash
# Local atomic snapshot only
python -m iaai_scraper.cli backup

# Upload latest DB (+ newest raw/*.jsonl.gz) + SHA-256 manifest
python -m iaai_scraper.cli offsite-backup

# Download latest snapshot → PRAGMA integrity_check + lot-count floor
python -m iaai_scraper.cli restore-drill --min-lots 1

# Cron wrapper (upload then restore drill)
scripts/offsite_backup.sh
```

## Backends

| Backend | When | Env |
|---|---|---|
| `filesystem` (default) | CI, lab, “off-host” directory on another volume | `IAAI_BACKUP_DIR` (default `data/offsite`) |
| `s3` | MinIO / R2 / B2 / AWS | `IAAI_BACKUP_S3_BUCKET` (+ optional endpoint/region/prefix). Requires `pip install boto3`. |

Object layout:

```text
latest.json
snapshots/<UTC-stamp>/iaai-ontario.db
snapshots/<UTC-stamp>/<raw-name>.jsonl.gz   # when present
snapshots/<UTC-stamp>/manifest.json         # SHA-256 per artifact
```

## Retention (doc 09 §7)

| Local | Policy |
|---|---|
| `data/backups/*.db` | Keep **5** newest after each offsite run (`IAAI_BACKUP_KEEP_LOCAL_DB`) |
| `data/raw/**/*.jsonl.gz` | Keep **90 days** hot (`IAAI_BACKUP_RAW_HOT_DAYS`); older deleted locally after upload |
| Off-host objects | Keep versioned; do not prune from this tool yet |

## Example crontab (daily)

```cron
15 3 * * * cd /srv/iaai && \
  IAAI_PYTHON=/srv/iaai/.venv/bin/python \
  IAAI_BACKUP_BACKEND=s3 \
  IAAI_BACKUP_S3_BUCKET=iaai-ontario-backups \
  IAAI_BACKUP_S3_ENDPOINT=https://… \
  IAAI_BACKUP_MIN_LOTS=500 \
  /srv/iaai/scripts/offsite_backup.sh >>/var/log/iaai-backup.log 2>&1
```

Set `IAAI_BACKUP_SKIP_DRILL=1` only for emergency uploads; restore proof is
required for the backup gate to count as done.

Optional failure hook:

```bash
export IAAI_BACKUP_ALERT_HOOK='curl -fsS -X POST -d @- https://hooks.example/iaai-backup'
```

## Acceptance

A restore drill must exit 0 against the uploaded snapshot (integrity `ok` and
`lot_count >= IAAI_BACKUP_MIN_LOTS`). Offline CI covers the filesystem backend.
