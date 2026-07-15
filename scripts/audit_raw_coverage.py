#!/usr/bin/env python3
"""Audit captured JSONL snapshots for completeness and schema drift.

This command is intentionally offline: it verifies what was captured without
issuing any request to IAA. It exits non-zero when critical invariants fail so it
can gate deployments and scheduled crawls.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import inspect
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from iaai_scraper.config import CrawlSettings  # noqa: E402
from iaai_scraper.lifecycle import best_final_price, derive_lot_status  # noqa: E402
from iaai_scraper.parser import parse_row  # noqa: E402

CRITICAL_FIELDS = ("stock_number", "year", "make", "model", "branch_id")


def _present(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def iter_rows(paths: Iterable[Path]) -> Iterable[dict[str, Any]]:
    for path in paths:
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
                if not isinstance(row, dict):
                    raise ValueError(f"{path}:{line_number}: expected a JSON object")
                yield row


def consumed_raw_fields() -> set[str]:
    """Return literal row.get("Field") keys consumed by normalization logic."""
    fields: set[str] = set()
    for function in (parse_row, best_final_price, derive_lot_status):
        tree = ast.parse(inspect.getsource(function))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "get" or not node.args:
                continue
            key = node.args[0]
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                fields.add(key.value)
    return fields


def audit(paths: Iterable[Path], *, min_ontario_lots: int = 1,
          max_critical_null_rate: float = 0.01) -> dict[str, Any]:
    paths = list(paths)
    settings = CrawlSettings()
    total = 0
    parse_failures = 0
    ontario = 0
    stocks: Counter[str] = Counter()
    branches: Counter[str] = Counter()
    raw_present: Counter[str] = Counter()
    normalized_present: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    unknown_item_statuses: Counter[str] = Counter()

    for row in iter_rows(paths):
        total += 1
        for key, value in row.items():
            if _present(value):
                raw_present[key] += 1
        lot = parse_row(row)
        if lot is None:
            parse_failures += 1
            continue
        stocks[lot.stock_number] += 1
        if not settings.is_ontario(lot.branch_id, lot.branch_name):
            continue
        ontario += 1
        branches[lot.branch_name or "?"] += 1
        statuses[lot.status] += 1
        item_status = (lot.item_status_desc or "").strip()
        if item_status and lot.status == "active":
            unknown_item_statuses[item_status] += 1
        for key, value in lot.model_dump(exclude={"raw"}).items():
            if _present(value):
                normalized_present[key] += 1

    duplicate_rows = sum(count - 1 for count in stocks.values() if count > 1)
    anomalies: list[str] = []
    if total == 0:
        anomalies.append("snapshot contains no rows")
    if parse_failures:
        anomalies.append(f"{parse_failures} rows could not be parsed")
    if duplicate_rows:
        anomalies.append(f"{duplicate_rows} duplicate stock rows found")
    if ontario < min_ontario_lots:
        anomalies.append(f"Ontario lot count {ontario} is below required {min_ontario_lots}")
    if unknown_item_statuses:
        anomalies.append("unknown ItemStatusDesc values were mapped to active")

    critical_null_rates: dict[str, float] = {}
    for field in CRITICAL_FIELDS:
        rate = 1.0 if ontario == 0 else 1 - (normalized_present[field] / ontario)
        critical_null_rates[field] = round(rate, 6)
        if rate > max_critical_null_rate:
            anomalies.append(
                f"critical field {field} null rate {rate:.2%} exceeds "
                f"{max_critical_null_rate:.2%}"
            )

    consumed = consumed_raw_fields()
    unconsumed = sorted(set(raw_present) - consumed)
    return {
        "files": [str(p) for p in paths],
        "total_rows": total,
        "unique_stock_numbers": len(stocks),
        "duplicate_rows": duplicate_rows,
        "parse_failures": parse_failures,
        "ontario_lots": ontario,
        "ontario_by_branch": dict(sorted(branches.items())),
        "status_counts": dict(sorted(statuses.items())),
        "unknown_item_statuses": dict(sorted(unknown_item_statuses.items())),
        "critical_null_rates": critical_null_rates,
        "raw_field_presence": dict(sorted(raw_present.items())),
        "consumed_raw_fields": sorted(consumed),
        "unconsumed_raw_fields": unconsumed,
        "normalized_field_presence": dict(sorted(normalized_present.items())),
        "anomalies": anomalies,
        "ok": not anomalies,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="JSONL or JSONL.GZ snapshots")
    parser.add_argument("--min-ontario-lots", type=int, default=1)
    parser.add_argument("--max-critical-null-rate", type=float, default=0.01)
    args = parser.parse_args()
    result = audit(
        args.paths,
        min_ontario_lots=args.min_ontario_lots,
        max_critical_null_rate=args.max_critical_null_rate,
    )
    print(json.dumps(result, indent=2, default=str))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
