# Implementation Plan: Dealership Platform Boundary + Media Serving

> Status: **plan complete — ready to implement.**  
> Linked specs: [`09-enterprise-hardening-plan.md`](./09-enterprise-hardening-plan.md),
> [`06-realtime-archive-infrastructure.md`](./06-realtime-archive-infrastructure.md),
> [`04-data-model.md`](./04-data-model.md), [`02-architecture-and-plan.md`](./02-architecture-and-plan.md).  
> Staging baseline: PR [#6](https://github.com/tsyed-agent/IAAI-Scraper/pull/6) (doc 09 Phases A–C code).

## 1. Overview

Complete the path from the hardened collector/API to a dealership-facing product
boundary: the **website never scrapes IAAI**; it only calls **our API**. Lot
metadata is already served from SQLite. Media must follow the same rule using
**pointers + optional on-demand thumbnail cache**, without putting image fetches
on the crawl critical path.

This plan turns chat decisions and doc 09 release gates into sequenced,
acceptance-tested work packages.

## 2. Requirements summary

### Functional

| ID | Requirement |
|---|---|
| F1 | Crawl stores structured lots + `image_url` (source thumbnail URL text). No image binaries in crawl. |
| F2 | List/detail API returns a **stable local media pointer** (`thumbnail_href`) the UI can call. |
| F3 | `GET /lots/{stock}/thumbnail` serves bytes: cache hit from disk, else fetch source URL once, store thumb, serve. |
| F4 | Full / remaining gallery photos are **not** scraped into results; keep source pointers only if present later; load on demand if product needs them. |
| F5 | Frontend talks only to our API (lots + thumbnail endpoint). Scraper remains the only unofficial bridge to IAAI search/HTML. |
| F6 | Production still requires doc 09 P0 gates (legal, off-host backup, TLS/gateway, durable worker, live canary). |

### Non-functional

| ID | Requirement |
|---|---|
| N1 | Crawl latency unchanged by media (no image I/O in crawler). |
| N2 | List API p95 stays under ~250 ms for indexed queries (media is async/out-of-band). |
| N3 | Thumbnail cache: small files only; allowlisted CDN hosts; size/timeout caps; atomic writes. |
| N4 | Auth: thumbnail route requires the same read token (header and/or `api_key` query for `<img>`). |
| N5 | Storage growth bounded: thumbs for active/recent lots; evict when lot removed or source URL changes. |

### Explicitly out of scope (this plan)

- Downloading full photo galleries during crawl.
- Hotlinking IAAI from the public website as the long-term design (allowed only as a temporary Tier-A fallback while cache is built).
- PostgreSQL / second marketplace (doc 09 Phase D — after P0).

## 3. Technical approach

```text
Crawl (unchanged hot path)
  → store lots + image_url pointer in SQLite
  → no image binaries

Private API
  GET /lots…           → metadata + thumbnail_href
  GET /lots/{id}/thumbnail
        → cache hit? serve local thumb
        → miss? validate allowlisted image_url → fetch → cache → serve

Dealership UI / BFF
  → loads lot JSON from our API (instant)
  → lazy-loads first viewport thumbs via thumbnail_href
  → never calls ca.iaai.com / Imperva
```

### Decisions locked by this plan

1. **Pointers first** — `image_url` remains the source of truth URL in DB.
2. **Local href for UI** — API adds `thumbnail_href` (e.g. `/lots/{stock}/thumbnail`).
3. **On-demand cache (Tier B)** — first viewer may pay fetch latency; everyone after hits disk.
4. **Crawl never waits on images.**
5. **Full photos** — pointer-only until a later task explicitly adds gallery URLs + on-demand fetch.

### Open (legal/product — still P0)

- Written authorization to display/cache IAAI imagery.
- Retention and takedown for cached thumbs.
- Whether public pages may show images vs dealer-only.

Until legal clears production display, implement behind the private API and keep
cache **off by default in public deployments** (`IAAI_IMAGE_CACHE=false` or
equivalent) if required by policy.

## 4. Implementation phases

### Phase 0 — Spec alignment (this document) ✅

- [x] Capture media pointer/cache design in-repo.
- [x] Sequence against doc 09 P0/P1 packages.
- [x] Define acceptance criteria and tests.

### Phase 1 — Media API (implement next)

**Goal:** UI can render thumbs via our API without scraping or hotlinking.

| Task | Owner | Acceptance |
|---|---|---|
| 1.1 Config: cache dir, enable flag, allowlisted hosts (`anvis.iaai.com`, …), max bytes, fetch timeout, TTL/eviction knobs | backend | env documented in `.env.example` |
| 1.2 `ThumbnailCache` module: validate URL host/scheme, atomic write, sidecar source URL for invalidation, size/type checks | backend | unit tests with mocked HTTP; no network in CI |
| 1.3 `GET /lots/{stock_number}/thumbnail` — 404 if no lot/URL; 502 on upstream failure; `Cache-Control` on hits | backend | API tests for hit/miss/deny-host/missing |
| 1.4 Auth: reuse read token; accept `api_key` query on thumbnail GET only | backend | auth tests |
| 1.5 `_hydrate`: add `thumbnail_href`; keep `image_url` for operators | backend | list/detail contract tests |
| 1.6 README + data-model note: crawl stores pointers; API may cache thumbs on demand | docs | README limitations updated |
| 1.7 Docker: cache under `/data/image_cache` on existing volume | platform | compose env wired |

**Status (2026-07-16):** Phase 1 implemented on branch — see checklist §8.

**Exit:** offline tests green; curling `/lots/{stock}/thumbnail` with a fixture URL populates cache once and serves thereafter without re-fetch (mocked).

### Phase 2 — Production P0 gates (doc 09 §11)

Do **not** block Phase 1 on these, but production traffic stays gated.

| Task | Acceptance |
|---|---|
| 2.1 Legal/auth + field/image display policy | signed terms; written thumb-cache/display rules |
| 2.2 Off-host versioned backups + restore drill | RPO/RTO met |
| 2.3 TLS gateway, rate/body limits, secrets, audit logs | abuse tests pass |
| 2.4 Durable scheduler/worker + metrics/alerts | restart preserves job state |
| 2.5 Authorized 3-run live canary | exact totals/branches/prices vs source |

### Phase 3 — Website BFF / UI integration

| Task | Acceptance |
|---|---|
| 3.1 Lot list loads metadata first; lazy-load viewport thumbs (≈20–30) via `thumbnail_href` | Lighthouse/network: list JSON not blocked on images |
| 3.2 Detail page: thumb immediately; gallery only if product adds gallery pointers later | no scrape from browser |
| 3.3 Optional BFF cache headers / CDN in front of thumbnail route | repeat views served from edge |

### Phase 4 — Multi-source / Postgres (doc 09 Phase D)

Unchanged: `(source, external_lot_id)`, adapter SDK, second site — **after** Phase 2.

## 5. Dependencies

```text
Phase 0 (plan) ──► Phase 1 (media API) ──► Phase 3 (UI)
                         │
doc 09 staging (PR #6) ──┘
                         │
                    Phase 2 (P0 legal/ops) ──► production cutover
                         │
                    Phase 4 (Postgres / 2nd source)
```

- Phase 1 depends on existing `image_url` column (already shipped).
- Phase 3 depends on Phase 1.
- Production depends on Phase 2 (especially image legal policy before public display).

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Hotlink / CDN blocks or ToS violation | Private API cache; legal gate before public; allowlist hosts only |
| SSRF via malicious `image_url` | Allowlist hosts; HTTPS only; no redirect off-allowlist; size cap |
| Cache disk growth | Thumbs only; evict on URL change / removed lots; optional TTL |
| `<img>` cannot send Bearer header | `api_key` query on thumbnail route only |
| First-thumb latency spike | Accept for cold miss; UI placeholder; optional warm first page later |
| Scope creep into full galleries | Explicitly out of Phase 1; separate task if needed |

## 7. Success criteria

- Crawl runtime and request count unchanged when cache enabled.
- `GET /lots` returns `thumbnail_href` whenever `image_url` is present.
- Thumbnail endpoint never fetches non-allowlisted URLs.
- Cache miss → one upstream GET → durable local file → subsequent hits are local.
- UI can ship a list page that never contacts `ca.iaai.com`.
- Doc 09 P0 checklist still required before production.

## 8. Tracking checklist (copy into issues/PRs)

### Phase 1 — Media API
- [x] 1.1 Config + `.env.example`
- [x] 1.2 `ThumbnailCache` + unit tests
- [x] 1.3 Thumbnail route
- [x] 1.4 Auth (`api_key` query)
- [x] 1.5 `thumbnail_href` on hydrate
- [x] 1.6 README / model docs
- [x] 1.7 Docker volume path

### Phase 1.5 — Media hardening (from doc 09 Phase 0 review fixes)
- [x] 0.2 Redirects refused in `images._default_fetch` (3xx → `ImageFetchError`)
- [x] 0.5a Per-key single-flight lock (concurrent misses → one upstream fetch)
- [x] 0.5b Negative caching of upstream failures (`IAAI_IMAGE_NEGATIVE_TTL`, default 10 min)
- [x] 0.5c Stale-on-error: serve last good thumb when refetch fails
- [x] 0.5d Eviction: `evict()`/`sweep()` — removed lots + TTL sweep at API startup (`IAAI_IMAGE_CACHE_TTL`)
- [x] 0.6a Access-log `api_key` redaction (`logging_utils.AccessLogTokenRedactor` on `uvicorn.access`)
- [x] 0.6b Signed short-lived media URLs (`?expires=&sig=` on `thumbnail_href`)
- [ ] Optional: post-crawl warmer for first-page active-lot thumbs (no cold miss on default view)

### Phase 2 — Production P0
- [ ] 2.1 Legal / image policy
- [ ] 2.2 Off-host backup/restore
- [ ] 2.3 Gateway / secrets
- [x] 2.4a Thin scheduler wrapper (`scripts/scheduled_crawl.sh` + `docs/ops-scheduling.md`) — full queue worker still open
- [ ] 2.4b Durable queue worker + metrics/alerts
- [ ] 2.5 Live canary

### Phase 3 — UI
- [ ] 3.1 Lazy viewport thumbs
- [ ] 3.2 Detail media behavior
- [ ] 3.3 Optional CDN in front of thumbs

### Phase 4 — Scale
- [ ] Postgres + source identity
- [ ] Adapter SDK
- [ ] Second marketplace
