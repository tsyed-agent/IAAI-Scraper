# IAAI-Scraper

Data collection system for **IAA / IAAI Canada** vehicle auction listings
(`https://ca.iaai.com/`), scoped initially to **Ontario**. The goal is to collect
structured auction lot data, store it in a **searchable** store, and expose it
through an **internal API** for querying, filtering, and retrieval — optimised for
completeness, speed, reliability, and cost. Images are intentionally **not**
collected for now.

> Current status: **research & planning**. No scraper code yet — see the design
> docs below.

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/01-research.md`](docs/01-research.md) | Target-site profile, how data is served, **Imperva anti-bot** findings, Ontario branch footprint, volume sizing, legal note. |
| [`docs/02-architecture-and-plan.md`](docs/02-architecture-and-plan.md) | System architecture, components, tech stack, and a phased build roadmap. |
| [`docs/03-storage-decision.md`](docs/03-storage-decision.md) | **Answer to "just JSON for now, or a database?"** |
| [`docs/04-data-model.md`](docs/04-data-model.md) | Proposed lot schema, SQLite DDL, upsert pattern, Postgres notes. |

## Key conclusions so far

- **No official IAAI API.** Data must be collected from the `Search` results and
  `VehicleDetails/{id}` pages.
- **Imperva/Incapsula protects the site.** Plain `curl`/`requests` is unreliable
  (intermittent JS challenge). The make-or-break component is a **browser-based
  challenge solve** (Playwright) and/or a **managed scraping API**, plus
  **residential proxies**. *(Confirmed live during research.)*
- The site exposes a **Province filter**, so we can scope to Ontario at the
  source — keeping the dataset small and cheap (~7 Ontario branches, an estimated
  few-thousand active lots ≈ ~15 MB per full snapshot).
- **Storage:** a single flat JSON file is fine only for a throwaway first
  snapshot. The product needs a database. Recommended: **raw JSON Lines (audit /
  replay) + SQLite (MVP) → PostgreSQL (scale)** behind a **FastAPI** read API.

## Proposed stack

Python · Playwright (anti-bot) · httpx · Pydantic · SQLite→PostgreSQL · FastAPI.

## Next step

De-risk the anti-bot in a **Phase 0 spike**: reliably pass Incapsula and pull one
Ontario `Search` page plus a few lot detail pages, then decide self-hosted
Playwright vs. a managed scraping API based on success rate and cost.
