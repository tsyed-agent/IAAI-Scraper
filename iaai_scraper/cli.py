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

import typer

from . import config
from .crawler import Crawler
from .storage import SqliteStore

app = typer.Typer(add_completion=False, help="IAAI Ontario scraper")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@app.command()
def crawl(
    max_pages: int = typer.Option(config.MAX_LIST_PAGES, min=1, help="hard cap on list pages"),
    page_size: int = typer.Option(config.PAGE_SIZE, min=1, max=100, help="rows per page (<=100)"),
    enrich: bool = typer.Option(config.ENRICH_DETAILS, help="fetch detail pages too"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a full Ontario crawl and write to the DB + raw JSONL."""
    _setup_logging(verbose)
    settings = config.CrawlSettings(
        page_size=page_size, max_list_pages=max_pages, enrich_details=enrich
    )
    report = asyncio.run(Crawler(settings).run())
    typer.echo(report.summary())
    typer.echo("Ontario by branch: " + json.dumps(report.ontario_by_branch))


@app.command()
def stats() -> None:
    """Print database statistics."""
    store = SqliteStore()
    try:
        typer.echo(json.dumps(store.stats(), indent=2, default=str))
    finally:
        store.close()


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the internal read API."""
    import uvicorn
    uvicorn.run("iaai_scraper.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
