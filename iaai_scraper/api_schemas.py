"""Versioned API response schemas (v1).

Field names match the current JSON wire format. Clients should treat
``X-API-Version: v1`` as the stable contract; see ``docs/api-versioning.md``.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

# Stable schema version advertised on every HTTP response.
API_SCHEMA_VERSION = "v1"

# Deprecation / Sunset for legacy ``?api_key=`` on thumbnail (RFC 8594).
API_KEY_QUERY_SUNSET = "Sat, 01 Nov 2026 00:00:00 GMT"
API_KEY_QUERY_DEPRECATION_LINK = (
    "https://github.com/tsyed-agent/IAAI-Scraper/blob/main/docs/api-versioning.md"
)

# Core lot fields that must remain in the v1 wire contract.
V1_LOT_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "stock_number",
    "status",
    "make",
    "model",
    "year",
    "branch_id",
    "branch_name",
    "province",
    "auction_date",
    "image_url",
    "thumbnail_href",
    "final_price",
    "high_prebid",
    "currency",
    "last_seen",
})

V1_LOTS_LIST_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "total",
    "limit",
    "offset",
    "count",
    "next_cursor",
    "next_offset",
    "results",
})

V1_STATS_REQUIRED_FIELDS: frozenset[str] = frozenset({
    "total_lots",
    "by_branch",
    "last_run",
})


class LotResponseV1(BaseModel):
    """Single lot as returned by ``GET /lots`` / ``GET /lots/{stock}``."""

    model_config = ConfigDict(extra="ignore")

    stock_number: str
    stock_id: Optional[int] = None
    vin: Optional[str] = None
    detail_url: Optional[str] = None
    year: Optional[int] = None
    make: Optional[str] = None
    model: Optional[str] = None
    engine: Optional[str] = None
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    odometer: Optional[int] = None
    odometer_unit: Optional[str] = None
    odometer_source: Optional[str] = None
    primary_damage: Optional[str] = None
    secondary_damage: Optional[str] = None
    title_brand: Optional[str] = None
    title_brand_type: Optional[str] = None
    damage_estimate: Optional[float] = None
    condition_text: Optional[str] = None
    runs: Optional[bool] = None
    starts: Optional[bool] = None
    has_keys: Optional[bool] = None
    branch_id: Optional[int] = None
    branch_name: Optional[str] = None
    location: Optional[str] = None
    location_name: Optional[str] = None
    province: Optional[str] = None
    auction_name: Optional[str] = None
    auction_id: Optional[int] = None
    auction_date: Optional[str] = None
    auction_datetime_display: Optional[str] = None
    auction_datetime_utc: Optional[str] = None
    auction_type: Optional[str] = None
    auction_type_id: Optional[int] = None
    auction_type_desc: Optional[str] = None
    auction_branch_id: Optional[int] = None
    auction_branch_name: Optional[str] = None
    auction_status_id: Optional[int] = None
    is_auction_closed: Optional[bool] = None
    is_regular_auction: Optional[bool] = None
    auction_offsite: Optional[bool] = None
    lane: Optional[str] = None
    sequence: Optional[int] = None
    is_timed_auction: Optional[bool] = None
    timed_auction_status: Optional[str] = None
    buy_now_price: Optional[float] = None
    buy_now_offer_price: Optional[float] = None
    timed_buy_now_price: Optional[float] = None
    high_prebid: Optional[float] = None
    timed_high_bid: Optional[float] = None
    winning_bid: Optional[float] = None
    currency: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    item_status_desc: Optional[str] = None
    prebid_item_status_desc: Optional[str] = None
    prebid_item_status_id: Optional[int] = None
    prebid_allowed: Optional[bool] = None
    prebid_closed: Optional[bool] = None
    buy_now_status: Optional[str] = None
    is_buy_now: Optional[bool] = None
    buy_now_allowed: Optional[bool] = None
    final_price: Optional[float] = None
    bid_closes_at: Optional[str] = None
    server_observed_at: Optional[str] = None
    last_price_at: Optional[str] = None
    status_updated_at: Optional[str] = None
    delisted_at: Optional[str] = None
    image_url: Optional[str] = None
    thumbnail_href: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    last_changed: Optional[str] = None
    missing_run_count: Optional[int] = None
    # Opt-in via ``?include_raw=true``; omitted from JSON when unset.
    raw: Optional[dict[str, Any]] = None


class LotsListResponseV1(BaseModel):
    """Paginated lot list from ``GET /lots``."""

    model_config = ConfigDict(extra="ignore")

    total: int
    limit: int
    offset: int
    count: int
    next_cursor: Optional[str] = None
    next_offset: Optional[int] = None
    results: list[LotResponseV1]


class BranchCountV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    branch_name: Optional[str] = None
    branch_id: Optional[int] = None
    n: int


class CrawlRunV1(BaseModel):
    """Subset of ``crawl_runs`` row fields used in stats/freshness."""

    model_config = ConfigDict(extra="allow")

    id: Optional[int] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: Optional[str] = None
    total_canada: Optional[int] = None
    ontario_seen: Optional[int] = None
    inserted: Optional[int] = None
    updated: Optional[int] = None
    unchanged: Optional[int] = None
    pages: Optional[int] = None
    run_type: Optional[str] = None
    note: Optional[str] = None


class StatsResponseV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    total_lots: int
    by_branch: list[BranchCountV1]
    last_run: Optional[CrawlRunV1] = None


class FreshnessResponseV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    last_crawl: Optional[CrawlRunV1] = None
    last_price_change_at: Optional[str] = None
    lots_with_price_history: int


class StatusFacetV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    count: int


class BranchFacetV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    branch_id: Optional[int] = None
    branch_name: Optional[str] = None
    count: int


class FiltersResponseV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    makes: list[str]
    models: list[str]
    years: list[int]
    provinces: list[str]
    title_brand_types: list[str]
    auction_types: list[str]
    primary_damages: list[str]
    secondary_damages: list[str]
    statuses: list[StatusFacetV1]
    branches: list[BranchFacetV1]


class PriceHistoryEntryV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    stock_number: str
    observed_at: str
    price_type: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    source_observed_at: Optional[str] = None
    run_id: Optional[int] = None


class PriceHistoryResponseV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stock_number: str
    total: int
    count: int
    limit: int
    offset: int
    history: list[PriceHistoryEntryV1]


class StatusHistoryEntryV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    stock_number: str
    observed_at: str
    old_status: Optional[str] = None
    new_status: str
    final_price: Optional[float] = None
    run_id: Optional[int] = None


class StatusHistoryResponseV1(BaseModel):
    model_config = ConfigDict(extra="ignore")

    stock_number: str
    total: int
    count: int
    limit: int
    offset: int
    history: list[StatusHistoryEntryV1]


class HealthResponseV1(BaseModel):
    status: str = Field(description="Always 'ok' when the process is up")


class ApiVersionInfoV1(BaseModel):
    """Documented in OpenAPI; also mirrored by the ``X-API-Version`` header."""

    schema_version: str = Field(
        default=API_SCHEMA_VERSION,
        description="Stable response schema version (header X-API-Version)",
    )
