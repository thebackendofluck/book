#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27d, PostgreSQL Aegis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Resharder — transactionally moves hash buckets to a new shard.

Strategy:
  * Read the current PgCat shard ring from pgcat admin interface.
  * For each (player_id % N) bucket assigned to the new shard:
      1. Create a replication slot on origin shard.
      2. COPY (SELECT ... WHERE player_id % N == bucket) into new shard.
      3. Replay WAL from slot's LSN up to commit time on origin, writing to
         the new shard in the same transaction.
      4. Atomically update the pgcat ring bucket → new shard.
      5. DELETE rows from origin shard (outside of hot path).

The script is idempotent: re-running resumes from wherever it stopped
(state persisted in the `reshard_state` table on every origin shard).

Requires: asyncpg, pyyaml, click
"""
from __future__ import annotations
import argparse
import sys

# This is a scaffold. The real implementation is substantial and lives
# alongside the book as reference material; the critical invariants are:
#
#   Invariant 1: a row is visible on EXACTLY ONE shard at any time.
#   Invariant 2: the pgcat ring update is atomic per bucket
#                (single PgCat admin command, not multiple steps).
#   Invariant 3: encrypted columns (pg_aegis / pgcrypto) travel as-is —
#                the DEK is the same on all shards (fetched from BAO),
#                so ciphertext round-trips without decrypt+re-encrypt.
#
# Usage (invoked by add-write-shard.sh):
#   _reshard.py --inventory inventory/lab-server.yml --new-shard c
#
# Additional safety flags:
#   --dry-run          : print per-bucket plan without executing
#   --bucket-limit N   : process at most N buckets then exit (resumable)
#   --copy-chunk K     : COPY in K-row batches (default 10 000)

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--new-shard", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bucket-limit", type=int, default=0)
    ap.add_argument("--copy-chunk", type=int, default=10_000)
    args = ap.parse_args()

    print(f"[reshard] inventory={args.inventory} new-shard={args.new_shard}")
    print("[reshard] THIS IS A SCAFFOLD — real resharder uses asyncpg + pg_logical_slots")
    print("[reshard] steps:")
    print("  1. Read pgcat ring, compute buckets moving to new shard.")
    print("  2. For each bucket: create slot, COPY, replay WAL, switch ring, DELETE old.")
    print("  3. Persist state to origin.reshard_state.")

    # Exit 0 so the shell script can chain steps; real implementation
    # returns 1 on partial completion (so the caller can resume).
    return 0


if __name__ == "__main__":
    sys.exit(main())
