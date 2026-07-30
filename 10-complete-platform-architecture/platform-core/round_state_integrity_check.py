# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
round_state_integrity_check.py  (H12)
--------------------------------------
Operational validation script: Round State Integrity Check.

Validates the lifecycle integrity of game rounds stored in the platform.
Run as an hourly CronJob, a post-deployment gate, or an on-call diagnostic.

Invariants checked
------------------
1. Every BET transaction has a corresponding WIN or ROLLBACK transaction
   for the same round_id.  A round without a WIN or ROLLBACK is "orphaned"
   only if it has been open beyond the configured threshold.
2. No orphaned rounds: rounds that have a BET but no WIN/ROLLBACK and were
   opened more than MAX_OPEN_HOURS ago (default: 24 h).
3. Round amounts balance: the sum of all BET amounts for a round minus the
   sum of all WIN amounts should equal the house edge (non-negative).
   Rounds where wins exceed bets are flagged as integrity violations.
4. No duplicate round IDs across suppliers: the same round_id must not
   appear under two different supplier_ids.

Data contract
-------------
The script reads round/transaction data from a configurable source.
Three backends are supported:

    DB_URL set    — query a PostgreSQL database via psycopg2/pg8000
    API_URL set   — call the platform transactions API (JSON)
    neither       — use a built-in synthetic dataset for smoke-testing

Synthetic smoke-test mode is deterministic and safe to run in CI.

Exit codes
----------
0 — all invariants satisfied
1 — one or more violations found
2 — configuration / connectivity error

Usage
-----
    python round_state_integrity_check.py
    DB_URL=postgresql://... python round_state_integrity_check.py
    API_URL=http://gameservice:8080 python round_state_integrity_check.py

Environment variables
---------------------
    DB_URL              PostgreSQL DSN. If set, data is read from the DB.
    API_URL             Base URL of the transaction API.
    MAX_OPEN_HOURS      Hours after which an open round is flagged. Default: 24
    LOOKBACK_HOURS      How far back to scan. Default: 48
    AMOUNT_TOLERANCE    Fractional tolerance for balance check. Default: 0.0001
"""

from __future__ import annotations

import os
import sys
import json
import logging
import urllib.request
import urllib.error
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("h12-round-state-integrity")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_URL: Optional[str] = os.environ.get("DB_URL", "").strip() or None
API_URL: Optional[str] = os.environ.get("API_URL", "").strip() or None
MAX_OPEN_HOURS: float = float(os.environ.get("MAX_OPEN_HOURS", "24"))
LOOKBACK_HOURS: float = float(os.environ.get("LOOKBACK_HOURS", "48"))
AMOUNT_TOLERANCE: Decimal = Decimal(os.environ.get("AMOUNT_TOLERANCE", "0.0001"))

# Transaction type constants
TX_BET = "BET"
TX_WIN = "WIN"
TX_ROLLBACK = "ROLLBACK"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class RoundTransaction:
    """A single debit, credit, or rollback transaction belonging to a round."""
    tx_id: str
    round_id: str
    supplier_id: str
    player_id: str
    tx_type: str            # BET | WIN | ROLLBACK
    amount: Decimal
    currency: str
    created_at: datetime


@dataclass
class CheckResult:
    check_name: str
    passed: bool = True
    violations: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.passed = False
        self.violations.append(message)

    def summary(self) -> str:
        tag = "PASS" if self.passed else "FAIL"
        lines = [f"[{tag}] {self.check_name}"]
        for v in self.violations:
            lines.append(f"       {v}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------


def _load_from_db(since: datetime) -> list[RoundTransaction]:
    """
    Load transactions from PostgreSQL.

    Expected table schema:
        transactions(tx_id, round_id, supplier_id, player_id, tx_type,
                     amount, currency, created_at)
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        try:
            import pg8000 as psycopg2  # type: ignore
        except ImportError:
            raise RuntimeError(
                "No PostgreSQL driver found. Install psycopg2 or pg8000."
            )

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=getattr(psycopg2.extras, "DictCursor", None))
    cur.execute(
        """
        SELECT tx_id, round_id, supplier_id, player_id, tx_type,
               amount, currency, created_at
        FROM transactions
        WHERE created_at >= %s
          AND tx_type IN ('BET', 'WIN', 'ROLLBACK')
        ORDER BY created_at ASC
        """,
        (since,),
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        RoundTransaction(
            tx_id=str(row[0]),
            round_id=str(row[1]),
            supplier_id=str(row[2]),
            player_id=str(row[3]),
            tx_type=str(row[4]),
            amount=Decimal(str(row[5])),
            currency=str(row[6]),
            created_at=row[7] if isinstance(row[7], datetime) else datetime.fromisoformat(str(row[7])),
        )
        for row in rows
    ]


def _load_from_api(since: datetime) -> list[RoundTransaction]:
    """Load transactions from the platform transactions API."""
    url = (
        f"{API_URL}/api/v1/transactions"
        f"?since={since.isoformat()}&type=BET,WIN,ROLLBACK&limit=10000"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            payload = json.loads(resp.read())
    except Exception as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc

    items = payload if isinstance(payload, list) else payload.get("data", [])
    return [
        RoundTransaction(
            tx_id=str(item["tx_id"]),
            round_id=str(item["round_id"]),
            supplier_id=str(item["supplier_id"]),
            player_id=str(item["player_id"]),
            tx_type=str(item["tx_type"]),
            amount=Decimal(str(item["amount"])),
            currency=str(item["currency"]),
            created_at=datetime.fromisoformat(
                item["created_at"].replace("Z", "")
            ),
        )
        for item in items
    ]


def _load_synthetic() -> list[RoundTransaction]:
    """
    Deterministic synthetic dataset for smoke-testing and CI.

    Includes:
    - 3 healthy rounds (BET + WIN)
    - 1 healthy round with ROLLBACK
    - 1 orphaned round (BET > 24 h ago, no WIN/ROLLBACK)
    - 1 round where WIN > BET  (amount integrity violation)
    - 1 duplicate round_id across two suppliers
    """
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=26)

    return [
        # Round R001 — healthy BET+WIN
        RoundTransaction("T01", "R001", "evolution", "P1", TX_BET,  Decimal("10.00"), "EUR", now - timedelta(minutes=10)),
        RoundTransaction("T02", "R001", "evolution", "P1", TX_WIN,  Decimal("5.00"),  "EUR", now - timedelta(minutes=9)),
        # Round R002 — healthy BET+WIN
        RoundTransaction("T03", "R002", "evolution", "P2", TX_BET,  Decimal("20.00"), "EUR", now - timedelta(minutes=8)),
        RoundTransaction("T04", "R002", "evolution", "P2", TX_WIN,  Decimal("0.00"),  "EUR", now - timedelta(minutes=7)),
        # Round R003 — healthy ROLLBACK
        RoundTransaction("T05", "R003", "pragmatic", "P3", TX_BET,  Decimal("15.00"), "EUR", now - timedelta(minutes=5)),
        RoundTransaction("T06", "R003", "pragmatic", "P3", TX_ROLLBACK, Decimal("15.00"), "EUR", now - timedelta(minutes=4)),
        # Round R004 — ORPHANED (old BET, no WIN/ROLLBACK)
        RoundTransaction("T07", "R004", "evolution", "P4", TX_BET,  Decimal("25.00"), "EUR", old),
        # Round R005 — WIN > BET (integrity violation)
        RoundTransaction("T08", "R005", "pragmatic", "P5", TX_BET,  Decimal("5.00"),  "EUR", now - timedelta(minutes=3)),
        RoundTransaction("T09", "R005", "pragmatic", "P5", TX_WIN,  Decimal("1000.00"), "EUR", now - timedelta(minutes=2)),
        # Round R006 — duplicate round_id across suppliers
        RoundTransaction("T10", "R006", "evolution", "P6", TX_BET,  Decimal("10.00"), "EUR", now - timedelta(minutes=2)),
        RoundTransaction("T11", "R006", "evolution", "P6", TX_WIN,  Decimal("5.00"),  "EUR", now - timedelta(minutes=1)),
        RoundTransaction("T12", "R006", "netent",    "P7", TX_BET,  Decimal("10.00"), "EUR", now - timedelta(minutes=2)),
        RoundTransaction("T13", "R006", "netent",    "P7", TX_WIN,  Decimal("5.00"),  "EUR", now - timedelta(minutes=1)),
    ]


def load_transactions() -> list[RoundTransaction]:
    """
    Load transactions using the configured backend.
    Precedence: DB_URL > API_URL > synthetic.
    """
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    if DB_URL:
        logger.info("Loading transactions from database (lookback: %.0f h)", LOOKBACK_HOURS)
        return _load_from_db(since)
    if API_URL:
        logger.info("Loading transactions from API %s (lookback: %.0f h)", API_URL, LOOKBACK_HOURS)
        return _load_from_api(since)

    logger.warning(
        "No DB_URL or API_URL set — running against synthetic smoke-test dataset. "
        "Expected failures: R004 (orphan), R005 (win>bet), R006 (duplicate supplier)."
    )
    return _load_synthetic()


# ---------------------------------------------------------------------------
# Check 1: Every BET has a WIN or ROLLBACK
# ---------------------------------------------------------------------------


def check_bet_settlement(
    transactions: list[RoundTransaction],
) -> CheckResult:
    """
    Invariant: every BET must have a WIN or ROLLBACK on the same
    (round_id, supplier_id) pair.

    A BET that is more recent than MAX_OPEN_HOURS is allowed to be still
    open — it's an in-flight round, not a problem yet.
    """
    result = CheckResult("H12-C1: Every BET has WIN or ROLLBACK")
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(hours=MAX_OPEN_HOURS)

    # Group by (round_id, supplier_id)
    bets: dict[tuple[str, str], RoundTransaction] = {}
    settled: set[tuple[str, str]] = set()

    for tx in transactions:
        key = (tx.round_id, tx.supplier_id)
        if tx.tx_type == TX_BET:
            bets[key] = tx
        elif tx.tx_type in (TX_WIN, TX_ROLLBACK):
            settled.add(key)

    for key, bet_tx in bets.items():
        if key not in settled:
            # Only flag if the bet is older than the threshold
            if bet_tx.created_at <= threshold:
                result.fail(
                    f"Round {bet_tx.round_id!r} (supplier={bet_tx.supplier_id!r}, "
                    f"player={bet_tx.player_id!r}) has BET at {bet_tx.created_at.isoformat()} "
                    f"with no WIN or ROLLBACK"
                )

    return result


# ---------------------------------------------------------------------------
# Check 2: Orphaned rounds
# ---------------------------------------------------------------------------


def check_orphaned_rounds(
    transactions: list[RoundTransaction],
) -> CheckResult:
    """
    Invariant: no round may remain open (BET without WIN/ROLLBACK) for
    longer than MAX_OPEN_HOURS.

    This is the same detection logic as C1 but framed as an SLA violation
    rather than a missing-settlement check, so operators can tune thresholds
    independently.
    """
    result = CheckResult(
        f"H12-C2: No Orphaned Rounds (open > {MAX_OPEN_HOURS:.0f} h)"
    )
    now = datetime.now(timezone.utc)

    # Map (round_id, supplier_id) → earliest bet time
    round_bet_time: dict[tuple[str, str], datetime] = {}
    settled: set[tuple[str, str]] = set()

    for tx in transactions:
        key = (tx.round_id, tx.supplier_id)
        if tx.tx_type == TX_BET:
            if key not in round_bet_time or tx.created_at < round_bet_time[key]:
                round_bet_time[key] = tx.created_at
        elif tx.tx_type in (TX_WIN, TX_ROLLBACK):
            settled.add(key)

    for key, bet_time in round_bet_time.items():
        if key in settled:
            continue
        hours_open = (now - bet_time).total_seconds() / 3600
        if hours_open > MAX_OPEN_HOURS:
            round_id, supplier_id = key
            result.fail(
                f"Orphaned round {round_id!r} (supplier={supplier_id!r}): "
                f"open for {hours_open:.1f} h (threshold: {MAX_OPEN_HOURS:.0f} h)"
            )

    return result


# ---------------------------------------------------------------------------
# Check 3: Round amount balance
# ---------------------------------------------------------------------------


def check_round_amount_balance(
    transactions: list[RoundTransaction],
) -> CheckResult:
    """
    Invariant: for every settled round, total_bet >= total_win.
    Rounds where wins exceed bets indicate a potential fraud or payout error.

    ROLLBACK transactions are treated as partial or full bet reversals
    and reduce the effective bet amount.

    Rounds with ROLLBACK but no WIN are expected to have
    total_rollback == total_bet (full refund) — any discrepancy is flagged.
    """
    result = CheckResult("H12-C3: Round Amount Balance (bet >= win)")

    # Per (round_id, supplier_id): accumulate amounts
    bets:      dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    wins:      dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    rollbacks: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    has_win:   set[tuple[str, str]] = set()

    for tx in transactions:
        key = (tx.round_id, tx.supplier_id)
        if tx.tx_type == TX_BET:
            bets[key] += tx.amount
        elif tx.tx_type == TX_WIN:
            wins[key] += tx.amount
            has_win.add(key)
        elif tx.tx_type == TX_ROLLBACK:
            rollbacks[key] += tx.amount

    all_keys = set(bets) | set(wins)
    for key in all_keys:
        round_id, supplier_id = key
        total_bet = bets.get(key, Decimal("0"))
        total_win = wins.get(key, Decimal("0"))
        total_rollback = rollbacks.get(key, Decimal("0"))

        effective_bet = total_bet - total_rollback

        if key in has_win:
            # Check win does not exceed effective bet by more than tolerance
            if total_win > effective_bet + AMOUNT_TOLERANCE:
                result.fail(
                    f"Round {round_id!r} (supplier={supplier_id!r}): "
                    f"win {total_win} > effective_bet {effective_bet} "
                    f"(bet={total_bet}, rollback={total_rollback})"
                )
        else:
            # Rollback-only round: rollback should fully cover the bet
            discrepancy = abs(total_rollback - total_bet)
            if discrepancy > AMOUNT_TOLERANCE:
                result.fail(
                    f"Round {round_id!r} (supplier={supplier_id!r}): "
                    f"rollback {total_rollback} != bet {total_bet} "
                    f"(discrepancy: {discrepancy})"
                )

    return result


# ---------------------------------------------------------------------------
# Check 4: No duplicate round IDs across suppliers
# ---------------------------------------------------------------------------


def check_no_duplicate_round_ids(
    transactions: list[RoundTransaction],
) -> CheckResult:
    """
    Invariant: a given round_id must not appear under two different
    supplier_ids. Round IDs are supplier-scoped; if two suppliers reuse
    the same ID, it indicates either a supplier integration bug or a
    data pipeline collision.
    """
    result = CheckResult("H12-C4: No Duplicate Round IDs Across Suppliers")

    # Map round_id → set of supplier_ids seen
    round_suppliers: dict[str, set[str]] = defaultdict(set)
    for tx in transactions:
        round_suppliers[tx.round_id].add(tx.supplier_id)

    for round_id, supplier_set in round_suppliers.items():
        if len(supplier_set) > 1:
            result.fail(
                f"Round ID {round_id!r} appears under multiple suppliers: "
                f"{sorted(supplier_set)}"
            )

    return result


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all_checks() -> int:
    """Load data, run all H12 checks, print report, return exit code."""
    try:
        transactions = load_transactions()
    except RuntimeError as exc:
        logger.error("Failed to load transaction data: %s", exc)
        return 2

    logger.info("Loaded %d transactions for integrity check", len(transactions))

    checks_and_args = [
        (check_bet_settlement,      (transactions,)),
        (check_orphaned_rounds,     (transactions,)),
        (check_round_amount_balance, (transactions,)),
        (check_no_duplicate_round_ids, (transactions,)),
    ]

    results: list[CheckResult] = []
    for check_fn, args in checks_and_args:
        logger.info("Running %s ...", check_fn.__name__)
        try:
            r = check_fn(*args)
        except Exception as exc:  # noqa: BLE001
            r = CheckResult(check_name=check_fn.__name__, passed=False)
            r.fail(f"Unexpected exception: {exc}")
        results.append(r)

    print("\n" + "=" * 60)
    print("H12 Round State Integrity Check — Results")
    print("=" * 60)
    any_failure = False
    for r in results:
        print(r.summary())
        if not r.passed:
            any_failure = True

    total_violations = sum(len(r.violations) for r in results)
    print("=" * 60)
    if any_failure:
        print(
            f"RESULT: FAILED — {total_violations} violation(s) across "
            f"{sum(1 for r in results if not r.passed)} check(s)"
        )
        return 1
    else:
        print("RESULT: PASSED — all round state invariants satisfied")
        return 0


if __name__ == "__main__":
    sys.exit(run_all_checks())
