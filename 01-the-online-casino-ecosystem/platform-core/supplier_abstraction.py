# Companion code for "The Backend of Luck" - Chapter 01, The Online Casino Ecosystem.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Game Supplier Abstraction Pattern
# Source: Production casino platform (sanitized)
# Chapter 1 - The Online Casino Ecosystem
#
# The AccountsProvider ABC is the central abstraction for integrating
# game suppliers. Every supplier (Evolution, NetEnt, Microgaming, etc.)
# interacts with the platform through this single interface, regardless
# of whether the supplier uses REST, SOAP, or proprietary protocols.
# =============================================================================

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


# ---------------------------------------------------------------------------
# CORE DOMAIN TYPES
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BalanceStatus:
    """Balance snapshot returned to suppliers after every operation."""
    cash_balance: int   # amounts stored in minor units (pence/cents)
    bonus_balance: int

    @property
    def total_balance(self) -> int:
        return self.cash_balance + self.bonus_balance


@dataclass(frozen=True)
class SupplierTransactionDetails:
    """Details attached to every supplier transaction for audit and replay."""
    txn_id: int
    supplier_id: int
    supplier_txn_ref: str
    mobile: Optional[bool] = None
    disallow_locked: bool = False
    reject_if_rc_time_elapsed: Optional[bool] = None
    offline: bool = False
    allow_rollback_when_round_complete: bool = False
    require_debits: bool = False


# ---------------------------------------------------------------------------
# SUPPLIER OPERATIONS (Discriminated union via subclasses)
# ---------------------------------------------------------------------------
# Every financial operation from a game supplier maps to one of these types.
# Using an ABC ensures exhaustive handling and prevents accidentally missing
# a new operation type.


class SupplierAccountsOperation(ABC):
    @property
    @abstractmethod
    def round_id(self) -> str:
        ...


@dataclass(frozen=True)
class SupplierDebit(SupplierAccountsOperation):
    """Debit: player places a bet."""
    _round_id: str
    amount: int
    error_if_using_bonus: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    wagering: bool = True

    @property
    def round_id(self) -> str:
        return self._round_id


@dataclass(frozen=True)
class SupplierCredit(SupplierAccountsOperation):
    """Credit: player receives a win."""
    _round_id: str
    amount: int
    apply_geoverification: bool = False

    @property
    def round_id(self) -> str:
        return self._round_id


@dataclass(frozen=True)
class SupplierCountdown(SupplierAccountsOperation):
    """Countdown: bonus wagering countdown tick."""
    _round_id: str

    @property
    def round_id(self) -> str:
        return self._round_id


@dataclass(frozen=True)
class SupplierAdjust(SupplierAccountsOperation):
    """Adjust: partial refund or resettlement up."""
    _round_id: str
    new_amount: int
    apply_wagering: bool

    @property
    def round_id(self) -> str:
        return self._round_id


@dataclass(frozen=True)
class SupplierClawback(SupplierAccountsOperation):
    """Clawback: resettlement down (claw back a previous credit)."""
    _round_id: str
    amount: int

    @property
    def round_id(self) -> str:
        return self._round_id


# ---------------------------------------------------------------------------
# THE ACCOUNTS PROVIDER ABC
# ---------------------------------------------------------------------------
# This is the primary interface that ALL game suppliers interact with.
# The platform ships with StandardAccountsProvider as the default
# implementation, but brands can override it via classpath-based
# provider resolution (see provider_for_brand below).


class AccountsProvider(ABC):

    @abstractmethod
    def login(
        self,
        brand_id: int,
        user: Any,
        user_ip: str,
        credentials: str,
        params: dict[str, str],
    ) -> Any:
        """
        Authenticate a player for a game session.

        Args:
            brand_id: Brand the player belongs to
            user: User value object
            user_ip: Player's IP address (for geo-compliance)
            credentials: Session credentials
            params: Additional launch parameters

        Returns:
            Authenticated player details with balance
        """

    def logout(self, player: Any) -> None:
        """Terminate a player's game session."""

    @abstractmethod
    def get_balance(self, player: Any, game_id: Optional[int]) -> BalanceStatus:
        """
        Retrieve player balance (cash + bonus).
        Called by suppliers before and after every game round.
        """

    @abstractmethod
    def apply_transaction(
        self,
        player: Any,
        game: Any,
        details: SupplierTransactionDetails,
        operations: Sequence[SupplierAccountsOperation],
        current_token: str,
    ) -> Any:
        """
        Apply a composite transaction (one or more debits/credits).
        This is the hot path -- called for every bet and win.
        Must be idempotent (duplicate supplier_txn_ref returns cached result).
        """

    @abstractmethod
    def reverse_transaction(
        self,
        player: Any,
        game: Any,
        details: SupplierTransactionDetails,
        operations: Sequence[SupplierAccountsOperation],
    ) -> Any:
        """Reverse a previously applied transaction (rollback)."""

    @abstractmethod
    def add_bonus(self, player: Any, amount: int, details: Any) -> Any:
        """Award a bonus from a supplier (e.g., free spin winnings)."""

    @abstractmethod
    def reality_check_confirm(self, player: Any, action: str) -> None:
        """Handle reality check confirmation (continue/close/view history)."""


# ---------------------------------------------------------------------------
# PROVIDER RESOLUTION: BRAND-BASED DISPATCH
# ---------------------------------------------------------------------------
# The platform resolves which AccountsProvider to use based on the brand.
# This allows different brands to use entirely different accounting systems
# while sharing the same supplier integration layer.

_PROVIDER_REGISTRY: dict[str, type[AccountsProvider]] = {}


def register_provider(name: str, cls: type[AccountsProvider]) -> None:
    """Register a named AccountsProvider implementation for a brand."""
    _PROVIDER_REGISTRY[name] = cls


def provider_for_brand(brand_id: int, brand_name: str) -> AccountsProvider:
    """
    Instantiate the correct AccountsProvider for a brand.
    Uses registry-based named provider resolution with
    StandardAccountsProvider as the default fallback.
    """
    if brand_name in _PROVIDER_REGISTRY:
        return _PROVIDER_REGISTRY[brand_name]()
    # Fall back to default (StandardAccountsProvider would be registered here)
    raise KeyError(f"No AccountsProvider registered for brand '{brand_name}' (id={brand_id})")


# ---------------------------------------------------------------------------
# WHY THIS PATTERN MATTERS
# ---------------------------------------------------------------------------
#
# 1. SUPPLIER AGNOSTIC: Whether Evolution sends JSON over REST or IGT
#    uses SOAP with XML, they all end up calling the same ABC methods.
#    The translation happens in each supplier's endpoint module.
#
# 2. TRANSACTION SAFETY: The ABC for operations ensures every
#    financial operation type is explicitly handled.
#
# 3. IDEMPOTENCY: The supplier_txn_ref in SupplierTransactionDetails
#    enables deduplication. If a supplier retries a transaction (common
#    during network issues), the platform returns the cached result
#    instead of double-processing.
#
# 4. MULTI-BRAND: The provider_for_brand resolution means Brand A can
#    run with the standard accounts engine while Brand B uses a
#    completely custom implementation -- on the same platform instance.
#
# 5. TESTABILITY: The ABC can be easily mocked or overridden for
#    integration testing without standing up the full accounts system.
