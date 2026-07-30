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
E2E test: compliance enforcement — geo-blocking, self-exclusion, reality check,
session limits, KYC.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts.limits_service import LimitsService
from acmetocasino.gameservice.accounts_bridge import AccountsBridge
from acmetocasino.gameservice.errors import (
    GeoBlockedError,
    InvalidSessionError,
)
from acmetocasino.gameservice.models.enums import CommandType, RealityCheckAction
from acmetocasino.gameservice.models.player_context import PlayerContext
from acmetocasino.gameservice.models.round_command import RoundCommand
from acmetocasino.gameservice.suppliers.registry import SupplierDisabledError, SupplierRegistry

from tests.conftest import InMemoryAccountsProvider


@pytest.fixture
def us_player() -> PlayerContext:
    return PlayerContext(
        player_id="p-us",
        brand_id="acme_uk",
        jurisdiction="US",
        ip_address="8.8.8.8",
        session_token="",
        cash_balance=Decimal("100"),
    )


@pytest.fixture
def excluded_player() -> PlayerContext:
    return PlayerContext(
        player_id="p-excluded",
        brand_id="acme_uk",
        jurisdiction="MGA",
        session_token="",
        cash_balance=Decimal("100"),
        self_excluded=True,
    )


@pytest.fixture
def unverified_player() -> PlayerContext:
    return PlayerContext(
        player_id="p-unverified",
        brand_id="acme_uk",
        jurisdiction="UKGC",
        session_token="",
        cash_balance=Decimal("50"),
        kyc_verified=False,
    )


# ---------------------------------------------------------------------------
# Geo-blocking via SupplierRegistry
# ---------------------------------------------------------------------------


def test_geo_blocked_supplier_raises_supplier_disabled(
    supplier_registry: SupplierRegistry,
) -> None:
    """Supplier blocked for a jurisdiction raises SupplierDisabledError."""
    mgr = supplier_registry.get_settings_manager()
    mgr.update_config("netent", {"blocked_jurisdictions": ["US"]})

    with pytest.raises(SupplierDisabledError):
        supplier_registry.resolve("netent", "acme_uk", "US")

    # Restore
    mgr.update_config("netent", {"blocked_jurisdictions": []})


def test_non_blocked_jurisdiction_allowed(supplier_registry: SupplierRegistry) -> None:
    mgr = supplier_registry.get_settings_manager()
    mgr.update_config("netent", {"blocked_jurisdictions": ["US"]})

    # MGA should still work
    adapter = supplier_registry.resolve("netent", "acme_uk", "MGA")
    assert adapter.supplier_id == "netent"

    # Restore
    mgr.update_config("netent", {"blocked_jurisdictions": []})


# ---------------------------------------------------------------------------
# Self-exclusion check (provider-level)
# ---------------------------------------------------------------------------


def test_self_excluded_player_rejected_on_login(
    excluded_player: PlayerContext,
) -> None:
    """A provider can reject a self-excluded player at login."""

    class ExclusionCheckProvider(InMemoryAccountsProvider):
        def authenticate(self, ctx: PlayerContext):  # type: ignore[override]
            if ctx.self_excluded:
                from acmetocasino.gameservice.errors import TransactionBlockedError
                raise TransactionBlockedError(
                    message="Player is self-excluded",
                    player_id=ctx.player_id,
                    reason_code="SELF_EXCLUDED",
                )
            return super().authenticate(ctx)

    provider = ExclusionCheckProvider()
    provider.seed_wallet(excluded_player.player_id, "EUR")
    bridge = AccountsBridge(default_provider=provider)

    from acmetocasino.gameservice.errors import TransactionBlockedError
    with pytest.raises(TransactionBlockedError) as exc_info:
        bridge.login(excluded_player)
    assert exc_info.value.reason_code == "SELF_EXCLUDED"


# ---------------------------------------------------------------------------
# Reality check enforcement
# ---------------------------------------------------------------------------


def test_reality_check_flag_not_set_without_interval(
    accounts_bridge: AccountsBridge,
) -> None:
    """Reality check is not triggered when interval is 0 (disabled)."""
    ctx = PlayerContext(
        player_id="player-001",
        brand_id="acme_uk",
        jurisdiction="MGA",
        session_token="",  # empty token accepted by InMemoryProvider
    )
    accounts_bridge.login(ctx)
    # interval = 0 means disabled
    assert not accounts_bridge._reality_check_elapsed("player-001")


def test_reality_check_resets_on_continue(
    provider: InMemoryAccountsProvider,
) -> None:
    bridge = AccountsBridge(
        default_provider=provider,
        reality_check_interval_minutes=1,
    )
    ctx = PlayerContext(
        player_id="player-001",
        brand_id="acme_uk",
        jurisdiction="MGA",
        session_token="",
    )
    bridge.login(ctx)
    # Force the session start to be old enough
    import time
    bridge._session_start["player-001"] = time.monotonic() - 120  # 2 minutes ago

    assert bridge._reality_check_elapsed("player-001") is True

    bridge.confirm_reality_check("player-001", "acme_uk", RealityCheckAction.CONTINUE)
    # After CONTINUE, timer resets — should not be elapsed
    assert bridge._reality_check_elapsed("player-001") is False


def test_reality_check_clears_on_take_break(
    provider: InMemoryAccountsProvider,
) -> None:
    bridge = AccountsBridge(
        default_provider=provider,
        reality_check_interval_minutes=1,
    )
    ctx = PlayerContext(
        player_id="player-001",
        brand_id="acme_uk",
        jurisdiction="MGA",
        session_token="",
    )
    bridge.login(ctx)
    bridge.confirm_reality_check("player-001", "acme_uk", RealityCheckAction.TAKE_BREAK)
    # Session cleared
    assert "player-001" not in bridge._session_start


# ---------------------------------------------------------------------------
# Session duration limits
# ---------------------------------------------------------------------------


def test_session_limit_blocks_when_expired() -> None:
    """LimitsService blocks transactions when session is over limit."""
    limits = LimitsService(default_session_duration_seconds=1)
    limits.record_session_start("p-session")

    import time
    time.sleep(1.1)  # Wait for limit to expire

    result = limits.check_session_duration("p-session")
    assert result.allowed is False
    assert "session" in result.reason.lower()


def test_session_limit_allows_within_window() -> None:
    limits = LimitsService(default_session_duration_seconds=3600)
    limits.record_session_start("p-session-ok")
    result = limits.check_session_duration("p-session-ok")
    assert result.allowed is True


# ---------------------------------------------------------------------------
# Wager limit enforcement in the full bridge flow
# ---------------------------------------------------------------------------


def test_wager_limit_integration(
    provider: InMemoryAccountsProvider,
) -> None:
    """A wager exceeding the limit is detected and rejected by the limits service."""
    limits = LimitsService()
    limits.set_limit("player-001", "wager", Decimal("10.00"), 86400)

    result = limits.check_wager_limit("player-001", Decimal("15.00"))
    assert result.allowed is False
    assert result.limit_type == "wager"

    # Confirm within-limit wager is allowed
    result_ok = limits.check_wager_limit("player-001", Decimal("9.00"))
    assert result_ok.allowed is True


# ---------------------------------------------------------------------------
# KYC enforcement stub
# ---------------------------------------------------------------------------


def test_kyc_not_verified_player_blocked_by_provider(
    unverified_player: PlayerContext,
) -> None:
    from acmetocasino.gameservice.errors import KycNotApprovedError

    class KycCheckProvider(InMemoryAccountsProvider):
        def authenticate(self, ctx: PlayerContext):  # type: ignore[override]
            if not ctx.kyc_verified and ctx.jurisdiction == "UKGC":
                raise KycNotApprovedError(
                    message="KYC required for UKGC",
                    player_id=ctx.player_id,
                    kyc_status="not_started",
                )
            return super().authenticate(ctx)

    provider = KycCheckProvider()
    provider.seed_wallet(unverified_player.player_id, "EUR")
    bridge = AccountsBridge(default_provider=provider)

    with pytest.raises(KycNotApprovedError):
        bridge.login(unverified_player)
