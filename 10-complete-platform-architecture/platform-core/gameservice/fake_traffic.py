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
fake_traffic.py
---------------
Simulates 100 concurrent game sessions against the AccountsBridge.

Each virtual player runs the full game lifecycle:
  1. authenticate  (mock)
  2. debit         (bet placement)
  3. credit        (win payout — 60% of players win)
  4. session ends

Metrics collected per-run:
  - total sessions attempted
  - successes, failures, idempotency replays
  - per-operation latency (p50 / p95 / p99)
  - error breakdown by type

Run with:
    python fake_traffic.py
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections import defaultdict
from decimal import Decimal
from typing import Optional

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from accounts_bridge import AccountsBridge, PlayerLockRegistry, TransactionCache
from accounts_provider import (
    CreditOperation,
    DebitOperation,
    PlayerSession,
)
from transaction_result import (
    BalanceStatus,
    GameServiceError,
    InsufficientFundsError,
    TransactionResult,
    TransactionStatus,
    TransactionType,
    success_result,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NUM_PLAYERS = 100
NUM_UNIQUE_PLAYERS = 20          # Players are recycled to exercise locking
SUPPLIERS = ["evolution", "pragmatic", "netent", "playngo", "hacksaw"]
WIN_RATE = 0.60                  # 60% of rounds produce a credit
BASE_BALANCE = Decimal("100000") # 1000.00 GBP in minor units


# ---------------------------------------------------------------------------
# Stub provider (in-process, no network)
# ---------------------------------------------------------------------------


class FakeProvider:
    """Fast in-memory provider that simulates realistic wallet behaviour."""

    def __init__(self, player_balances: dict[str, Decimal]) -> None:
        self._balances = player_balances
        self._lock = asyncio.Lock()

    async def authenticate(self, token: str) -> PlayerSession:
        player_id = token.split(":")[0]
        return _make_session(player_id)

    async def get_balance(self, session: PlayerSession, game_id: Optional[str] = None) -> BalanceStatus:
        return _balance_for(session.player_id, self._balances)

    async def debit(self, session: PlayerSession, operation: DebitOperation, context) -> TransactionResult:
        return await self.apply_transaction(session, [operation], context)

    async def credit(self, session: PlayerSession, operation: CreditOperation, context) -> TransactionResult:
        return await self.apply_transaction(session, [operation], context)

    async def refund(self, session, operation, context) -> TransactionResult:
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(self, session, operations, context) -> TransactionResult:
        # Simulate ~1ms of I/O per transaction
        await asyncio.sleep(0.001)
        async with self._lock:
            balance_val = self._balances.get(session.player_id, BASE_BALANCE)
            for op in operations:
                if isinstance(op, DebitOperation):
                    if balance_val < op.amount:
                        raise InsufficientFundsError("insufficient funds")
                    balance_val -= op.amount
                elif isinstance(op, CreditOperation):
                    balance_val += op.amount
            self._balances[session.player_id] = balance_val

        balance = BalanceStatus(
            cash_balance=balance_val,
            bonus_balance=Decimal("0"),
            currency=session.currency,
        )
        tx_type = TransactionType.DEBIT if any(isinstance(o, DebitOperation) for o in operations) else TransactionType.CREDIT
        return success_result(
            tx_type=tx_type,
            balance=balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
        )

    async def reverse_transaction(self, session, operations, context) -> TransactionResult:
        await asyncio.sleep(0.001)
        balance = _balance_for(session.player_id, self._balances)
        return success_result(
            tx_type=TransactionType.REFUND,
            balance=balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(player_id: str) -> PlayerSession:
    return PlayerSession(
        player_id=player_id,
        brand_id="brand-test",
        external_id=player_id,
        currency="GBP",
        country="GB",
        jurisdiction="UK",
        session_token=f"{player_id}:tok",
        game_id="gates-of-olympus",
    )


def _balance_for(player_id: str, balances: dict[str, Decimal]) -> BalanceStatus:
    return BalanceStatus(
        cash_balance=balances.get(player_id, BASE_BALANCE),
        bonus_balance=Decimal("0"),
        currency="GBP",
    )


# ---------------------------------------------------------------------------
# Player simulation
# ---------------------------------------------------------------------------


async def simulate_player(
    bridge: AccountsBridge,
    player_id: str,
    supplier_id: str,
    metrics: dict,
) -> None:
    session = _make_session(player_id)
    bet_amount = Decimal(str(random.randint(10, 500)))  # 0.10–5.00 GBP
    win_amount = Decimal(str(random.randint(int(bet_amount), int(bet_amount) * 10))) if random.random() < WIN_RATE else Decimal("0")
    round_id = f"round-{uuid.uuid4().hex[:8]}"
    supplier_ref_debit = f"debit-{uuid.uuid4().hex[:12]}"
    supplier_ref_credit = f"credit-{uuid.uuid4().hex[:12]}"

    # --- Debit (bet) ---------------------------------------------------------
    t0 = time.monotonic()
    try:
        debit_result = await bridge.debit(
            session=session,
            supplier_id=supplier_id,
            supplier_ref=supplier_ref_debit,
            round_id=round_id,
            amount=bet_amount,
        )
        latency_ms = (time.monotonic() - t0) * 1000
        metrics["debit_latencies"].append(latency_ms)

        if debit_result.succeeded:
            metrics["debit_success"] += 1
        elif debit_result.already_processed:
            metrics["debit_idempotent"] += 1
        else:
            metrics["debit_failure"] += 1

    except InsufficientFundsError:
        metrics["debit_insufficient_funds"] += 1
        return
    except GameServiceError as exc:
        metrics["errors"].append(type(exc).__name__)
        return

    # --- Credit (win) --------------------------------------------------------
    if win_amount > 0:
        t0 = time.monotonic()
        try:
            credit_result = await bridge.credit(
                session=session,
                supplier_id=supplier_id,
                supplier_ref=supplier_ref_credit,
                round_id=round_id,
                amount=win_amount,
            )
            latency_ms = (time.monotonic() - t0) * 1000
            metrics["credit_latencies"].append(latency_ms)

            if credit_result.succeeded:
                metrics["credit_success"] += 1
            elif credit_result.already_processed:
                metrics["credit_idempotent"] += 1
            else:
                metrics["credit_failure"] += 1

        except GameServiceError as exc:
            metrics["errors"].append(type(exc).__name__)

    metrics["sessions_completed"] += 1


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def run_traffic_test() -> None:
    print(f"\n{'='*60}")
    print(f"Fake Traffic Test — {NUM_PLAYERS} concurrent game sessions")
    print(f"{'='*60}\n")

    player_ids = [f"player-{i:03d}" for i in range(NUM_UNIQUE_PLAYERS)]
    player_balances: dict[str, Decimal] = {pid: BASE_BALANCE for pid in player_ids}

    provider = FakeProvider(player_balances)
    bridge = AccountsBridge(
        provider_factory=lambda sid: provider,
        player_repo=_make_in_memory_repo(),
        lock_registry=PlayerLockRegistry(),
        tx_cache=TransactionCache(),
    )

    metrics: dict = defaultdict(int)
    metrics["debit_latencies"] = []
    metrics["credit_latencies"] = []
    metrics["errors"] = []

    t_start = time.monotonic()

    tasks = [
        simulate_player(
            bridge=bridge,
            player_id=random.choice(player_ids),
            supplier_id=random.choice(SUPPLIERS),
            metrics=metrics,
        )
        for _ in range(NUM_PLAYERS)
    ]
    await asyncio.gather(*tasks)

    elapsed = time.monotonic() - t_start

    # --- Report --------------------------------------------------------------
    all_debit_lat = sorted(metrics["debit_latencies"])
    all_credit_lat = sorted(metrics["credit_latencies"])

    def percentile(data: list[float], p: int) -> float:
        if not data:
            return 0.0
        idx = int(len(data) * p / 100)
        return data[min(idx, len(data) - 1)]

    print(f"Wall time:              {elapsed*1000:.1f} ms")
    print(f"Sessions completed:     {metrics['sessions_completed']} / {NUM_PLAYERS}")
    print(f"")
    print(f"Debit results:")
    print(f"  success:              {metrics['debit_success']}")
    print(f"  idempotent replays:   {metrics['debit_idempotent']}")
    print(f"  insufficient funds:   {metrics['debit_insufficient_funds']}")
    print(f"  other failures:       {metrics['debit_failure']}")
    print(f"")
    print(f"Credit results:")
    print(f"  success:              {metrics['credit_success']}")
    print(f"  idempotent replays:   {metrics['credit_idempotent']}")
    print(f"  other failures:       {metrics['credit_failure']}")
    print(f"")
    print(f"Latency — debit (ms):")
    print(f"  p50={percentile(all_debit_lat, 50):.2f}  p95={percentile(all_debit_lat, 95):.2f}  p99={percentile(all_debit_lat, 99):.2f}")
    print(f"Latency — credit (ms):")
    print(f"  p50={percentile(all_credit_lat, 50):.2f}  p95={percentile(all_credit_lat, 95):.2f}  p99={percentile(all_credit_lat, 99):.2f}")
    print(f"")
    if metrics["errors"]:
        from collections import Counter
        print(f"Errors: {dict(Counter(metrics['errors']))}")
    else:
        print(f"Errors:                 none")

    success_rate = metrics["sessions_completed"] / NUM_PLAYERS * 100
    print(f"\nOverall success rate:   {success_rate:.1f}%")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# In-memory repo (reuse from tests concept)
# ---------------------------------------------------------------------------

from accounts_bridge import PlayerRepository, TransactionRecord  # noqa: E402


class _InMemoryRepo(PlayerRepository):
    def __init__(self) -> None:
        self._records: dict = {}

    async def load_player(self, player_id: str):
        return {"player_id": player_id}

    async def load_from_supplier_ref(self, supplier_id: str, supplier_ref: str):
        return self._records.get((supplier_id, supplier_ref))

    async def record_request(self, record: TransactionRecord) -> TransactionRecord:
        self._records[(record.supplier_id, record.supplier_ref)] = record
        return record

    async def record_result(self, record: TransactionRecord, delete_existing: bool = False) -> None:
        self._records[(record.supplier_id, record.supplier_ref)] = record

    async def mark_refunded(self, record: TransactionRecord) -> None:
        record.refunded = True


def _make_in_memory_repo() -> _InMemoryRepo:
    return _InMemoryRepo()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(run_traffic_test())
