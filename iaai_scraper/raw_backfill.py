"""One-time / offline raw JSONL baseline backfill into SQLite (Phase 2 · 2.6).

Replays surviving ``data/raw`` archives (plain or gzipped JSONL) through the
same ``parse_row`` → Ontario filter → ``SqliteStore.upsert_lot`` path the crawler
uses. Safe to re-run (upserts are idempotent). Never contacts the network.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from . import config
from .config import CrawlSettings
from .parser import parse_row
from .storage import SqliteStore

log = logging.getLogger("iaai.raw_backfill")


@dataclass
class BackfillReport:
    started_at: datetime
    finished_at: Optional[datetime] = None
    raw_dir: str = ""
    files: int = 0
    rows_seen: int = 0
    ontario_seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped_non_ontario: int = 0
    skipped_bad_rows: int = 0
    status: str = "running"
    note: str = ""
    run_id: Optional[int] = None
    dlq_path: Optional[str] = None
    ontario_by_branch: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        return (
            f"status={self.status} files={self.files} rows={self.rows_seen} "
            f"ontario={self.ontario_seen} inserted={self.inserted} "
            f"updated={self.updated} unchanged={self.unchanged} "
            f"bad={self.skipped_bad_rows} non_ontario={self.skipped_non_ontario}"
        )


class BackfillAbort(RuntimeError):
    """Raised in ``--strict`` mode when a row cannot be replayed."""


def discover_raw_files(raw_dir: Path) -> list[Path]:
    """Return landing JSONL / JSONL.gz paths under ``raw_dir`` (oldest first).

    Companion DLQ artifacts (``*.dlq.*``) are excluded so we never re-ingest
    rejected envelopes as source rows.
    """
    raw_dir = Path(raw_dir)
    if not raw_dir.is_dir():
        return []
    files: list[Path] = []
    for path in raw_dir.rglob("*"):
        if not path.is_file():
            continue
        if ".dlq." in path.name:
            continue
        name = path.name
        if name.endswith(".jsonl.gz") or name.endswith(".jsonl"):
            files.append(path)
    return sorted(files, key=lambda p: (p.stat().st_mtime, str(p)))


def iter_raw_lines(paths: Iterable[Path]) -> Iterator[tuple[Path, int, str]]:
    """Yield ``(path, line_number, line)`` from plain or gzipped JSONL files."""
    for path in paths:
        opener = gzip.open if path.name.endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                yield path, line_number, line


def parse_jsonl_line(line: str, *, path: Path, line_number: int) -> dict[str, Any]:
    """Parse one JSONL line into a dict, or raise ``ValueError``."""
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    if not isinstance(row, dict):
        raise ValueError(f"{path}:{line_number}: expected a JSON object")
    return row


class _BackfillDlq:
    """DLQ writer matching ``RawWriter.write_dlq`` envelope shape (no landing file)."""

    def __init__(self, base_dir: Path):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.dir = Path(base_dir) / day
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"backfill-{ts}.dlq.jsonl.gz"
        self._staging = self.path.with_suffix("")  # .jsonl until close
        self._fh = None
        self.count = 0
        self._closed = False

    def write(
        self,
        raw_row: Any,
        error: Any,
        *,
        phase: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("Backfill DLQ is already closed")
        if self._fh is None:
            self._fh = open(self._staging, "a", encoding="utf-8")
        envelope = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "error": str(error),
            "metadata": metadata or {},
            "row": raw_row,
        }
        self._fh.write(json.dumps(envelope, ensure_ascii=False, default=str) + "\n")
        self.count += 1

    def close(self) -> None:
        if self._closed:
            return
        if self._fh is None:
            self._closed = True
            return
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
            with open(self._staging, "rb") as src, gzip.open(self.path, "wb") as dst:
                dst.write(src.read())
            with open(self.path, "rb") as dst:
                os.fsync(dst.fileno())
            self._staging.unlink(missing_ok=True)
            self._closed = True
        except Exception:
            log.exception("Backfill DLQ close failed for %s", self.path)
            raise


def _append_note(current: str, extra: str) -> str:
    return f"{current}; {extra}" if current else extra


def run_raw_backfill(
    *,
    raw_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    settings: Optional[CrawlSettings] = None,
    strict: bool = False,
    ontario_only: bool = True,
    commit_every: int = 200,
) -> BackfillReport:
    """Replay raw archives into SQLite and record a ``crawl_runs`` backfill row."""
    raw_dir = Path(raw_dir) if raw_dir is not None else config.RAW_DIR
    db_path = Path(db_path) if db_path is not None else config.DB_PATH
    settings = settings or CrawlSettings()
    report = BackfillReport(
        started_at=datetime.now(timezone.utc),
        raw_dir=str(raw_dir),
    )
    files = discover_raw_files(raw_dir)
    report.files = len(files)
    if not files:
        report.note = f"no raw JSONL archives under {raw_dir}"
        log.warning(report.note)

    store: Optional[SqliteStore] = None
    dlq: Optional[_BackfillDlq] = None
    run_id: Optional[int] = None
    try:
        store = SqliteStore(db_path=db_path)
        run_id = store.begin_run(report.started_at)
        report.run_id = run_id
        dlq = _BackfillDlq(raw_dir)

        for path, line_number, line in iter_raw_lines(files):
            report.rows_seen += 1
            meta = {
                "run_id": run_id,
                "source_path": str(path),
                "line": line_number,
            }
            try:
                row = parse_jsonl_line(line, path=path, line_number=line_number)
            except ValueError as exc:
                _handle_bad_row(
                    report, dlq, {"_raw_line": line}, exc, phase="json_decode",
                    metadata=meta, strict=strict,
                )
                continue
            try:
                lot = parse_row(row)
            except Exception as exc:  # noqa: BLE001 - isolate one malformed row
                _handle_bad_row(
                    report, dlq, row, exc, phase="parse_exception",
                    metadata=meta, strict=strict,
                )
                continue
            if lot is None:
                _handle_bad_row(
                    report, dlq, row, "parse_row returned no lot",
                    phase="parse_rejected", metadata=meta, strict=strict,
                )
                continue

            if ontario_only and not settings.is_ontario(lot.branch_id, lot.branch_name):
                report.skipped_non_ontario += 1
                continue

            report.ontario_seen += 1
            branch = lot.branch_name or "?"
            report.ontario_by_branch[branch] = report.ontario_by_branch.get(branch, 0) + 1

            outcome = store.upsert_lot(lot)
            if outcome == "inserted":
                report.inserted += 1
            elif outcome == "updated":
                report.updated += 1
            else:
                report.unchanged += 1

            if commit_every > 0 and report.rows_seen % commit_every == 0:
                store.commit()

        store.commit()
        report.status = "completed"
        if report.skipped_bad_rows:
            report.note = _append_note(
                report.note,
                f"{report.skipped_bad_rows} bad rows DLQ'd (non-strict)",
            )
        if not files:
            report.note = _append_note(report.note, "empty archive set")
    except BackfillAbort as exc:
        report.status = "failed"
        report.note = _append_note(report.note, str(exc))
        log.error("Backfill aborted (strict): %s", exc)
        if store is not None:
            try:
                store.commit()
            except Exception:  # noqa: BLE001
                log.exception("commit after strict abort failed")
    except Exception as exc:  # noqa: BLE001 - record failure for ops visibility
        report.status = "failed"
        report.note = _append_note(report.note, f"{type(exc).__name__}: {exc}")
        log.exception("Raw backfill failed")
    finally:
        report.finished_at = datetime.now(timezone.utc)
        if dlq is not None:
            try:
                dlq.close()
                if dlq.count:
                    report.dlq_path = str(dlq.path)
            except Exception as exc:  # noqa: BLE001
                report.status = "failed"
                report.note = _append_note(
                    report.note,
                    f"DLQ finalization failed: {type(exc).__name__}: {exc}",
                )
                log.exception("Backfill DLQ finalization failed")
        if store is not None and run_id is not None:
            try:
                note = report.note or (
                    f"raw baseline backfill from {report.files} file(s) under {raw_dir}"
                )
                store.finish_run(
                    run_id,
                    started_at=report.started_at,
                    finished_at=report.finished_at,
                    total_canada=None,
                    ontario_seen=report.ontario_seen,
                    inserted=report.inserted,
                    updated=report.updated,
                    unchanged=report.unchanged,
                    skipped_bad_rows=report.skipped_bad_rows,
                    canada_rows_seen=report.rows_seen,
                    archived=0,
                    pages=report.files,
                    status=report.status,
                    run_type="backfill",
                    note=note,
                )
            except Exception as exc:  # noqa: BLE001
                report.status = "failed"
                report.note = _append_note(
                    report.note,
                    f"crawl_runs finalization failed: {type(exc).__name__}: {exc}",
                )
                log.exception("Backfill crawl_runs finalization failed")
            try:
                store.close()
            except Exception as exc:  # noqa: BLE001
                report.status = "failed"
                report.note = _append_note(
                    report.note,
                    f"database close failed: {type(exc).__name__}: {exc}",
                )
                log.exception("Database close failed after backfill")
        log.info("Raw backfill done: %s", report.summary())
    return report


def _handle_bad_row(
    report: BackfillReport,
    dlq: _BackfillDlq,
    row: Any,
    error: Any,
    *,
    phase: str,
    metadata: dict[str, Any],
    strict: bool,
) -> None:
    dlq.write(row, error, phase=phase, metadata=metadata)
    report.skipped_bad_rows += 1
    if strict:
        raise BackfillAbort(
            f"{phase} at {metadata.get('source_path')}:{metadata.get('line')}: {error}"
        )
