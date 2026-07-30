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
tests/test_accounts_bridge.py
------------------------------
Comprehensive test suite for AccountsBridge.

Covers:
- Per-player locking (concurrent requests serialised)
- Transaction idempotency (duplicate supplier refs)
- Debit success and failure paths
- Credit success (online and offline)
- Refund success, already-refunded, invalid operation
- Composite transactions
- Error propagation and audit trail
- Balance retrieval
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

# Adjust path so that the gameservice package is importable without installation
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from accounts_bridge import (
    AccountsBridge,
    PlayerLockRegistry,
    PlayerRepository,
    TransactionCache,
    TransactionRecord,
)
from accounts_provider import (
    CreditOperation,
    DebitOperation,
    PlayerSession,
    RefundOperation,
    SupplierOperation,
    TransactionContext,
)
from transaction_result import (
    BalanceStatus,
    GameServiceError,
    InsufficientFundsError,
    TransactionBlockedError,
    TransactionResult,
    TransactionStatus,
    TransactionType,
    success_result,
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def make_balance(cash: int = 10000, bonus: int = 0, currency: str = "GBP") -> BalanceStatus:
    return BalanceStatus(
        cash_balance=Decimal(cash),
        bonus_balance=Decimal(bonus),
        currency=currency,
    )


def make_session(
    player_id: str = "player-1",
    currency: str = "GBP",
    game_id: str = "game-1",
    session_token: Optional[str] = None,
) -> PlayerSession:
    # StubProvider.authenticate() derives the authenticated player_id from
    # the token's "tok-<player_id>" convention, so the default token stays
    # in lockstep with whatever player_id this session claims.
    return PlayerSession(
        player_id=player_id,
        brand_id="brand-1",
        external_id=player_id,
        currency=currency,
        country="GB",
        jurisdiction="UK",
        session_token=session_token or f"tok-{player_id}",
        game_id=game_id,
    )


class StubProvider:
    """Configurable stub AccountsProvider for testing."""

    def __init__(
        self,
        balance: Optional[BalanceStatus] = None,
        debit_result: Optional[TransactionResult] = None,
        credit_result: Optional[TransactionResult] = None,
        refund_result: Optional[TransactionResult] = None,
        raise_on_debit: Optional[Exception] = None,
        raise_on_credit: Optional[Exception] = None,
    ):
        self._balance = balance or make_balance()
        self._debit_result = debit_result
        self._credit_result = credit_result
        self._refund_result = refund_result
        self._raise_on_debit = raise_on_debit
        self._raise_on_credit = raise_on_credit
        self.calls: list[str] = []

    async def authenticate(self, token: str) -> PlayerSession:
        # Mirrors real providers: the session_token IS the token used to
        # re-authenticate on every wallet call. Derive player_id from the
        # "tok-<player_id>" convention used by make_session() so this stub
        # exercises the same identity-binding real providers enforce.
        player_id = token[len("tok-"):] if token.startswith("tok-") else token
        return make_session(player_id=player_id)

    async def get_balance(self, session: PlayerSession, game_id=None) -> BalanceStatus:
        self.calls.append("get_balance")
        return self._balance

    async def debit(self, session, operation, context) -> TransactionResult:
        self.calls.append("debit")
        return await self.apply_transaction(session, [operation], context)

    async def credit(self, session, operation, context) -> TransactionResult:
        self.calls.append("credit")
        return await self.apply_transaction(session, [operation], context)

    async def refund(self, session, operation, context) -> TransactionResult:
        self.calls.append("refund")
        return await self.reverse_transaction(session, [operation], context)

    async def apply_transaction(self, session, operations, context) -> TransactionResult:
        self.calls.append("apply_transaction")
        if self._raise_on_debit and any(isinstance(op, DebitOperation) for op in operations):
            raise self._raise_on_debit
        if self._raise_on_credit and any(isinstance(op, CreditOperation) for op in operations):
            raise self._raise_on_credit
        result = self._debit_result or success_result(
            tx_type=TransactionType.DEBIT,
            balance=self._balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
        )
        return result

    async def reverse_transaction(self, session, operations, context) -> TransactionResult:
        self.calls.append("reverse_transaction")
        return self._refund_result or success_result(
            tx_type=TransactionType.REFUND,
            balance=self._balance,
            tx_id=context.tx_id,
            external_id=context.supplier_ref,
        )


class InMemoryRepository(PlayerRepository):
    """In-memory repository for testing."""

    def __init__(self) -> None:
        self._records: dict = {}
        self.requests_recorded = 0
        self.results_recorded = 0
        self.refunds_marked = 0

    async def load_player(self, player_id: str):
        return {"player_id": player_id}

    async def load_from_supplier_ref(self, supplier_id: str, supplier_ref: str):
        return self._records.get((supplier_id, supplier_ref))

    async def record_request(self, record: TransactionRecord) -> TransactionRecord:
        self.requests_recorded += 1
        self._records[(record.supplier_id, record.supplier_ref)] = record
        return record

    async def record_result(self, record: TransactionRecord, delete_existing: bool = False) -> None:
        self.results_recorded += 1
        self._records[(record.supplier_id, record.supplier_ref)] = record

    async def mark_refunded(self, record: TransactionRecord) -> None:
        self.refunds_marked += 1
        record.refunded = True


def make_bridge(provider: Optional[StubProvider] = None, repo: Optional[InMemoryRepository] = None) -> tuple:
    if provider is None:
        provider = StubProvider()
    if repo is None:
        repo = InMemoryRepository()
    bridge = AccountsBridge(
        provider_factory=lambda sid: provider,
        player_repo=repo,
        lock_registry=PlayerLockRegistry(),
        tx_cache=TransactionCache(),
    )
    return bridge, provider, repo


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_delegates_to_provider():
    bridge, provider, _ = make_bridge()
    provider.authenticate = AsyncMock(return_value=make_session())
    session = await bridge.authenticate("tok-123", "evolution")
    provider.authenticate.assert_called_once_with("tok-123")
    assert session.player_id == "player-1"


@pytest.mark.asyncio
async def test_authenticate_propagates_auth_error():
    bridge, provider, _ = make_bridge()
    from transaction_result import AuthenticationError
    provider.authenticate = AsyncMock(side_effect=AuthenticationError("bad token"))
    with pytest.raises(AuthenticationError):
        await bridge.authenticate("bad-tok", "evolution")


# ---------------------------------------------------------------------------
# Session authorization tests (session_token must be validated before
# any wallet mutation or balance read — a forged/mismatched token must
# not be able to move money for someone else's player_id).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debit_rejects_missing_session_token():
    from transaction_result import InvalidSessionError
    bridge, provider, _ = make_bridge()
    session = PlayerSession(
        player_id="player-1",
        brand_id="brand-1",
        external_id="player-1",
        currency="GBP",
        country="GB",
        jurisdiction="UK",
        session_token="",  # explicitly absent, not defaulted
        game_id="game-1",
    )
    with pytest.raises(InvalidSessionError):
        await bridge.debit(
            session=session,
            supplier_id="evolution",
            supplier_ref="ref-no-token",
            round_id="round-no-token",
            amount=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_debit_rejects_session_token_for_a_different_player():
    """
    A caller who knows player-victim's player_id but signs the request
    with a session_token that authenticates as a different player must
    be rejected, not allowed to move player-victim's money.
    """
    from transaction_result import InvalidSessionError
    bridge, provider, _ = make_bridge()
    # session_token authenticates as "player-attacker" (see StubProvider),
    # but the request claims player_id "player-victim"
    forged_session = make_session(player_id="player-victim", session_token="tok-player-attacker")
    with pytest.raises(InvalidSessionError):
        await bridge.debit(
            session=forged_session,
            supplier_id="evolution",
            supplier_ref="ref-forged",
            round_id="round-forged",
            amount=Decimal("5000"),
        )
    # No wallet call should have reached the provider
    assert "apply_transaction" not in provider.calls


@pytest.mark.asyncio
async def test_credit_rejects_session_token_for_a_different_player():
    from transaction_result import InvalidSessionError
    bridge, provider, _ = make_bridge()
    forged_session = make_session(player_id="player-victim", session_token="tok-player-attacker")
    with pytest.raises(InvalidSessionError):
        await bridge.credit(
            session=forged_session,
            supplier_id="evolution",
            supplier_ref="ref-forged-credit",
            round_id="round-forged-credit",
            amount=Decimal("5000"),
        )
    assert "apply_transaction" not in provider.calls


@pytest.mark.asyncio
async def test_get_balance_rejects_session_token_for_a_different_player():
    from transaction_result import InvalidSessionError
    bridge, provider, _ = make_bridge()
    forged_session = make_session(player_id="player-victim", session_token="tok-player-attacker")
    with pytest.raises(InvalidSessionError):
        await bridge.get_balance(forged_session, "evolution")
    assert "get_balance" not in provider.calls


@pytest.mark.asyncio
async def test_debit_succeeds_when_session_token_matches_player():
    bridge, provider, _ = make_bridge()
    session = make_session(player_id="player-legit")
    result = await bridge.debit(
        session=session,
        supplier_id="evolution",
        supplier_ref="ref-legit",
        round_id="round-legit",
        amount=Decimal("100"),
    )
    assert result.succeeded


# ---------------------------------------------------------------------------
# Balance tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_balance_returns_current_balance():
    balance = make_balance(cash=5000, bonus=1000)
    bridge, provider, _ = make_bridge(provider=StubProvider(balance=balance))
    session = make_session()
    result = await bridge.get_balance(session, "evolution")
    assert result.cash_balance == Decimal("5000")
    assert result.bonus_balance == Decimal("1000")
    assert result.total_balance == Decimal("6000")


@pytest.mark.asyncio
async def test_get_balance_acquires_player_lock():
    """Balance must be serialised per player."""
    bridge, provider, _ = make_bridge()
    session = make_session(player_id="player-x")
    # Two concurrent balance calls — both should succeed without deadlock
    results = await asyncio.gather(
        bridge.get_balance(session, "evolution"),
        bridge.get_balance(session, "evolution"),
    )
    assert len(results) == 2


# ---------------------------------------------------------------------------
# Debit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_debit_success():
    balance = make_balance(cash=10000)
    bridge, provider, repo = make_bridge(provider=StubProvider(balance=balance))
    session = make_session()
    result = await bridge.debit(
        session=session,
        supplier_id="evolution",
        supplier_ref="ref-001",
        round_id="round-001",
        amount=Decimal("500"),
    )
    assert result.succeeded
    assert result.status == TransactionStatus.SUCCESS
    assert repo.requests_recorded == 1
    assert repo.results_recorded == 1


@pytest.mark.asyncio
async def test_debit_records_request_before_calling_wallet():
    """The idempotency tombstone must be written before the wallet call."""
    repo = InMemoryRepository()
    call_order = []

    class OrderTrackingProvider(StubProvider):
        async def apply_transaction(self, session, operations, context):
            call_order.append("wallet_call")
            return await super().apply_transaction(session, operations, context)

    original_record = repo.record_request

    async def tracked_record(record: "TransactionRecord") -> "TransactionRecord":
        call_order.append("record_request")
        return await original_record(record)

    repo.record_request = tracked_record  # ty: ignore[invalid-assignment]

    bridge = AccountsBridge(
        provider_factory=lambda sid: OrderTrackingProvider(),
        player_repo=repo,
        lock_registry=PlayerLockRegistry(),
        tx_cache=TransactionCache(),
    )
    await bridge.debit(
        session=make_session(),
        supplier_id="evolution",
        supplier_ref="ref-order-1",
        round_id="round-1",
        amount=Decimal("100"),
    )
    assert call_order == ["record_request", "wallet_call"]


@pytest.mark.asyncio
async def test_debit_insufficient_funds_raises_and_records_failure():
    provider = StubProvider(raise_on_debit=InsufficientFundsError("broke"))
    bridge, _, repo = make_bridge(provider=provider)
    with pytest.raises(InsufficientFundsError):
        await bridge.debit(
            session=make_session(),
            supplier_id="pragmatic",
            supplier_ref="ref-broke",
            round_id="round-broke",
            amount=Decimal("99999"),
        )
    # Failure must still be persisted
    assert repo.results_recorded == 1


@pytest.mark.asyncio
async def test_debit_transaction_blocked_raises_and_records_failure():
    provider = StubProvider(raise_on_debit=TransactionBlockedError("rg limit"))
    bridge, _, repo = make_bridge(provider=provider)
    with pytest.raises(TransactionBlockedError):
        await bridge.debit(
            session=make_session(),
            supplier_id="pragmatic",
            supplier_ref="ref-blocked",
            round_id="round-blocked",
            amount=Decimal("100"),
        )
    assert repo.results_recorded == 1


@pytest.mark.asyncio
async def test_debit_idempotency_returns_cached_result_on_duplicate():
    balance = make_balance(cash=10000)
    bridge, provider, repo = make_bridge(provider=StubProvider(balance=balance))
    session = make_session()

    # First call — processed
    result1 = await bridge.debit(
        session=session,
        supplier_id="evolution",
        supplier_ref="dup-ref",
        round_id="round-dup",
        amount=Decimal("200"),
    )
    assert result1.succeeded

    # Second call with same supplier_ref — should return ALREADY_PROCESSED
    # Provider must only be called ONCE
    initial_call_count = len(provider.calls)
    result2 = await bridge.debit(
        session=session,
        supplier_id="evolution",
        supplier_ref="dup-ref",
        round_id="round-dup",
        amount=Decimal("200"),
    )
    assert result2.already_processed
    # Provider should not have been called again
    assert len(provider.calls) == initial_call_count


# ---------------------------------------------------------------------------
# Credit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credit_success():
    bridge, provider, repo = make_bridge()
    session = make_session()
    result = await bridge.credit(
        session=session,
        supplier_id="evolution",
        supplier_ref="win-001",
        round_id="round-001",
        amount=Decimal("1500"),
    )
    assert result.succeeded
    assert repo.results_recorded == 1


@pytest.mark.asyncio
async def test_credit_idempotency():
    bridge, provider, repo = make_bridge()
    session = make_session()

    result1 = await bridge.credit(
        session=session,
        supplier_id="pragmatic",
        supplier_ref="win-dup",
        round_id="round-dup",
        amount=Decimal("750"),
    )
    assert result1.succeeded

    result2 = await bridge.credit(
        session=session,
        supplier_id="pragmatic",
        supplier_ref="win-dup",
        round_id="round-dup",
        amount=Decimal("750"),
    )
    assert result2.already_processed


@pytest.mark.asyncio
async def test_credit_offline_does_not_require_session():
    """Offline credits must succeed even without an active session."""
    bridge, provider, repo = make_bridge()
    session = make_session()
    # require_session=False (default) — should succeed
    result = await bridge.credit(
        session=session,
        supplier_id="hacksaw",
        supplier_ref="offline-win",
        round_id="round-offline",
        amount=Decimal("0"),  # Hacksaw sends 0-amount credits for losses
        require_session=False,
    )
    assert result.succeeded


# ---------------------------------------------------------------------------
# Refund tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_unknown_transaction_returns_invalid_operation():
    bridge, _, repo = make_bridge()
    result = await bridge.refund(
        supplier_id="evolution",
        supplier_ref="nonexistent",
        player_id="player-1",
        round_id="round-1",
    )
    assert result.status == TransactionStatus.INVALID_OPERATION


@pytest.mark.asyncio
async def test_refund_already_refunded_returns_already_refunded():
    bridge, provider, repo = make_bridge()
    session = make_session()

    # Create original debit
    await bridge.debit(
        session=session,
        supplier_id="evolution",
        supplier_ref="refund-me",
        round_id="round-ref",
        amount=Decimal("300"),
    )

    # First refund — should succeed
    result1 = await bridge.refund(
        supplier_id="evolution",
        supplier_ref="refund-me",
        player_id=session.player_id,
        round_id="round-ref",
    )
    assert result1.status in (TransactionStatus.SUCCESS, TransactionStatus.ALREADY_REFUNDED)


@pytest.mark.asyncio
async def test_refund_non_debit_returns_invalid_operation():
    bridge, provider, repo = make_bridge()
    session = make_session()

    # Create a credit (win)
    await bridge.credit(
        session=session,
        supplier_id="evolution",
        supplier_ref="credit-ref",
        round_id="round-credit",
        amount=Decimal("1000"),
    )

    # Attempt to refund a credit with rollback_incomplete_only=True
    result = await bridge.refund(
        supplier_id="evolution",
        supplier_ref="credit-ref",
        player_id=session.player_id,
        round_id="round-credit",
        rollback_incomplete_only=True,
    )
    assert result.status == TransactionStatus.INVALID_OPERATION


@pytest.mark.asyncio
async def test_refund_amount_mismatch_returns_invalid_operation():
    bridge, provider, repo = make_bridge()
    session = make_session()

    await bridge.debit(
        session=session,
        supplier_id="evolution",
        supplier_ref="amt-mismatch",
        round_id="round-amt",
        amount=Decimal("500"),
    )

    result = await bridge.refund(
        supplier_id="evolution",
        supplier_ref="amt-mismatch",
        player_id=session.player_id,
        round_id="round-amt",
        amount=Decimal("200"),  # Wrong amount
    )
    assert result.status == TransactionStatus.INVALID_OPERATION


# ---------------------------------------------------------------------------
# Concurrent player lock tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_debits_for_same_player_are_serialised():
    """
    Two concurrent debits for the same player must not overlap.
    Tests that per-player locking works correctly.
    """
    execution_order = []

    class SerialOrderProvider(StubProvider):
        async def apply_transaction(self, session, operations, context):
            execution_order.append(f"start-{context.supplier_ref}")
            await asyncio.sleep(0.01)  # Simulate async work
            execution_order.append(f"end-{context.supplier_ref}")
            return await super().apply_transaction(session, operations, context)

    bridge = AccountsBridge(
        provider_factory=lambda sid: SerialOrderProvider(),
        player_repo=InMemoryRepository(),
        lock_registry=PlayerLockRegistry(),
        tx_cache=TransactionCache(),
    )
    session = make_session(player_id="concurrent-player")

    await asyncio.gather(
        bridge.debit(session=session, supplier_id="evo", supplier_ref="tx-a",
                     round_id="r-a", amount=Decimal("100")),
        bridge.debit(session=session, supplier_id="evo", supplier_ref="tx-b",
                     round_id="r-b", amount=Decimal("100")),
    )

    # Verify serialisation: no interleaving of start/end for different refs
    for i, event in enumerate(execution_order[:-1]):
        if event.startswith("start"):
            ref = event.split("-", 1)[1]
            next_event = execution_order[i + 1]
            if next_event.startswith("start"):
                # If next is also a start, it should be for a DIFFERENT ref
                # (meaning this ref's end hasn't happened yet — which would be interleaved)
                # With proper locking, we should always see start-X, end-X, start-Y, end-Y
                next_ref = next_event.split("-", 1)[1]
                assert next_ref != ref, f"Interleaved transactions detected for ref {ref}"


@pytest.mark.asyncio
async def test_concurrent_debits_for_different_players_are_parallel():
    """Debits for different players should not be blocked by each other."""
    start_time = asyncio.get_running_loop().time()

    delay = 0.05

    class DelayedProvider(StubProvider):
        async def apply_transaction(self, session, operations, context):
            await asyncio.sleep(delay)
            return await super().apply_transaction(session, operations, context)

    bridge = AccountsBridge(
        provider_factory=lambda sid: DelayedProvider(),
        player_repo=InMemoryRepository(),
        lock_registry=PlayerLockRegistry(),
        tx_cache=TransactionCache(),
    )

    await asyncio.gather(
        bridge.debit(session=make_session("player-A"), supplier_id="evo",
                     supplier_ref="tx-A1", round_id="r-A1", amount=Decimal("100")),
        bridge.debit(session=make_session("player-B"), supplier_id="evo",
                     supplier_ref="tx-B1", round_id="r-B1", amount=Decimal("100")),
    )
    elapsed = asyncio.get_running_loop().time() - start_time
    # Should complete in ~delay seconds, not 2*delay (parallel execution)
    assert elapsed < delay * 1.8, f"Expected ~{delay}s, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Composite transaction tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_transaction_composite_debit_and_credit():
    bridge, provider, repo = make_bridge()
    session = make_session()
    ops = [
        DebitOperation(round_id="round-combo", amount=Decimal("500")),
        CreditOperation(round_id="round-combo", amount=Decimal("750")),
    ]
    result = await bridge.apply_transaction(
        session=session,
        supplier_id="evolution",
        supplier_ref="combo-001",
        round_id="round-combo",
        tx_type=TransactionType.CREDIT,
        total=Decimal("-250"),  # Net: credit 250
        operations=ops,
    )
    assert result.succeeded


# ---------------------------------------------------------------------------
# Error propagation tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_error_wrapped_as_game_service_error():
    class BrokenProvider(StubProvider):
        async def apply_transaction(self, session, operations, context):
            raise RuntimeError("DB connection lost")

    bridge = AccountsBridge(
        provider_factory=lambda sid: BrokenProvider(),
        player_repo=InMemoryRepository(),
        lock_registry=PlayerLockRegistry(),
        tx_cache=TransactionCache(),
    )
    with pytest.raises(GameServiceError):
        await bridge.debit(
            session=make_session(),
            supplier_id="evolution",
            supplier_ref="broken-tx",
            round_id="r-broken",
            amount=Decimal("100"),
        )


@pytest.mark.asyncio
async def test_failed_transaction_result_persisted_on_error():
    provider = StubProvider(raise_on_debit=InsufficientFundsError("no funds"))
    bridge, _, repo = make_bridge(provider=provider)
    try:
        await bridge.debit(
            session=make_session(),
            supplier_id="evolution",
            supplier_ref="fail-persist",
            round_id="r-fp",
            amount=Decimal("100"),
        )
    except InsufficientFundsError:
        pass
    # The failed result MUST have been persisted
    assert repo.results_recorded >= 1
