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
gameservice.accounts_bridge — Central Transaction Coordinator
=============================================================

:class:`AccountsBridge` is the single entry point for all wallet operations
within the game service.  It sits between supplier adapters (which speak the
supplier's protocol) and the :class:`~acmetocasino.gameservice.accounts_provider.AccountsProvider`
(which speaks the wallet back-end's protocol).

Responsibilities
----------------
1. **Per-player locking** — prevents race conditions when the same player
   triggers concurrent operations (e.g. two browser tabs).
2. **Idempotency deduplication** — tracks ``supplier_ref`` values so that
   duplicate callbacks return the cached result without re-applying.
3. **Structured logging** — emits JSON-friendly log records with
   ``correlation_id``, ``player_id``, ``operation``, and ``duration_ms``.
4. **Multi-brand routing** — resolves the correct ``AccountsProvider``
   implementation for each brand at call time.
5. **Reality-check enforcement** — transparently appends the
   ``reality_check_elapsed`` flag on balance responses when applicable.

Usage example::

    from acmetocasino.gameservice.accounts_bridge import AccountsBridge
    from my_wallet import MyWalletProvider  # satisfies AccountsProvider

    bridge = AccountsBridge(
        default_provider=MyWalletProvider(),
        brand_providers={"acme_mt": MyWalletProvider(brand="acme_mt")},
    )
    result = bridge.debit(
        player_id="p-123",
        brand_id="acme_mt",
        game_id="starburst",
        round_id="round-abc",
        commands=[RoundCommand(...)],
        correlation_id="req-xyz",
    )
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from decimal import Decimal
from typing import Any

from acmetocasino.gameservice.accounts_provider import AccountsProvider, AuthResult
from acmetocasino.gameservice.errors import (
    GameServiceError,
    InsufficientFundsError,
    InvalidSessionError,
)
from acmetocasino.gameservice.models.enums import CommandType, RealityCheckAction
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.transaction_result import TransactionResult

logger = logging.getLogger(__name__)


class _IdempotencyStore:
    """Thread-safe in-memory idempotency cache.

    Maps ``supplier_ref`` strings to their :class:`TransactionResult` so that
    duplicate callbacks receive the same response without re-applying the
    wallet operation.

    In production this would be backed by Redis or a database table with a
    TTL matching the supplier's retry window (typically 24–72 hours).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, TransactionResult] = {}

    def get(self, supplier_ref: str) -> TransactionResult | None:
        with self._lock:
            return self._store.get(supplier_ref)

    def put(self, supplier_ref: str, result: TransactionResult) -> None:
        with self._lock:
            self._store[supplier_ref] = result

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)


class AccountsBridge:
    """Coordinates wallet operations across suppliers and account providers.

    Parameters
    ----------
    default_provider:
        The fallback :class:`AccountsProvider` used when no brand-specific
        provider is registered.
    brand_providers:
        Optional mapping of ``brand_id`` → ``AccountsProvider`` for multi-
        brand deployments where each brand has its own wallet back-end.
    reality_check_interval_minutes:
        Default interval (in minutes) after which a reality-check flag is
        set.  This can be overridden per-call when you have the player's
        :class:`~acmetocasino.gameservice.models.JurisdictionProfile`.
        ``0`` disables the check.
    """

    def __init__(
        self,
        default_provider: AccountsProvider,
        brand_providers: dict[str, AccountsProvider] | None = None,
        reality_check_interval_minutes: int = 0,
    ) -> None:
        self._default_provider = default_provider
        self._brand_providers: dict[str, AccountsProvider] = brand_providers or {}
        self._reality_check_interval = reality_check_interval_minutes

        # Per-player locks — prevents concurrent wallet mutations for the same
        # player.  Using a dict of threading.Lock gives O(1) lookup while
        # keeping locks scoped to individual players.
        self._player_locks: dict[str, threading.Lock] = {}
        self._locks_meta = threading.Lock()  # guards _player_locks itself

        # Idempotency cache for dedup of supplier callbacks.
        self._idempotency: _IdempotencyStore = _IdempotencyStore()

        # Track per-player session start times for reality-check enforcement.
        self._session_start: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _provider_for(self, brand_id: str) -> AccountsProvider:
        """Return the provider for *brand_id*, falling back to the default."""
        return self._brand_providers.get(brand_id, self._default_provider)

    def _player_lock(self, player_id: str) -> threading.Lock:
        """Return (and lazily create) the per-player mutex."""
        with self._locks_meta:
            if player_id not in self._player_locks:
                self._player_locks[player_id] = threading.Lock()
            return self._player_locks[player_id]

    @staticmethod
    def _new_correlation_id() -> str:
        return str(uuid.uuid4())

    def _reality_check_elapsed(self, player_id: str) -> bool:
        """Return True if the player has exceeded the reality-check interval."""
        if self._reality_check_interval <= 0:
            return False
        start = self._session_start.get(player_id)
        if start is None:
            return False
        elapsed_minutes = (time.monotonic() - start) / 60
        return elapsed_minutes >= self._reality_check_interval

    def _log(
        self,
        level: int,
        operation: str,
        player_id: str,
        correlation_id: str,
        extra: dict[str, Any] | None = None,
        *,
        duration_ms: float | None = None,
        error: Exception | None = None,
    ) -> None:
        """Emit a structured log record.

        All records include ``operation``, ``player_id``, and
        ``correlation_id`` so they can be correlated across systems in a
        log aggregator (e.g. Loki, Elasticsearch).
        """
        record: dict[str, Any] = {
            "operation": operation,
            "player_id": player_id,
            "correlation_id": correlation_id,
        }
        if duration_ms is not None:
            record["duration_ms"] = round(duration_ms, 2)
        if error is not None:
            record["error_type"] = type(error).__name__
            record["error_message"] = str(error)
        if extra:
            record.update(extra)
        logger.log(level, record)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def login(
        self,
        ctx: PlayerContext,
        correlation_id: str | None = None,
    ) -> AuthResult:
        """Authenticate a player and open a tracked session.

        Records the session start time for reality-check enforcement.

        Parameters
        ----------
        ctx:
            Player context containing the session token to validate.
        correlation_id:
            Optional trace ID; auto-generated if omitted.

        Returns
        -------
        AuthResult
            Fresh session token and player identity.

        Raises
        ------
        ~acmetocasino.gameservice.errors.InvalidSessionError
            If the session token is invalid or expired.
        """
        cid = correlation_id or self._new_correlation_id()
        t0 = time.monotonic()
        provider = self._provider_for(ctx.brand_id)
        try:
            result = provider.authenticate(ctx)
            self._session_start[ctx.player_id] = time.monotonic()
            self._log(
                logging.INFO,
                "login",
                ctx.player_id,
                cid,
                duration_ms=(time.monotonic() - t0) * 1000,
            )
            return result
        except GameServiceError:
            raise
        except Exception as exc:
            self._log(
                logging.ERROR,
                "login",
                ctx.player_id,
                cid,
                duration_ms=(time.monotonic() - t0) * 1000,
                error=exc,
            )
            raise InvalidSessionError(
                message=f"Authentication failed: {exc}",
                player_id=ctx.player_id,
                correlation_id=cid,
            ) from exc

    def logout(self, player_id: str, brand_id: str = "") -> None:
        """Remove the player's session-start record.

        Does not communicate with the provider — session invalidation is
        handled at the API layer.  This only clears the local reality-check
        timer.

        Parameters
        ----------
        player_id:
            Player to log out.
        brand_id:
            Unused; kept for API symmetry with other methods.
        """
        self._session_start.pop(player_id, None)
        self._log(logging.INFO, "logout", player_id, self._new_correlation_id())

    def get_balance(
        self,
        player_id: str,
        brand_id: str,
        game_id: str | None = None,
        correlation_id: str | None = None,
    ) -> WalletSnapshot:
        """Return the player's current balance.

        Parameters
        ----------
        player_id:
            Target player.
        brand_id:
            Brand to route to the correct provider.
        game_id:
            Optional game scope for free-round credit lookup.
        correlation_id:
            Optional trace ID.

        Returns
        -------
        WalletSnapshot
            Current balance snapshot.
        """
        cid = correlation_id or self._new_correlation_id()
        t0 = time.monotonic()
        provider = self._provider_for(brand_id)
        snapshot = provider.get_balance(player_id, game_id)
        self._log(
            logging.DEBUG,
            "get_balance",
            player_id,
            cid,
            extra={"game_id": game_id, "total": str(snapshot.total_balance)},
            duration_ms=(time.monotonic() - t0) * 1000,
        )
        return snapshot

    def debit(
        self,
        player_id: str,
        brand_id: str,
        game_id: str,
        round_id: str,
        commands: list[RoundCommand],
        correlation_id: str | None = None,
    ) -> TransactionResult:
        """Apply a debit (or batch of round commands) to the player's wallet.

        Per-player locking ensures that concurrent debit calls are serialised,
        preventing double-spend race conditions.

        Parameters
        ----------
        player_id:
            Target player.
        brand_id:
            Brand routing key.
        game_id:
            Game for which the commands are submitted.
        round_id:
            Supplier round identifier.
        commands:
            Ordered list of round commands.
        correlation_id:
            Optional trace ID.

        Returns
        -------
        TransactionResult
            Outcome including post-debit balance.

        Raises
        ------
        ~acmetocasino.gameservice.errors.InsufficientFundsError
            If the player cannot cover the debit.
        """
        return self._apply(
            operation="debit",
            player_id=player_id,
            brand_id=brand_id,
            game_id=game_id,
            round_id=round_id,
            commands=commands,
            correlation_id=correlation_id,
        )

    def credit(
        self,
        player_id: str,
        brand_id: str,
        game_id: str,
        round_id: str,
        commands: list[RoundCommand],
        correlation_id: str | None = None,
    ) -> TransactionResult:
        """Apply a credit (win payout) to the player's wallet.

        Parameters
        ----------
        player_id:
            Target player.
        brand_id:
            Brand routing key.
        game_id:
            Game for which the credit is issued.
        round_id:
            Supplier round identifier.
        commands:
            Ordered list of round commands (should be CREDIT type).
        correlation_id:
            Optional trace ID.

        Returns
        -------
        TransactionResult
            Outcome including post-credit balance.
        """
        return self._apply(
            operation="credit",
            player_id=player_id,
            brand_id=brand_id,
            game_id=game_id,
            round_id=round_id,
            commands=commands,
            correlation_id=correlation_id,
        )

    def debit_and_credit(
        self,
        player_id: str,
        brand_id: str,
        game_id: str,
        round_id: str,
        commands: list[RoundCommand],
        correlation_id: str | None = None,
    ) -> TransactionResult:
        """Apply a mixed batch of debit + credit commands atomically.

        Some suppliers (e.g. live blackjack) send both a bet placement and a
        payout in the same callback.  This method applies them as a single
        atomic unit.

        Parameters
        ----------
        player_id, brand_id, game_id, round_id, commands, correlation_id:
            Same semantics as :meth:`debit`.
        """
        return self._apply(
            operation="debit_and_credit",
            player_id=player_id,
            brand_id=brand_id,
            game_id=game_id,
            round_id=round_id,
            commands=commands,
            correlation_id=correlation_id,
        )

    def refund(
        self,
        player_id: str,
        brand_id: str,
        round_id: str,
        commands: list[RoundCommand],
        correlation_id: str | None = None,
    ) -> TransactionResult:
        """Reverse a previously-applied debit (rollback / refund).

        Parameters
        ----------
        player_id:
            Target player.
        brand_id:
            Brand routing key.
        round_id:
            The round to reverse.
        commands:
            ROLLBACK commands to apply.
        correlation_id:
            Optional trace ID.

        Returns
        -------
        TransactionResult
            Outcome including post-reversal balance.

        Raises
        ------
        ~acmetocasino.gameservice.errors.NoMatchingDebitError
            If no original debit for ``round_id`` exists.
        """
        cid = correlation_id or self._new_correlation_id()
        t0 = time.monotonic()

        # Idempotency check — deduplicate by supplier_ref if present.
        for cmd in commands:
            if cmd.supplier_ref:
                cached = self._idempotency.get(cmd.supplier_ref)
                if cached is not None:
                    cached_idempotent = TransactionResult(
                        external_id=cached.external_id,
                        balance=cached.balance,
                        cash_usage=cached.cash_usage,
                        bonus_usage=cached.bonus_usage,
                        already_processed=True,
                    )
                    return cached_idempotent

        lock = self._player_lock(player_id)
        with lock:
            provider = self._provider_for(brand_id)
            try:
                result = provider.reverse_transaction(player_id, round_id, commands)
                # Cache result for idempotency.
                for cmd in commands:
                    if cmd.supplier_ref:
                        self._idempotency.put(cmd.supplier_ref, result)
                self._log(
                    logging.INFO,
                    "refund",
                    player_id,
                    cid,
                    extra={"round_id": round_id},
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
                return result
            except GameServiceError as exc:
                self._log(
                    logging.WARNING,
                    "refund",
                    player_id,
                    cid,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error=exc,
                )
                raise

    def add_bonus(
        self,
        player_id: str,
        brand_id: str,
        amount: Decimal,
        bonus_type: str,
        correlation_id: str | None = None,
    ) -> TransactionResult:
        """Credit a bonus award to the player's bonus balance.

        Parameters
        ----------
        player_id:
            Target player.
        brand_id:
            Brand routing key.
        amount:
            Bonus amount in the player's currency.
        bonus_type:
            Classifier string (e.g. ``"welcome"``, ``"free_round_win"``).
        correlation_id:
            Optional trace ID.

        Returns
        -------
        TransactionResult
            Outcome including updated balance.
        """
        cid = correlation_id or self._new_correlation_id()
        t0 = time.monotonic()
        lock = self._player_lock(player_id)
        with lock:
            provider = self._provider_for(brand_id)
            try:
                result = provider.add_bonus(player_id, amount, bonus_type)
                self._log(
                    logging.INFO,
                    "add_bonus",
                    player_id,
                    cid,
                    extra={"amount": str(amount), "bonus_type": bonus_type},
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
                return result
            except GameServiceError as exc:
                self._log(
                    logging.WARNING,
                    "add_bonus",
                    player_id,
                    cid,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error=exc,
                )
                raise

    def confirm_reality_check(
        self,
        player_id: str,
        brand_id: str,
        action: RealityCheckAction,
        correlation_id: str | None = None,
    ) -> None:
        """Record the player's response to a reality-check prompt.

        Resets the session-start timer on ``CONTINUE`` so the interval
        begins again.

        Parameters
        ----------
        player_id:
            Target player.
        brand_id:
            Brand routing key.
        action:
            Player's chosen response.
        correlation_id:
            Optional trace ID.
        """
        cid = correlation_id or self._new_correlation_id()
        provider = self._provider_for(brand_id)
        provider.confirm_reality_check(player_id, action)

        if action == RealityCheckAction.CONTINUE:
            # Reset the clock so the next interval starts from now.
            self._session_start[player_id] = time.monotonic()
        else:
            # Player chose to break — clear the session record.
            self._session_start.pop(player_id, None)

        self._log(
            logging.INFO,
            "confirm_reality_check",
            player_id,
            cid,
            extra={"action": action.value},
        )

    # ------------------------------------------------------------------
    # Internal orchestration
    # ------------------------------------------------------------------

    def _apply(
        self,
        operation: str,
        player_id: str,
        brand_id: str,
        game_id: str,
        round_id: str,
        commands: list[RoundCommand],
        correlation_id: str | None,
    ) -> TransactionResult:
        """Apply a batch of round commands with locking and idempotency."""
        cid = correlation_id or self._new_correlation_id()
        t0 = time.monotonic()

        # Idempotency check — if *any* command in the batch has a known
        # supplier_ref, the whole batch is considered already processed.
        for cmd in commands:
            if cmd.supplier_ref:
                cached = self._idempotency.get(cmd.supplier_ref)
                if cached is not None:
                    self._log(
                        logging.DEBUG,
                        f"{operation}.idempotent_replay",
                        player_id,
                        cid,
                        extra={"supplier_ref": cmd.supplier_ref},
                    )
                    return TransactionResult(
                        external_id=cached.external_id,
                        balance=cached.balance,
                        cash_usage=cached.cash_usage,
                        bonus_usage=cached.bonus_usage,
                        reality_check_elapsed=self._reality_check_elapsed(player_id),
                        already_processed=True,
                    )

        lock = self._player_lock(player_id)
        with lock:
            provider = self._provider_for(brand_id)
            try:
                result = provider.apply_transaction(
                    player_id, game_id, round_id, commands
                )
                # Stamp reality-check status.
                rc_elapsed = self._reality_check_elapsed(player_id)
                if rc_elapsed and not result.reality_check_elapsed:
                    result = result.model_copy(
                        update={"reality_check_elapsed": True}
                    )

                # Cache for idempotency.
                for cmd in commands:
                    if cmd.supplier_ref:
                        self._idempotency.put(cmd.supplier_ref, result)

                self._log(
                    logging.INFO,
                    operation,
                    player_id,
                    cid,
                    extra={
                        "round_id": round_id,
                        "game_id": game_id,
                        "commands": len(commands),
                        "succeeded": result.succeeded,
                    },
                    duration_ms=(time.monotonic() - t0) * 1000,
                )
                return result

            except InsufficientFundsError as exc:
                exc.correlation_id = cid
                self._log(
                    logging.WARNING,
                    operation,
                    player_id,
                    cid,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error=exc,
                )
                raise
            except GameServiceError as exc:
                exc.correlation_id = cid
                self._log(
                    logging.ERROR,
                    operation,
                    player_id,
                    cid,
                    duration_ms=(time.monotonic() - t0) * 1000,
                    error=exc,
                )
                raise


__all__ = ["AccountsBridge"]
