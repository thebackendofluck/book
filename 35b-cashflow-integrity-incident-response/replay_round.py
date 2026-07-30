#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 35b, Cash-Flow Integrity Incident Response.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""replay_round.py — recompute the payout for a settled round and compare against the recorded payout.

A discrepancy in one round means a bug. A discrepancy in many rounds with the
same delta means the RTP table is wrong. A discrepancy that does not reproduce
means a deploy happened mid-incident and the wrong code was live.

Use HSM-sourced seeds (--rng-seed-source hsm) for evidentiary weight; using the
DB seed lets a compromised application fake the replay result.

Usage:
  replay_round.py --round-id 8821743 --rng-seed-source hsm --report /var/forensics/replay-8821743.json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from decimal import Decimal
from importlib import import_module

import psycopg


def fetch_round(conn, round_id: int) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT round_id, game_id, player_id, operator_id, bet_amount,
                   win_amount, settled_at, rng_seed_id, game_logic_version
              FROM game_rounds
             WHERE round_id = %s
            """,
            (round_id,),
        )
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"round {round_id} not found")
    cols = ["round_id", "game_id", "player_id", "operator_id",
            "bet_amount", "win_amount", "settled_at",
            "rng_seed_id", "game_logic_version"]
    return dict(zip(cols, row))


def fetch_seed_from_hsm(seed_id: str) -> str:
    out = subprocess.check_output(
        ["yubihsm-shell", "--action", "get-opaque",
         "--object-id", seed_id, "--connector", "http://127.0.0.1:12345"],
        stderr=subprocess.STDOUT,
    )
    return out.decode().strip()


def fetch_seed_from_db(conn, seed_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT seed_value FROM rng_seed_log WHERE seed_id = %s;", (seed_id,))
        row = cur.fetchone()
    if not row:
        raise SystemExit(f"seed {seed_id} not in db")
    return row[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--round-id", type=int, required=True)
    ap.add_argument("--rng-seed-source", choices=("hsm", "db"), default="hsm",
                    help="HSM is evidentiary; DB is debug-only.")
    ap.add_argument("--game-lib-path", default=os.environ.get("GAME_LIB_PATH", "casino_games"),
                    help="Python module containing per-game replay() functions.")
    ap.add_argument("--report", help="Output report path (JSON). Defaults to stdout.")
    ap.add_argument("--db", default=os.environ.get("PG_DSN", "postgres:///casino"))
    args = ap.parse_args()

    with psycopg.connect(args.db) as conn:
        rnd = fetch_round(conn, args.round_id)
        if args.rng_seed_source == "hsm":
            seed = fetch_seed_from_hsm(rnd["rng_seed_id"])
        else:
            seed = fetch_seed_from_db(conn, rnd["rng_seed_id"])

    games_pkg = import_module(args.game_lib_path)
    game_module = getattr(games_pkg, rnd["game_id"], None)
    if game_module is None or not hasattr(game_module, "replay"):
        raise SystemExit(f"no replay() found for game {rnd['game_id']} in {args.game_lib_path}")

    computed = game_module.replay(
        bet_amount=Decimal(str(rnd["bet_amount"])),
        seed=seed,
        version=rnd["game_logic_version"],
    )
    recorded = Decimal(str(rnd["win_amount"]))
    delta = computed - recorded

    report = {
        "round_id": rnd["round_id"],
        "game_id": rnd["game_id"],
        "rng_seed_id": rnd["rng_seed_id"],
        "rng_seed_source": args.rng_seed_source,
        "game_logic_version": rnd["game_logic_version"],
        "bet_amount": str(rnd["bet_amount"]),
        "win_recorded": str(recorded),
        "win_computed": str(computed),
        "delta": str(delta),
        "matches": delta == 0,
    }

    out = json.dumps(report, indent=2, default=str)
    if args.report:
        with open(args.report, "w") as f:
            f.write(out)
        print(f"report: {args.report}  matches={report['matches']}  delta={delta}")
    else:
        print(out)
    return 0 if report["matches"] else 3


if __name__ == "__main__":
    sys.exit(main())
