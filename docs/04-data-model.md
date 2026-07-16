# IAAI Lot Data Model (Ontario)

> Proposed normalized schema for an auction lot. Field availability will be
> confirmed against live `VehicleDetails/{id}` pages during Phase 0. The crawl
> stores thumbnail *URL pointers* (`image_url`) only — it does not download
> binaries. Serving thumbs to a UI is an API concern (on-demand cache); see
> [`10-implementation-plan.md`](./10-implementation-plan.md).

## 1. Field catalogue

### Identity
| Field | Type | Notes |
|---|---|---|
| `stock_number` | string | **Primary key**. IAAI lot/stock id. |
| `vin` | string | 17-char VIN where available. |
| `detail_url` | string | `https://ca.iaai.com/VehicleDetails/{stock_number}`. |

### Vehicle
| Field | Type | Notes |
|---|---|---|
| `year` | int | |
| `make` | string | |
| `model` | string | |
| `series_trim` | string | |
| `body_style` | string | |
| `vehicle_type` | string | car / SUV / truck / moto / etc. |
| `color` | string | |
| `engine` | string | e.g. "2.5L 4". |
| `cylinders` | int | |
| `fuel_type` | string | gas / diesel / hybrid / EV. |
| `drivetrain` | string | FWD / RWD / AWD / 4WD. |
| `transmission` | string | automatic / manual. |
| `odometer` | int | |
| `odometer_unit` | enum | `km` / `mi`. |
| `odometer_brand` | string | actual / not actual / exempt. |

### Condition / loss
| Field | Type | Notes |
|---|---|---|
| `loss_type` | string | collision, theft, water/flood, etc. |
| `primary_damage` | string | |
| `secondary_damage` | string | |
| `run_and_drive` | enum | run-and-drive / starts / engine-start-program / unknown. |
| `keys_present` | bool | |
| `airbags` | string | deployed / intact / unknown (if present). |

### Title / legal
| Field | Type | Notes |
|---|---|---|
| `title_type` | string | salvage, rebuilt, clean, non-repairable, etc. |
| `title_state_province` | string | issuing province/state. |
| `sale_document` | string | bill of sale / certificate of destruction / etc. |

### Sale / auction
| Field | Type | Notes |
|---|---|---|
| `branch` | string | Ontario branch name. |
| `branch_id` | string | IAAI branch id (e.g. `Imp_10`). |
| `province` | string | `ON` (scope guard). |
| `auction_date` | datetime | scheduled sale date/time (store UTC + original tz). |
| `lane` | string | auction lane. |
| `run_number` | string | run order. |
| `sale_status` | string | upcoming / live / sold / pending / cancelled. |
| `current_bid` | decimal | |
| `buy_now_price` | decimal | if offered. |
| `currency` | enum | `CAD` (default for `ca.iaai.com`). |
| `seller` | string | seller / source. |
| `seller_type` | string | insurance / dealer / fleet / etc. |

### Valuation (when shown)
| Field | Type | Notes |
|---|---|---|
| `acv` | decimal | actual cash value. |
| `est_retail_value` | decimal | |
| `repair_cost` | decimal | estimated repair cost. |

### Provenance / housekeeping
| Field | Type | Notes |
|---|---|---|
| `first_seen` | datetime | first time we collected this lot. |
| `last_seen` | datetime | most recent collection. |
| `last_changed` | datetime | last time a tracked field changed. |
| `source` | string | `ca.iaai.com`. |
| `raw_json` | json/text | full raw parsed payload (replay/debug). |
| `image_url` | text | Source thumbnail URL pointer (e.g. `anvis.iaai.com`). Crawl never stores binaries; API may cache thumbs on demand. |

## 2. SQLite DDL (MVP)

```sql
CREATE TABLE IF NOT EXISTS lots (
    stock_number          TEXT PRIMARY KEY,
    vin                   TEXT,
    detail_url            TEXT,

    year                  INTEGER,
    make                  TEXT,
    model                 TEXT,
    series_trim           TEXT,
    body_style            TEXT,
    vehicle_type          TEXT,
    color                 TEXT,
    engine                TEXT,
    cylinders             INTEGER,
    fuel_type             TEXT,
    drivetrain            TEXT,
    transmission          TEXT,
    odometer              INTEGER,
    odometer_unit         TEXT,
    odometer_brand        TEXT,

    loss_type             TEXT,
    primary_damage        TEXT,
    secondary_damage      TEXT,
    run_and_drive         TEXT,
    keys_present          INTEGER,   -- 0/1
    airbags               TEXT,

    title_type            TEXT,
    title_state_province  TEXT,
    sale_document         TEXT,

    branch                TEXT,
    branch_id             TEXT,
    province              TEXT,
    auction_date          TEXT,      -- ISO 8601 UTC
    lane                  TEXT,
    run_number            TEXT,
    sale_status           TEXT,
    current_bid           REAL,
    buy_now_price         REAL,
    currency              TEXT DEFAULT 'CAD',
    seller                TEXT,
    seller_type           TEXT,

    acv                   REAL,
    est_retail_value      REAL,
    repair_cost           REAL,

    first_seen            TEXT,
    last_seen             TEXT,
    last_changed          TEXT,
    source                TEXT DEFAULT 'ca.iaai.com',
    raw_json              TEXT,
    image_urls            TEXT
);

CREATE INDEX IF NOT EXISTS idx_lots_make_model_year ON lots (make, model, year);
CREATE INDEX IF NOT EXISTS idx_lots_branch          ON lots (branch_id);
CREATE INDEX IF NOT EXISTS idx_lots_auction_date    ON lots (auction_date);
CREATE INDEX IF NOT EXISTS idx_lots_sale_status     ON lots (sale_status);
CREATE INDEX IF NOT EXISTS idx_lots_vin             ON lots (vin);
```

### Upsert pattern (idempotent re-scrape)

```sql
INSERT INTO lots (stock_number, /* …cols… */ first_seen, last_seen)
VALUES (:stock_number, /* …vals… */ :now, :now)
ON CONFLICT(stock_number) DO UPDATE SET
    /* …cols… */
    last_seen = excluded.last_seen,
    last_changed = CASE
        WHEN lots.sale_status IS NOT excluded.sale_status
          OR lots.current_bid  IS NOT excluded.current_bid
        THEN excluded.last_seen ELSE lots.last_changed END;
```

## 3. PostgreSQL notes (Phase 2)

- Same columns, plus store the full payload in a **`JSONB`** column (`raw_json`)
  for flexible querying and a **`GIN`** index on it.
- Add **B-tree** indexes mirroring the SQLite ones; optional **`tsvector`** +
  full-text index across make/model/series/damage for keyword search.
- Consider a separate **`lot_history`** table (append-only) if we need a full
  time-series of bid/status changes rather than just `last_changed`.

## 4. Raw JSONL layout

```
data/raw/{branch_id}/{YYYY-MM-DD}.jsonl.gz   # one parsed lot per line
```

Each line is the complete parsed object before normalization, enabling
re-derivation of the DB without re-scraping.
