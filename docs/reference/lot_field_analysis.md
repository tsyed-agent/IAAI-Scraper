# Live Lot Field Analysis (2026-06-24)

Manual investigation via `spike/collect_lot_samples.py` (Playwright in-page fetch, **not** the production scraper). Full payloads for 10 diverse Ontario lots plus a 500-row field census are in [`lot_samples_reference.json`](./lot_samples_reference.json).

## Method

1. Solved Imperva on `/Search`, then POSTed `/Search/GetSearchResult/` for pages 1–5 (500 rows, 173 Ontario).
2. Selected 10 Ontario lots spanning branches, `ItemStatusDesc` values, and pre-bid signals.
3. Visited each VDP (`/Vehicles/VehicleDetails?stockno=…`) and scraped label:value pairs.

## RunList size

- **100 keys** per row in `RunList`.
- Current parser maps **35** fields into `Lot`; **65** keys are only in `Lot.raw`.

## Lifecycle / status fields (confirmed live)

| Field | Presence (500 rows) | Values observed | Role |
|---|---|---|---|
| `ItemStatusDesc` | 500/500 | *(empty)*, `Sold`, `IfBid`, `Pass` | **Primary lifecycle signal** |
| `PrebidItemStatusDesc` | 500/500 | *(empty)*, `BiddingComplete` | Auction window ended |
| `PrebidItemStatusID` | 500/500 | `4` when complete | Numeric companion |

### Status distribution (500-row scan)

| Signal | Count |
|---|---|
| `ItemStatusDesc=Sold` | 32 |
| `ItemStatusDesc=IfBid` | 51 |
| `ItemStatusDesc=Pass` | 5 |
| `PrebidItemStatusDesc=BiddingComplete` | 88 |
| `ItemStatusDesc` empty (active/upcoming) | remainder |

**Key insight:** `Sold + IfBid + Pass = 88 = BiddingComplete count`. When bidding is complete, `ItemStatusDesc` always disambiguates the outcome. Active/upcoming lots have **both** fields empty.

### Recommended ingest mapping

| `ItemStatusDesc` | Normalized `status` | Notes |
|---|---|---|
| *(empty)* | `active` | On market / upcoming |
| `Sold` | `sold` | Completed sale |
| `IfBid` | `if_bid` | Conditional sale (seller may accept) |
| `Pass` | `passed` | Did not sell at auction |

Do **not** infer `sold` from `PrebidItemStatusDesc=BiddingComplete` alone.

## Price / bid fields (confirmed live)

| Field | Presence | Notes |
|---|---|---|
| `HighPrebidValue` | 500/500 | Numeric; best anonymous final-bid signal for sold/if-bid |
| `HighPrebid` | 500/500 | Formatted string twin of above |
| `WinningbidAmount` | 500/500 | **Usually empty** for anonymous users even when `Sold` |
| `TimedAuctionHighestBidAmountValue` | 500/500 | Live timed-auction high bid |
| `BuyNowPrice` | 500/500 | Often `$0.00` |
| `BuyNowOfferPrice` | 500/500 | Offer price when applicable |
| `TimedAuctionBuyNowPrice` | 500/500 | Timed buy-now |
| `BidItemClosingDateUTC` | 500/500 | Close time (null sentinel common) |
| `ServerCurrentDateUTC` | 500/500 | Server clock |

### Price examples from 10-lot sample

| Stock | ItemStatusDesc | HighPrebidValue | WinningbidAmount |
|---|---|---|---|
| 11997575 | Sold | 0 | empty |
| 12143176 | Sold | 1850 | empty |
| 12249600 | IfBid | 750 | empty |
| 12071604 | *(active)* | 175 | empty |

**Takeaway:** use `HighPrebidValue` (or timed high bid) for `final_price`; do not rely on `WinningbidAmount` at the anonymous tier.

## Fields worth adding to parser (next phase)

Priority for lifecycle + pricing:

- `ItemStatusDesc` → `item_status_desc`
- `PrebidItemStatusDesc` → `prebid_item_status_desc`
- `PrebidItemStatusID` → `prebid_item_status_id`
- `TimedAuctionHighestBidAmountValue` → `timed_high_bid`
- `BidItemClosingDateUTC` → `bid_closes_at`
- `derive_lot_status()` → `status` (computed)

Lower priority (buyer-session / UI state):

- `TimedAuctionStatusDesc`, `BuyNowStatus`, `MyPrebidValue`, `IsMyPreBidHighest`, etc.

## Scraper crawl note

On 2026-06-24 the manual spike script succeeded, but the production `iaai_scraper.cli crawl` intermittently failed with `Page.goto: Page crashed` in this VM (likely Playwright/Chromium resource limits). Field analysis above is from the successful manual run.

## Reference files

| File | Contents |
|---|---|
| `docs/reference/lot_samples_reference.json` | 10 full RunList rows + VDP specs + 500-row census |
| `spike/collect_lot_samples.py` | Script to regenerate the reference |
