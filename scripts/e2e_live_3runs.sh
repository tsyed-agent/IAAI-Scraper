#!/usr/bin/env bash
# Run 3 complete live crawl cycles with verification.
set -euo pipefail
cd /home/ubuntu/IAAI-Scraper
. .venv/bin/activate
pip install httpx -q 2>/dev/null || true

# Never touch production ``data/``. The environment is set before each Python
# process starts so config.py resolves all storage paths into this temp tree.
E2E_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/iaai-e2e.XXXXXX")"
trap 'rm -rf "$E2E_ROOT"' EXIT
export IAAI_DATA_DIR="$E2E_ROOT/data"
mkdir -p "$IAAI_DATA_DIR/raw"
LOG="$E2E_ROOT/e2e_live_results.jsonl"
KPI="$E2E_ROOT/e2e_kpis.json"
: > "$LOG"

for RUN in 1 2 3; do
  echo "========== RUN $RUN: crawl start $(date -u +%Y-%m-%dT%H:%M:%SZ) =========="
  if ! python -m iaai_scraper.cli crawl -v 2>&1 | tee "$E2E_ROOT/e2e_run${RUN}_crawl.log"; then
    echo "CRAWL FAILED run=$RUN" | tee -a "$LOG"
  fi
  echo "========== RUN $RUN: verify =========="
  python scripts/e2e_verify.py "$RUN" | tee "$E2E_ROOT/e2e_run${RUN}_kpi.json"
  echo "---" >> "$LOG"
  cat "$E2E_ROOT/e2e_run${RUN}_kpi.json" >> "$LOG"
done

python3 - "$E2E_ROOT" << 'PY'
import json
import sys
from pathlib import Path
kpis = []
root = Path(sys.argv[1])
for i in (1, 2, 3):
    p = root / f"e2e_run{i}_kpi.json"
    if p.exists():
        kpis.append(json.loads(p.read_text()))
(root / "e2e_kpis.json").write_text(json.dumps(kpis, indent=2))
print("Wrote", root / "e2e_kpis.json", "with", len(kpis), "runs")
PY
