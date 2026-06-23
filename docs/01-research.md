# IAAI Canada (Ontario) — Research Findings

> Status: Research phase. This document captures what we learned about the target
> site (`https://ca.iaai.com/`) and the constraints that shape the system design.
> Date of investigation: 2026-06-23.

## 1. Goal recap

Build a reliable data collection system for **IAA / IAAI Canada** vehicle auction
listings, scoped initially to **Ontario only**, that:

- Collects **structured lot data** (all available lot parameters).
- Stores it in a **searchable** store.
- Exposes it through an **internal API** for query / filter / retrieval.
- Optimises for **completeness, speed, reliability, and cost**.
- **Skips images for now** (save space and bandwidth).

## 2. Target site profile

| Property | Finding |
|---|---|
| Base URL | `https://ca.iaai.com/` |
| Search page | `https://ca.iaai.com/Search` (filters incl. **Province**, Branch, Auction Date, Make/Model/Year, Vehicle Type, Category) |
| Lot detail page | `https://ca.iaai.com/VehicleDetails/{stockNumber}` (server-rendered) |
| Branch pages | `https://ca.iaai.com/auctions/locations` |
| Backend stack | ASP.NET MVC 5.2 on Microsoft IIS 10 (`x-aspnetmvc-version: 5.2`, `x-powered-by: ASP.NET`) |
| CDN / WAF | **Imperva (Incapsula)** — confirmed via `x-cdn: Imperva`, `visid_incap_*`, `incap_ses_*` cookies |
| CSRF | `__RequestVerificationToken` cookie issued by the Search page (needed for POST/AJAX search calls) |
| Official API | **None.** IAAI does not publish a public auction API. |

### How data is served

The search page is a server-rendered ASP.NET MVC page. Result rows and filter
values are populated via authenticated **AJAX/POST calls** that require:

- A valid Incapsula session (the JS-challenge cookies, see below).
- The `__RequestVerificationToken` (anti-CSRF) value, sent as a request header
  alongside the matching cookie.
- ASP.NET session cookies (`ASP.NET_SessionId`, `IAAIT`).

Lot detail pages are directly reachable at `…/VehicleDetails/{stockNumber}` and
return full HTML (server-rendered), so per-lot enrichment can be done by fetching
the detail page and parsing it — no separate authenticated API call is strictly
required for the public fields.

### The Province filter is the key lever

The Search UI exposes a **Province** filter. This is exactly what we need: we can
constrain collection to Ontario at the source (Province = Ontario, and/or by the
specific Ontario branch list below) instead of scraping all of Canada and
filtering client-side. This keeps request volume and storage low.

## 3. Anti-bot reality (the main engineering risk)

The site is fronted by **Imperva/Incapsula**. Behaviour observed during research:

- One bare `curl` to `/Search` returned a full 181 KB page (lucky pass).
- A subsequent identical request returned the **classic Incapsula JS challenge**:
  a 950-byte page loading `/_Incapsula_Resource?...` inside an iframe with the text
  `Request unsuccessful. Incapsula incident ID: …`.

**Conclusion:** plain HTTP clients (`curl`, Python `requests`) are **not reliable**.
Incapsula uses a JavaScript interrogation (the `___utmvc` / `reese84` family of
challenges) that mints session cookies (`visid_incap_*`, `incap_ses_*`,
`nlbi_*`). Those cookies are bound to the IP and browser fingerprint that solved
the challenge. Requests without a freshly-solved, fingerprint-consistent session
get blocked (often a 200 OK containing a block page, not an HTTP 403).

### Implications for the scraper

To pass the challenge reliably we need **one of**:

1. **Headless browser** (Playwright / Puppeteer, ideally with stealth patches)
   that executes the challenge JS and yields valid cookies. We can then either
   keep driving the browser, or **harvest the cookies + token and replay them
   with a fast HTTP client** for the high-volume list/detail calls (recommended
   for speed + cost — browser only for the handshake).
2. **A managed anti-bot / scraping API** (e.g. Scrapfly, ZenRows, ScraperAPI,
   Bright Data, Oxylabs) that solves Incapsula for us and returns HTML/JSON.
   Higher per-request cost, far less maintenance, best reliability.
3. A **custom Incapsula solver** (reverse-engineer `_Incapsula_Resource`). Highest
   effort and brittle; not recommended.

Additional requirements that fall out of Imperva:
- **Residential / good-reputation proxies** with rotation. Datacenter IPs (incl.
  this cloud VM, `18.118.243.20`) are flagged quickly.
- **Session reuse**: keep the same IP + cookies for the life of a solved session;
  rotate the whole session together, never the IP mid-session.
- **Human-like pacing**: throttling, jitter, and modest concurrency.

## 4. Ontario footprint (sizing the job)

IAAI Canada Ontario branches (from `ca.iaai.com/auctions/locations`):

| Branch | Location | Auction cadence |
|---|---|---|
| London (ON) | 1900 Gore Road, London | Wednesdays |
| Toronto (Oshawa) | 535 Wentworth St W, Oshawa | Tuesdays |
| Toronto North | 16505 Hwy 48, Stouffville | weekly |
| Toronto West | 13726 Airport Rd, Caledon East | ~daily |
| Hamilton | 406 Lake Ave North, Hamilton | weekly |
| Ottawa | 6160 Thunder Road, Ottawa | weekly |
| Garson (Sudbury) | 90 National Street, Garson | weekly |

≈ **7 Ontario branches**. Each branch typically runs a few hundred to ~1,000+
lots per weekly sale, and inventory turns over continuously.

### Rough volume estimate

- **Active Ontario inventory at any moment:** ~3,000–10,000 open lots (order of
  magnitude; to be confirmed by reading the live "total results" count once the
  scraper can pass the challenge).
- **Per-lot record size (no images):** ~30–60 structured fields ≈ **2–4 KB** of
  JSON each.
- **One full Ontario snapshot:** ~5,000 lots × 3 KB ≈ **~15 MB**.
- **Growth over time** (continuous auctions + sale-price history): roughly
  thousands of new/changed lots per week → **hundreds of thousands of rows/year**.

This volume estimate directly drives the **storage decision** in
[`03-storage-decision.md`](./03-storage-decision.md).

## 5. Legal / ToS note (flagged, not legal advice)

IAAI's Terms of Use prohibit automated access, and the site actively defends with
Imperva. This is an **internal** data-collection effort; before scaling we should:

- Review IAAI ToS and `robots.txt`, and document the decision/risk-acceptance.
- Prefer the **lowest request volume** that meets the need (Ontario-only, dedup,
  incremental updates, no images).
- Consider whether a **licensed data provider** (Carapis, Apibara, etc.) is more
  appropriate / lower-risk for production use — they resell normalized IAAI data
  via clean JSON APIs and remove the anti-bot burden entirely. Worth a
  build-vs-buy comparison (see plan).

## 6. Key takeaways feeding the design

1. No official API → scrape the Search results + lot detail pages.
2. Imperva is the dominant technical risk → budget for a **browser-based
   challenge solve** and/or a **managed scraping API**, plus **residential
   proxies**. This is the make-or-break component.
3. Province filter lets us scope to Ontario at the source → small, cheap dataset.
4. Volume is modest now but grows unbounded over time and the explicit goal is
   "**searchable**, filterable" → flat JSON alone is not the right serving layer
   (see storage decision).
