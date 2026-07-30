#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 15, Casino Mathematics and Game Economy.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""GLI-12 v3.0 (2026) — progressive jackpot reserve verification.

Asserts that the operator's funded reserve is at all times >= the certified
minimum reserve commitment for every active progressive jackpot. GLI-12
inspectors sample this evidence during on-site audits; failure to demonstrate
the reserve in real time is a Notifiable Event under most jurisdictions.

Designed to run on a cron (every 5 min in production) against the wallet/jackpot
ledger. Exit code:

    0  reserve sufficient for every jackpot
    1  one or more jackpots underfunded — page on-call
    2  config or data error — page on-call

Usage:
    uv run jackpot-reserve-check.py --config jackpot-config.json \\
                                    --ledger jackpot-ledger.csv

The config file enumerates each certified jackpot with seed/reset and
contractual reserve floor (a multiple of seed, per GLI-12 § 3.x). The
ledger is the read-only snapshot of contributions / payouts emitted by
the wallet service; we never write here.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JackpotConfig:
    jackpot_id: str
    seed_amount_cents: int
    reserve_floor_multiplier: float  # GLI-12 typically 1.0–1.5


@dataclass(frozen=True)
class JackpotLedgerSnapshot:
    jackpot_id: str
    pool_balance_cents: int
    funded_reserve_cents: int


def load_config(path: Path) -> dict[str, JackpotConfig]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {
        item["jackpot_id"]: JackpotConfig(
            jackpot_id=item["jackpot_id"],
            seed_amount_cents=int(item["seed_amount_cents"]),
            reserve_floor_multiplier=float(item["reserve_floor_multiplier"]),
        )
        for item in raw["jackpots"]
    }


def load_ledger(path: Path) -> list[JackpotLedgerSnapshot]:
    rows: list[JackpotLedgerSnapshot] = []
    with path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(
                JackpotLedgerSnapshot(
                    jackpot_id=row["jackpot_id"],
                    pool_balance_cents=int(row["pool_balance_cents"]),
                    funded_reserve_cents=int(row["funded_reserve_cents"]),
                )
            )
    return rows


def check(
    cfgs: dict[str, JackpotConfig],
    ledger: list[JackpotLedgerSnapshot],
) -> tuple[int, list[str]]:
    breaches: list[str] = []
    for snap in ledger:
        cfg = cfgs.get(snap.jackpot_id)
        if cfg is None:
            breaches.append(
                f"unknown jackpot in ledger: {snap.jackpot_id} — "
                "every active jackpot must appear in the certified config"
            )
            continue
        required = int(cfg.seed_amount_cents * cfg.reserve_floor_multiplier)
        if snap.funded_reserve_cents < required:
            breaches.append(
                f"{snap.jackpot_id}: reserve {snap.funded_reserve_cents}c < "
                f"required {required}c (seed={cfg.seed_amount_cents}c, "
                f"floor={cfg.reserve_floor_multiplier:.2f}x)"
            )
    return (1 if breaches else 0, breaches)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--ledger", type=Path, required=True)
    args = p.parse_args()

    if not args.config.is_file() or not args.ledger.is_file():
        print("error: config or ledger path missing", file=sys.stderr)
        return 2

    cfgs = load_config(args.config)
    ledger = load_ledger(args.ledger)
    code, breaches = check(cfgs, ledger)
    if breaches:
        print("GLI-12 reserve breach detected:", file=sys.stderr)
        for b in breaches:
            print(f"  - {b}", file=sys.stderr)
    else:
        print(f"OK: {len(ledger)} jackpot(s) reserved per GLI-12 v3.0")
    return code


if __name__ == "__main__":
    sys.exit(main())
