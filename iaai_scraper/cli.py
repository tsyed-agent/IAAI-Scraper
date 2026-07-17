"""Command-line entrypoints.

  python -m iaai_scraper.cli crawl            # run a full Ontario crawl
  python -m iaai_scraper.cli crawl --max-pages 3   # limited test crawl
  python -m iaai_scraper.cli stats            # show DB stats
  python -m iaai_scraper.cli backup           # local atomic SQLite snapshot
  python -m iaai_scraper.cli offsite-backup   # upload snapshot + manifest off-host
  python -m iaai_scraper.cli restore-drill    # download + integrity/lot-count check
  python -m iaai_scraper.cli backfill-raw     # replay surviving raw JSONL into SQLite
  python -m iaai_scraper.cli serve            # run the read API
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from . import config
from .crawler import Crawler
from .offsite_backup import (
    DEFAULT_KEEP_LOCAL_DB,
    DEFAULT_RAW_HOT_DAYS,
    object_store_from_env,
    run_offsite_backup,
    run_restore_drill,
)
from .raw_backfill import run_raw_backfill
from .storage import SqliteStore, backup_sqlite

app = typer.Typer(add_completion=False, help="IAAI Ontario scraper")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _env_int_opt(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise typer.BadParameter(f"invalid {name}={raw!r}; expected an integer") from exc


@app.command()
def crawl(
    max_pages: int = typer.Option(config.MAX_LIST_PAGES, min=1, help="hard cap on list pages"),
    page_size: Optional[int] = typer.Option(
        None, min=1, max=1000,
        help="rows per page (default: 1000 Ontario-at-source, 100 legacy Canada-wide)",
    ),
    canada_wide: bool = typer.Option(
        False, "--canada-wide", help="legacy: crawl all Canada and filter client-side",
    ),
    enrich: bool = typer.Option(config.ENRICH_DETAILS, help="fetch detail pages too"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a full Ontario crawl and write to the DB + raw JSONL."""
    _setup_logging(verbose)
    ontario_at_source = not canada_wide
    if page_size is None:
        # 1000 is only verified for the BranchIds-filtered Ontario path; the
        # legacy Canada-wide crawl was validated at 100 rows per page.
        page_size = config.ONTARIO_PAGE_SIZE if ontario_at_source else config.PAGE_SIZE
    settings = config.CrawlSettings(
        page_size=page_size,
        max_list_pages=max_pages,
        enrich_details=enrich,
        ontario_at_source=ontario_at_source,
        branch_ids=config.ONTARIO_BRANCH_IDS_CSV if ontario_at_source else "",
    )
    report = asyncio.run(Crawler(settings).run())
    typer.echo(report.summary())
    typer.echo("Ontario by branch: " + json.dumps(report.ontario_by_branch))
    # A partial/unknown snapshot is useful diagnostic output but must not be
    # mistaken for a successful scheduled crawl.
    if report.status != "completed":
        raise typer.Exit(code=1)


@app.command()
def stats() -> None:
    """Print database statistics."""
    store = SqliteStore()
    try:
        typer.echo(json.dumps(store.stats(), indent=2, default=str))
    finally:
        store.close()


@app.command()
def backup(
    destination: Optional[Path] = typer.Option(
        None,
        "--destination",
        "-d",
        help="snapshot path (default: data/backups/<timestamp>.db)",
    ),
) -> None:
    """Create an atomic online SQLite snapshot for off-host retention."""
    if destination is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = config.DATA_DIR / "backups" / f"iaai-ontario-{stamp}.db"
    path = backup_sqlite(config.DB_PATH, destination)
    typer.echo(str(path))


@app.command("offsite-backup")
def offsite_backup(
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="filesystem|s3 (default: IAAI_BACKUP_BACKEND or filesystem)",
    ),
    backup_dir: Optional[Path] = typer.Option(
        None,
        "--backup-dir",
        help="filesystem object-store root (IAAI_BACKUP_DIR)",
    ),
    keep_local_db: Optional[int] = typer.Option(
        None,
        "--keep-local-db",
        min=0,
        help="local DB backups to retain (default: IAAI_BACKUP_KEEP_LOCAL_DB or 5)",
    ),
    raw_hot_days: Optional[int] = typer.Option(
        None,
        "--raw-hot-days",
        min=0,
        help="local raw hot window days (default: IAAI_BACKUP_RAW_HOT_DAYS or 90)",
    ),
    skip_fresh_snapshot: bool = typer.Option(
        False,
        "--skip-fresh-snapshot",
        help="reuse latest local DB backup instead of creating a new one",
    ),
) -> None:
    """Upload latest DB (+ newest raw) to versioned object storage with manifest."""
    store = object_store_from_env(backend=backend, filesystem_root=backup_dir)
    report = run_offsite_backup(
        config.DATA_DIR,
        store,
        db_path=config.DB_PATH,
        create_fresh_snapshot=not skip_fresh_snapshot,
        keep_local_db=(
            keep_local_db
            if keep_local_db is not None
            else _env_int_opt("IAAI_BACKUP_KEEP_LOCAL_DB", DEFAULT_KEEP_LOCAL_DB)
        ),
        raw_hot_days=(
            raw_hot_days
            if raw_hot_days is not None
            else _env_int_opt("IAAI_BACKUP_RAW_HOT_DAYS", DEFAULT_RAW_HOT_DAYS)
        ),
    )
    typer.echo(json.dumps(report, indent=2, default=str))


@app.command("restore-drill")
def restore_drill(
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="filesystem|s3 (default: IAAI_BACKUP_BACKEND or filesystem)",
    ),
    backup_dir: Optional[Path] = typer.Option(
        None,
        "--backup-dir",
        help="filesystem object-store root (IAAI_BACKUP_DIR)",
    ),
    min_lots: Optional[int] = typer.Option(
        None,
        "--min-lots",
        min=0,
        help="lot-count floor (default: IAAI_BACKUP_MIN_LOTS or 1)",
    ),
    snapshot_id: Optional[str] = typer.Option(
        None,
        "--snapshot-id",
        help="specific snapshot; default reads latest.json",
    ),
    restore_dir: Optional[Path] = typer.Option(
        None,
        "--restore-dir",
        help="directory for downloaded artifacts (default: temp)",
    ),
) -> None:
    """Download latest off-host snapshot and prove integrity + lot-count floor."""
    store = object_store_from_env(backend=backend, filesystem_root=backup_dir)
    dest = restore_dir or Path(tempfile.mkdtemp(prefix="iaai-restore-"))
    result = run_restore_drill(
        store,
        dest,
        min_lots=(
            min_lots
            if min_lots is not None
            else _env_int_opt("IAAI_BACKUP_MIN_LOTS", 1)
        ),
        snapshot_id=snapshot_id,
    )
    typer.echo(json.dumps(result.as_dict(), indent=2, default=str))
    if not result.ok:
        raise typer.Exit(code=1)


@app.command("backfill-raw")
def backfill_raw(
    raw_dir: Optional[Path] = typer.Option(
        None,
        "--raw-dir",
        help="raw JSONL root (default: data/raw or IAAI_DATA_DIR/raw)",
    ),
    db_path: Optional[Path] = typer.Option(
        None,
        "--db-path",
        help="SQLite path (default: IAAI_DB_PATH or data/iaai_ontario.db)",
    ),
    strict: bool = typer.Option(
        False,
        "--strict",
        help="abort on the first bad/unparseable row (default: DLQ and continue)",
    ),
    include_non_ontario: bool = typer.Option(
        False,
        "--include-non-ontario",
        help="upsert lots outside Ontario branches (default: Ontario only)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Replay surviving raw JSONL / JSONL.gz archives into SQLite (offline).

    Uses the same parse_row → upsert_lot path as a live crawl. Records a
    crawl_runs row with run_type=backfill. Safe to re-run. See docs/ops-backfill.md.
    """
    _setup_logging(verbose)
    report = run_raw_backfill(
        raw_dir=raw_dir if raw_dir is not None else config.RAW_DIR,
        db_path=db_path if db_path is not None else config.DB_PATH,
        strict=strict,
        ontario_only=not include_non_ontario,
    )
    typer.echo(json.dumps(report.as_dict(), indent=2, default=str))
    if report.status != "completed":
        raise typer.Exit(code=1)


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the internal read API."""
    import uvicorn

    from .logging_utils import install_access_log_redaction

    # Thumbnail routes accept ?api_key= for <img src>; never log the token.
    install_access_log_redaction()
    uvicorn.run("iaai_scraper.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
