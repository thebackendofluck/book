#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 20b, OpenBao Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Rewrap every ciphertext in a PostgreSQL column forward to the latest
version of a Transit key, and emit a report that is sufficient to gate the
`min_decryption_version` advance that follows.

This is the middle step of a three-step crypto-period rotation:

    1. bao write -f transit/keys/<key>/rotate
    2. rewrap-all-ciphertexts.py  (this script, once per table/column)
    3. bao write transit/keys/<key>/config min_decryption_version=N

Step 3 is destructive in the way that matters: once `min_decryption_version`
is raised, every ciphertext still carrying an older version becomes
permanently undecryptable. So step 3 must not be run on the strength of "the
rewrap job exited 0". Exiting 0 only says the rows this invocation selected
were rewrapped; it says nothing about rows written *during* the run, rows in
columns nobody remembered to list, or rows the WHERE clause failed to match.

The report closes that gap as far as a single column can:

  * after the rewrap pass commits, the stale-row predicate is re-run and the
    remaining count is reported. A non-zero remaining count sets
    `safe_to_advance` false and exits 3.
  * the exact scope covered (table, column, primary key, target version) is
    recorded, so the operator advancing the key can check the set of reports
    against their own inventory of encrypted columns.
  * `--dry-run` always reports `safe_to_advance: false`, because a dry run
    changed nothing.

The scope caveat cannot be solved from inside this script and is stated in the
report itself: this covers ONE column. If the platform encrypts six columns
under one Transit key, six reports must all say safe_to_advance before
min_decryption_version is advanced.

Usage:
    rewrap-all-ciphertexts.py \
        --bao-addr http://127.0.0.1:18300 \
        --bao-token "$(python3 -c 'import json;print(json.load(open("/tmp/openbao-sandbox-20b/init.json"))["root_token"])')" \
        --key platform-pii \
        --dsn "postgresql://user:pass@host:5432/db" \
        --table players \
        --column email_encrypted \
        --target-version 2 \
        --report-file /var/lib/bao-rotation/players.email_encrypted.json

Exit codes:
    0  every row at or above --target-version; safe_to_advance true
    2  prerequisite missing (psycopg2)
    3  rows remain below --target-version, or this was a dry run
    4  operational failure (database or OpenBao error)
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterator

try:
    import psycopg2  # type: ignore[import-untyped]
    from psycopg2 import sql as pgsql  # type: ignore[import-untyped]
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False


class RewrapError(RuntimeError):
    """Raised when the Transit rewrap call fails."""


def bao_rewrap(addr: str, token: str, key: str, ciphertext: str, insecure: bool) -> str:
    """Round-trip a single ciphertext through transit/rewrap/<key>."""
    req = urllib.request.Request(
        f"{addr}/v1/transit/rewrap/{key}",
        data=json.dumps({"ciphertext": ciphertext}).encode("utf-8"),
        headers={"X-Vault-Token": token, "Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl._create_unverified_context() if insecure else None  # noqa: SLF001
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise RewrapError(f"transit/rewrap/{key} returned HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RewrapError(f"could not reach {addr}: {exc.reason}") from exc
    try:
        return body["data"]["ciphertext"]
    except (KeyError, TypeError) as exc:
        raise RewrapError(f"unexpected transit/rewrap response shape: {body!r}") from exc


def _stale_predicate(column: pgsql.Identifier) -> pgsql.Composed:
    """SQL predicate matching rows whose ciphertext version is below a bound.

    The version lives in the `vault:vN:` prefix that Transit puts on every
    ciphertext, so it can be compared without decrypting anything.
    """
    return pgsql.SQL(
        "{col} LIKE 'vault:v%:%' "
        "AND CAST(SPLIT_PART(SPLIT_PART({col}, ':', 2), 'v', 2) AS INTEGER) < %s"
    ).format(col=column)


def count_stale(cursor, table: pgsql.Identifier, column: pgsql.Identifier,
                min_version: int) -> int:
    """Count rows still below `min_version`."""
    cursor.execute(
        pgsql.SQL("SELECT COUNT(*) FROM {tbl} WHERE {pred}").format(
            tbl=table, pred=_stale_predicate(column)
        ),
        (min_version,),
    )
    return int(cursor.fetchone()[0])


def select_stale(cursor, table: pgsql.Identifier, column: pgsql.Identifier,
                 pk: pgsql.Identifier, min_version: int,
                 batch_size: int) -> Iterator[tuple[Any, str]]:
    """Yield (pk, ciphertext) for rows below `min_version`, in batches.

    Keyset pagination on the primary key, not a bare re-SELECT. The previous
    version re-ran an identical query each time round the loop and relied on
    the UPDATE having removed the rows from the result set. Under --dry-run
    nothing was updated, so the same batch was yielded forever and the dry run
    never terminated. Keyset pagination is correct in both modes and does not
    re-scan the rows it has already passed.
    """
    last_pk: Any = None
    while True:
        if last_pk is None:
            query = pgsql.SQL(
                "SELECT {pk}, {col} FROM {tbl} WHERE {pred} ORDER BY {pk} LIMIT %s"
            ).format(pk=pk, col=column, tbl=table, pred=_stale_predicate(column))
            params: tuple[Any, ...] = (min_version, batch_size)
        else:
            query = pgsql.SQL(
                "SELECT {pk}, {col} FROM {tbl} WHERE {pred} AND {pk} > %s "
                "ORDER BY {pk} LIMIT %s"
            ).format(pk=pk, col=column, tbl=table, pred=_stale_predicate(column))
            params = (min_version, last_pk, batch_size)

        cursor.execute(query, params)
        rows = cursor.fetchall()
        if not rows:
            return
        for row in rows:
            last_pk = row[0]
            yield row[0], row[1]


def build_report(args: argparse.Namespace, rewrapped: int, remaining: int,
                 started: str) -> dict[str, Any]:
    safe = (not args.dry_run) and remaining == 0
    return {
        "tool": "rewrap-all-ciphertexts.py",
        "report_version": 1,
        "key": args.key,
        "target_version": args.target_version,
        "scope": {
            "table": args.table,
            "column": args.column,
            "primary_key": args.primary_key,
        },
        "started_at": started,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "rows_rewrapped": rewrapped,
        "rows_remaining_below_target": remaining,
        "safe_to_advance": safe,
        "scope_caveat": (
            "This report covers one table/column only. min_decryption_version "
            "must not be advanced until every column encrypted under this key "
            "has its own report with safe_to_advance=true, and no writer is "
            "still producing ciphertext at an older version."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bao-addr", required=True)
    parser.add_argument("--bao-token", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--dsn", required=True, help="PostgreSQL DSN")
    parser.add_argument("--table", required=True)
    parser.add_argument("--column", required=True)
    parser.add_argument("--primary-key", default="id",
                        help="Primary key column used for keyset pagination (default: id)")
    parser.add_argument("--target-version", type=int, required=True,
                        help="Rewrap all ciphertexts whose version is strictly less than this")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--insecure", action="store_true", help="Skip TLS verification (dev only)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-file",
                        help="Write the JSON gate report here (also printed to stdout)")
    args = parser.parse_args()

    if not HAS_PSYCOPG2:
        print("ERROR: psycopg2 is required. Install with `pip install psycopg2-binary`.",
              file=sys.stderr)
        return 2

    started = datetime.now(timezone.utc).isoformat()
    table = pgsql.Identifier(args.table)
    column = pgsql.Identifier(args.column)
    pk = pgsql.Identifier(args.primary_key)

    total = 0
    try:
        with psycopg2.connect(args.dsn) as conn:
            with conn.cursor() as sel_cur, conn.cursor() as upd_cur:
                before = count_stale(sel_cur, table, column, args.target_version)
                print(f"rows below v{args.target_version} at start: {before}", file=sys.stderr)

                for row_pk, ct in select_stale(sel_cur, table, column, pk,
                                               args.target_version, args.batch_size):
                    new_ct = bao_rewrap(args.bao_addr, args.bao_token, args.key,
                                        ct, args.insecure)
                    if args.dry_run:
                        print(f"DRY: {args.primary_key}={row_pk} "
                              f"{ct[:20]}... -> {new_ct[:20]}...", file=sys.stderr)
                    else:
                        upd_cur.execute(
                            pgsql.SQL("UPDATE {tbl} SET {col} = %s WHERE {pk} = %s").format(
                                tbl=table, col=column, pk=pk
                            ),
                            (new_ct, row_pk),
                        )
                    total += 1
                    if total % args.batch_size == 0:
                        conn.commit()
                        print(f"committed {total} rows", file=sys.stderr)
                conn.commit()

            # Re-count AFTER the commit, in a fresh cursor. This is the gate:
            # it is the only statement here that can distinguish "the rows I
            # selected are done" from "no rows are left below the target".
            with conn.cursor() as chk_cur:
                remaining = count_stale(chk_cur, table, column, args.target_version)
    except RewrapError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4
    except psycopg2.Error as exc:
        print(f"ERROR: database failure: {exc}", file=sys.stderr)
        return 4

    report = build_report(args, total, remaining, started)
    rendered = json.dumps(report, indent=2)
    print(rendered)
    if args.report_file:
        with open(args.report_file, "w", encoding="utf-8") as handle:
            handle.write(rendered + "\n")
        print(f"report written to {args.report_file}", file=sys.stderr)

    if not report["safe_to_advance"]:
        if args.dry_run:
            print("dry run: nothing changed, min_decryption_version must NOT be advanced",
                  file=sys.stderr)
        else:
            print(f"ERROR: {remaining} row(s) still below v{args.target_version} -- "
                  "min_decryption_version must NOT be advanced", file=sys.stderr)
        return 3

    print(f"rewrap complete: {total} rewrapped, 0 remaining below "
          f"v{args.target_version}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
