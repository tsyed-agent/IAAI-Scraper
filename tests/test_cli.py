from datetime import datetime, timezone

from typer.testing import CliRunner

from iaai_scraper import cli
from iaai_scraper.crawler import CrawlReport


runner = CliRunner()


class _FakeCrawler:
    status = "completed"
    last_settings = None

    def __init__(self, settings):
        _FakeCrawler.last_settings = settings

    async def run(self):
        return CrawlReport(
            started_at=datetime.now(timezone.utc),
            status=self.status,
        )


def test_crawl_cli_exits_zero_only_for_completed(monkeypatch):
    monkeypatch.setattr(cli, "Crawler", _FakeCrawler)
    _FakeCrawler.status = "completed"
    assert runner.invoke(cli.app, ["crawl", "--max-pages", "1"]).exit_code == 0
    _FakeCrawler.status = "completed_partial"
    assert runner.invoke(cli.app, ["crawl", "--max-pages", "1"]).exit_code == 1
    _FakeCrawler.status = "failed"
    assert runner.invoke(cli.app, ["crawl", "--max-pages", "1"]).exit_code == 1


def test_crawl_cli_page_size_default_tracks_mode(monkeypatch):
    from iaai_scraper import config

    monkeypatch.setattr(cli, "Crawler", _FakeCrawler)
    _FakeCrawler.status = "completed"
    assert runner.invoke(cli.app, ["crawl"]).exit_code == 0
    assert _FakeCrawler.last_settings.page_size == config.ONTARIO_PAGE_SIZE
    assert runner.invoke(cli.app, ["crawl", "--canada-wide"]).exit_code == 0
    assert _FakeCrawler.last_settings.page_size == config.PAGE_SIZE
    assert runner.invoke(cli.app, ["crawl", "--canada-wide", "--page-size", "50"]).exit_code == 0
    assert _FakeCrawler.last_settings.page_size == 50
