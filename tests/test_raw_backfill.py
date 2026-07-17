"""Offline tests for raw JSONL → SQLite baseline backfill."""
from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from iaai_scraper import cli
from iaai_scraper.raw_backfill import discover_raw_files, run_raw_backfill
from iaai_scraper.storage import SqliteStore
from tests.conftest import SAMPLE_ROW, make_row


def _write_jsonl(path: Path, rows: list[dict], *, gzipped: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if gzipped else open
    with opener(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    return path


def test_discover_skips_dlq_and_orders_by_mtime(tmp_path: Path):
    raw = tmp_path / "raw"
    older = _write_jsonl(raw / "2026-01-01" / "run-a.jsonl.gz", [SAMPLE_ROW])
    newer = _write_jsonl(raw / "2026-01-02" / "run-b.jsonl.gz", [SAMPLE_ROW])
    _write_jsonl(raw / "2026-01-02" / "run-b.dlq.jsonl.gz", [{"row": {}}])
    plain = _write_jsonl(raw / "2026-01-03" / "run-c.jsonl", [SAMPLE_ROW], gzipped=False)
    now = time.time()
    os.utime(older, (now - 100, now - 100))
    os.utime(newer, (now - 50, now - 50))
    os.utime(plain, (now, now))
    found = discover_raw_files(raw)
    assert found == [older, newer, plain]
    assert all(".dlq." not in p.name for p in found)


def test_backfill_inserts_ontario_and_records_run(tmp_path: Path):
    raw = tmp_path / "raw"
    db = tmp_path / "iaai.db"
    rows = [
        make_row("1001"),
        make_row("1002", HighPrebidValue=1500),
        make_row("2001", branch_id=999, branch_desc="Montreal"),
    ]
    _write_jsonl(raw / "2026-07-16" / "run-test.jsonl.gz", rows)

    report = run_raw_backfill(raw_dir=raw, db_path=db)
    assert report.status == "completed"
    assert report.files == 1
    assert report.rows_seen == 3
    assert report.ontario_seen == 2
    assert report.inserted == 2
    assert report.skipped_non_ontario == 1
    assert report.run_id is not None

    store = SqliteStore(db_path=db)
    try:
        assert store.get_lot("1001") is not None
        assert store.get_lot("1002") is not None
        assert store.get_lot("2001") is None
        run = store.conn.execute(
            "SELECT run_type, status, ontario_seen, inserted, note FROM crawl_runs WHERE id = ?",
            (report.run_id,),
        ).fetchone()
        assert run["run_type"] == "backfill"
        assert run["status"] == "completed"
        assert run["ontario_seen"] == 2
        assert run["inserted"] == 2
        assert run["note"]
    finally:
        store.close()


def test_backfill_idempotent_rerun(tmp_path: Path):
    raw = tmp_path / "raw"
    db = tmp_path / "iaai.db"
    _write_jsonl(raw / "day" / "a.jsonl.gz", [make_row("3001"), make_row("3002")])

    first = run_raw_backfill(raw_dir=raw, db_path=db)
    assert first.inserted == 2
    second = run_raw_backfill(raw_dir=raw, db_path=db)
    assert second.status == "completed"
    assert second.inserted == 0
    assert second.unchanged == 2

    store = SqliteStore(db_path=db)
    try:
        n = store.conn.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
        runs = store.conn.execute(
            "SELECT COUNT(*) FROM crawl_runs WHERE run_type = 'backfill'"
        ).fetchone()[0]
        assert n == 2
        assert runs == 2
    finally:
        store.close()


def test_bad_rows_dlq_and_continue(tmp_path: Path):
    raw = tmp_path / "raw"
    db = tmp_path / "iaai.db"
    bad = {"Make": "TOYOTA", "StockBranchId": 70, "StockBranchDescription": "Toronto North"}
    path = raw / "day" / "mixed.jsonl.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(bad) + "\n")
        fh.write("{not json\n")
        fh.write(json.dumps(make_row("4001")) + "\n")

    report = run_raw_backfill(raw_dir=raw, db_path=db, strict=False)
    assert report.status == "completed"
    assert report.skipped_bad_rows == 2
    assert report.inserted == 1
    assert report.dlq_path is not None
    assert Path(report.dlq_path).is_file()
    with gzip.open(report.dlq_path, "rt", encoding="utf-8") as fh:
        phases = [json.loads(line)["phase"] for line in fh]
    assert "parse_rejected" in phases
    assert "json_decode" in phases

    store = SqliteStore(db_path=db)
    try:
        assert store.get_lot("4001") is not None
    finally:
        store.close()


def test_strict_aborts_on_bad_row(tmp_path: Path):
    raw = tmp_path / "raw"
    db = tmp_path / "iaai.db"
    bad = {"Make": "TOYOTA", "StockBranchId": 70, "StockBranchDescription": "Toronto North"}
    _write_jsonl(raw / "day" / "bad.jsonl.gz", [bad, make_row("5001")])

    report = run_raw_backfill(raw_dir=raw, db_path=db, strict=True)
    assert report.status == "failed"
    assert report.skipped_bad_rows == 1
    assert report.inserted == 0

    store = SqliteStore(db_path=db)
    try:
        assert store.get_lot("5001") is None
        run = store.conn.execute(
            "SELECT status, run_type FROM crawl_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert run["run_type"] == "backfill"
        assert run["status"] == "failed"
    finally:
        store.close()


def test_plain_jsonl_and_cli(tmp_path: Path):
    raw = tmp_path / "raw"
    db = tmp_path / "iaai.db"
    _write_jsonl(raw / "day" / "plain.jsonl", [make_row("6001")], gzipped=False)

    runner = CliRunner()
    result = runner.invoke(
        cli.app,
        ["backfill-raw", "--raw-dir", str(raw), "--db-path", str(db)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "completed"
    assert payload["inserted"] == 1

    store = SqliteStore(db_path=db)
    try:
        assert store.get_lot("6001") is not None
    finally:
        store.close()
