# Regulator Revenue Collector

A weekly batch job that downloads publicly-published gaming-revenue reports
from US state regulators and republishes them as a structured JSON archive
that the project's Next.js website renders at `/regulator-reports`.

## Why

The five-year story of US iGaming and sports-wagering revenue is told one
weekly Excel file at a time, scattered across state regulator websites that
re-organize their URL schemes every few years. This collector is the public,
open-data mirror of those reports — a stable archive that anyone can use
to chart trends without hand-scraping each regulator every Monday morning.

It also doubles as a worked example for **Chapter 25b — Regulatory Reporting
and Evidence Export**, which discusses the operator-side equivalent: shipping
your own data to regulators in their preferred shape. This collector inverts
the flow (regulator → public) using the same primitives.

## Architecture

```
run.py                    ← entry point, runs all collectors concurrently
collectors/
  base.py                 ← StateCollector: streaming download + retries + hash
  ny.py                   ← NewYorkCollector (slug-predictable URLs)
  nj.py                   ← NJ DGE / PA PGCB / MI MGCB stubs (TODO: scrape)
parsers/
  excel.py                ← top-N rows from the first xlsx worksheet
  pdf.py                  ← largest table on page 1 via pdfplumber
output/
  snapshots/{STATE}.json  ← committed to repo so diffs are visible
  {state}/*.{pdf,xlsx}    ← raw downloads (gitignored)
tests/
  test_models.py          ← model invariants
  test_ny_collector.py    ← URL-construction unit tests (no network)
```

Each state is a plug-in (subclass of `StateCollector`). Adding a new state
means writing one file in `collectors/` and appending it to
`ALL_COLLECTORS` in `collectors/__init__.py`.

## Running locally

```bash
uv run --with-requirements requirements.txt python run.py --only NY
```

Outputs:

- `output/ny/del-lago-resort-and-casino-weekly.pdf` and friends — raw downloads
- `output/snapshots/NY.json` — structured snapshot
- `../../../website/public/data/regulator-revenue/NY.json` — same file, in the
  Next.js site's `public/data/` so the site reads it at build time

Add `--no-parse` to skip the Excel/PDF summarisers (3-4× faster; useful in CI
when you only need to refresh the file inventory).

## Jurisdictional scope

| State | Online Casino | Sports Betting | Coverage in this collector |
|-------|---------------|----------------|----------------------------|
| **NY** | ❌ Not permitted | ✅ Mobile + retail | Commercial casino + sports + video gaming reports |
| **NJ** | ✅ Permitted (since 2013) | ✅ Mobile + retail | Stub — DGE press-release scraping pending |
| **PA** | ✅ Permitted (since 2019) | ✅ Mobile + retail | Stub — PGCB CSV download pending |
| **MI** | ✅ Permitted (since 2021) | ✅ Mobile + retail | Stub — MGCB monthly PDF pending |

NY's omission of online casino is itself the story: only retail casinos and
mobile sports wagering are reported; no iCasino numbers appear because no
iCasino operates legally in NY today.

## Weekly cadence

The GitHub Actions workflow at
`.github/workflows/regulator-revenue-collect.yml` runs every Monday at 12:00
UTC, executes `run.py`, commits the refreshed JSON snapshots, and triggers
the website deploy that picks up the new data.
