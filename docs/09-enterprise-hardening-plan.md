# Enterprise Hardening and Multi-Source Implementation Plan

## 1. Objective

Turn the current IAA Ontario collector into a reliable ingestion and serving
foundation for a dealership inventory syndication and historical-market platform.
The public website must always read the last verified snapshot from our database;
it must never depend on a live source-site request. The scraper is the **only**
(unofficial) bridge to the source site; the UI can only point at and pull from
our API — it has no ability to scrape.

This plan distinguishes **release gates** (required before production) from scale
features that should be added only after measured demand.

## 2. Non-negotiable release gates

1. **Fail-closed completeness** — an empty, short, duplicated, malformed, or
   partially parsed response fails/quarantines the run; it never archives inventory.
2. **Durable history** — current state, price observations, lifecycle transitions,
   raw payloads, and crawl provenance survive restarts and deployments.
3. **Safe serving boundary** — authentication, TLS/reverse proxy, least-privilege
   command access, bounded queries, redacted responses, and freshness-aware readiness.
4. **Restore proof** — automated backups are not considered complete until a restore
   test succeeds.

### Current implementation status (2026-07-16)

Completed in the staging baseline (PR #6, reviewed 2026-07-16; 111 offline tests
pass on Python 3.11/3.13):

- fail-closed response validation, exact count reconciliation, anomalous-page retries,
  non-zero scheduler exit codes, and OS-owned crawl locking;
- raw-before-parse landing, page/parse DLQ metadata, two-run miss confirmation,
  separate status history, online SQLite backup, and non-destructive E2E isolation;
- separate read/command tokens, raw redaction, bounded filters/history queries,
  keyset cursors, and freshness-aware readiness;
- media pointers (`image_url`) + on-demand thumbnail cache behind the private API;
- captured-payload field coverage audit and Python 3.11/3.13 CI matrix.

Still blocking production:

- **Phase 0 review fixes below** (small, contained corrections to PR #6);
- written collection/retention/redistribution authorization;
- versioned off-host object storage plus scheduled, monitored backup/restore drills;
- reverse-proxy TLS, rate/body limits, network policy, centralized secrets, and audit logs;
- durable external scheduler/worker, telemetry/alerts, load/failure testing, and an
  authorized three-run live canary;
- PostgreSQL/source-aware identity before onboarding a second website.

## 3. Target architecture

```text
source adapters -> immutable raw landing -> validation/DLQ -> normalization
       -> append-only observations -> current-inventory projection
       -> private API/BFF -> dealership website / internal tools / analytics
```

Each future source implements the same adapter contract:

- `discover()` — enumerate source partitions and authoritative counts;
- `fetch_page(cursor)` — return payload plus source metadata;
- `validate(payload)` — enforce source response contracts;
- `normalize(row)` — produce canonical vehicle/lot/price observations;
- `checkpoint()` — persist durable progress and retry state.

The canonical identity is `(source, external_lot_id)`. A separate optional
`canonical_vehicle_id` links cross-source records only when VIN or a sufficiently
strong evidence set supports the match.

### Media architecture (decided)

Images follow the Marketplace pattern: **search/list responses are metadata +
URL pointers only; pixels are a lazy, viewport-scoped side channel.**

- The crawl stores `image_url` text and never downloads binaries (crawl latency
  and anti-bot exposure unchanged).
- The API returns a stable local pointer (`thumbnail_href`); the UI lazy-loads
  the ~20–30 visible thumbs first and more on scroll.
- `GET /lots/{stock}/thumbnail` serves bytes: disk-cache hit, else one
  allowlisted upstream fetch, then cached for every later viewer.
- Full galleries are never scraped into results — pointer-only, fetched on
  demand only if a later product task needs them.
- This is confirmed as the right long-term shape; remaining work is hardening
  (Phase 0 items 0.2/0.5/0.6 and doc 10 Phase 1.5), not redesign. An optional
  post-crawl warmer may pre-fetch thumbs for the first page of active lots
  (sorted by `auction_date`) so the default UI view never pays a cold miss.

## 4. Data requirements and provenance

### Required current-inventory domains

- identity: source, external lot ID, stock number, VIN/masked VIN;
- vehicle: year, make, model, trim, body style, engine, transmission, fuel;
- condition: odometer/unit/source, damage, title/brand, run/start/keys;
- location: source branch, canonical location, province;
- auction: auction ID/type/date, lane/run, close time, eligibility;
- price signals: prebid, timed bid, buy-now, offer, winning bid;
- lifecycle: active, conditional, passed, sold, removed, reappeared;
- provenance: crawl run, observed time, source server time, raw object/checksum.

Every price must include `price_type`, `amount`, `currency`, `observed_at`,
`source_observed_at`, and a confidence/provenance classification. A high bid or
disappeared listing must never be represented as a confirmed sale price.
`server_observed_at` is already parsed onto lots; Phase 0 item 0.3 carries it
into `price_history` before real history accumulates.

Price history is **sampling-based**: a row is appended only when an observed
value changes, so the recorded curve is bounded by crawl cadence (see §6).
"Unchanged" and "not observed" are distinguished via `lots.last_seen`, not via
history rows.

Raw payload retention is the schema-drift safety net. A field does not have to be
promoted immediately to remain recoverable, but the coverage audit must identify all
unconsumed fields for product review.

## 5. Implementation phases

### Phase 0 — PR #6 review fixes (do first; amend PR #6 or one follow-up PR)

Small, contained corrections found in the 2026-07-16 code review. They either sit
on the API hot path or become expensive to retrofit once real history accumulates.

| # | Status | Fix | Where | Acceptance |
|---|---|---|---|---|
| 0.1 | ✅ done | **Run DDL/migrations once at startup, not per request.** `_store()` builds a new `SqliteStore` per request and `__init__` always executed DDL + `_migrate()` — including the `INSERT OR IGNORE … FROM price_history` backfill scan and an `UPDATE lots …` — i.e. write statements and a growing-table scan on every read, contending with a running crawl for the WAL write lock. | `iaai_scraper/storage.py` (`SqliteStore.__init__`, `_SCHEMA_READY` memo), `iaai_scraper/api.py` (`_lifespan`) | Schema work runs once per path per process; request-path connections are write-free; `test_schema_setup_runs_once_per_path`. |
| 0.2 | ✅ done | **Thumbnail fetch must not follow redirects off the allowlist.** `urlopen` followed 3xx by default, so the host allowlist only constrained the first hop — contradicted doc 10 §6's stated SSRF mitigation. | `iaai_scraper/images.py` (`_RefuseRedirects`, `_default_fetch`) | All redirects refused (3xx → `ImageFetchError`); `test_default_fetch_refuses_redirects` uses a local 302 server. |
| 0.3 | ✅ done | **Add `source_observed_at` and `currency` columns to `price_history`** (mandated by §4), populated from `Lot.server_observed_at` / `Lot.currency`. | storage DDL, `_MIGRATIONS`, `_record_price_changes` | `test_price_history_carries_provenance`; idempotent migration. |
| 0.4 | ✅ done | **Stop dual-writing `status_*` rows into `price_history`.** `lot_status_history` is the only lifecycle log; legacy `status_*` rows are filtered out of `GET /lots/{stock}/price-history`. | `storage._record_status_change`, `get_price_history`/`count_price_history` | Price-history responses contain only real price types; `test_legacy_status_rows_hidden_from_price_history`. |
| 0.5 | ✅ done | **Thumbnail cache hardening:** per-key single-flight lock (N concurrent first viewers → one upstream fetch); negative caching of upstream failures (default 10 min); stale-on-error (serve last good thumb when a refetch fails); `evict()`/`sweep()` with removed-lot eviction + TTL sweep wired into API startup. | `iaai_scraper/images.py`, `api._lifespan`, config `IAAI_IMAGE_NEGATIVE_TTL` / `IAAI_IMAGE_CACHE_TTL` | Meets doc 10 N5; concurrency, negative-cache, stale-serve, and evict/sweep tests. |
| 0.6 | ⏳ open | **No tokens in logs.** `?api_key=` lands in uvicorn/proxy access logs; add log redaction and replace with short-lived signed media URLs before any UI GA. | logging config / gateway; later `auth.py` | Grep of access logs shows no token material. |
| 0.7 | ✅ done | **`/readyz` reachable by probes.** `IAAI_READYZ_PUBLIC=true` exempts the route from auth (it exposes only counts/timestamps). | `auth.require_readyz_auth` | `test_readyz_public_env_allows_unauthenticated_probe`. |
| 0.8 | ✅ done | **Fix `sold_from`/`sold_to` semantics** — they filtered `status_updated_at` for *any* status; now they also require a concluded outcome. | `storage._build_where` | `test_sold_filter_excludes_non_concluded_status_changes`. |
| 0.9 | ✅ done | **Tie `--canada-wide` page-size default to the flag** (legacy verified max is 100, not 1000). | `cli.py`, `api.CrawlCommand.resolved_page_size` | CLI + API tests cover both modes and explicit override. |
| 0.10 | ✅ done | **Normalize `StockNum` identically** in duplicate-page detection and the parser (whitespace differences previously defeated the duplicate guard). | `crawler._fetch_page_with_recovery` | str+strip normalization matches `parser._str`. |
| 0.11 | ◐ partial | **In-container crawl.** Added a `/dev/shm` tmpfs so Chromium has shared memory under `read_only: true`. Live verification of `POST /commands/crawl` inside the container remains open. | `docker-compose.yml` | Crawl completes inside the container (manual check). |
| 0.12 | ⏳ deferred | Unchanged lots still get a full ~60-column row rewrite (including the raw blob) every crawl; update only `last_seen`/`missing_run_count` on the unchanged path. | `storage.upsert_lot` | Revisit at multi-source scale; harmless at 1.5k lots. |

**Exit:** suite green (123 tests as of 2026-07-16); read path proven write-free;
redirect, eviction, negative-cache, and single-flight tests pass. Remaining:
0.6 (gateway/log redaction + signed media URLs) and the 0.11 manual container check.

### Phase A — Recovery and correctness baseline ✅ (landed in PR #6)

- Recover the price-history, Ontario-at-source, API security, and Docker commits.
- Replace destructive E2E cleanup with isolated temporary data directories.
- Make failed, partial, and unknown-completeness CLI jobs exit non-zero.
- Validate response shape before treating JSON as search results.
- Retry anomalous empty/short/duplicate pages, then fail loudly.
- Require exact authoritative-count reconciliation and zero parse anomalies before
  marking a crawl complete or considering archival.
- Replace stale file locking with an OS-owned lock and guaranteed cleanup.

**Exit:** all offline tests pass; fault-injection tests prove anomalous pages cannot
produce a completed run or archive a lot.

### Phase B — History and durability ✅ core landed; remainder tracked

- Keep append-only `price_history` and separate `lot_status_history`. ✅
- Require at least two consecutive verified misses before delisting a lot. ✅
- Reset miss counters when a lot reappears. ✅
- Persist malformed rows and invalid page payloads to a DLQ with run/page/reason. ✅
- Create online SQLite backups before mutation. ✅ Retention and checksum manifests: open.
- Upload raw snapshots and backups to versioned object storage. (open — P0)
- Add a one-time baseline backfill from surviving raw archives. (open)
- Price provenance columns (Phase 0 item 0.3). (open)

**Exit:** migration preserves the existing lot count; price/status transitions are
queryable; backup/restore and reappearance tests pass.

### Phase C — Private production API ✅ core landed; gateway work open

- Omit raw source payloads by default and constrain explicit raw retrieval. ✅
- Use separate read and command credentials; keep command routes off the public edge. ✅
- Reject invalid status, sort, branch, and pagination values with 4xx responses. ✅
- Add stable keyset/cursor pagination with stock number as the tie-breaker. ✅
- Readiness requires populated data and a recent completed crawl. ✅
- Put TLS, request rate limits, body limits, access logs, and IP policy at the reverse
  proxy/API gateway. (open — P0)
- Add versioned response schemas and deprecation policy. (open)

**Exit:** API contract tests, authorization tests, load tests, and stale-data tests pass.

### Phase D — Multi-source/PostgreSQL migration

- Introduce source registry and `(source, external_lot_id)` keys.
- Split source lots, canonical vehicles, observations, prices, lifecycle events, and
  crawl pages into explicit tables.
- Backfill IAA records under a stable source ID.
- Migrate serving/storage to PostgreSQL using versioned migrations.
- Add make/model/trim normalization and confidence-scored entity resolution.
- Use PostgreSQL trigram/full-text search first; add a search engine only if measured
  latency or relevance requires it.

**Exit:** two source adapters can ingest overlapping identifiers without collision;
cross-source matches remain explainable and reversible.

### Phase E — Operations and scale

- Run ingestion as durable scheduled jobs/queue workers, not API-process background tasks.
- Implement the adaptive crawl schedule (§6), including sale-window tightening.
- Partition jobs by source and branch; use conservative source-specific concurrency.
- Add structured logs, Prometheus/OpenTelemetry metrics, dashboards, and paging alerts.
- Add deployment health checks, rolling rollback, database PITR, object lifecycle rules,
  proxy cost controls, and secrets rotation.
- Run periodic schema-drift, null-rate, price-confidence, and archive-consistency audits.

**Exit:** canary and rollback drills, restore drills, and failure-injection exercises meet
the agreed SLOs.

## 6. Refresh cadence and scheduling

An Ontario-at-source crawl is ~2–3 HTTP requests plus one Chromium challenge
solve (~21 s wall). The challenge solve — not the data volume — is the expensive
and detection-sensitive part, so cadence policy is about how often we solve, and
about concentrating frequency where price signal actually moves.

**Recommended policy:**

- **Inventory baseline: every 4–6 hours** (4–6 runs/day). This meets the 12-hour
  freshness SLO and — because archival requires **two consecutive** completed
  misses — bounds removal-detection latency at two crawl intervals (≤ 12 h at a
  6-hour cadence). Crawling less often than every 6 h silently breaks the
  freshness SLO through the two-miss rule.
- **Sale windows: tighten to every 15–30 minutes** only while lots are closing.
  `bid_closes_at` and `auction_datetime_utc` are stored; the scheduler should
  tighten when any tracked active lot closes within the next N hours, and relax
  afterwards. This is what makes fluctuating auction prices land in
  `price_history` as usable bid curves without hammering the source all day.
- **Run from a real scheduler** (cron/systemd timer invoking the CLI, which exits
  non-zero on anything but `completed`), not `POST /commands/crawl`. The
  in-process job manager is for manual syncs; it has no durability or retry
  across restarts.
- **Whole-run retry belongs to the scheduler:** on a failed run, retry once after
  15–30 minutes; after two consecutive failures, alert and stop retrying until a
  human looks (repeated challenge solves against a blocking source make things
  worse, and the API keeps serving the last verified snapshot regardless).
- Jitter start times; never run two crawls concurrently (the OS lock already
  enforces this).

## 7. Retention and archival rules

Explicit answers to "how long do we keep things":

| Data | Rule | Rationale |
|---|---|---|
| `lots` rows (incl. `sold`/`if_bid`/`passed`/`removed`) | **Keep indefinitely.** Lots are never deleted; concluded lots get `delisted_at` stamped and drop out of active scans. | The historical sold archive *is* the market-data product. Storage cost is trivial (~KBs/lot). |
| `price_history` / `lot_status_history` | Keep indefinitely (append-only). | Same; the whole point of the platform. |
| Raw JSONL snapshots (`data/raw/`) | Keep **90 days hot on disk**, then move to versioned off-host object storage (P0 package); retain ≥ 12 months there. Never delete a snapshot that is the only evidence for a disputed record. | Replay/audit safety net; local disk stays bounded. |
| DLQ files | Same lifecycle as raw snapshots; alert when a DLQ file is non-empty. | A quiet DLQ is a monitoring bug. |
| Pre-migration DB backups (`data/backups/`) | Keep the 5 most recent locally; all copies off-host. | Bounded local disk. |
| Scheduled DB snapshots | Per RPO in §11: 15-minute WAL-friendly snapshots off-host, daily retained 30 days, monthly retained 12 months, **restore-tested**. | Backups without restore proof don't count (§2.4). |
| Thumbnail cache | Evict on lot `removed`/delisted and on source-URL change; TTL sweep (e.g. 30 days) + max-size cap. (Phase 0 item 0.5.) | Bounded by *active* inventory, not full history. |
| Correction/takedown | Legal-defined workflow (P0) must be able to purge a specific lot's raw rows, cached thumbs, and API visibility while logging the action. | Compliance gate. |

## 8. Error handling, retries, and fallbacks

Current behavior (verified in code review) and the target state:

| Layer | Today | Verdict / change |
|---|---|---|
| Transport (`session.post_json`) | Retries `MAX_RETRIES=4` with exponential backoff + jitter; re-solves the Imperva session on block pages; in-page fetch has its own timeout with `AbortController`. | ✅ Sound. |
| Page anomalies (empty/short/duplicate windows) | `PAGE_RECOVERY_RETRIES=2` re-fetches with polite delay; every anomalous window is DLQ'd; persistent empty/duplicate while incomplete → run fails. | ✅ Sound and fail-closed. |
| Response-contract violations (`SearchResponseError`) | DLQ'd and the run fails **immediately, without recovery retries**. | Acceptable (fail-closed), but give contract errors the same 1–2 recovery attempts as page anomalies — a transiently truncated JSON body shouldn't cost a whole run. |
| Bad rows (parse failure) | Row is DLQ'd and skipped, and any bad row marks the run `failed` (never archives). Rows are **not** individually retried. | ✅ Correct — parse failures are deterministic; retrying the row is pointless. The whole-run retry (§6) is the retry. |
| Whole run | CLI exits non-zero on anything but `completed`; failed runs never archive; a later good run recovers with no manual repair. | ✅ Data-safe. Scheduler-level retry/alerting per §6 is the missing piece (P0 durable-worker package). |
| Run finalization (raw close / run record / DB close) | Each failure is captured into the report note and forces `failed`; lock is always released; archival shares the run-record transaction and rolls back together. | ✅ Sound. |
| API when data is stale/missing | Keeps serving the last verified snapshot; `/readyz` flips 503 (`inventory_empty`, `no_completed_crawl`, `stale`). | ✅ This *is* the fallback: serve-stale + signal. UI should surface `last_completed_at` from `/stats/freshness` as a "data as of …" badge rather than hiding staleness. |
| Thumbnails | Single attempt; upstream failure → 502; no negative cache; no stale-serve. | Change per Phase 0 item 0.5: single-flight, negative cache, stale-on-error. UI additionally falls back to a placeholder. |

## 9. Caching strategy

- **Do not add an external cache layer yet.** SQLite reads at 1.5k lots are
  sub-millisecond once Phase 0 item 0.1 removes the per-request migration work —
  that fix *is* the biggest cache-equivalent win available.
- **In-process micro-cache (cheap, optional):** `/filters` and `/stats` run
  multiple full-table `DISTINCT`/`GROUP BY` scans per call and change only when
  a crawl commits; cache their responses in-process for 60 s or invalidate on
  crawl completion.
- **HTTP cache headers:** thumbnail route already sends `Cache-Control` — keep
  ≥ 24 h. Add `Cache-Control: private, max-age=30–60` to `/lots` list responses
  once the UI exists; lot data changes at crawl cadence, not per request.
- **CDN/edge:** only in front of the thumbnail route, only after the UI ships
  and only with the signed-URL scheme (Phase 0 item 0.6), so tokens never reach
  edge logs.
- **What not to cache:** `/readyz`, `/commands/*`, and anything derived from
  job state.

## 10. Test strategy

### Every change

- parser/model/storage/API unit tests;
- static compilation and migration tests;
- captured-payload contract tests;
- raw-field and normalized-field coverage audit;
- security/authentication tests;
- no-network crawler tests with deterministic fakes.

### Fault-injection matrix

Test HTTP errors, timeouts, block pages, valid JSON with missing keys, empty middle pages,
truncated short pages, repeated first/last pages, total-count drift, browser crashes, raw
disk-full errors, SQLite busy/locked errors, process cancellation, stale locks, parse
failures, missing branches, and clock skew. Add (Phase 0): thumbnail 302 to a
non-allowlisted host, concurrent thumbnail misses, upstream image failure with a
stale cached copy present, and reads issued during an active crawl transaction.

Every failure case must prove:

- the run is not reported complete;
- the process/API job exposes a failure or partial state;
- existing inventory is not archived;
- raw/DLQ evidence remains available;
- a later good run can recover without manual database repair.

### Authorized live canary

Run only after commercial authorization. Start with one Ontario branch and compare:

- authoritative total versus unique captured stock numbers;
- branch and lifecycle counts versus the source UI;
- every raw key versus the promoted-field catalog;
- critical null rates versus the prior accepted baseline;
- selected price/status records against manually verified examples.

Expand to all branches only after three consecutive clean canaries. Never run a live
test against the production database; promote its verified output after validation.

## 11. Proposed service levels

These are starting targets and should be confirmed with product stakeholders:

- verified inventory crawl completeness: 100% of the authoritative scoped count or fail;
- critical parser failure rate: 0%; noncritical null-rate drift: alert at +5 percentage points;
- API availability: 99.9% monthly, excluding planned maintenance;
- API latency: p95 under 250 ms for indexed inventory queries;
- inventory freshness: under 12 hours (implies ≤ 6-hour crawl cadence via the
  two-miss archival rule, §6); price freshness during configured sale windows:
  under 15 minutes (implies sale-window tightening, §6);
- database RPO: 15 minutes; raw archive RPO: one completed crawl; RTO: 4 hours;
- false archival tolerance: zero known cases.

## 12. Deployment sequence

1. Land Phase 0 fixes (amend PR #6 or an immediate follow-up).
2. Merge to a staging branch and migrate a copy of the existing database.
3. Replay surviving raw data into a baseline history and run coverage audits.
4. Run the complete offline/fault-injection suite.
5. Build the hardened container, verify the in-container crawl (Phase 0 item 0.11),
   and restore a backup into a clean environment.
6. After authorization, run branch-scoped live canaries into an isolated database.
7. Shadow the website API without exposing results publicly.
8. Enable internal users, then a limited public cohort, then full traffic.
9. Keep the last verified deployment and database snapshot available for rollback.

## 13. Remaining product decisions

- exact refresh cadence and acceptable staleness — **recommended default: 4–6 h
  baseline + 15–30 min sale-window tightening (§6)**; confirm with stakeholders;
- which source fields and images may **legally** be displayed (technical media
  approach is decided — §3 media architecture and
  [`10-implementation-plan.md`](./10-implementation-plan.md): store URL pointers,
  serve thumbs via our API with on-demand cache; crawl never downloads binaries);
- definition of a confirmed sale versus inferred bid signal;
- retention overrides to the §7 defaults, and the correction/takedown workflow
  (including cached thumbs);
- public versus dealer-only access to VIN, raw fields, and historical prices;
- first additional source used to validate the adapter architecture.

## 14. Verified staging evidence

The surviving 2026-06-26 raw snapshot was audited offline after recovery:

- 3,984 source rows and 3,984 unique stock numbers;
- zero duplicate stock numbers and zero parser failures;
- 1,247 Ontario lots across all seven configured branches;
- 1,051 active, 102 sold, 87 conditional, and 7 passed records;
- zero nulls in stock number, year, make, model, and branch ID;
- 89 source fields observed, with 60 consumed by normalization and 34 unconsumed
  buyer/UI/locale fields retained in raw storage for future review.

Migration and restore validation preserved all 1,247 normalized lots and returned
`PRAGMA integrity_check=ok`. The offline suite has **111 passing tests** on
Python 3.11 and 3.13 (2026-07-16, including the media/thumbnail tests). This
proves captured-payload behavior, not current source-site behavior; the
authorized live canary remains a production gate.

## 15. Prioritized implementation work packages

| Order | Work package | Primary owner | Depends on | Acceptance evidence |
|---|---|---|---|---|
| **P0** | **Phase 0 PR #6 review fixes (§5 Phase 0 table)** | backend | PR #6 | follow-up merged; write-free read path; redirect/eviction/single-flight tests |
| P0 | Data license/authorization and retention/display rules | legal + product | source selection | signed terms and field/image policy |
| P0 | Off-host versioned raw/DB backups, retention (§7), checksums, restore job | platform | storage account | scheduled restore succeeds and meets RPO/RTO |
| P0 | TLS gateway, rate/body limits, command-route network isolation, managed secrets, log redaction | platform/security | deployment environment | external security and abuse tests pass; no tokens in logs |
| P0 | Durable scheduled worker with §6 cadence policy, retry/alerting, structured metrics | backend/platform | scheduler/queue choice | restart/fault tests preserve job state; failed runs alert |
| P0 | Authorized three-run branch/all-Ontario canary and manual source reconciliation | data QA | authorization + staging | exact totals, branches, schema, prices, and status samples |
| P1 | Adaptive sale-window scheduling (tighten on `bid_closes_at` horizon) | backend | durable worker | price-history density meets the 15-min sale-window SLO |
| P1 | PostgreSQL canonical schema with source-aware keys and versioned migrations | backend/data | second-source choice | collision/backfill/rollback tests pass |
| P1 | Adapter SDK, fixture contract, checkpoint/DLQ conventions | data engineering | canonical schema | two adapters pass the same conformance suite |
| P1 | Website BFF, public response schemas, dealer/public RBAC, lazy viewport thumbs, "data as of" freshness badge | web/backend/security | private API + media route + gateway | load/auth/privacy/contract tests pass |
| P2 | Search relevance, entity resolution, price analytics and confidence labels | data/product | two-source history | explainable match and pricing-quality benchmarks |

P0 items are release blockers and should be completed in order where dependencies
require it. P1 is required before the second marketplace is onboarded. P2 should be
driven by measured dealer workflows and archive quality rather than scraper volume.

## 16. Actionable next plan

The sequenced implementation plan (media boundary, UI integration, and remaining
P0 gates) lives in
[`10-implementation-plan.md`](./10-implementation-plan.md).
Use that document for task checklists and acceptance criteria; keep this file as
the enterprise release-gate and multi-source architecture source of truth.
