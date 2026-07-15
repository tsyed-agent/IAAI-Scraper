# Enterprise Hardening and Multi-Source Implementation Plan

## 1. Objective

Turn the current IAA Ontario collector into a reliable ingestion and serving
foundation for a dealership inventory syndication and historical-market platform.
The public website must always read the last verified snapshot from our database;
it must never depend on a live source-site request.

This plan distinguishes **release gates** (required before production) from scale
features that should be added only after measured demand.

## 2. Non-negotiable release gates

2. **Fail-closed completeness** — an empty, short, duplicated, malformed, or
   partially parsed response fails/quarantines the run; it never archives inventory.
3. **Durable history** — current state, price observations, lifecycle transitions,
   raw payloads, and crawl provenance survive restarts and deployments.
4. **Safe serving boundary** — authentication, TLS/reverse proxy, least-privilege
   command access, bounded queries, redacted responses, and freshness-aware readiness.
5. **Restore proof** — automated backups are not considered complete until a restore
   test succeeds.

### Current implementation status (2026-07-15)

Completed in the staging baseline:

- recovered historical price/archive and API-security work from the unmerged branch;
- fail-closed response validation, exact count reconciliation, anomalous-page retries,
  non-zero scheduler exit codes, and OS-owned crawl locking;
- raw-before-parse landing, page/parse DLQ metadata, two-run miss confirmation,
  separate status history, online SQLite backup, and non-destructive E2E isolation;
- separate read/command tokens, raw redaction, bounded filters/history queries,
  keyset cursors, and freshness-aware readiness;
- captured-payload field coverage audit and Python 3.11/3.13 CI matrix.

Still blocking production:

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

Raw payload retention is the schema-drift safety net. A field does not have to be
promoted immediately to remain recoverable, but the coverage audit must identify all
unconsumed fields for product review.

## 5. Implementation phases

### Phase A — Recovery and correctness baseline

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

### Phase B — History and durability

- Keep append-only `price_history` and separate `lot_status_history`.
- Require at least two consecutive verified misses before delisting a lot.
- Reset miss counters when a lot reappears.
- Persist malformed rows and invalid page payloads to a DLQ with run/page/reason.
- Create online SQLite backups before mutation; add retention and checksum manifests.
- Upload raw snapshots and backups to versioned object storage.
- Add a one-time baseline backfill from surviving raw archives.

**Exit:** migration preserves the existing lot count; price/status transitions are
queryable; backup/restore and reappearance tests pass.

### Phase C — Private production API

- Omit raw source payloads by default and constrain explicit raw retrieval.
- Use separate read and command credentials; keep command routes off the public edge.
- Reject invalid status, sort, branch, and pagination values with 4xx responses.
- Add stable keyset/cursor pagination with stock number as the tie-breaker.
- Readiness requires populated data and a recent completed crawl.
- Put TLS, request rate limits, body limits, access logs, and IP policy at the reverse
  proxy/API gateway.
- Add versioned response schemas and deprecation policy.

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
- Partition jobs by source and branch; use conservative source-specific concurrency.
- Add structured logs, Prometheus/OpenTelemetry metrics, dashboards, and paging alerts.
- Add deployment health checks, rolling rollback, database PITR, object lifecycle rules,
  proxy cost controls, and secrets rotation.
- Run periodic schema-drift, null-rate, price-confidence, and archive-consistency audits.

**Exit:** canary and rollback drills, restore drills, and failure-injection exercises meet
the agreed SLOs.

## 6. Test strategy

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
failures, missing branches, and clock skew.

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

## 7. Proposed service levels

These are starting targets and should be confirmed with product stakeholders:

- verified inventory crawl completeness: 100% of the authoritative scoped count or fail;
- critical parser failure rate: 0%; noncritical null-rate drift: alert at +5 percentage points;
- API availability: 99.9% monthly, excluding planned maintenance;
- API latency: p95 under 250 ms for indexed inventory queries;
- inventory freshness: under 12 hours; price freshness during configured sale windows:
  under 15 minutes;
- database RPO: 15 minutes; raw archive RPO: one completed crawl; RTO: 4 hours;
- false archival tolerance: zero known cases.

## 8. Deployment sequence

1. Merge to a staging branch and migrate a copy of the existing database.
2. Replay surviving raw data into a baseline history and run coverage audits.
3. Run the complete offline/fault-injection suite.
4. Build the hardened container and restore a backup into a clean environment.
5. After authorization, run branch-scoped live canaries into an isolated database.
6. Shadow the website API without exposing results publicly.
7. Enable internal users, then a limited public cohort, then full traffic.
8. Keep the last verified deployment and database snapshot available for rollback.

## 9. Remaining product decisions

- exact refresh cadence and acceptable staleness;
- which source fields and images may legally be displayed;
- definition of a confirmed sale versus inferred bid signal;
- retention period and correction/takedown workflow;
- public versus dealer-only access to VIN, raw fields, and historical prices;
- first additional source used to validate the adapter architecture.

## 10. Verified staging evidence

The surviving 2026-06-26 raw snapshot was audited offline after recovery:

- 3,984 source rows and 3,984 unique stock numbers;
- zero duplicate stock numbers and zero parser failures;
- 1,247 Ontario lots across all seven configured branches;
- 1,051 active, 102 sold, 87 conditional, and 7 passed records;
- zero nulls in stock number, year, make, model, and branch ID;
- 89 source fields observed, with 60 consumed by normalization and 34 unconsumed
  buyer/UI/locale fields retained in raw storage for future review.

Migration and restore validation preserved all 1,247 normalized lots and returned
`PRAGMA integrity_check=ok`. The current offline suite has 101 passing tests on both
Python 3.11 and 3.13. This proves captured-payload behavior, not current source-site
behavior; the authorized live canary remains a production gate.

## 11. Prioritized implementation work packages

| Order | Work package | Primary owner | Depends on | Acceptance evidence |
|---|---|---|---|---|
| P0 | Data license/authorization and retention/display rules | legal + product | source selection | signed terms and field/image policy |
| P0 | Off-host versioned raw/DB backups, retention, checksums, restore job | platform | storage account | scheduled restore succeeds and meets RPO/RTO |
| P0 | TLS gateway, rate/body limits, command-route network isolation, managed secrets | platform/security | deployment environment | external security and abuse tests pass |
| P0 | Durable scheduled worker, cancellation/retry state, structured metrics and alerts | backend/platform | scheduler/queue choice | restart/fault tests preserve job state and page ops |
| P0 | Authorized three-run branch/all-Ontario canary and manual source reconciliation | data QA | authorization + staging | exact totals, branches, schema, prices, and status samples |
| P1 | PostgreSQL canonical schema with source-aware keys and versioned migrations | backend/data | second-source choice | collision/backfill/rollback tests pass |
| P1 | Adapter SDK, fixture contract, checkpoint/DLQ conventions | data engineering | canonical schema | two adapters pass the same conformance suite |
| P1 | Website BFF, cache policy, public response schemas and dealer/public RBAC | web/backend/security | private API + gateway | load/auth/privacy/contract tests pass |
| P2 | Search relevance, entity resolution, price analytics and confidence labels | data/product | two-source history | explainable match and pricing-quality benchmarks |

P0 items are release blockers and should be completed in order where dependencies
require it. P1 is required before the second marketplace is onboarded. P2 should be
driven by measured dealer workflows and archive quality rather than scraper volume.
