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
accounts_bridge.py
------------------
Central transaction coordinator for the Game Aggregation Layer (GAL).

The AccountsBridge is the single entry-point for all wallet operations
triggered by supplier callbacks. It is responsible for:

1. Per-player locking — only one concurrent wallet operation per player.
2. Transaction idempotency — duplicate supplier references are detected
   and the cached result is returned without re-processing.
3. Routing — delegating to the correct AccountsProvider based on supplier.
4. Audit logging — every request and result is persisted before and after
   the wallet call, even if the wallet call fails.
5. Error normalisation — supplier exceptions are mapped to platform errors.

Architecture note
-----------------
The bridge does NOT hold a database connection. It calls into
repository abstractions (PlayerRepository, TransactionRepository) that
are injected at construction time. This keeps the bridge testable and
decoupled from the storage layer.

All public methods are async and designed to be called from the FastAPI
route handlers in main.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from decimal import Decimal
from typing import AsyncGenerator, Callable, Optional

from accounts_provider import (
    AccountsProvider,
    AdjustOperation,
    ClawbackOperation,
    CreditOperation,
    DebitOperation,
    PlayerSession,
    RefundOperation,
    SupplierOperation,
    TransactionContext,
)
from transaction_result import (
    BalanceStatus,
    DatabaseError,
    GameServiceError,
    InsufficientFundsError,
    InvalidSessionError,
    NoMatchingDebitError,
    TransactionBlockedError,
    TransactionResult,
    TransactionStatus,
    TransactionType,
    already_processed_result,
    failure_result,
    success_result,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Idempotency / dedup records
# ---------------------------------------------------------------------------


@dataclass
class TransactionRecord:
    """Persisted record of a transaction request and its result."""

    tx_id: str
    supplier_id: str
    supplier_ref: str
    player_id: str
    game_id: str
    tx_type: TransactionType
    amount: Decimal
    round_id: str
    result: Optional[TransactionResult] = None
    refunded: bool = False

    @property
    def succeeded(self) -> bool:
        return self.result is not None and self.result.succeeded

    @property
    def previously_failed(self) -> bool:
        return self.result is not None and not self.result.succeeded


# ---------------------------------------------------------------------------
# Repository protocols (injected — not imported from a concrete module)
# ---------------------------------------------------------------------------


class PlayerRepository:
    """
    Thin in-memory stub — replace with a real async DB repository.

    In production this wraps asyncpg or SQLAlchemy async.
    """

    async def load_player(self, player_id: str) -> Optional[dict]:
        raise NotImplementedError

    async def load_from_supplier_ref(
        self, supplier_id: str, supplier_ref: str
    ) -> Optional[TransactionRecord]:
        raise NotImplementedError

    async def record_request(self, record: TransactionRecord) -> TransactionRecord:
        raise NotImplementedError

    async def record_result(self, record: TransactionRecord, delete_existing: bool = False) -> None:
        raise NotImplementedError

    async def mark_refunded(self, record: TransactionRecord) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# In-process lock registry (per-player mutex)
# ---------------------------------------------------------------------------


class PlayerLockRegistry:
    """
    Manages one asyncio.Lock per player_id.

    Locks are created lazily and held for the duration of a transaction.
    This prevents concurrent wallet calls for the same player, which would
    cause race conditions on balance checks and idempotency lookups.

    NOTE: This registry is in-process only. In a multi-instance deployment
    you need a distributed lock (e.g. Redis SETNX with TTL) instead.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._meta_lock = asyncio.Lock()

    async def get_lock(self, player_id: str) -> asyncio.Lock:
        async with self._meta_lock:
            if player_id not in self._locks:
                self._locks[player_id] = asyncio.Lock()
            return self._locks[player_id]

    @asynccontextmanager
    async def player_lock(self, player_id: str) -> AsyncGenerator[None, None]:
        lock = await self.get_lock(player_id)
        async with lock:
            yield


# ---------------------------------------------------------------------------
# Dedup cache (in-memory; replace with Redis in production)
# ---------------------------------------------------------------------------


class TransactionCache:
    """
    In-memory idempotency cache keyed by (supplier_id, supplier_ref).

    In production, back this with Redis with a 24-hour TTL so that
    duplicate requests that arrive after a pod restart are still handled.
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], TransactionRecord] = {}

    def get(self, supplier_id: str, supplier_ref: str) -> Optional[TransactionRecord]:
        return self._cache.get((supplier_id, supplier_ref))

    def put(self, record: TransactionRecord) -> None:
        self._cache[(record.supplier_id, record.supplier_ref)] = record

    def remove(self, supplier_id: str, supplier_ref: str) -> None:
        self._cache.pop((supplier_id, supplier_ref), None)


# ---------------------------------------------------------------------------
# AccountsBridge
# ---------------------------------------------------------------------------


class AccountsBridge:
    """
    Central transaction coordinator.

    Typical call flow for a debit:

        1.  Route receives POST from supplier → calls bridge.debit()
        2.  Bridge acquires per-player lock
        3.  Bridge records PENDING request (idempotency key)
        4.  Bridge resolves AccountsProvider for the supplier
        5.  Bridge calls provider.debit()
        6.  Bridge records result (SUCCESS or FAILURE)
        7.  Bridge releases lock and returns TransactionResult

    If the same (supplier_id, supplier_ref) is received again in step 3,
    the bridge returns the cached result immediately without re-processing.
    """

    def __init__(
        self,
        provider_factory: Callable[[str], AccountsProvider],
        player_repo: PlayerRepository,
        lock_registry: Optional[PlayerLockRegistry] = None,
        tx_cache: Optional[TransactionCache] = None,
    ) -> None:
        """
        Args:
            provider_factory:  Callable that returns the AccountsProvider
                               for a given supplier_id.
            player_repo:       Repository for player and transaction data.
            lock_registry:     Optional — defaults to a new in-process registry.
            tx_cache:          Optional — defaults to a new in-memory cache.
        """
        self._provider_factory = provider_factory
        self._player_repo = player_repo
        self._locks = lock_registry or PlayerLockRegistry()
        self._cache = tx_cache or TransactionCache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def authenticate(self, token: str, supplier_id: str) -> PlayerSession:
        """
        Validate a game-launch token and return the player session.

        The supplier_id is used to resolve the correct provider — different
        suppliers use different token formats and validation endpoints.
        """
        provider = self._provider_factory(supplier_id)
        session = await provider.authenticate(token)
        logger.info("authenticate OK player_id=%s supplier=%s", session.player_id, supplier_id)
        return session

    async def get_balance(self, session: PlayerSession, supplier_id: str) -> BalanceStatus:
        """
        Retrieve the player's current balance under a per-player lock.

        The lock prevents a race between a concurrent debit and this
        balance read. The returned balance is always post-debit.
        """
        async with self._locks.player_lock(session.player_id):
            provider = self._provider_factory(supplier_id)
            await self._authorize_session(provider, session)
            balance = await provider.get_balance(session, session.game_id)
            logger.debug(
                "get_balance player_id=%s balance=%s %s",
                session.player_id,
                balance.total_balance,
                balance.currency,
            )
            return balance

    async def debit(
        self,
        session: PlayerSession,
        supplier_id: str,
        supplier_ref: str,
        round_id: str,
        amount: Decimal,
        reject_if_rc_elapsed: bool = False,
    ) -> TransactionResult:
        """
        Deduct a stake from the player's wallet.

        Idempotency: if (supplier_id, supplier_ref) has already been
        processed, returns the cached result without touching the wallet.
        """
        operation = DebitOperation(round_id=round_id, amount=amount)
        context = TransactionContext(
            tx_id=str(uuid.uuid4()),
            supplier_ref=supplier_ref,
            disallow_locked=True,
            reject_if_rc_elapsed=reject_if_rc_elapsed,
        )
        return await self._execute_transaction(
            session=session,
            supplier_id=supplier_id,
            supplier_ref=supplier_ref,
            tx_type=TransactionType.DEBIT,
            round_id=round_id,
            amount=amount,
            operations=[operation],
            context=context,
        )

    async def credit(
        self,
        session: PlayerSession,
        supplier_id: str,
        supplier_ref: str,
        round_id: str,
        amount: Decimal,
        require_session: bool = False,
        related_tx_id: Optional[str] = None,
    ) -> TransactionResult:
        """
        Add winnings to the player's wallet.

        Credits can be offline (no active session required) unless
        require_session=True. This allows suppliers to credit wins for
        rounds that finished after the player navigated away.
        """
        operation = CreditOperation(round_id=round_id, amount=amount)
        context = TransactionContext(
            tx_id=str(uuid.uuid4()),
            supplier_ref=supplier_ref,
            offline=not require_session,
        )
        return await self._execute_transaction(
            session=session,
            supplier_id=supplier_id,
            supplier_ref=supplier_ref,
            tx_type=TransactionType.CREDIT,
            round_id=round_id,
            amount=amount,
            operations=[operation],
            context=context,
        )

    async def refund(
        self,
        supplier_id: str,
        supplier_ref: str,
        player_id: str,
        round_id: str,
        amount: Optional[Decimal] = None,
        rollback_incomplete_only: bool = True,
    ) -> TransactionResult:
        """
        Reverse a previous debit (incomplete-round rollback).

        Locates the original transaction by (supplier_id, supplier_ref)
        and reverses it. If the transaction was already refunded, returns
        a cached ALREADY_REFUNDED result.

        Args:
            rollback_incomplete_only: When True (default), only DEBIT
                transactions can be rolled back. Credits and mixed types
                are rejected with INVALID_OPERATION.
        """
        async with self._locks.player_lock(player_id):
            # Look up original transaction
            original = self._cache.get(supplier_id, supplier_ref)
            if original is None:
                original = await self._player_repo.load_from_supplier_ref(supplier_id, supplier_ref)

            if original is None:
                logger.warning(
                    "refund: original tx not found supplier=%s ref=%s",
                    supplier_id, supplier_ref,
                )
                return TransactionResult(
                    status=TransactionStatus.INVALID_OPERATION,
                    tx_type=TransactionType.REFUND,
                    error_message=f"Original transaction not found: {supplier_id}/{supplier_ref}",
                )

            if original.refunded:
                balance = await self._get_balance_unsafe(original.player_id, supplier_id)
                return already_processed_result(
                    tx_type=TransactionType.REFUND,
                    tx_id=original.tx_id,
                    balance=balance,
                    refunded=True,
                )

            if rollback_incomplete_only and original.tx_type != TransactionType.DEBIT:
                return TransactionResult(
                    tx_id=original.tx_id,
                    status=TransactionStatus.INVALID_OPERATION,
                    tx_type=TransactionType.REFUND,
                    error_message=(
                        f"Rollback only allowed on DEBIT transactions, "
                        f"got {original.tx_type}"
                    ),
                )

            if amount is not None and amount != original.amount:
                return TransactionResult(
                    tx_id=original.tx_id,
                    status=TransactionStatus.INVALID_OPERATION,
                    tx_type=TransactionType.REFUND,
                    error_message=(
                        f"Refund amount {amount} does not match "
                        f"original amount {original.amount}"
                    ),
                )

            provider = self._provider_factory(supplier_id)
            context = TransactionContext(
                tx_id=str(uuid.uuid4()),
                supplier_ref=supplier_ref,
                allow_rollback_when_round_complete=not rollback_incomplete_only,
            )
            operation = RefundOperation(
                round_id=original.round_id,
                original_tx_id=original.tx_id,
            )

            try:
                result = await provider.reverse_transaction(
                    session=PlayerSession(
                        player_id=original.player_id,
                        brand_id="",
                        external_id="",
                        currency="",
                        country="",
                        jurisdiction="",
                        session_token="",
                        game_id=original.game_id,
                    ),
                    operations=[operation],
                    context=context,
                )
                original.refunded = True
                await self._player_repo.mark_refunded(original)
                logger.info(
                    "refund OK tx_id=%s supplier=%s ref=%s",
                    original.tx_id, supplier_id, supplier_ref,
                )
                return result
            except Exception as exc:
                logger.error(
                    "refund FAILED tx_id=%s supplier=%s ref=%s error=%s",
                    original.tx_id, supplier_id, supplier_ref, exc,
                )
                raise

    async def apply_transaction(
        self,
        session: PlayerSession,
        supplier_id: str,
        supplier_ref: str,
        round_id: str,
        tx_type: TransactionType,
        total: Decimal,
        operations: list[SupplierOperation],
        reject_if_rc_elapsed: bool = False,
        offline: bool = False,
    ) -> TransactionResult:
        """
        Apply a composite transaction (e.g. Evolution's combined bet+win).

        Some suppliers send a single callback that contains both a debit
        and a credit. This method applies all operations atomically.
        """
        context = TransactionContext(
            tx_id=str(uuid.uuid4()),
            supplier_ref=supplier_ref,
            disallow_locked=True,
            reject_if_rc_elapsed=reject_if_rc_elapsed,
            offline=offline,
        )
        return await self._execute_transaction(
            session=session,
            supplier_id=supplier_id,
            supplier_ref=supplier_ref,
            tx_type=tx_type,
            round_id=round_id,
            amount=total,
            operations=operations,
            context=context,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute_transaction(
        self,
        session: PlayerSession,
        supplier_id: str,
        supplier_ref: str,
        tx_type: TransactionType,
        round_id: str,
        amount: Decimal,
        operations: list[SupplierOperation],
        context: TransactionContext,
    ) -> TransactionResult:
        """
        Core transaction execution pipeline with locking and idempotency.
        """
        async with self._locks.player_lock(session.player_id):
            start_ts = time.monotonic()

            provider = self._provider_factory(supplier_id)
            await self._authorize_session(provider, session)

            # --- Idempotency check -------------------------------------------
            cached = self._cache.get(supplier_id, supplier_ref)
            if cached is not None:
                if cached.refunded:
                    cached_balance = cached.result.balance if cached.result else None
                    logger.debug(
                        "idempotent ALREADY_REFUNDED supplier=%s ref=%s",
                        supplier_id, supplier_ref,
                    )
                    return already_processed_result(
                        tx_type=tx_type,
                        tx_id=cached.tx_id,
                        balance=cached_balance,
                        refunded=True,
                    )
                if cached.succeeded:
                    cached_balance = cached.result.balance if cached.result else None
                    logger.debug(
                        "idempotent ALREADY_PROCESSED supplier=%s ref=%s",
                        supplier_id, supplier_ref,
                    )
                    return already_processed_result(
                        tx_type=tx_type,
                        tx_id=cached.tx_id,
                        balance=cached_balance,
                    )

            # --- Record request (idempotency tombstone) ----------------------
            record = TransactionRecord(
                tx_id=context.tx_id,
                supplier_id=supplier_id,
                supplier_ref=supplier_ref,
                player_id=session.player_id,
                game_id=session.game_id,
                tx_type=tx_type,
                amount=amount,
                round_id=round_id,
            )
            try:
                record = await self._player_repo.record_request(record)
                self._cache.put(record)
            except Exception as exc:
                logger.error(
                    "record_request FAILED supplier=%s ref=%s error=%s",
                    supplier_id, supplier_ref, exc,
                )
                raise DatabaseError(f"Failed to record transaction request: {exc}") from exc

            # --- Execute wallet operation ------------------------------------
            try:
                result = await provider.apply_transaction(
                    session=session,
                    operations=operations,
                    context=context,
                )
                record.result = result
                await self._player_repo.record_result(
                    record, delete_existing=record.previously_failed
                )
                elapsed_ms = (time.monotonic() - start_ts) * 1000
                logger.info(
                    "transaction OK tx_id=%s type=%s supplier=%s ref=%s "
                    "player=%s amount=%s elapsed_ms=%.1f",
                    record.tx_id, tx_type.value, supplier_id, supplier_ref,
                    session.player_id, amount, elapsed_ms,
                )
                return result

            except (InsufficientFundsError, TransactionBlockedError) as exc:
                # Balance/RG rejections are expected — log at INFO not ERROR
                logger.info(
                    "transaction REJECTED tx_id=%s type=%s supplier=%s ref=%s "
                    "player=%s reason=%s",
                    record.tx_id, tx_type.value, supplier_id, supplier_ref,
                    session.player_id, type(exc).__name__,
                )
                result = failure_result(
                    tx_type=tx_type,
                    error_message=str(exc),
                    tx_id=record.tx_id,
                )
                record.result = result
                await self._player_repo.record_result(record, delete_existing=True)
                raise

            except GameServiceError as exc:
                logger.error(
                    "transaction FAILED tx_id=%s type=%s supplier=%s ref=%s "
                    "player=%s error=%s",
                    record.tx_id, tx_type.value, supplier_id, supplier_ref,
                    session.player_id, exc,
                )
                result = failure_result(
                    tx_type=tx_type,
                    error_message=str(exc),
                    tx_id=record.tx_id,
                )
                record.result = result
                await self._player_repo.record_result(record, delete_existing=True)
                raise

            except Exception as exc:
                logger.error(
                    "transaction UNEXPECTED_ERROR tx_id=%s supplier=%s ref=%s "
                    "player=%s error=%s",
                    record.tx_id, supplier_id, supplier_ref,
                    session.player_id, exc, exc_info=True,
                )
                result = failure_result(
                    tx_type=tx_type,
                    error_message=f"Unexpected error: {exc}",
                    tx_id=record.tx_id,
                )
                record.result = result
                await self._player_repo.record_result(record, delete_existing=True)
                raise GameServiceError(f"Unexpected error processing transaction: {exc}") from exc

    async def _authorize_session(
        self, provider: AccountsProvider, session: PlayerSession
    ) -> None:
        """
        Re-validate the caller-supplied session_token before any wallet
        mutation or balance read.

        Route handlers build PlayerSession straight from the request body
        (see main.py's _session_from_request), which is untrusted input.
        Without this check, any caller who already knows a player_id could
        move money for that player by sending an arbitrary session_token —
        nothing downstream ever verified it belongs to that player.

        session_token is the same launch token the supplier already
        re-sends on every wallet callback (see EvolutionProvider's
        authenticate(), which HMAC-verifies it), so re-authenticating here
        reuses the supplier's own verification rather than inventing a
        parallel session store.
        """
        if not session.session_token:
            raise InvalidSessionError("Missing session_token")
        try:
            authenticated = await provider.authenticate(session.session_token)
        except GameServiceError:
            raise
        except Exception as exc:
            raise InvalidSessionError(f"Session validation failed: {exc}") from exc

        if authenticated.player_id != session.player_id:
            logger.warning(
                "Session/player mismatch: token authenticates player_id=%s "
                "but request claims player_id=%s",
                authenticated.player_id, session.player_id,
            )
            raise InvalidSessionError("session_token does not match player_id")

    async def _get_balance_unsafe(self, player_id: str, supplier_id: str) -> Optional[BalanceStatus]:
        """
        Fetch balance WITHOUT acquiring the player lock.

        Only call this from within an already-locked section.
        Returns None if the balance cannot be fetched (used for idempotency
        responses where balance is best-effort).
        """
        try:
            provider = self._provider_factory(supplier_id)
            # Construct a minimal session just for balance retrieval
            session = PlayerSession(
                player_id=player_id,
                brand_id="",
                external_id=player_id,
                currency="",
                country="",
                jurisdiction="",
                session_token="",
                game_id="",
            )
            return await provider.get_balance(session)
        except Exception as exc:
            logger.warning("Failed to fetch balance for idempotency response: %s", exc)
            return None
