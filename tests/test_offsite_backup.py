"""Offline tests for off-host backup + restore drill (filesystem backend)."""
from __future__ import annotations

import gzip
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from iaai_scraper.offsite_backup import (
    FilesystemObjectStore,
    newest_raw_snapshot,
    object_store_from_env,
    prune_local_db_backups,
    prune_local_raw,
    run_offsite_backup,
    run_restore_drill,
    sha256_file,
)
from iaai_scraper.parser import parse_row
from iaai_scraper.storage import SqliteStore
from tests.conftest import SAMPLE_ROW, make_row

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "offsite_backup.sh"


def _seed_db(db_path: Path, n: int = 3) -> None:
    store = SqliteStore(db_path=db_path)
    try:
        for i in range(n):
            lot = parse_row(make_row(f"9000{i:04d}"))
            assert lot is not None
            store.upsert_lot(lot)
        store.commit()
    finally:
        store.close()


def _write_raw(raw_root: Path, name: str = "run-test.jsonl.gz") -> Path:
    day = raw_root / "2026-07-16"
    day.mkdir(parents=True, exist_ok=True)
    path = day / name
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(SAMPLE_ROW) + "\n")
    return path


def test_sha256_and_prune(tmp_path: Path):
    bdir = tmp_path / "backups"
    bdir.mkdir()
    paths = []
    for i in range(7):
        p = bdir / f"iaai-ontario-{i}.db"
        p.write_bytes(b"x" * (i + 1))
        # Distinct mtimes for deterministic prune order.
        os.utime(p, (time.time() - (7 - i), time.time() - (7 - i)))
        paths.append(p)
    removed = prune_local_db_backups(bdir, keep=5)
    assert len(removed) == 2
    assert len(list(bdir.glob("*.db"))) == 5
    assert sha256_file(paths[-1]) == sha256_file(bdir / "iaai-ontario-6.db")


def test_newest_raw_and_prune_raw(tmp_path: Path):
    raw = tmp_path / "raw"
    old = _write_raw(raw, "run-old.jsonl.gz")
    new = _write_raw(raw, "run-new.jsonl.gz")
    os.utime(old, (time.time() - 200 * 86400, time.time() - 200 * 86400))
    os.utime(new, (time.time(), time.time()))
    assert newest_raw_snapshot(raw) == new
    pruned = prune_local_raw(raw, hot_days=90)
    assert old in pruned
    assert new.exists()


def test_offsite_backup_and_restore_drill(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "iaai_ontario.db"
    _seed_db(db, n=4)
    _write_raw(data / "raw")

    store = FilesystemObjectStore(tmp_path / "offsite")
    report = run_offsite_backup(
        data,
        store,
        db_path=db,
        create_fresh_snapshot=True,
        keep_local_db=5,
        raw_hot_days=90,
        snapshot_id="20260716T120000Z",
    )
    assert report["snapshot_id"] == "20260716T120000Z"
    assert store.exists("latest.json")
    assert store.exists("snapshots/20260716T120000Z/manifest.json")
    assert store.exists("snapshots/20260716T120000Z/iaai-ontario.db")

    restore_dir = tmp_path / "restore"
    result = run_restore_drill(store, restore_dir, min_lots=4)
    assert result.ok
    assert result.integrity_check == "ok"
    assert result.lot_count == 4
    assert all(result.verified_sha256.values())


def test_restore_drill_fails_lot_floor(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "iaai_ontario.db"
    _seed_db(db, n=2)
    store = FilesystemObjectStore(tmp_path / "offsite")
    run_offsite_backup(
        data,
        store,
        db_path=db,
        snapshot_id="20260716T130000Z",
    )
    result = run_restore_drill(store, tmp_path / "restore", min_lots=10)
    assert not result.ok
    assert result.lot_count == 2
    assert "min_lots" in result.detail


def test_object_store_from_env_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IAAI_BACKUP_BACKEND", "filesystem")
    monkeypatch.setenv("IAAI_BACKUP_DIR", str(tmp_path / "bucket"))
    store = object_store_from_env()
    assert isinstance(store, FilesystemObjectStore)
    probe = tmp_path / "x.bin"
    probe.write_bytes(b"abc")
    store.put("a/b.bin", probe)
    assert store.exists("a/b.bin")


def test_cli_offsite_and_restore(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "iaai_ontario.db"
    _seed_db(db, n=3)
    offsite = tmp_path / "offsite"
    monkeypatch.setenv("IAAI_DATA_DIR", str(data))
    monkeypatch.setenv("IAAI_DB_PATH", str(db))
    monkeypatch.setenv("IAAI_BACKUP_BACKEND", "filesystem")
    monkeypatch.setenv("IAAI_BACKUP_DIR", str(offsite))

    # Re-import config paths are already bound; pass explicit dirs via CLI flags.
    from typer.testing import CliRunner

    from iaai_scraper.cli import app

    runner = CliRunner()
    # Patch module-level config used by CLI.
    import iaai_scraper.cli as cli_mod
    import iaai_scraper.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(cli_mod.config, "DATA_DIR", data)
    monkeypatch.setattr(cli_mod.config, "DB_PATH", db)

    r1 = runner.invoke(
        app,
        ["offsite-backup", "--backend", "filesystem", "--backup-dir", str(offsite)],
    )
    assert r1.exit_code == 0, r1.output
    restore = tmp_path / "restore"
    r2 = runner.invoke(
        app,
        [
            "restore-drill",
            "--backend", "filesystem",
            "--backup-dir", str(offsite),
            "--min-lots", "3",
            "--restore-dir", str(restore),
        ],
    )
    assert r2.exit_code == 0, r2.output
    payload = json.loads(r2.output)
    assert payload["ok"] is True
    assert payload["lot_count"] == 3


def test_restore_rejects_path_traversal_name(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "iaai_ontario.db"
    _seed_db(db, n=1)
    store = FilesystemObjectStore(tmp_path / "offsite")
    run_offsite_backup(data, store, db_path=db, snapshot_id="20260716T140000Z")

    # Tamper the uploaded manifest to point at a traversal name.
    manifest_key = "snapshots/20260716T140000Z/manifest.json"
    local = tmp_path / "offsite" / manifest_key
    manifest = json.loads(local.read_text(encoding="utf-8"))
    manifest["artifacts"][0]["name"] = "../escape.db"
    local.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe artifact name"):
        run_restore_drill(store, tmp_path / "restore", min_lots=1)


def test_cli_reads_backup_env_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "iaai_ontario.db"
    _seed_db(db, n=2)
    offsite = tmp_path / "offsite"

    from typer.testing import CliRunner

    from iaai_scraper.cli import app
    import iaai_scraper.cli as cli_mod
    import iaai_scraper.config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "DB_PATH", db)
    monkeypatch.setattr(cli_mod.config, "DATA_DIR", data)
    monkeypatch.setattr(cli_mod.config, "DB_PATH", db)
    monkeypatch.setenv("IAAI_BACKUP_MIN_LOTS", "2")
    monkeypatch.setenv("IAAI_BACKUP_KEEP_LOCAL_DB", "3")
    monkeypatch.setenv("IAAI_BACKUP_RAW_HOT_DAYS", "30")

    runner = CliRunner()
    r1 = runner.invoke(
        app,
        ["offsite-backup", "--backend", "filesystem", "--backup-dir", str(offsite)],
    )
    assert r1.exit_code == 0, r1.output
    r2 = runner.invoke(
        app,
        [
            "restore-drill",
            "--backend", "filesystem",
            "--backup-dir", str(offsite),
            "--restore-dir", str(tmp_path / "restore"),
        ],
    )
    assert r2.exit_code == 0, r2.output
    payload = json.loads(r2.output)
    assert payload["min_lots"] == 2
    assert payload["ok"] is True


def test_shell_wrapper_runs_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data = tmp_path / "data"
    data.mkdir()
    db = data / "iaai_ontario.db"
    _seed_db(db, n=2)
    offsite = tmp_path / "offsite"

    python = os.environ.get("IAAI_PYTHON") or sys.executable
    env = {
        **os.environ,
        "IAAI_PYTHON": python,
        "IAAI_DATA_DIR": str(data),
        "IAAI_DB_PATH": str(db),
        "IAAI_BACKUP_BACKEND": "filesystem",
        "IAAI_BACKUP_DIR": str(offsite),
        "IAAI_BACKUP_MIN_LOTS": "2",
        "IAAI_BACKUP_KEEP_LOCAL_DB": "5",
        "IAAI_BACKUP_RAW_HOT_DAYS": "90",
    }
    # Ensure script is executable.
    SCRIPT.chmod(SCRIPT.stat().st_mode | stat.S_IXUSR)
    r = subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "restore drill" in r.stdout
    assert "ok" in r.stdout.lower()
