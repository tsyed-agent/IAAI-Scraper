#!/usr/bin/env python3
"""Verify Ontario-at-source crawl results against expected invariants.

Usage:
  python scripts/verify_ontario_crawl.py          # check existing DB
  python scripts/verify_ontario_crawl.py --crawl  # run crawl then verify

Exit 0 when all checks pass; non-zero on failure.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from iaai_scraper import config
from iaai_scraper.crawler import Crawler
from iaai_scraper.storage import SqliteStore

ONTARIO_BRANCH_IDS = set(config.ONTARIO_BRANCH_IDS)
MIN_ONTARIO_LOTS = 1300  # live inventory fluctuates; floor for pass


def verify_db() -> list[str]:
    errors: list[str] = []
    store = SqliteStore()
    try:
        total = store.conn.execute("SELECT COUNT(*) AS n FROM lots").fetchone()["n"]
        if total < MIN_ONTARIO_LOTS:
            errors.append(f"total_lots={total} below floor {MIN_ONTARIO_LOTS}")

        non_on = store.conn.execute(
            "SELECT COUNT(*) AS n FROM lots WHERE branch_id NOT IN ({})".format(
                ",".join("?" * len(ONTARIO_BRANCH_IDS))
            ),
            list(ONTARIO_BRANCH_IDS),
        ).fetchone()["n"]
        if non_on:
            errors.append(f"{non_on} lots outside Ontario branch ids")

        last = store.conn.execute(
            "SELECT status, ontario_seen, pages, total_canada FROM crawl_runs "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not last:
            errors.append("no crawl_runs recorded")
        elif last["status"] != "completed":
            errors.append(f"last crawl status={last['status']!r} (expected completed)")
        elif last["pages"] and last["pages"] > 5:
            errors.append(
                f"last crawl used {last['pages']} pages (expected <=5 for Ontario-at-source)"
            )

        mapped = store.conn.execute(
            "SELECT COUNT(*) AS n FROM lots WHERE image_url IS NOT NULL"
        ).fetchone()["n"]
        if total and mapped / total < 0.95:
            errors.append(
                f"image_url populated on only {mapped}/{total} lots "
                f"(extended parser regression?)"
            )
    finally:
        store.close()
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Ontario crawl invariants")
    parser.add_argument(
        "--crawl", action="store_true", help="Run a full Ontario crawl before verifying"
    )
    args = parser.parse_args()

    if args.crawl:
        report = asyncio.run(Crawler().run())
        print(report.summary())
        if report.status not in ("completed", "completed_unknown_total"):
            print("Crawl did not complete successfully.", file=sys.stderr)
            return 1

    errors = verify_db()
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    store = SqliteStore()
    try:
        total = store.conn.execute("SELECT COUNT(*) AS n FROM lots").fetchone()["n"]
        last = store.conn.execute(
            "SELECT status, pages, ontario_seen FROM crawl_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        print(f"OK: {total} Ontario lots, last crawl status={last['status']}, pages={last['pages']}")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
