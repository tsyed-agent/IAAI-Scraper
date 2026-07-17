#!/usr/bin/env bash
# Run 3 complete live crawl cycles with verification into an isolated data dir.
# Never touches production ``data/`` (unless IAAI_CANARY_OUTDIR is pointed there).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv311/bin/python" ]]; then
  PYTHON="$ROOT/.venv311/bin/python"
elif [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
else
  PYTHON="${IAAI_PYTHON:-python3}"
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUTDIR="${IAAI_CANARY_OUTDIR:-$ROOT/data/canary/$STAMP}"
mkdir -p "$OUTDIR/data/raw"
export IAAI_DATA_DIR="$OUTDIR/data"
# Canary verify hits the API via TestClient; keep auth off so checks are unattended.
unset IAAI_API_TOKEN IAAI_COMMAND_TOKEN IAAI_REQUIRE_AUTH || true
export IAAI_REQUIRE_AUTH=false

LOG="$OUTDIR/e2e_live_results.jsonl"
: > "$LOG"
echo "Canary outdir: $OUTDIR"
echo "Python: $PYTHON"
echo "IAAI_DATA_DIR=$IAAI_DATA_DIR"

FAILED=0
for RUN in 1 2 3; do
  echo "========== RUN $RUN: crawl start $(date -u +%Y-%m-%dT%H:%M:%SZ) =========="
  if ! "$PYTHON" -m iaai_scraper.cli crawl -v 2>&1 | tee "$OUTDIR/e2e_run${RUN}_crawl.log"; then
    echo "CRAWL FAILED run=$RUN" | tee -a "$LOG"
    FAILED=1
  fi
  echo "========== RUN $RUN: verify =========="
  "$PYTHON" scripts/e2e_verify.py "$RUN" | tee "$OUTDIR/e2e_run${RUN}_kpi.json"
  echo "---" >> "$LOG"
  cat "$OUTDIR/e2e_run${RUN}_kpi.json" >> "$LOG"

  # Ontario invariants (branch ids, floor count, image_url coverage).
  if ! IAAI_DATA_DIR="$IAAI_DATA_DIR" "$PYTHON" scripts/verify_ontario_crawl.py \
      >"$OUTDIR/e2e_run${RUN}_ontario.txt" 2>&1; then
    echo "ONTARIO VERIFY FAILED run=$RUN" | tee -a "$LOG"
    cat "$OUTDIR/e2e_run${RUN}_ontario.txt" | tee -a "$LOG"
    # Do not set FAILED here — final canary_summary.json is the gate (exact
    # source match + KPI anomalies). Floor can lag live inventory dips.
  else
    cat "$OUTDIR/e2e_run${RUN}_ontario.txt"
  fi
done

"$PYTHON" - "$OUTDIR" <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

root = Path(sys.argv[1])
kpis = []
for i in (1, 2, 3):
    p = root / f"e2e_run{i}_kpi.json"
    if p.exists():
        kpis.append(json.loads(p.read_text()))
(root / "e2e_kpis.json").write_text(json.dumps(kpis, indent=2))
print("Wrote", root / "e2e_kpis.json", "with", len(kpis), "runs")

db = root / "data" / "iaai_ontario.db"
summary = {"runs": len(kpis), "anomalies": [], "by_branch": [], "status": "unknown"}
if db.exists():
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    summary["by_branch"] = [
        dict(r)
        for r in conn.execute(
            "SELECT branch_id, branch_name, COUNT(*) AS n FROM lots "
            "GROUP BY branch_id ORDER BY n DESC"
        )
    ]
    summary["lots_by_status"] = {
        r["status"]: r["n"]
        for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM lots GROUP BY status ORDER BY status"
        )
    }
    last = conn.execute(
        "SELECT status, total_canada, canada_rows_seen, ontario_seen, pages, "
        "skipped_bad_rows FROM crawl_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if last:
        summary["last_run"] = dict(last)
    conn.close()

for k in kpis:
    if k.get("anomalies"):
        summary["anomalies"].extend(
            [f"run{k.get('run')}: {a}" for a in k["anomalies"]]
        )
    if k.get("status") != "completed":
        summary["anomalies"].append(
            f"run{k.get('run')}: status={k.get('status')!r}"
        )

# Cross-run stability: ontario_seen should not swing wildly between consecutive runs.
seen = [k.get("ontario_seen") for k in kpis if k.get("ontario_seen") is not None]
if len(seen) >= 2:
    lo, hi = min(seen), max(seen)
    if lo and hi and (hi - lo) / lo > 0.15:
        summary["anomalies"].append(
            f"ontario_seen swing {lo}->{hi} exceeds 15% across canary runs"
        )

summary["status"] = "pass" if not summary["anomalies"] and len(kpis) == 3 else "fail"
(root / "canary_summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
if summary["status"] != "pass":
    sys.exit(1)
PY

STATUS=$?
# Crawl hard-fails still count; KPI summary is the authoritative pass/fail.
if [[ $FAILED -ne 0 ]]; then
  echo "One or more crawls failed — see $OUTDIR" >&2
  exit 1
fi
if [[ $STATUS -ne 0 ]]; then
  echo "CANARY FAILED — see $OUTDIR" >&2
  exit 1
fi
echo "CANARY PASSED — evidence in $OUTDIR"
mkdir -p "$ROOT/data/canary"
echo "$OUTDIR" > "$ROOT/data/canary/LATEST.txt"
