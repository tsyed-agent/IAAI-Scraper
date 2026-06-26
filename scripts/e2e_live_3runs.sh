#!/usr/bin/env bash
# Run 3 complete live crawl cycles with verification.
set -euo pipefail
cd /home/ubuntu/IAAI-Scraper
. .venv/bin/activate
pip install httpx -q 2>/dev/null || true

LOG="/home/ubuntu/IAAI-Scraper/data/e2e_live_results.jsonl"
KPI="/home/ubuntu/IAAI-Scraper/data/e2e_kpis.json"
: > "$LOG"

clean_storage() {
  rm -f data/iaai_ontario.db data/crawl.lock
  rm -rf data/raw/*
  mkdir -p data/raw
}

for RUN in 1 2 3; do
  echo "========== RUN $RUN: clean storage =========="
  clean_storage
  echo "========== RUN $RUN: crawl start $(date -u +%Y-%m-%dT%H:%M:%SZ) =========="
  if ! python -m iaai_scraper.cli crawl -v 2>&1 | tee "data/e2e_run${RUN}_crawl.log"; then
    echo "CRAWL FAILED run=$RUN" | tee -a "$LOG"
  fi
  echo "========== RUN $RUN: verify =========="
  python scripts/e2e_verify.py "$RUN" | tee "data/e2e_run${RUN}_kpi.json"
  echo "---" >> "$LOG"
  cat "data/e2e_run${RUN}_kpi.json" >> "$LOG"
done

python3 << 'PY'
import json
from pathlib import Path
kpis = []
for i in (1, 2, 3):
    p = Path(f"data/e2e_run{i}_kpi.json")
    if p.exists():
        kpis.append(json.loads(p.read_text()))
Path("data/e2e_kpis.json").write_text(json.dumps(kpis, indent=2))
print("Wrote data/e2e_kpis.json with", len(kpis), "runs")
PY
