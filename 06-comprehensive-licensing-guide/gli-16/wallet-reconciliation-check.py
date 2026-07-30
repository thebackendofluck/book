#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""GLI-16 v3.0 (2024) — cashless deposit/withdrawal reconciliation.

Reconciles the operator's internal wallet ledger against the upstream payment
provider's settlement export. Designed for the PIX → wallet → game-account
path described in Chapter 6 § Brazil, but the algorithm is provider-agnostic:
any closed-loop deposit/withdrawal flow with double-entry at both ends works.

Failure modes flagged (in order of severity):

    orphan_credit   provider says paid, operator has no matching deposit
    orphan_debit    operator booked a withdrawal not in provider settlement
    amount_mismatch matched txid but cents differ
    duplicate_txid  same txid appears twice on either side

Run nightly against the previous business day. Operators should keep 5 years
of reconciliation reports on hand for regulator audits (BACEN / SPA in Brazil,
PCI-DSS evidence elsewhere).

Exit codes:
    0  perfect reconciliation
    1  one or more discrepancies (details on stderr + JSON report)
    2  config / input error

Usage:
    uv run wallet-reconciliation-check.py \\
        --provider provider-settlement-2026-05-04.csv \\
        --operator operator-wallet-2026-05-04.csv \\
        --report   recon-2026-05-04.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Movement:
    txid: str
    direction: str  # "deposit" | "withdrawal"
    amount_cents: int


def load_movements(path: Path) -> list[Movement]:
    rows: list[Movement] = []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                Movement(
                    txid=row["txid"],
                    direction=row["direction"],
                    amount_cents=int(row["amount_cents"]),
                )
            )
    return rows


@dataclass
class Report:
    orphan_credit: list[str] = field(default_factory=list)
    orphan_debit: list[str] = field(default_factory=list)
    amount_mismatch: list[dict[str, object]] = field(default_factory=list)
    duplicate_txid: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (
            self.orphan_credit
            or self.orphan_debit
            or self.amount_mismatch
            or self.duplicate_txid
        )


def index(movements: list[Movement]) -> tuple[dict[str, Movement], list[str]]:
    out: dict[str, Movement] = {}
    dups: list[str] = []
    for m in movements:
        if m.txid in out:
            dups.append(m.txid)
        else:
            out[m.txid] = m
    return out, dups


def reconcile(provider: list[Movement], operator: list[Movement]) -> Report:
    rep = Report()
    p_idx, p_dups = index(provider)
    o_idx, o_dups = index(operator)
    rep.duplicate_txid = sorted(set(p_dups + o_dups))

    for txid, p in p_idx.items():
        o = o_idx.get(txid)
        if o is None:
            if p.direction == "deposit":
                rep.orphan_credit.append(txid)
            else:
                rep.orphan_debit.append(txid)
            continue
        if p.amount_cents != o.amount_cents or p.direction != o.direction:
            rep.amount_mismatch.append(
                {
                    "txid": txid,
                    "provider": {"dir": p.direction, "cents": p.amount_cents},
                    "operator": {"dir": o.direction, "cents": o.amount_cents},
                }
            )

    for txid, o in o_idx.items():
        if txid not in p_idx:
            if o.direction == "withdrawal":
                rep.orphan_debit.append(txid)
            else:
                rep.orphan_credit.append(txid)
    rep.orphan_credit = sorted(set(rep.orphan_credit))
    rep.orphan_debit = sorted(set(rep.orphan_debit))
    return rep


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--provider", type=Path, required=True)
    p.add_argument("--operator", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    if not args.provider.is_file() or not args.operator.is_file():
        print("error: input files missing", file=sys.stderr)
        return 2

    provider = load_movements(args.provider)
    operator = load_movements(args.operator)
    rep = reconcile(provider, operator)
    args.report.write_text(
        json.dumps(
            {
                "ok": rep.ok,
                "orphan_credit": rep.orphan_credit,
                "orphan_debit": rep.orphan_debit,
                "amount_mismatch": rep.amount_mismatch,
                "duplicate_txid": rep.duplicate_txid,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if rep.ok:
        print(f"OK: {len(provider)} provider mvmt vs {len(operator)} operator mvmt — clean")
        return 0

    summary: dict[str, int] = defaultdict(int)
    summary["orphan_credit"] = len(rep.orphan_credit)
    summary["orphan_debit"] = len(rep.orphan_debit)
    summary["amount_mismatch"] = len(rep.amount_mismatch)
    summary["duplicate_txid"] = len(rep.duplicate_txid)
    print(f"FAIL: discrepancies — {dict(summary)}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
