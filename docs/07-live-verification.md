# Live Verification — Ontario-at-Source Crawl

> Verified on **2026-06-26** from this VM against `https://ca.iaai.com/`.

## Command

```bash
python -m iaai_scraper.cli crawl -v
# or re-check an existing DB:
python scripts/verify_ontario_crawl.py
```

## Results (2026-06-26)

| Check | Result |
|---|---|
| Scope | Ontario-only via `BranchIds=10,52,56,61,64,70,71` |
| Page size | 1000 |
| Pages fetched | **2** (1000 + 453 rows) |
| Wall time | ~21 s (challenge + 2 fetches) |
| Authoritative total | 1453 |
| Ontario lots stored | **1453** |
| Crawl status | `completed` |
| Duplicates | 0 |
| Bad rows | 0 |
| Archived | 0 (first run on empty DB) |

### By branch

| Branch | Lots |
|---|---|
| Toronto (Oshawa) | 477 |
| Hamilton | 226 |
| London | 218 |
| Ottawa | 170 |
| Toronto North | 142 |
| Toronto West | 141 |
| Sudbury | 79 |

## Comparison to legacy Canada-wide crawl (2026-06-24)

| Metric | Legacy | Ontario-at-source |
|---|---|---|
| Requests | ~53 pages | **2 pages** |
| Rows fetched | 5218 (all Canada) | 1453 (Ontario only) |
| Wall time | ~4m 24s | **~21s** |
| Ontario lots | 1448 | 1453 |

Inventory counts differ slightly day-to-day (live churn); the optimized path is ~**12× faster** and fetches only Ontario rows at the source.

## Automated verification

`scripts/verify_ontario_crawl.py` asserts:

- ≥ 1300 Ontario lots in DB
- All lots have Ontario `branch_id`
- Last crawl status is `completed`
- Ontario-at-source uses ≤ 5 pages
- Extended parser fields (`image_url`) populated on ≥ 95% of lots

## Re-run after parser changes

```bash
python -m iaai_scraper.cli crawl -v
python scripts/verify_ontario_crawl.py
python -m pytest -q
```
