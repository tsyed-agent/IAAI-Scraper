#!/usr/bin/env sh
# Cron/systemd entrypoint for a whole Ontario crawl with one delayed retry.
#
# - Invokes the CLI crawl (not POST /commands/crawl).
# - On lock contention ("another crawl running"), exits 0 without retry.
# - On other failures: wait, retry once; on second failure, loud log + optional hook.
#
# Env:
#   IAAI_PYTHON              python binary (default: python3)
#   IAAI_CRAWL_CMD           full command string (default: "$IAAI_PYTHON -m iaai_scraper.cli crawl")
#   IAAI_SCHED_RETRY_DELAY_S seconds before retry (default: 1200 = 20 minutes)
#   IAAI_SCHED_ALERT_HOOK    optional shell command run after two failures
#
# See docs/ops-scheduling.md.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RETRY_DELAY_S="${IAAI_SCHED_RETRY_DELAY_S:-1200}"
PYTHON="${IAAI_PYTHON:-python3}"
CRAWL_CMD="${IAAI_CRAWL_CMD:-$PYTHON -m iaai_scraper.cli crawl}"
ALERT_HOOK="${IAAI_SCHED_ALERT_HOOK:-}"
TAG="iaai-scheduled-crawl"

log() {
  printf '%s %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" "$*"
}

is_lock_busy() {
  # Match the RuntimeError text from iaai_scraper.crawler._acquire_lock
  grep -qi 'Another crawl appears to be running' "$1"
}

run_crawl() {
  # Allow IAAI_CRAWL_CMD to be a multi-word command string (tests / wrappers).
  # shellcheck disable=SC2086
  sh -c "$CRAWL_CMD"
}

OUT="$(mktemp "${TMPDIR:-/tmp}/iaai-sched.XXXXXX")"
trap 'rm -f "$OUT"' EXIT

log "starting crawl: $CRAWL_CMD"
set +e
run_crawl >"$OUT" 2>&1
RC=$?
set -e
cat "$OUT"

if [ "$RC" -eq 0 ]; then
  log "crawl completed"
  exit 0
fi

if is_lock_busy "$OUT"; then
  log "another crawl running — skip (no retry)"
  exit 0
fi

log "crawl failed (rc=$RC); retrying once after ${RETRY_DELAY_S}s"
sleep "$RETRY_DELAY_S"

log "retry starting"
set +e
run_crawl >"$OUT" 2>&1
RC=$?
set -e
cat "$OUT"

if [ "$RC" -eq 0 ]; then
  log "crawl completed on retry"
  exit 0
fi

if is_lock_busy "$OUT"; then
  log "another crawl running on retry — skip"
  exit 0
fi

MSG="scheduled crawl failed twice (last_rc=$RC)"
log "ALERT: $MSG"
if [ -n "$ALERT_HOOK" ]; then
  # Hook receives the alert text on stdin; failures here must not mask exit 1.
  printf '%s\n' "$MSG" | sh -c "$ALERT_HOOK" || log "alert hook exited non-zero"
fi
exit 1
