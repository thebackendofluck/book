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
Shared pytest fixtures for the acmetocasino gameservice test suite.

All fixtures use in-memory implementations — no external services required.
"""
from __future__ import annotations

import sys
from pathlib import Path

# When pytest is invoked from the repo-wide `scripts/` rootdir rather
# than from inside `chapter-10/`, the `acmetocasino` package sits in a
# sibling directory that isn't yet on sys.path. Put it there first so
# the fixture imports below (and the tests themselves) resolve cleanly.
_CHAPTER_ROOT = Path(__file__).resolve().parent.parent
if str(_CHAPTER_ROOT) not in sys.path:
    sys.path.insert(0, str(_CHAPTER_ROOT))

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest

from acmetocasino.gameservice.accounts.balance_policy import BalancePolicy
from acmetocasino.gameservice.accounts.bonus_service import BonusService
from acmetocasino.gameservice.accounts.ledger_adapter import InMemoryLedgerAdapter
from acmetocasino.gameservice.accounts.limits_service import LimitsService
from acmetocasino.gameservice.accounts.wallet_service import (
    InMemoryWalletStore,
    WalletService,
)
from acmetocasino.gameservice.accounts_bridge import AccountsBridge
from acmetocasino.gameservice.accounts_provider import AccountsProvider, AuthResult
from acmetocasino.gameservice.models.enums import RealityCheckAction
from acmetocasino.gameservice.models.launch_request import LaunchRequest
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.models.wallet_snapshot import WalletSnapshot
from acmetocasino.gameservice.suppliers.capability_matrix import CapabilityMatrix
from acmetocasino.gameservice.suppliers.evolution.adapter import EvolutionAdapter
from acmetocasino.gameservice.suppliers.hacksaw.adapter import HacksawAdapter
from acmetocasino.gameservice.suppliers.igt.adapter import IGTAdapter
from acmetocasino.gameservice.suppliers.kambi.adapter import KambiAdapter
from acmetocasino.gameservice.suppliers.netent.adapter import NetEntAdapter
from acmetocasino.gameservice.suppliers.nyx.adapter import NYXAdapter
from acmetocasino.gameservice.suppliers.playngo.adapter import PlayngoAdapter
from acmetocasino.gameservice.suppliers.pragmatic.adapter import PragmaticAdapter
from acmetocasino.gameservice.suppliers.push_gaming.adapter import PushGamingAdapter
from acmetocasino.gameservice.suppliers.registry import SupplierRegistry
from acmetocasino.gameservice.suppliers.relax.adapter import RelaxAdapter
from acmetocasino.gameservice.suppliers.settings import SupplierConfig, SupplierSettingsManager
from acmetocasino.gameservice.transaction_result import TransactionResult


# ---------------------------------------------------------------------------
# In-memory event publisher (fake)
# ---------------------------------------------------------------------------


@dataclass
class FakePublisher:
    """Records emitted events for test assertions."""
    events: list[dict[str, Any]] = field(default_factory=list)

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"type": event_type, "payload": payload})

    def clear(self) -> None:
        self.events.clear()

    def events_of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["type"] == event_type]


# ---------------------------------------------------------------------------
# In-memory AccountsProvider implementation
# ---------------------------------------------------------------------------


@dataclass
class _SimpleAuthResult:
    player_id: str
    session_token: str


class InMemoryAccountsProvider:
    """A minimal AccountsProvider backed by WalletService for tests."""

    def __init__(self) -> None:
        self._store = InMemoryWalletStore()
        self._wallet = WalletService(store=self._store)
        self._sessions: dict[str, str] = {}  # token -> player_id

    def seed_wallet(
        self,
        player_id: str,
        currency: str = "EUR",
        cash: Decimal = Decimal("100.00"),
        bonus: Decimal = Decimal("0"),
    ) -> WalletSnapshot:
        snap = self._store.create(player_id, currency, initial_cash=cash)
        if bonus > Decimal("0"):
            snap = snap.with_bonus_delta(bonus)
            self._store.save(player_id, snap)
        return snap

    def register_session(self, player_id: str, token: str) -> None:
        self._sessions[token] = player_id

    def authenticate(self, ctx: PlayerContext) -> _SimpleAuthResult:
        from acmetocasino.gameservice.errors import InvalidSessionError
        if ctx.session_token and ctx.session_token in self._sessions:
            return _SimpleAuthResult(
                player_id=ctx.player_id,
                session_token=ctx.session_token,
            )
        # If no token check (empty token), accept for simplicity in tests
        if not ctx.session_token:
            return _SimpleAuthResult(
                player_id=ctx.player_id,
                session_token=str(uuid.uuid4()),
            )
        raise InvalidSessionError(
            message="Invalid session token",
            player_id=ctx.player_id,
            session_token=ctx.session_token,
        )

    def get_balance(self, player_id: str, game_id: str | None = None) -> WalletSnapshot:
        return self._wallet.get_balance(player_id)

    def apply_transaction(
        self,
        player_id: str,
        game_id: str,
        round_id: str,
        operations: list[RoundCommand],
    ) -> TransactionResult:
        from acmetocasino.gameservice.models.enums import CommandType
        net_debit = Decimal("0")
        net_credit = Decimal("0")
        for op in operations:
            if op.command_type == CommandType.DEBIT:
                net_debit += op.amount
            elif op.command_type == CommandType.CREDIT:
                net_credit += op.amount

        if net_debit > Decimal("0"):
            res_id = self._wallet.reserve(player_id, net_debit, round_id)
            self._wallet.commit(res_id)

        if net_credit > Decimal("0"):
            self._wallet.credit(player_id, net_credit, round_id)

        balance = self._wallet.get_balance(player_id)
        return TransactionResult(
            external_id=str(uuid.uuid4()),
            balance=balance,
            cash_usage=net_debit,
            bonus_usage=Decimal("0"),
        )

    def reverse_transaction(
        self,
        player_id: str,
        round_id: str,
        operations: list[RoundCommand],
    ) -> TransactionResult:
        total = sum(op.amount for op in operations)
        self._wallet.credit(player_id, total, round_id)
        balance = self._wallet.get_balance(player_id)
        return TransactionResult(
            external_id=str(uuid.uuid4()),
            balance=balance,
            cash_usage=Decimal("0"),
            bonus_usage=Decimal("0"),
        )

    def add_bonus(
        self,
        player_id: str,
        amount: Decimal,
        bonus_type: str,
    ) -> TransactionResult:
        snap = self._wallet.get_balance(player_id)
        updated = snap.with_bonus_delta(amount)
        self._store.save(player_id, updated)
        return TransactionResult(
            external_id=str(uuid.uuid4()),
            balance=updated,
            cash_usage=Decimal("0"),
            bonus_usage=amount,
        )

    def confirm_reality_check(self, player_id: str, action: RealityCheckAction) -> None:
        pass  # no-op in tests


# ---------------------------------------------------------------------------
# Helper: build a SupplierConfig for a named supplier
# ---------------------------------------------------------------------------


def _make_supplier_config(supplier_id: str, brand_id: str = "acme_uk") -> SupplierConfig:
    return SupplierConfig(
        supplier_id=supplier_id,
        display_name=supplier_id.capitalize(),
        api_base_url=f"https://api.{supplier_id}.example.com",
        enabled_brands=["*"],
        supported_currencies=["EUR", "GBP", "USD"],
    )


def _make_settings_with_all_suppliers() -> SupplierSettingsManager:
    supplier_ids = [
        "evolution", "pragmatic", "netent", "kambi", "playngo",
        "hacksaw", "push_gaming", "igt", "nyx", "relax", "betgenius",
    ]
    mgr = SupplierSettingsManager()
    raw: dict = {}
    for sid in supplier_ids:
        raw[sid] = {
            "supplier_id": sid,
            "display_name": sid.replace("_", " ").title(),
            "api_base_url": f"https://api.{sid}.example.com",
            "enabled_brands": ["*"],
            "supported_currencies": ["EUR", "GBP", "USD"],
            # Evolution-specific extras
            "casino_key": "test-casino-key",
            "api_token": "test-api-token",
            "webhook_secret": "",
            "environment": "staging",
            "table_config_url": "",
            # Pragmatic-specific extras
            "operator_id": "test-operator",
            "secret_key": "test-secret",
        }
    mgr.load(raw)
    return mgr


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_wallet() -> InMemoryWalletStore:
    """Fresh in-memory wallet store."""
    return InMemoryWalletStore()


@pytest.fixture
def wallet_service(fake_wallet: InMemoryWalletStore) -> WalletService:
    """WalletService backed by an in-memory store."""
    return WalletService(store=fake_wallet)


@pytest.fixture
def fake_publisher() -> FakePublisher:
    """Event publisher that records events in memory."""
    return FakePublisher()


@pytest.fixture
def player_context() -> PlayerContext:
    """A standard player context for MGA jurisdiction."""
    return PlayerContext(
        player_id="player-001",
        brand_id="acme_uk",
        jurisdiction="MGA",
        language="en-GB",
        currency="EUR",
        ip_address="1.2.3.4",
        session_token="tok-abc123",
        cash_balance=Decimal("100.00"),
        bonus_balance=Decimal("10.00"),
        kyc_verified=True,
        age_verified=True,
        self_excluded=False,
    )


@pytest.fixture
def launch_request(player_context: PlayerContext) -> LaunchRequest:
    """A real-money launch request for starburst via NetEnt."""
    return LaunchRequest(
        player=player_context,
        game_id="starburst",
        supplier_id="netent",
        channel="web",
    )


@pytest.fixture
def supplier_settings() -> SupplierSettingsManager:
    return _make_settings_with_all_suppliers()


@pytest.fixture
def capability_matrix() -> CapabilityMatrix:
    return CapabilityMatrix()


@pytest.fixture
def supplier_registry(
    supplier_settings: SupplierSettingsManager,
    capability_matrix: CapabilityMatrix,
) -> SupplierRegistry:
    """SupplierRegistry with all 11 suppliers registered."""
    registry = SupplierRegistry(
        settings_manager=supplier_settings,
        capability_matrix=capability_matrix,
    )
    registry.register("evolution", EvolutionAdapter)
    registry.register("pragmatic", PragmaticAdapter)
    registry.register("netent", NetEntAdapter)
    registry.register("kambi", KambiAdapter)
    registry.register("playngo", PlayngoAdapter)
    registry.register("hacksaw", HacksawAdapter)
    registry.register("push_gaming", PushGamingAdapter)
    registry.register("igt", IGTAdapter)
    registry.register("nyx", NYXAdapter)
    registry.register("relax", RelaxAdapter)
    return registry


@pytest.fixture
def provider() -> InMemoryAccountsProvider:
    """In-memory AccountsProvider with a seeded player wallet."""
    p = InMemoryAccountsProvider()
    p.seed_wallet("player-001", "EUR", cash=Decimal("100.00"))
    return p


@pytest.fixture
def accounts_bridge(provider: InMemoryAccountsProvider) -> AccountsBridge:
    """AccountsBridge using the in-memory provider."""
    return AccountsBridge(default_provider=provider)


@pytest.fixture
def bonus_service() -> BonusService:
    return BonusService(default_wagering_multiplier=Decimal("35"))


@pytest.fixture
def limits_service() -> LimitsService:
    return LimitsService()


@pytest.fixture
def balance_policy() -> BalancePolicy:
    return BalancePolicy()


@pytest.fixture
def ledger() -> InMemoryLedgerAdapter:
    return InMemoryLedgerAdapter()
