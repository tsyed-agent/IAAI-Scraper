#!/usr/bin/env sh
# Cron/systemd entrypoint: local SQLite snapshot → off-host upload + optional restore drill.
#
# Env:
#   IAAI_PYTHON                 python binary (default: python3)
#   IAAI_BACKUP_BACKEND         filesystem|s3 (default: filesystem)
#   IAAI_BACKUP_DIR             filesystem object-store root
#   IAAI_BACKUP_S3_BUCKET       required when backend=s3
#   IAAI_BACKUP_S3_ENDPOINT     optional (MinIO / R2 / B2)
#   IAAI_BACKUP_S3_REGION       optional
#   IAAI_BACKUP_S3_PREFIX       key prefix (default: iaai-ontario)
#   IAAI_BACKUP_KEEP_LOCAL_DB   local *.db backups to keep (default: 5)
#   IAAI_BACKUP_RAW_HOT_DAYS    local raw hot window days (default: 90)
#   IAAI_BACKUP_MIN_LOTS        restore-drill lot floor (default: 1)
#   IAAI_BACKUP_SKIP_DRILL=1    skip restore drill after upload
#   IAAI_BACKUP_ALERT_HOOK      optional shell command on failure
#
# See docs/ops-backup.md.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${IAAI_PYTHON:-python3}"
KEEP_LOCAL_DB="${IAAI_BACKUP_KEEP_LOCAL_DB:-5}"
RAW_HOT_DAYS="${IAAI_BACKUP_RAW_HOT_DAYS:-90}"
MIN_LOTS="${IAAI_BACKUP_MIN_LOTS:-1}"
SKIP_DRILL="${IAAI_BACKUP_SKIP_DRILL:-0}"
ALERT_HOOK="${IAAI_BACKUP_ALERT_HOOK:-}"
TAG="iaai-offsite-backup"

log() {
  printf '%s %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" "$*"
}

fail() {
  MSG="$1"
  log "ALERT: $MSG"
  if [ -n "$ALERT_HOOK" ]; then
    printf '%s\n' "$MSG" | sh -c "$ALERT_HOOK" || log "alert hook exited non-zero"
  fi
  exit 1
}

case "$KEEP_LOCAL_DB" in
  ''|*[!0-9]*) fail "invalid IAAI_BACKUP_KEEP_LOCAL_DB='$KEEP_LOCAL_DB'" ;;
esac
case "$RAW_HOT_DAYS" in
  ''|*[!0-9]*) fail "invalid IAAI_BACKUP_RAW_HOT_DAYS='$RAW_HOT_DAYS'" ;;
esac
case "$MIN_LOTS" in
  ''|*[!0-9]*) fail "invalid IAAI_BACKUP_MIN_LOTS='$MIN_LOTS'" ;;
esac

log "starting offsite backup"
if ! "$PYTHON" -m iaai_scraper.cli offsite-backup \
  --keep-local-db "$KEEP_LOCAL_DB" \
  --raw-hot-days "$RAW_HOT_DAYS"
then
  fail "offsite-backup failed"
fi

if [ "$SKIP_DRILL" = "1" ] || [ "$SKIP_DRILL" = "true" ]; then
  log "restore drill skipped (IAAI_BACKUP_SKIP_DRILL=$SKIP_DRILL)"
  exit 0
fi

log "running restore drill (min_lots=$MIN_LOTS)"
if ! "$PYTHON" -m iaai_scraper.cli restore-drill --min-lots "$MIN_LOTS"
then
  fail "restore-drill failed"
fi

log "offsite backup + restore drill ok"
exit 0
