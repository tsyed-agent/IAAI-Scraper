# API schema versioning and deprecation

The IAAI Ontario API uses **header-based schema versioning**. Current clients keep
the same URL paths; the contract is identified by response headers and OpenAPI
models rather than a `/v1` path prefix.

## Current version

| Signal | Value |
|--------|--------|
| Header | `X-API-Version: v1` (on every response, including `/healthz`) |
| OpenAPI | Typed response models `*ResponseV1` / `LotResponseV1` |
| Package constant | `iaai_scraper.api_schemas.API_SCHEMA_VERSION` |

v1 field names match the existing JSON wire format. Renaming or removing a v1
field is a **breaking change** and requires a new schema version (`v2`) plus a
migration window.

## Compatibility rules

1. **Additive changes** (new optional fields, new endpoints) are allowed in v1
   without bumping the version.
2. **Breaking changes** (rename/remove/repurpose a field, change type/meaning,
   remove a route) require a new schema version. Old and new versions must
   coexist until the sunset window ends.
3. Contract tests in `tests/test_api_schemas.py` pin required v1 fields so
   accidental renames fail CI.

## Deprecation policy

| Step | Requirement |
|------|-------------|
| Announce | Mark the field/param/route `deprecated` in OpenAPI; document here |
| Headers | For request knobs still accepted, send `Deprecation: true` and `Sunset` (RFC 8594 HTTP-date) on affected responses |
| Window | **≥ 90 days** between Sunset date and removal (one release cycle minimum) |
| Prefer | Point clients at the replacement (`Link: …; rel="deprecation"` when useful) |

After Sunset, removal is allowed in a minor/major release. Do not silently
change meaning of an existing field name.

## Active deprecations

### Thumbnail `?api_key=` (legacy media auth)

| | |
|--|--|
| Status | **Deprecated**, still accepted |
| Replacement | Short-lived `?expires=&sig=` (preferred for `<img src>`), or `Authorization` / `X-API-Key` headers |
| OpenAPI | Query parameter `api_key` marked `deprecated: true` |
| Response headers | When auth succeeds via `?api_key=` only: `Deprecation: true`, `Sunset: Sat, 01 Nov 2026 00:00:00 GMT` |
| Docs | This file; `iaai_scraper.auth.require_api_auth_flexible` |

Do not embed long-lived tokens in image URLs. Prefer `thumbnail_href` from lot
responses (already signed when API auth is enabled).

## Client checklist

1. Read `X-API-Version` and treat unexpected values as unsupported.
2. Prefer OpenAPI / generated clients bound to `*V1` models.
3. Ignore unknown JSON fields (forward compatibility).
4. Migrate off anything marked deprecated before its `Sunset` date.
