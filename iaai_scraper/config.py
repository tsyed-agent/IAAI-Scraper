"""Central configuration for the IAAI Ontario scraper.

All tunables live here so the crawl can be adjusted (politeness, scope, paths)
without touching logic. Values can be overridden via environment variables to
support different deployments without code changes.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

_log = logging.getLogger("iaai.config")


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        _log.warning("Invalid %s=%r; using default %s", name, val, default)
        return default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        _log.warning("Invalid %s=%r; using default %s", name, val, default)
        return default

# --------------------------------------------------------------------------- #
# Target site
# --------------------------------------------------------------------------- #
BASE_URL = "https://ca.iaai.com"
SEARCH_PAGE_URL = f"{BASE_URL}/Search"
SEARCH_RESULT_ENDPOINT = "/Search/GetSearchResult/"
# Vehicle Detail Page (VDP). Discovered from the site JS:
#   window.location.href = "/Vehicles/VehicleDetails?stockno=" + StockNum
DETAIL_URL_TEMPLATE = f"{BASE_URL}/Vehicles/VehicleDetails?stockno={{stock_num}}"

# --------------------------------------------------------------------------- #
# Ontario scope
# --------------------------------------------------------------------------- #
# Authoritative classifier: a lot belongs to Ontario when its StockBranchId is in
# this set. Discovered + validated empirically during Phase 0 (see docs).
# We ALSO accept a lot if its StockBranchDescription matches a known Ontario city
# (defence in depth, in case IAAI changes a branch id).
ONTARIO_BRANCH_IDS: dict[int, str] = {
    10: "London",
    52: "Hamilton",
    56: "Toronto (Oshawa)",
    61: "Ottawa",
    64: "Sudbury",
    70: "Toronto North",
    71: "Toronto West",
}
ONTARIO_BRANCH_NAMES = {name.lower() for name in ONTARIO_BRANCH_IDS.values()}

# --------------------------------------------------------------------------- #
# Crawl behaviour / politeness
# --------------------------------------------------------------------------- #
# IMPORTANT: a stable, deterministic sort is what makes pagination complete and
# non-overlapping. STOCK ASC sorts by the immutable stock number, so result
# "windows" do not shift as live auctions churn. Confirmed 0 overlap in Phase 0.
LIST_SORT = "STOCK ASC"
PAGE_SIZE = 100                      # legacy Canada-wide default (rows per page)
ONTARIO_PAGE_SIZE = _env_int("IAAI_ONTARIO_PAGE_SIZE", 1000)  # live-verified max (doc 06)
# Comma-separated StockBranchIds for server-side Ontario filter (doc 06 §1.1).
ONTARIO_BRANCH_IDS_CSV = ",".join(str(i) for i in ONTARIO_BRANCH_IDS)
# When true (default), crawl Ontario lots at the source via BranchIds instead of
# paging all of Canada and filtering client-side (~25× fewer requests).
ONTARIO_AT_SOURCE = os.getenv("IAAI_ONTARIO_AT_SOURCE", "true").lower() == "true"

# Politeness: human-like pacing between requests (seconds). A random jitter in
# [min, max] is slept between each list page / detail fetch to avoid the
# machine-regular timing that Imperva's behavioural layer flags.
DELAY_MIN_S = _env_float("IAAI_DELAY_MIN", 1.5)
DELAY_MAX_S = _env_float("IAAI_DELAY_MAX", 3.5)

# Safeguards against runaway loops. The full Canada inventory is ~5k lots
# (~52 pages); this cap is a hard stop well above that so a pagination bug can
# never loop forever.
MAX_LIST_PAGES = _env_int("IAAI_MAX_LIST_PAGES", 200)

# Retries with exponential backoff for transient errors / soft blocks.
MAX_RETRIES = _env_int("IAAI_MAX_RETRIES", 4)
BACKOFF_BASE_S = _env_float("IAAI_BACKOFF_BASE", 4.0)
# Recovery attempts are deliberately separate from transport retries: these
# cover valid HTTP responses whose page is transiently empty/short/duplicated.
PAGE_RECOVERY_RETRIES = _env_int("IAAI_PAGE_RECOVERY_RETRIES", 2)
# Timeout for the in-page fetch itself (the browser navigation timeout does not
# apply to a fetch started from page JavaScript).
FETCH_TIMEOUT_S = _env_float("IAAI_FETCH_TIMEOUT", 30.0)

# Detail-page enrichment is OFF by default: it multiplies request volume (one
# request per lot) and therefore anti-bot exposure and runtime. The list row is
# already comprehensive. Enable when richer per-lot fields are required.
ENRICH_DETAILS = os.getenv("IAAI_ENRICH_DETAILS", "false").lower() == "true"

# --------------------------------------------------------------------------- #
# Browser / session
# --------------------------------------------------------------------------- #
HEADLESS = os.getenv("IAAI_HEADLESS", "true").lower() == "true"
# Optional residential proxy, e.g. "http://user:pass@host:port". Strongly
# recommended for sustained crawling from datacenter IPs (which Imperva flags).
PROXY_SERVER = os.getenv("IAAI_PROXY", "") or None
LOCALE = "en-CA"
TIMEZONE = "America/Toronto"
# Max time to wait for the Incapsula JS challenge to clear, in seconds.
CHALLENGE_TIMEOUT_S = _env_int("IAAI_CHALLENGE_TIMEOUT", 45)

# --------------------------------------------------------------------------- #
# Storage paths
# --------------------------------------------------------------------------- #
DATA_DIR = Path(os.getenv("IAAI_DATA_DIR", "data"))
RAW_DIR = DATA_DIR / "raw"               # gzipped JSONL landing layer
DB_PATH = Path(os.getenv("IAAI_DB_PATH", str(DATA_DIR / "iaai_ontario.db")))
# On-demand thumbnail cache (API only — never used by the crawl hot path).
IMAGE_CACHE_DIR = Path(
    os.getenv("IAAI_IMAGE_CACHE_DIR", str(DATA_DIR / "image_cache"))
)
# Private API default: on. Disable until legal/display policy allows caching.
IMAGE_CACHE_ENABLED = os.getenv("IAAI_IMAGE_CACHE", "true").lower() in (
    "1", "true", "yes", "on",
)
IMAGE_FETCH_TIMEOUT_S = _env_float("IAAI_IMAGE_FETCH_TIMEOUT", 15.0)
IMAGE_CACHE_MAX_BYTES = _env_int("IAAI_IMAGE_CACHE_MAX_BYTES", 2_000_000)
# Comma-separated HTTPS hosts allowed as thumbnail sources (SSRF guard).
_IMAGE_HOSTS_RAW = os.getenv(
    "IAAI_IMAGE_ALLOWED_HOSTS",
    "anvis.iaai.com,vis.iaai.com",
)
IMAGE_ALLOWED_HOSTS = frozenset(
    h.strip().lower() for h in _IMAGE_HOSTS_RAW.split(",") if h.strip()
)
IMAGE_CACHE_CONTROL = os.getenv(
    "IAAI_IMAGE_CACHE_CONTROL", "public, max-age=86400"
)


@dataclass
class CrawlSettings:
    """Per-run settings, overridable from the CLI."""
    page_size: int = ONTARIO_PAGE_SIZE if ONTARIO_AT_SOURCE else PAGE_SIZE
    sort: str = LIST_SORT
    max_list_pages: int = MAX_LIST_PAGES
    enrich_details: bool = ENRICH_DETAILS
    delay_min_s: float = DELAY_MIN_S
    delay_max_s: float = DELAY_MAX_S
    ontario_at_source: bool = ONTARIO_AT_SOURCE
    branch_ids: str = ONTARIO_BRANCH_IDS_CSV if ONTARIO_AT_SOURCE else ""
    ontario_branch_ids: dict[int, str] = field(default_factory=lambda: dict(ONTARIO_BRANCH_IDS))
    page_recovery_retries: int = PAGE_RECOVERY_RETRIES
    fetch_timeout_s: float = FETCH_TIMEOUT_S

    def is_ontario(self, stock_branch_id, stock_branch_desc) -> bool:
        """True if a lot (by branch id or description) belongs to an Ontario branch."""
        if stock_branch_id in self.ontario_branch_ids:
            return True
        if stock_branch_desc and stock_branch_desc.strip().lower() in ONTARIO_BRANCH_NAMES:
            return True
        return False
