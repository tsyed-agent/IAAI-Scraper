#!/usr/bin/env sh
# Durable crawl schedule entrypoint (Phase 2 · 2.4b).
#
# Enqueues a crawl job then runs the durable worker once. Job state lives in
# SQLite (data/jobs.db) so a crash mid-run is recovered on the next invocation.
# Prefer this over scripts/scheduled_crawl.sh for production.
#
# Env:
#   IAAI_PYTHON              python binary (default: python3)
#   IAAI_SCHED_JITTER_S      max startup delay seconds (default: 300)
#   IAAI_SCHED_RETRY_DELAY_S seconds before processing a re-queued retry (default: 1200)
#   IAAI_SCHED_ALERT_HOOK    optional alert command (also used by the worker)
#   IAAI_JOBS_DB             optional jobs SQLite path
#
# See docs/ops-scheduling.md.
set -eu

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

JITTER_MAX_S="${IAAI_SCHED_JITTER_S:-300}"
RETRY_DELAY_S="${IAAI_SCHED_RETRY_DELAY_S:-1200}"
PYTHON="${IAAI_PYTHON:-python3}"
TAG="iaai-scheduled-worker"

log() {
  printf '%s %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" "$*"
}

configuration_error() {
  log "ALERT: $1"
  exit 2
}

case "$JITTER_MAX_S" in
  ''|*[!0-9]*) configuration_error "invalid IAAI_SCHED_JITTER_S='$JITTER_MAX_S'" ;;
esac
case "$RETRY_DELAY_S" in
  ''|*[!0-9]*) configuration_error "invalid IAAI_SCHED_RETRY_DELAY_S='$RETRY_DELAY_S'" ;;
esac

random_jitter() {
  MAX="$1"
  if [ "$MAX" -eq 0 ]; then
    printf '0\n'
    return 0
  fi
  VALUE="$(od -An -N4 -tu4 /dev/urandom 2>/dev/null |
    awk -v max="$MAX" 'NF { print $1 % (max + 1); exit }')"
  if [ -z "$VALUE" ]; then
    return 1
  fi
  printf '%s\n' "$VALUE"
}

JITTER_DELAY_S="$(random_jitter "$JITTER_MAX_S")" || {
  configuration_error "unable to generate startup jitter from /dev/urandom"
}
if [ "$JITTER_DELAY_S" -gt 0 ]; then
  log "waiting ${JITTER_DELAY_S}s startup jitter (max ${JITTER_MAX_S}s)"
  sleep "$JITTER_DELAY_S"
fi

log "enqueue crawl job"
"$PYTHON" -m iaai_scraper.cli enqueue-crawl
log "worker --once"
set +e
"$PYTHON" -m iaai_scraper.cli worker --once
RC=$?
set -e

# Exit 2 from worker means the job was re-queued after a failed attempt.
if [ "$RC" -eq 2 ]; then
  log "job re-queued after failure; sleeping ${RETRY_DELAY_S}s then retrying worker"
  sleep "$RETRY_DELAY_S"
  set +e
  "$PYTHON" -m iaai_scraper.cli worker --once
  RC=$?
  set -e
fi

if [ "$RC" -ne 0 ]; then
  log "ALERT: durable worker exited rc=$RC"
  exit "$RC"
fi
log "done"
exit 0
