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
tax_imputed.py — derive `tax_paid` facts from existing `ggr` facts using
statutory tax rates, for jurisdictions whose regulator publishes GGR but
NOT a per-period tax line item.

Without this pass, the dashboard's "Effective tax rate" chart silently
drops UK / ON / SE / DK / NL / ES because their tax_cents column is null.
That gives the false visual impression those countries don't tax gambling
at all, when in reality they all do — they just publish the data via a
separate Treasury / Skatteverket / Belastingdienst stream we don't yet
collect.

Imputation uses each jurisdiction's published statutory rate, applied per
vertical because rates differ by product (e.g., UK's Remote Gaming Duty is
21% for casino but General Betting Duty is 15% for sports). Every imputed
fact is tagged with the operator string `<state> Statewide (imputed)` so
analysts can identify it and drill down to the source rate.

Sources for the rates (audited 2026-04):
  - UK:  https://www.gov.uk/topic/business-tax/gambling-duties
  - ON:  https://www.iglo.ca/financial-statements (Crown corp profit, ~20%)
  - SE:  Spelinspektionen quarterly statistics (18% statutory)
  - DK:  https://www.skat.dk/data.aspx?oid=1957961 (28%)
  - NL:  https://www.belastingdienst.nl/ (kansspelbelasting 30.5%)
  - ES:  https://www.boe.es/ (Ley 13/2011 — 20% remote, 25% bets)
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

import psycopg


# (state, vertical) → effective statutory tax rate on GGR (decimal).
# Where multiple sub-rates exist, a blended representative rate is used.
_RATES: dict[tuple[str, str], float] = {
    # United Kingdom — Remote Gaming Duty / General Betting Duty / Bingo Duty
    ("UK", "igaming"):         0.21,
    ("UK", "sports-wagering"): 0.15,
    ("UK", "remote-total"):    0.18,   # weighted blend
    ("UK", "bingo-remote"):    0.10,
    ("UK", "bingo"):           0.10,
    ("UK", "retail-betting"):  0.15,
    ("UK", "retail-casino"):   0.20,
    ("UK", "arcades"):         0.05,

    # Ontario — iGaming Ontario shares 20% of GGR with the province (Crown).
    ("ON", "igaming"):         0.20,
    ("ON", "sports-wagering"): 0.20,

    # Sweden — single 18% on operator GGR (Spelskattelagen 2018:1139).
    ("SE", "igaming"):         0.18,
    ("SE", "sports-wagering"): 0.18,

    # Denmark — gambling duty: online casino & sports both 28%.
    ("DK", "igaming"):         0.28,
    ("DK", "sports-wagering"): 0.28,

    # Netherlands — kansspelbelasting raised to 34.2% in 2025; use 30.5% blend
    # for the historical series we hold (2022-2024 was 29% → 30.5%).
    ("NL", "igaming"):         0.305,
    ("NL", "sports-wagering"): 0.305,

    # Spain — IAJ (Impuesto sobre Actividades de Juego): 20% online, 22% bets.
    ("ES", "igaming"):         0.20,
    ("ES", "sports-wagering"): 0.22,
    ("ES", "online-poker"):    0.20,

    # Germany — GlüStV virtual slot tax 5.3% on stakes ≈ 22% GGR effective.
    ("GE", "igaming"):         0.22,
    ("GE", "sports-wagering"): 0.053,

    # France — ANJ casino remote 55% of GGR (high), sports 33.7%.
    ("FR", "igaming"):         0.45,
    ("FR", "sports-wagering"): 0.337,
    ("FR", "horse-racing"):    0.40,

    # Italy — ADM: 25% on remote casino GGR, 24.5% on remote betting.
    ("IT", "igaming"):         0.25,
    ("IT", "sports-wagering"): 0.245,
    ("IT", "online-poker"):    0.25,

    # Norway — state monopoly; "tax" is the surplus to public causes (~70% of GGR).
    ("NO", "igaming"):         0.70,
    ("NO", "sports-wagering"): 0.70,
    ("NO", "lottery"):         0.70,

    # Belgium — federal gambling tax 11% on GGR for online + 15% sports retail.
    ("BE", "igaming"):         0.11,
    ("BE", "sports-wagering"): 0.15,
    ("BE", "land-based-casino"): 0.15,

    # Greece — HGC: 35% GGR online casino, 24% online sports.
    ("GR", "igaming"):         0.35,
    ("GR", "sports-wagering"): 0.24,
}


def impute_tax_facts(dsn: str) -> int:
    """Read ggr facts from metric_facts; for any (state, operator, vertical,
    period) that has GGR but no matching tax_paid, insert one using the
    statutory rate from `_RATES`. Returns the number of rows inserted.

    The imputed rows use `metric_name='tax_paid'` so they participate in the
    same SUM aggregation as parser-extracted tax. Operator is rewritten with
    a `(imputed)` suffix to make the audit trail explicit.
    """
    inserted = 0
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            # Pull ALL ggr rows for the imputed-rate states, then check which
            # don't yet have a tax_paid sibling.
            states = sorted({k[0] for k in _RATES})
            cur.execute(
                """
                SELECT state, operator, vertical, period, value_usd_cents,
                       source_url
                FROM metric_facts
                WHERE state = ANY(%s) AND metric_name = 'ggr'
                """,
                (states,),
            )
            ggr_rows = cur.fetchall()

            cur.execute(
                """
                SELECT state, operator, vertical, period
                FROM metric_facts
                WHERE state = ANY(%s) AND metric_name = 'tax_paid'
                """,
                (states,),
            )
            existing_tax = {tuple(r) for r in cur.fetchall()}

            new_rows: list[tuple] = []
            for state, operator, vertical, period, ggr_cents, src in ggr_rows:
                rate = _RATES.get((state, vertical))
                if rate is None:
                    continue
                key = (state, operator, vertical, period)
                if key in existing_tax:
                    continue
                tax_cents = int(int(ggr_cents) * rate)
                imp_op = (operator + " (imputed)") if "(imputed)" not in operator else operator
                new_rows.append((
                    state, imp_op, vertical, period, "tax_paid",
                    tax_cents, src,
                ))

            if new_rows:
                cur.executemany(
                    """
                    INSERT INTO metric_facts
                      (state, operator, vertical, period, metric_name,
                       value_usd_cents, source_url)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (state, operator, vertical, period, metric_name)
                    DO UPDATE SET
                        value_usd_cents = EXCLUDED.value_usd_cents,
                        source_url      = EXCLUDED.source_url,
                        inserted_at     = NOW()
                    """,
                    new_rows,
                )
                inserted = len(new_rows)
        conn.commit()
    return inserted


def _cli() -> None:
    """Standalone runner: `python tax_imputed.py` against the DATABASE_URL env."""
    import os
    import sys
    dsn = os.environ.get("DATABASE_URL", "")
    if dsn.startswith("postgresql+asyncpg://"):
        dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    if not dsn:
        sys.exit("DATABASE_URL not set")
    n = impute_tax_facts(dsn)
    print(f"imputed {n} tax_paid facts")


if __name__ == "__main__":
    _cli()
