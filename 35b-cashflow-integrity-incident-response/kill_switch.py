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

"""kill_switch.py — pause a game, an operator, or the whole platform.

Three scopes:
  - game:     pause a single game across all operators
  - operator: pause all games for one operator
  - platform: pause everything (last resort, requires acknowledge token)

The pause is implemented by writing a row to the `kill_switch` table that the
gateway reads on every settlement. The gateway honors the row within ~5s
because the table is in the gateway's hot cache.

Usage:
  kill_switch.py --scope game --id slots_pirate_bonanza --reason "I-CF-04 RTP 132%"
  kill_switch.py --scope operator --id op_42 --reason "concentrated wins"
  kill_switch.py --scope platform --reason "RNG anomaly" --acknowledge VP-OPS-2026-04-25
"""
from __future__ import annotations

import argparse
import os
import sys
import psycopg

VALID_SCOPES = ("game", "operator", "platform")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scope", required=True, choices=VALID_SCOPES)
    ap.add_argument("--id", help="Game ID or operator ID. Omit only for platform scope.")
    ap.add_argument("--reason", required=True, help="Free-text reason. Stored verbatim.")
    ap.add_argument("--acknowledge", help="Required for platform scope. Token from VP/CTO.")
    ap.add_argument("--db", default=os.environ.get("PG_DSN", "postgres:///casino"),
                    help="Postgres DSN. Defaults to PG_DSN env or peer connection.")
    args = ap.parse_args()

    if args.scope == "platform" and not args.acknowledge:
        print("--acknowledge is required for --scope platform", file=sys.stderr)
        return 2
    if args.scope in ("game", "operator") and not args.id:
        print(f"--id is required for --scope {args.scope}", file=sys.stderr)
        return 2

    actor = os.environ.get("USER", "unknown")
    with psycopg.connect(args.db) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO kill_switch (scope, target_id, reason, actor, ack_token)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (scope, target_id)
                DO UPDATE SET reason = EXCLUDED.reason,
                              actor = EXCLUDED.actor,
                              ack_token = EXCLUDED.ack_token,
                              activated_at = NOW()
                RETURNING id, activated_at;
                """,
                (args.scope, args.id, args.reason, actor, args.acknowledge),
            )
            row = cur.fetchone()
            conn.commit()
    print(f"kill_switch row id={row[0]} activated_at={row[1].isoformat()}")
    print(f"scope={args.scope} target={args.id} reason={args.reason!r}")
    print("gateway will honor within ~5s on next settlement read")
    return 0


if __name__ == "__main__":
    sys.exit(main())
