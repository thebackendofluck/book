# Regulator Pipeline Repair Plan — 2026-06-08

**Summary:** 22 collectors diagnosed. **13 quick wins** (trivial/easy, high confidence — apply first), **8 moderate/hard** (URL/markup/format/anti-scraping work), **1 infeasible** (MX — no public data). Of the 13 quick wins, 12 are code-only fixes (parser tag, unit ×100, UA header, signature, regex/markup, dedup) and 1 (FR) is a no-op-now staleness with a small forward-proofing tweak. NO and FR are "no current fix needed" but carry latent probe bugs grouped under quick wins. Two large online markets (IT, IN, MA, UK) sit in the quick-win tier — do those first for maximum market-weighted impact.

Difficulty tally by `fix_difficulty`: trivial=2 (MA, GR), easy=12 (IT, OH, IN, MT, FR, NO, NL, BE, UK, ON, WV, RO, PT — note PT/IT/BE counted in easy), moderate=5 (IL, DE, AZ, TN, MI), hard=1 (VA), infeasible=1 (MX).

---

## Quick wins (trivial/easy, high confidence) — apply first

These are isolated, well-understood code fixes (parser metric tag, unit scaling, UA header, function signature, regex/markup, dedup). No new parsers required. High confidence on all.

| State | Root cause | Fix | File | Expected value |
|-------|-----------|-----|------|----------------|
| **IT** | Multi-row label merge captures only col[0] of first fragment → sports rows ("Gioco a base ippica/sportiva") dropped; duplicate verticals collide on ON CONFLICT, negative igaming overwrites positive | (1) Accumulate label fragments from all rows into `pending_label`; (2) aggregate duplicate (vertical, period, metric_name) facts by summing before emit | `parsers/it_metrics.py` (~L223-270, post-loop dedup) | 2023-12 total ≈ **$4.52B** (igaming ≈$2,586M, sports ≈$1,671M, bingo ≈$69M, poker ≈$191M) |
| **OH** | (1) `_to_cents()` treats whole-dollar cells as cents (100× too small); (2) collector probes dead per-month URL pattern — OCCC switched to annual cumulative files w/ Cloudinary hash | (1) Add `×100` in both branches of `_to_cents()`; (2) replace `list_reports()` per-month probe with hardcoded annual-file list (2024/2025 PDF, 2026 XLSX) + current-year probe | `parsers/oh_metrics.py` (L110-119), `collectors/oh.py` | OH sports GGR ~$890M FY2024; monthly state GGR $55M–$115M after ×100 |
| **IN** | Casino sheet emits `metric_name="win"` not `"ggr"` (invisible to GGR query); AGR regex `\brevenue\b` too broad; duplicate IN dispatch block | Change `"win"`→`"ggr"` (L129); tighten `_AGR_RE` to drop `\brevenue\b` (L42); delete duplicate IN block (backfill.py L695-701) | `parsers/in_metrics.py`, `backfill.py` | Casino Win ~$150–200M/mo; sports AGR ~$20–40M/mo; ~500 casino + ~360 sports GGR facts |
| **MA** | `parse_ma_pdf(path, operator, source_url)` is 3-arg but `operator_arg=True` calls with 4 args → TypeError swallowed → 0 facts | Add `vertical: str` param to signature (ignore in body) | `parsers/ma_metrics.py` (L59) | ~$5M–$20M/operator/mo; aggregate ~$40–$60M/mo across ~10 operators |
| **MT** | `_detect_report_type()` runs on cache slug (no "report"/year) → always "unknown"; image-heavy PDFs fail text fallback; 4× vertical duplication | Detect on `source_url.rsplit('/',1)[-1]` instead of `path.name` (L393); emit one ReportFile per PDF in collector | `parsers/mt_metrics.py`, `collectors/mt.py` | MGA annual ~€6–9B/yr; igaming ~300–500M/mo, sports ~100–180M, poker ~15–36M, lottery ~10–27M |
| **GR** | F5 WAF blocks abbreviated UA strings (403 on PDF downloads); index scrape succeeds but every PDF download fails | Replace custom UA with full browser UA + Referer (gr.py L58); update global httpx client UA (backfill.py L643) | `collectors/gr.py`, `backfill.py` | Total GGR €1.86B (2021) → €2.80B (2024); online share ~33–39% |
| **NL** | INDEX URL now returns 403 (site restructured, bot-blocked); `list_reports()` returns [] | Replace single-INDEX scrape with hardcoded per-report landing page seeds + parse `<a href="*.pdf">`; optionally scrape `/nieuws` feed | `collectors/nl.py` | Online casino ~€97–100M/mo, sports ~€29M/mo; marktscan 2024 casino ~€1,113M + sports ~€353M/yr |
| **BE** | (1) PDF downloads use base-class collector UA → "Remote end closed" (403); (2) 2024 file renamed `2024-KSC_...` defeats year regex | (1) Override `collect()`/client to use Mozilla UA for PDF downloads; (2) add `_LEADING_YEAR_RE = ^(\d{4})-KSC_` as first check in `_detect_data_year` | `collectors/be.py`, `parsers/be_metrics.py` | 2024 total ~€1.1–1.3B (igaming ~€750–800M, sports ~€250–270M, land casino ~€140–155M) |
| **UK** | UKGC slug changed `-q3-`→`-quarter-3-`; PUB_RE only matches `-q(\d)` → Q3 FY2025-26 silently skipped | Extend PUB_RE to `(?:q|quarter-)(\d)` (L29-33); add Q3 FY2025-26 XLSX to `_HARDCODED_URLS` (L47-60) | `collectors/uk.py` | Q3 FY2025-26 Total GGY £4.5B; remote casino ~£1.3–1.5B/quarter |
| **ON** | igamingontario.ca TLS cert expired 2026-06-07 → ConnectError swallowed by `except httpx.HTTPError: pass`; `_0` suffix variant not probed | Add `verify=False` to the ON httpx client (backfill.py ~L641); log warning instead of silent pass; probe `_0.xlsx` variant on 404 | `backfill.py`, `collectors/on.py` | ~$30M–$60M/mo across 3 verticals; ~500+ GGR facts (cumulative history back to Apr 2022) |
| **WV** | Collector `a.find_parent()` stops at link `<td>` (no date) → TITLE_RE always fails → 0 ReportFiles; no parser; WV absent from backfill | (1) `a.find_parent("tr")` so date sibling cell is captured (L50-51); (2) create `wv_metrics.py` PDF parser (pdfplumber, igaming + sports); (3) add WV to ALL_STATES + dispatch block | `collectors/wv.py`, `parsers/wv_metrics.py` (new), `backfill.py` | iGaming $10–20M/mo, sports $5–15M/mo; 10 monthly PDFs (Jul 2025–Apr 2026) |
| **RO** | 3-layer URL failure: scrape pages 503, landing page moved, fallback templates probe wrong path | Update `ACTIVITY_REPORTS_URL` to `/relatii-publice/.../rapoarte-de-activitate/`; replace `_KNOWN_ANNUAL_PDF_TEMPLATES` with real URL-encoded `Legea-544` year-keyed paths; drop month dimension | `collectors/ro.py` | ~2,700M RON online casino + ~1,300M RON sports (2023) ≈ $880M USD total |
| **PT** | SRIJ dropped `_jogo` from filename stem (Q3 2022+); collector probe grid, HREF regex, and parser FILENAME_RE all miss new pattern | Make `_jogo` optional in regexes (`(?:_jogo)?`); extend probe grid + hardcoded 2022-07 bulk-migration list; update parser `_FILENAME_RE` (L78) | `collectors/pt.py`, `parsers/pt_metrics.py` | Online GGR 2024 ~€280–350M/yr; per quarter ~€70–90M EUR |

### Latent / no-fix-now (grouped here — high confidence, structural cadence lag only)

| State | Status | Action | File |
|-------|--------|--------|------|
| **FR** | Stale 2024-12 is correct — ANJ 2025 annual data not published until ~Dec 2026 (annual-only cadence) | No immediate fix. Forward-proof: promote `ORG_URL` substring search to primary fetch path, remove hardcoded `DATASET_SLUG` reliance to survive slug rotation | `collectors/fr.py` |
| **NO** | Stale 2024-12 is correct — Norway annual-only, ~13-mo lag; 2025 report won't exist until ~Jan 2027 | No immediate fix. Fix latent probe bug: `upload_year = probe_year + 2` and loop months `('01','02','03')` (L113-116) | `collectors/no.py` |

---

## Moderate (URL / markup / format / new-parser work needed)

These require a new parser module AND/OR backfill wiring AND/OR anti-scraping workarounds. Confidence high on all; effort is moderate because more than a one-liner.

- **IL** (`reachable_data_present`) — No `il_metrics.py` parser; IL absent from backfill dispatch and ALL_STATES. Collector is complete (74 monthly WebForms-POST CSVs, IGB endpoint live). **Needs:** (1) create `parsers/il_metrics.py` to parse AllActivitySportDetail CSV → MetricFact(vertical='sports-wagering', metric_name='ggr'); (2) custom `backfill_il()` mirroring the collector's POST logic (generic GET dispatcher won't work for WebForms POST); (3) add IL to ALL_STATES (L885). CSVs already cached → parser testable immediately. *~740 facts (74 mo × ~10 operators), ~$80–120M/mo statewide.*

- **DE** (`reachable_data_present`) — No `de_metrics.py`; DE absent from backfill if-chain and ALL_STATES. Collector lists ~21 HTML report pages. **Needs:** (1) create `parsers/de_metrics.py` (BeautifulSoup "MONTH ENDING" layout, must extract all 3 operators internally since vertical_arg=True passes only `(path, vertical, source_url)`); (2) dispatch block + ALL_STATES entry. *~$5–15M/mo total, ~$100–200M/yr.*

- **MI** (`blocked_403`) — michigan.gov fronted by Akamai EdgeSuite (403 to all headless clients); no `mi_metrics.py`; MI absent from backfill; vertical-tag bug (`internet gaming` vs URL-encoded `internet%20gaming`). **Needs:** (1) replace michigan.gov scrape with GovDelivery bulletin discovery (`content.govdelivery.com/accounts/MIGCB/bulletins/{hex}` — not Akamai-blocked, same attachments); (2) fix vertical tagging via `urllib.parse.unquote`; (3) create `parse_mi_xlsx(path, vertical, source_url)` (AGR / Gross Sports Betting Revenue, ×100); (4) add MI to backfill + ALL_STATES. *Large market: ~$200–400M/mo combined; ~$3.8B total 2025.* **Priority despite moderate effort — see execution order.**

- **AZ** (`blocked_403`) — No `az_metrics.py`; AZ absent from backfill; index behind Cloudflare bot-challenge (403). **Needs:** (1) create PDF parser for "EW Website Report" layout (sports-wagering, monthly); (2) backfill dispatch + ALL_STATES; (3) replace HTML-index scrape with direct dated PDF URL construction (`EW%20Website%20Report%20-%20{Mon}%20{YYYY}.pdf`) + full browser headers or Playwright fallback. *~$35–50M/mo.*

- **TN** (`blocked_403`) — No `tn_metrics.py`; TN absent from backfill; tn.gov landing page behind WAF/JS-challenge (TLS handshakes but no HTTP response). **Needs:** (1) create `parse_tn_pdf`/`parse_tn_xlsx` (AGR→ggr, state-aggregated, 'TN Statewide'); (2) backfill dispatch + ALL_STATES; (3) bypass WAF via Playwright fetch OR hardcode DAM URLs (`/content/dam/tn/swac/documents/report/{YYYY}/`). *~$30–60M/mo, ~$400–700M/yr.*

---

## Hard / blocked (full reformat, or no public data)

- **VA** (`reachable_data_present`, **hard**) — Board-meeting PDFs are vector-image slide decks; `pdfplumber.extract_tables()` returns empty (parser docstring acknowledges this). Collector discovers ~21–23 PDFs and dispatch is wired, but `parse_va_pdf()` returns [] for every file. **Needs:** (1) PRIMARY — pdfplumber `table_settings={'vertical_strategy':'lines','horizontal_strategy':'lines'}` + `extract_words()` positional clustering; (2) FALLBACK — integrate `camelot-py` lattice mode in `_parse_page()` after the existing attempt, feed results through existing `_parse_operator_major/_parse_metric_major`; add camelot-py dependency. Some pre-2022 decks are pure image → accept [] for those (genuinely not machine-readable). *~$30–60M/mo AGR market-wide; ~$15–40M GGR per quarterly deck if parsed.*

- **MX** (`reachable_no_data`, **infeasible**) — SEGOB/DGJYS publishes no machine-readable revenue series. Collector and parser are intentional no-ops (documented in docstring). Primary URL returns JS challenge; datos.gob.mx CKAN 404. **No fix possible until SEGOB publishes data.** Backfill dispatch already wired (L784-794, vertical_arg=True) — monitor `gob.mx/segob/documentos` and `datos.gob.mx` for a future "Estadísticas de Juegos y Sorteos" series, then implement `list_reports()` scrape + `parse_mx_pdf()`.

---

## Recommended order of execution (weighted by market size)

Large online markets (MI, IT, FR, UK, IL, IN, MA) carry the most dashboard-visible GGR — prioritize them, but FR/NO are correct-as-is (no current data exists).

**Wave 1 — Largest markets that are quick wins (do first, highest ROI):**
1. **IT** — quick win, ~$4.52B 2023 swing (currently shows ~$0/negative due to overwrite bug). Single highest-value fix.
2. **UK** — quick win (trivial regex), £4.5B/quarter; restores Q3 FY2025-26.
3. **IN** — quick win, restores ~$150–200M/mo casino GGR (currently invisible under "win" tag).
4. **MA** — trivial 1-line signature fix, restores ~$40–60M/mo.

**Wave 2 — Other quick wins (batch all remaining easy/trivial code-only fixes):**
5. GR, NL, BE, ON, OH, MT, RO, PT, WV — apply together; mostly UA/URL/regex/unit one-to-few-liners. (NO + FR forward-proofing tweaks fold in here.)

**Wave 3 — Large markets needing moderate work (new parser + wiring):**
6. **MI** — large market (~$200–400M/mo, ~$3.8B/yr 2025). Moderate effort (GovDelivery bypass + new parser) but high market weight → do before smaller moderate states.
7. **IL** — large market (~$80–120M/mo). New parser + custom POST backfill.

**Wave 4 — Remaining moderate (smaller markets):**
8. DE, AZ, TN — new parsers + wiring + anti-scraping; lower market weight.

**Wave 5 — Hard / blocked:**
9. **VA** — hard (camelot-py integration for image slide decks); schedule after quick wins land.
10. **MX** — infeasible; monitor only, no work until source data appears.

**No action required now (verify only):** FR and NO are correctly stale (annual cadence, next data ~Dec 2026 / ~Jan 2027). Land the latent probe/slug forward-proofing tweaks opportunistically with Wave 2.
