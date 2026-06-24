# Real-Time Pricing + Historical Archive — Infrastructure Research & Recommendation

> Goal (from product owner): never go stale. Maintain a permanent **historical
> archive** of every Ontario lot and **what it sold for**, capture **price as
> often as possibly/affordably**, on **completely free** infrastructure that runs
> sustainably and doesn't break. Two cadences:
>
> - **Everything (full metadata snapshot): every 12 h** (twice a day is enough).
> - **Pricing: as close to real-time as free infra allows** (price is the only
>   field that changes fast — it can move within 5 minutes during a live sale).
>
> This document is the research + recommendation. Findings below are from **live
> probes against `ca.iaai.com` on 2026-06-24**, not assumptions.

---

## 1. The key discoveries that make this cheap (live-verified)

These three findings collapse the cost of frequent price syncs by ~25×, which is
what makes "almost real-time, for free" actually feasible.

### 1.1 We can scope to Ontario **at the source**
`GetSearchResult` accepts `BranchIds`. Sending the 7 Ontario branch ids
(`10,52,56,61,64,70,71`) returns **only** Ontario lots:

```
baseline (no filter)         total=5216, rows are mixed-province
BranchIds=10,52,56,61,64,70,71   total=1419, non_ontario_rows=0   ✅
```

So we no longer page the whole 5,217-lot Canada set and filter client-side; we
ask the server for the ~1,450 Ontario lots directly.

### 1.2 `PageSize` goes up to **1000**
The endpoint honours large page sizes, so the entire Ontario set fits in **2–3
requests** instead of 53 pages:

```
BranchIds=ONT, PageSize=500    -> 500 rows / request (~6 s)
BranchIds=ONT, PageSize=1000   -> 1000 rows / request (~10 s)
=> full Ontario snapshot = 2 requests (PageSize 1000) ≈ 20–30 s wall time
```

### 1.3 The **live price is already in the search payload** — no detail page
Each search row carries far more pricing than the current scraper stores. Probed
field list on a live row includes:

| Field | Meaning | Why it matters |
|---|---|---|
| `HighPrebidValue` / `HighPrebid` | current high **pre-bid** | accrues over days before sale |
| `TimedAuctionHighestBidAmountValue` | **live high bid on a timed (online) auction** | this is the value that moves minute-to-minute |
| `WinningbidAmount` | winning bid once complete | best signal of **sold price** |
| `BuyNowPrice` / `TimedAuctionBuyNowPrice` / `BuyNowOfferPrice` | buy-now / offer prices | |
| `PrebidItemStatusID` / `PrebidItemStatusDesc` | e.g. `BiddingComplete` | lifecycle / sold detection |
| `BidItemClosingDateUTC` | when a timed lot closes | drives "poll harder near close" |
| `ServerCurrentDateUTC` | server clock | align our snapshots to server time |

**Consequence:** near-real-time price tracking = re-poll the Ontario search every
few minutes and read these fields. We do **not** need to hit a per-lot detail
page (which would be 1,450 extra requests). The VDP exposed no separate bid API
(only an ad pixel), confirming the search endpoint is the price source of truth.

> Caveat on "sold price" fidelity: timed/online auctions expose the live high bid
> and winning bid anonymously. Pure **live-lane** hammer prices are generally only
> visible to logged-in buyers, so for those we record the **best available**
> value (last observed high bid near close). This is the free-tier ceiling; a
> logged-in session could close the gap later if ever in scope (ToS permitting).

---

## 2. What this means for compute budget

| Task | Requests | Wall time | Frequency | Notes |
|---|---|---|---|---|
| Full metadata snapshot | 2–3 | ~30 s | every 12 h | all fields, raw archived |
| Price refresh | 2–3 | ~20–30 s | every 2–15 min | reads price fields only, change-only writes |
| Live-window price refresh | 2–3 | ~20–30 s | every 1–3 min | only while a branch's sale is live (`auction_datetime_utc` / `BidItemClosingDateUTC`) |

One Imperva challenge solve takes ~3–5 s. If we **keep one warm browser session**
and reuse it, each refresh is just the 2–3 fetches. This is tiny — the whole
thing fits inside any free tier; the only real constraints are (a) anti-bot
exposure from frequency and (b) where we can run a browser for free.

---

## 3. Free infrastructure options (researched, 2026)

### 3.1 Where to RUN it (must execute a real Chromium for Imperva)

| Option | Free? | Always-on? | Sub-5-min cadence? | Risk / catch |
|---|---|---|---|---|
| **Your own always-on box** (Raspberry Pi / mini-PC / spare laptop) | 100%, forever | Yes | Yes (1–2 min) | You maintain it; needs power/uptime. **Residential IP = best anti-bot** (no proxy needed). |
| **Oracle Cloud Always Free** (Ampere A1, up to 4 OCPU/24 GB; or 2× AMD micro 1 GB) | Yes, indefinitely | Yes | Yes | **Idle-reclaim** if 7-day p95 CPU/net/mem all <20%; **regional A1 capacity** not guaranteed; CC verification at signup. Datacenter IP → higher block risk. |
| **GitHub Actions** (public repo) | Yes, **unlimited minutes** on public repos | No (ephemeral) | **No — 5-min cron floor, best-effort** (10–18 min delays at peak); disabled after 60 days idle | Zero servers to maintain. Cold Imperva solve each run. Datacenter IP. |
| Google Cloud `e2-micro` Always Free | Yes | Yes | Yes | 1 GB RAM is tight for Chromium (run single page, `--disable-dev-shm-usage`). US regions only. |
| Fly.io / Render / Railway free tiers | Reduced/removed in 2024–25 | Spin-down | No | Not reliably "free + always-on" anymore. Not recommended. |
| Cloudflare Workers | Yes | n/a | n/a | **Cannot run full Chromium** for the Imperva solve. Not suitable as the scraper runtime. |

### 3.2 Where to STORE it (durable, growing history, free)

| Option | Free tier | Fit |
|---|---|---|
| **Local SQLite (WAL)** on an always-on box | unlimited (your disk) | Best for a VM/Pi: no write quota, fast, simple. Back it up off-box. |
| **Turso** (hosted libSQL/SQLite) | 5 GB, 500 M reads, **10 M writes/mo**, 1-day PITR | Best when the runner is ephemeral (GitHub Actions). Use **change-only writes** to stay under 10 M/mo. |
| **Cloudflare R2 / Backblaze B2** | ~10 GB free | Object store for **gzipped raw JSONL snapshots** (full replay / audit). |
| Supabase / Neon (Postgres) | 0.5 GB, pauses when idle | Too small for unbounded history; pausing breaks "don't go stale". |

### 3.3 Keeping it alive (free)
- **Healthchecks.io** (free): each sync pings a URL; if a ping is missed you get
  alerted (dead-man's-switch — solves GitHub's silent skipped-cron problem).
- **systemd** auto-restart (on a VM/Pi) so a crash self-heals.

---

## 4. Recommendation

There is a real trade-off between **truly hands-off** and **truly real-time**, so
the recommendation is tiered. All three are completely free.

### 🥇 Best overall: self-hosted worker on an always-on box you control
If you have (or can get) a Raspberry Pi 4/5, mini-PC, or a spare always-on
machine, this is the strongest option:
- **Free forever**, no cloud account, no capacity/reclaim risk, you control uptime.
- **Residential IP** — materially lower Imperva block risk than any datacenter
  IP, so **no paid proxy needed** (the one thing that otherwise costs money).
- Can poll **every 1–2 min** with a warm session → genuinely near-real-time.
- Local SQLite (no write limits) + nightly backup to R2/B2/Turso.

### 🥈 Best cloud (no hardware): Oracle Cloud Always Free Ampere A1 VM
- Always-on VM (start with 1 OCPU/6 GB), warm session, in-process scheduler,
  sub-minute capable.
- Mitigate the documented risks: choose a home region with A1 capacity; real
  every-few-minutes polling provides legitimate load (lowers idle-reclaim odds);
  `systemd` auto-restart; **back up off-box** so a reclaim is recoverable, not
  catastrophic; datacenter IP means run conservative cadence + auto re-solve.

### 🥉 Zero-maintenance fallback: GitHub Actions (public repo) + Turso
- Two cron workflows: **full snapshot every 12 h**, **price every ~15 min**
  (`*/15`, offset to `:07/:22/:37/:52` to dodge top-of-hour delays).
- Push to **Turso** (change-only writes) + raw gzip to **R2**; Healthchecks.io
  dead-man's-switch; a tiny weekly heartbeat commit avoids the 60-day disable.
- Honest limitation: **~10–15 min price latency, not sub-5-min** (GitHub's 5-min
  floor + best-effort scheduling + cold Imperva solve each run). Great for "fresh
  twice-hourly + full archive", not for catching a bid that changes in 60 s.

**Net recommendation:** run the worker on **your own always-on box if available
(🥇)**, else **Oracle Always Free (🥈)**. Use **GitHub Actions (🥉)** as the
zero-ops option or as a redundant backup runner. In every case: **local
SQLite/Turso for queryable data + object storage for raw snapshots + a
dead-man's-switch monitor.**

---

## 5. Proposed architecture

```
            ┌──────────────────────── always-on worker ────────────────────────┐
            │  warm IaaiSession (solve Imperva once, reuse; re-solve on block)   │
            │                                                                    │
   12h ───► full_snapshot():  BranchIds=ON, PageSize=1000  ──► parse ALL fields  │
            │     • upsert lots (status, all price fields)                       │
            │     • write gzip raw JSONL  ──────────────────────► object storage │
            │     • mark lots not seen (past auction) as SOLD/CLOSED             │
            │                                                                    │
  ~Nmin ─► price_refresh(): BranchIds=ON, PageSize=1000 ──► read price fields    │
            │     • append to price_history ONLY when a price changed            │
            │     • bump lots.last_price_at / current price columns              │
            │     • near a lot's close time → poll that branch every 1–3 min     │
            │                                                                    │
            │  every cycle: ping Healthchecks.io (dead-man's-switch)             │
            └────────────────────────────────────────────────────────────────────┘
                          │                                   │
                  SQLite (WAL) / Turso              gzip snapshots (R2/B2)
                          │
                   read-only FastAPI  (existing api.py, + history endpoints)
```

### 5.1 Schema additions (archive + price history)

```sql
-- lots: add lifecycle + richer current price (keep existing columns)
ALTER TABLE lots ADD COLUMN status         TEXT DEFAULT 'active';  -- active|sold|closed|removed
ALTER TABLE lots ADD COLUMN timed_high_bid REAL;                   -- TimedAuctionHighestBidAmountValue
ALTER TABLE lots ADD COLUMN winning_bid    REAL;                   -- WinningbidAmount
ALTER TABLE lots ADD COLUMN bid_status     TEXT;                   -- PrebidItemStatusDesc
ALTER TABLE lots ADD COLUMN closes_at      TEXT;                   -- BidItemClosingDateUTC
ALTER TABLE lots ADD COLUMN last_price_at  TEXT;                   -- when any price last moved
ALTER TABLE lots ADD COLUMN final_price    REAL;                   -- best-known sold value
ALTER TABLE lots ADD COLUMN closed_at      TEXT;

-- append-only price time series (the historical record of what price did)
CREATE TABLE price_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_number TEXT NOT NULL,
    observed_at  TEXT NOT NULL,               -- our capture time (UTC)
    server_at    TEXT,                        -- ServerCurrentDateUTC, for accuracy
    price_type   TEXT NOT NULL,               -- prebid | timed_bid | buy_now | winning | offer
    amount       REAL,
    run_id       INTEGER,                     -- FK -> crawl_runs.id
    UNIQUE(stock_number, observed_at, price_type)
);
CREATE INDEX idx_price_hist_stock ON price_history(stock_number, observed_at);

-- crawl_runs: distinguish run types
ALTER TABLE crawl_runs ADD COLUMN run_type TEXT DEFAULT 'full';   -- full | price
```

- **Old lots are never deleted.** When a lot with a past `auction_datetime_utc`
  stops appearing in a full snapshot → `status='sold'` (or `closed`), set
  `final_price` from the last `winning_bid`/`timed_high_bid`/`high_prebid`, set
  `closed_at`. It stays queryable forever = the archive.
- **price_history is append-on-change**, so storage grows with *real* price
  movement, not with poll frequency. This keeps us comfortably under Turso's 10 M
  writes/month even at a 5-min cadence (and unlimited on local SQLite).

### 5.2 New API endpoints (read-only, additive)
- `GET /lots/{stock}/price-history` — the full price time series for a lot.
- `GET /lots?status=sold&sold_from=…&sold_to=…` — query the sold archive.
- `GET /stats/freshness` — last full + last price sync time, lots updated, lag.

---

## 6. Honest limitations / decisions to confirm

1. **"Almost real-time" on free infra ≈ 1–3 min** (own box / Oracle) or **~15 min**
   (GitHub Actions). Sub-minute, 24/7, isn't sustainable for free without raising
   block risk. Recommend tightening cadence **only during live auction windows**.
2. **Sold price for live-lane sales** may be approximate (best observed bid), since
   anonymous users don't see live-lane hammer prices. Timed/online auctions give
   accurate winning bids.
3. **ToS / anti-bot:** higher frequency = higher detection + ToS exposure. Keep a
   sane floor, jitter, warm-session reuse, auto re-solve, and monitor block rate.
   A residential IP (own box) is the single biggest reliability win and is free.
4. **Pick one storage path** based on runtime: local SQLite (VM/Pi) vs Turso
   (GitHub Actions). Both back up raw snapshots to object storage.

---

## 7. Suggested implementation phases (build order)

1. **Capture more price fields now**: extend `parser.py`/`models.py`/`storage.py`
   with `timed_high_bid`, `winning_bid`, `bid_status`, `closes_at` (no infra change).
2. **Ontario-at-source + big pages**: add `BranchIds`/`PageSize=1000` fast path to
   `search_client.py` for cheap refreshes.
3. **price_history table + change-only writer** and **sold/closed detection** in
   the crawler.
4. **Two-cadence scheduler** (`APScheduler`) with a warm session + live-window
   tightening; `systemd` unit + Healthchecks.io ping.
5. **Durability**: nightly DB backup + gzip raw snapshots to R2/B2 (and/or Turso
   mirror).
6. **API**: add price-history / sold-archive / freshness endpoints.
7. **Deploy** to the chosen runtime (own box → Oracle → GitHub Actions).
