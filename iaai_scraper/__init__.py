"""IAAI Canada (Ontario) auction lot scraper + internal API.

Package layout:
  config      - tunable settings, Ontario branch registry
  session     - Playwright session manager (solves Incapsula, runs in-page fetches)
  search_client - typed wrapper around the /Search/GetSearchResult endpoint
  parser      - maps a raw RunList row -> normalized Lot model
  models      - Pydantic Lot schema
  storage     - raw JSONL landing layer + SQLite serving DB (upsert/query)
  detail      - optional lot detail-page enrichment
  crawler     - orchestrates the full Ontario crawl with safeguards
  api         - FastAPI read API
  cli         - command-line entrypoints
"""

__version__ = "0.1.0"
