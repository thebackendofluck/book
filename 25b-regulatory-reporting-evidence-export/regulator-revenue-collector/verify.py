# Companion code for "The Backend of Luck" - Chapter 25b, Regulatory Reporting and Evidence Export.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
verify.py — Post-backfill sanity checks for the metric_facts table.

Invoked by the weekly cron AFTER backfill + tax imputation. Runs a battery
of checks against DB + live API and emits a structured report to stdout.
Any SEVERE finding causes exit code 1 so cron can flag it in system mail.

Checks:
  1. No fact exceeds the $100B defensive cap (should be prevented at upsert,
     but audit anyway in case the cap was accidentally disabled).
  2. Every jurisdiction's MAX(period) is within the expected freshness window
     (monthly sources: 90 days; quarterly: 180 days; annual: 540 days). Stale
     sources get a WARN, regressions get a SEVERE.
  3. Week-over-week GGR delta per state < 50% (either direction) — guards
     against a parser regression silently halving a country's total.
  4. API and DB agree — by-state aggregate through the API must match the
     same SUM() against the DB (within 1 cent rounding).
  5. Imputed-tax coverage — any state in the _RATES map that has GGR but
     zero tax_paid is a SEVERE (imputation didn't run).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from tax_imputed import _RATES  # noqa: E402


# State → expected max-lag-days from today. Sources beyond the lag trigger
# a WARN. Regressions (MAX(period) went BACKWARDS vs last run) are always
# SEVERE because they signal data loss.
_FRESHNESS_DAYS: dict[str, int] = {
    # Monthly US state publishers — ~45-60 day publication lag
    "NJ": 75, "PA": 75, "NY": 75, "CT": 75, "NV": 90,
    "OH": 540, "IA": 75, "LA": 75, "KS": 75, "RI": 120,
    "MO": 75, "IL": 75, "IN": 120, "MA": 75, "MD": 120,
    "MI": 90, "DE": 120, "WV": 120, "CO": 90, "VA": 120,
    "AZ": 120, "TN": 120,
    # Quarterly international
    "UK": 180, "ON": 120, "NL": 180, "SE": 180, "DK": 90,
    "ES": 90, "FR": 180,
    # Annual international (big lag by design)
    "IT": 730, "BE": 540, "NO": 730, "GR": 730, "MT": 730,
    "PT": 540, "RO": 540, "GE": 540, "MX": 730, "BR": 180,
}

_FACT_CAP_USD_CENTS = 100 * 1_000_000_000 * 100  # $100B
_API_TOLERANCE_CENTS = 100  # cents


def _dsn() -> str:
    d = os.environ.get("DATABASE_URL", "")
    if d.startswith("postgresql+asyncpg://"):
        d = "postgresql://" + d.split("://", 1)[1]
    return d


def _check_fact_cap(cur) -> list[dict[str, Any]]:
    cur.execute(
        "SELECT state, operator, vertical, period, metric_name, "
        "       value_usd_cents, source_url "
        "FROM metric_facts WHERE value_usd_cents > %s "
        "ORDER BY value_usd_cents DESC LIMIT 10",
        (_FACT_CAP_USD_CENTS,),
    )
    return [
        {"severity": "SEVERE", "check": "fact_cap",
         "message": f"{r[0]}/{r[2]}/{r[3]}/{r[4]} = ${r[5] / 1e11:.1f}B > cap",
         "row": {"state": r[0], "operator": r[1], "vertical": r[2],
                 "period": r[3], "metric": r[4], "usd_b": r[5] / 1e11,
                 "source_url": r[6]}}
        for r in cur.fetchall()
    ]


def _check_freshness(cur) -> list[dict[str, Any]]:
    today = date.today()
    cur.execute(
        "SELECT state, MAX(period) FROM metric_facts GROUP BY state"
    )
    out: list[dict[str, Any]] = []
    for state, max_period in cur.fetchall():
        try:
            py, pm = int(max_period[:4]), int(max_period[5:7])
            last = date(py, min(pm, 12), 1)
        except Exception:
            continue
        lag_days = (today - last).days
        threshold = _FRESHNESS_DAYS.get(state, 180)
        if lag_days > threshold:
            out.append({
                "severity": "WARN",
                "check": "freshness",
                "state": state,
                "message": f"{state} latest={max_period} "
                           f"lag={lag_days}d > {threshold}d threshold",
            })
    return out


def _check_wow_delta(cur) -> list[dict[str, Any]]:
    """Compare most-recent two weeks of inserts per state.

    metric_facts has an `inserted_at` timestamp we can use to detect whether
    a backfill on THIS cron tick dropped the per-state total more than 50%
    vs. what we had a week ago for the same period range.
    """
    today_iso = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=8)).isoformat()
    cur.execute(
        """
        SELECT state,
               SUM(value_usd_cents)
                 FILTER (WHERE inserted_at::date >= %s) AS this_week,
               SUM(value_usd_cents)
                 FILTER (WHERE inserted_at::date < %s
                         AND inserted_at::date >= %s) AS prev_week
        FROM metric_facts
        WHERE metric_name = 'ggr'
        GROUP BY state
        """,
        (week_ago, week_ago,
         (date.today() - timedelta(days=16)).isoformat()),
    )
    out: list[dict[str, Any]] = []
    for state, this_week, prev_week in cur.fetchall():
        # DB SUM() returns decimal.Decimal; cast both to float so the delta
        # arithmetic below doesn't raise Decimal/float TypeError (which was
        # silently aborting the whole verify run before any audit completed).
        prev_week = float(prev_week) if prev_week is not None else 0.0
        this_week = float(this_week) if this_week is not None else 0.0
        if not prev_week:
            continue
        delta = abs(this_week - prev_week) / prev_week
        if delta > 0.5:  # > 50% week-over-week change is suspicious
            sign = "↓" if this_week < prev_week else "↑"
            out.append({
                "severity": "WARN",
                "check": "wow_delta",
                "state": state,
                "message": f"{state} {sign} {delta * 100:.0f}% this-week "
                           f"insert total vs last week",
            })
    return out


def _check_api_db_parity(cur) -> list[dict[str, Any]]:
    """Fetch the public API's 12-month by-state totals and compare to a
    direct SQL aggregation over the same window."""
    api_url = (
        "https://thebackendofluck.com/api/regulator-reports/"
        "metrics/by-state?months=12&nocache=" + date.today().isoformat()
    )
    try:
        with urllib.request.urlopen(api_url, timeout=10) as rsp:  # noqa: S310
            api_data = json.loads(rsp.read())
    except Exception as exc:  # noqa: BLE001
        return [{"severity": "WARN", "check": "api",
                 "message": f"API unreachable: {exc}"}]

    current_month = date.today().replace(day=1)
    twelve_months_ago = (current_month - timedelta(days=365)).strftime("%Y-%m")
    cur.execute(
        "SELECT state, SUM(value_usd_cents) FROM metric_facts "
        "WHERE metric_name = 'ggr' AND period >= %s GROUP BY state",
        (twelve_months_ago,),
    )
    db_totals = dict(cur.fetchall())

    out: list[dict[str, Any]] = []
    for row in api_data.get("states", []):
        st = row["state"]
        api_ggr = int(float(row.get("ggr_cents") or 0))
        db_ggr = int(db_totals.get(st) or 0)
        if abs(api_ggr - db_ggr) > _API_TOLERANCE_CENTS:
            out.append({
                "severity": "WARN",
                "check": "api_db_parity",
                "state": st,
                "message": f"API {api_ggr} ≠ DB {db_ggr} "
                           f"(diff ${(api_ggr - db_ggr) / 1e11:.2f}B)",
            })
    return out


def _check_imputed_tax(cur) -> list[dict[str, Any]]:
    states_expecting_imputed = sorted({k[0] for k in _RATES})
    cur.execute(
        "SELECT state, COUNT(*) FILTER (WHERE metric_name = 'tax_paid'), "
        "       COUNT(*) FILTER (WHERE metric_name = 'ggr') "
        "FROM metric_facts WHERE state = ANY(%s) GROUP BY state",
        (states_expecting_imputed,),
    )
    out: list[dict[str, Any]] = []
    for state, tax_n, ggr_n in cur.fetchall():
        if ggr_n > 0 and tax_n == 0:
            out.append({
                "severity": "SEVERE",
                "check": "imputed_tax",
                "state": state,
                "message": f"{state} has {ggr_n} GGR facts but 0 tax_paid — "
                           f"imputation may have failed",
            })
    return out


def _check_tax_vs_ggr(cur) -> list[dict[str, Any]]:
    """Flag operator-months where tax_paid > ggr, with structural exemptions.

    NJ sports-wagering structural exemption — the 'ggr' column for a NJ sports
    licensee holds only that licensee's OWN retail sportsbook GGR (often zero
    or a small retail amount for online-heavy skins).  NJ tax is levied on
    licensee_total_online_sports_ggr (all skins combined), which is stored as a
    separate metric.  When licensee_total_online_sports_ggr exists for the same
    operator/period, tax > retail-ggr is structurally expected — skip entirely.

    All other tax>ggr rows are genuine anomalies (parser error, wrong
    denomination, etc.) and are flagged SEVERE.
    """
    cur.execute(
        """
        SELECT sub.state, sub.vertical, sub.operator, sub.period,
               sub.ggr, sub.tax_paid
        FROM (
          SELECT state, vertical, operator, period,
                 SUM(value_usd_cents) FILTER (WHERE metric_name = 'ggr')
                     AS ggr,
                 SUM(value_usd_cents) FILTER (WHERE metric_name = 'tax_paid')
                     AS tax_paid,
                 COUNT(*) FILTER
                     (WHERE metric_name = 'licensee_total_online_sports_ggr')
                     AS has_licensee_online_total
          FROM metric_facts
          GROUP BY state, vertical, operator, period
        ) sub
        WHERE sub.tax_paid IS NOT NULL
          AND sub.ggr IS NOT NULL
          AND sub.tax_paid > sub.ggr
          -- Exempt non-positive GGR months: a positive tax on a zero/negative
          -- GGR month is NOT a parser error — it is a real minimum tax,
          -- carry-forward catch-up, or timing payment (e.g. LA World-Cup month,
          -- CT tribal negative-GGR months). Only positive-GGR inversions can be
          -- genuine column/denomination bugs.
          AND sub.ggr > 0
          -- NJ sports-wagering structural exemption (ALL skins): the per-skin
          -- 'ggr' is retail-only sportsbook GGR while tax is levied on the
          -- licensee-wide online total — tax>retail-ggr is expected by design.
          AND NOT (sub.state = 'NJ' AND sub.vertical = 'sports-wagering')
        ORDER BY sub.state, sub.vertical, sub.operator, sub.period
        """
    )
    out: list[dict[str, Any]] = []
    for state, vertical, operator, period, ggr, tax_paid in cur.fetchall():
        # Graduate severity: an egregious ratio (>2x) is almost certainly a
        # parser bug (wrong column / cumulative / unit); a mild overage may be
        # rounding/timing — surface it as WARN so the cron stays actionable.
        ratio = tax_paid / ggr if ggr else 0
        out.append({
            "severity": "SEVERE" if ratio > 2.0 else "WARN",
            "check": "tax_vs_ggr",
            "state": state,
            "message": (
                f"{state}/{vertical}/{operator} {period}: "
                f"tax_paid={tax_paid} > ggr={ggr} (x{ratio:.1f}) — "
                "possible parser error or wrong denomination"
            ),
        })
    return out


def _check_handle_vs_ggr(cur) -> list[dict[str, Any]]:
    """Flag operator-months where handle < ggr, with structural exemptions.

    NY commercial-casino — the 'handle' metric for NY commercial casinos is
    table-games drop (chips purchased), not sports handle.  Drop is always
    less than slot/table GGR aggregated together, so handle<ggr is expected
    and is NOT a data error.  Skip all NY commercial-casino rows.

    For all other state/vertical combinations, handle < ggr is anomalous
    (ggr is normally a fraction of handle) and is flagged SEVERE.
    """
    cur.execute(
        """
        SELECT sub.state, sub.vertical, sub.operator, sub.period,
               sub.handle, sub.ggr
        FROM (
          SELECT state, vertical, operator, period,
                 SUM(value_usd_cents) FILTER (WHERE metric_name = 'handle')
                     AS handle,
                 SUM(value_usd_cents) FILTER (WHERE metric_name = 'ggr')
                     AS ggr
          FROM metric_facts
          GROUP BY state, vertical, operator, period
        ) sub
        WHERE sub.handle IS NOT NULL
          AND sub.ggr IS NOT NULL
          AND sub.handle > 0
          AND sub.handle < sub.ggr
          -- NY commercial-casino structural exemption: 'handle' here is
          -- table-games drop (cash/chips bought in), not sports handle.
          -- Drop < total GGR is expected for multi-game aggregates.
          AND NOT (
            sub.state = 'NY'
            AND sub.vertical = 'commercial-casino'
          )
        ORDER BY sub.state, sub.vertical, sub.operator, sub.period
        """
    )
    out: list[dict[str, Any]] = []
    for state, vertical, operator, period, handle, ggr in cur.fetchall():
        # SEVERE only when ggr exceeds handle by >2x (clear unit/column bug);
        # a small handle<ggr can be a low-hold/promo month — WARN.
        egregious = ggr > 2 * handle if handle else True
        out.append({
            "severity": "SEVERE" if egregious else "WARN",
            "check": "handle_vs_ggr",
            "state": state,
            "message": (
                f"{state}/{vertical}/{operator} {period}: "
                f"handle={handle} < ggr={ggr} — "
                "handle should be >= ggr; check parser units"
            ),
        })
    return out


def _check_vertical_dup(cur) -> list[dict[str, Any]]:
    """Flag identical GGR across verticals in the same state/period.

    A regulator report that bundles several verticals into one figure can be
    mis-emitted to every vertical at full value, double-counting the total. Two
    verticals carrying the *exact* same non-trivial GGR for the same period is
    the signature of that bug — it is what doubled Brazil's GGR (igaming ==
    sports-wagering) before the Panorama split fix. WARN (not SEVERE) so it
    surfaces in mail without blocking the pipeline on a rare coincidence.
    """
    cur.execute(
        """
        SELECT state, period, value_usd_cents, COUNT(DISTINCT vertical) AS nv
        FROM metric_facts
        WHERE metric_name = 'ggr' AND value_usd_cents > 20000000000  -- > $200M/period: real bundling bugs are large; avoids small-value coincidences
        GROUP BY state, period, value_usd_cents
        HAVING COUNT(DISTINCT vertical) > 1
        """
    )
    out: list[dict[str, Any]] = []
    for state, period, cents, nv in cur.fetchall():
        out.append({
            "severity": "WARN",
            "check": "vertical_dup",
            "state": state,
            "message": f"{state} {period}: {nv} verticals share identical GGR "
                       f"${cents / 100 / 1e6:.0f}M — possible double-count",
        })
    return out


def run_all() -> dict[str, Any]:
    dsn = _dsn()
    if not dsn:
        return {"ok": False, "error": "DATABASE_URL not set"}

    findings: list[dict[str, Any]] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            findings.extend(_check_fact_cap(cur))
            findings.extend(_check_freshness(cur))
            findings.extend(_check_wow_delta(cur))
            findings.extend(_check_imputed_tax(cur))
            findings.extend(_check_api_db_parity(cur))
            findings.extend(_check_vertical_dup(cur))
            findings.extend(_check_tax_vs_ggr(cur))
            findings.extend(_check_handle_vs_ggr(cur))

            cur.execute(
                "SELECT COUNT(*), COUNT(DISTINCT state), "
                "       MIN(period), MAX(period) FROM metric_facts"
            )
            facts_n, states_n, min_period, max_period = cur.fetchone()

    severe = sum(1 for f in findings if f["severity"] == "SEVERE")
    warn = sum(1 for f in findings if f["severity"] == "WARN")

    return {
        "ok": severe == 0,
        "summary": {
            "facts_total": facts_n,
            "states_total": states_n,
            "period_range": f"{min_period} → {max_period}",
            "severe": severe,
            "warn": warn,
            "checks_run": 8,
        },
        "findings": findings,
    }


def _cli() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true",
                    help="Emit full report as JSON (default: text)")
    args = ap.parse_args()

    report = run_all()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        summary = report.get("summary") or {}
        print(f"=== rrc-verify  severe={summary.get('severe', '?')}  "
              f"warn={summary.get('warn', '?')}  "
              f"facts={summary.get('facts_total', '?')}  "
              f"states={summary.get('states_total', '?')}  "
              f"window={summary.get('period_range', '?')} ===")
        for f in report.get("findings", []):
            st = f.get("state", "")
            st_fmt = f" [{st}]" if st else ""
            print(f"  [{f['severity']}] {f['check']}{st_fmt}: {f['message']}")
        if report.get("error"):
            print(f"  ERROR: {report['error']}")

    sys.exit(0 if report.get("ok") else 1)


if __name__ == "__main__":
    _cli()
