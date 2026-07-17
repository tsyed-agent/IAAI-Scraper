#!/usr/bin/env python3
"""Post-crawl verification + KPI collection for E2E live testing."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iaai_scraper.api import app  # noqa: E402
from iaai_scraper import config  # noqa: E402


def verify_run(run_num: int) -> dict:
    db_path = config.DB_PATH
    anomalies: list[str] = []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    run = conn.execute(
        "SELECT * FROM crawl_runs ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not run:
        anomalies.append("No crawl_runs row found")
        return {"run": run_num, "anomalies": anomalies}

    run = dict(run)
    status = run["status"]
    total_canada = run["total_canada"]
    canada_rows_seen = run["canada_rows_seen"]
    ontario_seen = run["ontario_seen"]

    if status != "completed":
        anomalies.append(f"crawl_runs.status={status!r} (expected 'completed')")
    if total_canada and canada_rows_seen < total_canada * 0.98:
        anomalies.append(
            f"canada_rows_seen ({canada_rows_seen}) < 98% of total_canada ({total_canada})"
        )
    if total_canada and canada_rows_seen > total_canada:
        anomalies.append(
            f"canada_rows_seen ({canada_rows_seen}) > total_canada ({total_canada})"
        )

    status_counts = {
        row["status"]: row["cnt"]
        for row in conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM lots GROUP BY status ORDER BY status"
        )
    }
    branch_counts = [
        dict(row)
        for row in conn.execute(
            "SELECT branch_id, branch_name, COUNT(*) AS cnt FROM lots "
            "GROUP BY branch_id ORDER BY cnt DESC"
        )
    ]
    ontario_db = conn.execute("SELECT COUNT(*) AS n FROM lots").fetchone()["n"]
    if ontario_db != ontario_seen:
        anomalies.append(
            f"DB lot count ({ontario_db}) != ontario_seen ({ontario_seen})"
        )

    active_with_price = conn.execute(
        "SELECT COUNT(*) AS n FROM lots WHERE status='active' AND final_price IS NOT NULL"
    ).fetchone()["n"]
    if active_with_price:
        anomalies.append(
            f"{active_with_price} active lots have final_price set"
        )

    spot = conn.execute(
        "SELECT stock_number, status, final_price, make, model, year "
        "FROM lots ORDER BY RANDOM() LIMIT 5"
    ).fetchall()
    spot_checks = [dict(r) for r in spot]
    for s in spot_checks:
        if not s["status"]:
            anomalies.append(f"Lot {s['stock_number']} missing status")

    conn.close()

    client = TestClient(app)
    api_results = {}
    for path in [
        "/healthz",
        "/readyz",
        "/lots?limit=5",
        "/lots?status=active&limit=5",
        "/lots?status=sold&limit=5",
        "/stats",
    ]:
        r = client.get(path)
        api_results[path] = {"status_code": r.status_code, "body": r.json()}

    if api_results["/healthz"]["status_code"] != 200:
        anomalies.append(f"/healthz returned {api_results['/healthz']['status_code']}")
    readyz = api_results["/readyz"]
    if readyz["status_code"] != 200:
        anomalies.append(f"/readyz returned {readyz['status_code']}: {readyz['body']}")
    elif readyz["body"].get("status") != "ready":
        anomalies.append(f"/readyz status={readyz['body'].get('status')!r}")

    started = run.get("started_at", "")
    finished = run.get("finished_at", "")
    duration_s = None
    if started and finished:
        from datetime import datetime

        fmt = "%Y-%m-%dT%H:%M:%S%z"
        try:
            t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(finished.replace("Z", "+00:00"))
            duration_s = round((t1 - t0).total_seconds(), 1)
        except ValueError:
            pass

    kpi = {
        "run": run_num,
        "started_at": started,
        "finished_at": finished,
        "duration_s": duration_s,
        "status": status,
        "total_canada": total_canada,
        "canada_rows_seen": canada_rows_seen,
        "ontario_seen": ontario_seen,
        "inserted": run.get("inserted"),
        "updated": run.get("updated"),
        "unchanged": run.get("unchanged"),
        "skipped_bad_rows": run.get("skipped_bad_rows"),
        "archived": run.get("archived"),
        "pages": run.get("pages"),
        "lots_by_status": status_counts,
        "lots_by_branch": branch_counts,
        "readyz": readyz["body"],
        "spot_checks": spot_checks,
        "anomalies": anomalies,
        "note": run.get("note"),
    }
    return kpi


if __name__ == "__main__":
    run_num = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    kpi = verify_run(run_num)
    print(json.dumps(kpi, indent=2, default=str))
