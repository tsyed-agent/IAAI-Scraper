"""Command-line entrypoints.

  python -m iaai_scraper.cli crawl            # run a full Ontario crawl
  python -m iaai_scraper.cli crawl --max-pages 3   # limited test crawl
  python -m iaai_scraper.cli stats            # show DB stats
  python -m iaai_scraper.cli serve            # run the read API
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from . import config
from .crawler import Crawler
from .storage import SqliteStore, backup_sqlite

app = typer.Typer(add_completion=False, help="IAAI Ontario scraper")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


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
