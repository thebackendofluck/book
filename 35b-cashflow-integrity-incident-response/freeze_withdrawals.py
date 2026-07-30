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

"""freeze_withdrawals.py — pause withdrawals for a list of players (does NOT revoke balance).

A frozen withdrawal stays in the queue with status `under_review`. Operators
can release it later via the case management UI (Chapter 33b/33d).

Usage:
  freeze_withdrawals.py --player-ids 1042,3318,9981 \
                        --duration-hours 24 \
                        --reason "I-CF-04 forensic review" \
                        --notify-player template:withdrawal_under_review
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
import psycopg


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--player-ids", required=True,
                    help="Comma-separated player IDs.")
    ap.add_argument("--duration-hours", type=int, default=24,
                    help="How long the freeze lasts (default 24h).")
    ap.add_argument("--reason", required=True, help="Free-text reason.")
    ap.add_argument("--notify-player",
                    help="Notification template name (e.g. template:withdrawal_under_review). "
                         "If omitted, no notification is sent — operator must inform player manually.")
    ap.add_argument("--db", default=os.environ.get("PG_DSN", "postgres:///casino"))
    args = ap.parse_args()

    try:
        ids = [int(x.strip()) for x in args.player_ids.split(",") if x.strip()]
    except ValueError:
        print("--player-ids must be comma-separated integers", file=sys.stderr)
        return 2

    until = datetime.now(timezone.utc) + timedelta(hours=args.duration_hours)
    actor = os.environ.get("USER", "unknown")

    with psycopg.connect(args.db) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO withdrawal_freeze (player_id, until, reason, actor, notify_template)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (player_id) WHERE released_at IS NULL
                DO UPDATE SET until = EXCLUDED.until,
                              reason = EXCLUDED.reason,
                              actor = EXCLUDED.actor,
                              notify_template = EXCLUDED.notify_template;
                """,
                [(pid, until, args.reason, actor, args.notify_player) for pid in ids],
            )
            conn.commit()
            cur.execute(
                "SELECT player_id FROM withdrawal_freeze "
                "WHERE released_at IS NULL AND player_id = ANY(%s);", (ids,)
            )
            frozen_now = [r[0] for r in cur.fetchall()]

    print(f"frozen players (until {until.isoformat()}): {frozen_now}")
    if args.notify_player:
        print(f"notification template scheduled: {args.notify_player}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
