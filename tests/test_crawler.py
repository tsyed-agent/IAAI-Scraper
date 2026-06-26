import asyncio

from iaai_scraper import config, crawler as crawler_mod
from iaai_scraper.crawler import Crawler
from iaai_scraper.storage import SqliteStore, RawWriter
from tests.fakes import FakeSession, FakeSearchClient
from tests.conftest import make_row


def run_crawl(monkeypatch, tmp_path, pages, settings=None):
    """Run Crawler.run() against fakes + temp DB/raw dirs. Returns (report, db_path)."""
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(config, "RAW_DIR", raw_dir, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)  # crawl lock (Task E4)
    monkeypatch.setattr(crawler_mod, "IaaiSession", lambda *a, **k: FakeSession())
    monkeypatch.setattr(crawler_mod, "SearchClient", lambda *a, **k: FakeSearchClient(pages))
    monkeypatch.setattr(crawler_mod, "SqliteStore", lambda *a, **k: SqliteStore(db_path=db))
    monkeypatch.setattr(crawler_mod, "RawWriter", lambda *a, **k: RawWriter(base_dir=raw_dir))
    c = Crawler(settings or config.CrawlSettings(page_size=2))
    return asyncio.run(c.run()), db


def test_all_unparseable_page_fails(monkeypatch, tmp_path):
    bad = [{"Make": "X"}, {"Make": "Y"}]  # no StockNum -> 0 parsed
    report, _ = run_crawl(monkeypatch, tmp_path, pages=[(bad, 2)])
    assert report.status == "failed"
    assert report.skipped_bad_rows == 2


def test_skipped_rows_counted(monkeypatch, tmp_path):
    page = [make_row("1001"), {"Make": "no stock"}]
    report, _ = run_crawl(monkeypatch, tmp_path, pages=[(page, 1)])
    assert report.skipped_bad_rows == 1
    assert report.ontario_seen == 1


def test_completed_crawl_archives_missing(monkeypatch, tmp_path):
    p1 = [make_row("9001"), make_row("9002")]
    run_crawl(monkeypatch, tmp_path, pages=[(p1, 2)])
    p2 = [make_row("9001")]
    report, db = run_crawl(monkeypatch, tmp_path, pages=[(p2, 1)])
    store = SqliteStore(db_path=db)
    assert store.get_lot("9001")["status"] == "active"
    assert store.get_lot("9002")["status"] == "removed"
    assert report.archived == 1
    store.close()


def test_partial_crawl_does_not_archive(monkeypatch, tmp_path):
    p1 = [make_row("8001"), make_row("8002")]
    run_crawl(monkeypatch, tmp_path, pages=[(p1, 2)])
    p2 = [make_row("8001")]
    report, db = run_crawl(monkeypatch, tmp_path, pages=[(p2, 100)])
    assert report.status == "completed_partial"
    store = SqliteStore(db_path=db)
    assert store.get_lot("8002")["status"] == "active"
    store.close()


def test_duplicate_page_before_complete_fails(monkeypatch, tmp_path):
    """A duplicate page mid-crawl must fail, not silently stop incomplete."""
    # Page 1: 2 lots; total says 5 — page 2 repeats page 1 (0 new).
    p1 = [make_row("7001"), make_row("7002")]
    dup = list(p1)
    settings = config.CrawlSettings(page_size=2, ontario_at_source=True)
    db = tmp_path / "t.db"
    raw_dir = tmp_path / "raw"
    monkeypatch.setattr(config, "DB_PATH", db, raising=False)
    monkeypatch.setattr(config, "RAW_DIR", raw_dir, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(crawler_mod, "IaaiSession", lambda *a, **k: FakeSession())
    monkeypatch.setattr(
        crawler_mod, "SearchClient",
        lambda *a, **k: FakeSearchClient([(p1, 5), (dup, 5)]),
    )
    monkeypatch.setattr(crawler_mod, "SqliteStore", lambda *a, **k: SqliteStore(db_path=db))
    monkeypatch.setattr(crawler_mod, "RawWriter", lambda *a, **k: RawWriter(base_dir=raw_dir))
    report = asyncio.run(Crawler(settings).run())
    assert report.status == "failed"
    assert "pagination overlap" in report.note.lower() or "0 new stock" in report.note.lower()
