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
Unit tests for BonusService: allocation, wagering progress, forfeit, apply.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts.bonus_service import BonusService, BonusStatus


def test_allocate_bonus_creates_active_allocation() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    assert alloc.status == BonusStatus.ACTIVE
    assert alloc.amount == Decimal("10")
    assert alloc.player_id == "p-1"
    assert alloc.bonus_type == "welcome"


def test_allocate_bonus_uses_default_wagering_multiplier() -> None:
    svc = BonusService(default_wagering_multiplier=Decimal("35"))
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    assert alloc.wager_target == Decimal("350")


def test_allocate_bonus_custom_multiplier() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("20"), "reload", wagering_requirement=Decimal("10"))
    assert alloc.wager_target == Decimal("200")


def test_allocate_bonus_zero_amount_raises() -> None:
    svc = BonusService()
    with pytest.raises(ValueError):
        svc.allocate_bonus("p-1", Decimal("0"), "welcome")


def test_allocate_bonus_negative_amount_raises() -> None:
    svc = BonusService()
    with pytest.raises(ValueError):
        svc.allocate_bonus("p-1", Decimal("-5"), "reload")


def test_check_wagering_progress_initial_state() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    progress = svc.check_wagering_progress("p-1", alloc.bonus_id)
    assert progress.wagered_so_far == Decimal("0")
    assert progress.remaining_wager == Decimal("350")
    assert progress.progress_pct == Decimal("0")


def test_apply_bonus_to_round_advances_progress() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    svc.apply_bonus_to_round("p-1", "r-1", alloc.bonus_id, Decimal("35"))
    progress = svc.check_wagering_progress("p-1", alloc.bonus_id)
    assert progress.wagered_so_far == Decimal("35")


def test_apply_bonus_completes_when_target_reached() -> None:
    svc = BonusService(default_wagering_multiplier=Decimal("1"))
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    updated = svc.apply_bonus_to_round("p-1", "r-1", alloc.bonus_id, Decimal("10"))
    assert updated.status == BonusStatus.COMPLETED


def test_apply_bonus_to_inactive_bonus_raises() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    svc.forfeit_bonus("p-1", alloc.bonus_id)
    with pytest.raises(ValueError):
        svc.apply_bonus_to_round("p-1", "r-1", alloc.bonus_id, Decimal("5"))


def test_forfeit_bonus_sets_forfeited_status() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    forfeited = svc.forfeit_bonus("p-1", alloc.bonus_id)
    assert forfeited.status == BonusStatus.FORFEITED


def test_forfeit_already_forfeited_raises() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    svc.forfeit_bonus("p-1", alloc.bonus_id)
    with pytest.raises(ValueError):
        svc.forfeit_bonus("p-1", alloc.bonus_id)


def test_check_progress_wrong_player_raises_permission_error() -> None:
    svc = BonusService()
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    with pytest.raises(PermissionError):
        svc.check_wagering_progress("p-OTHER", alloc.bonus_id)


def test_unknown_bonus_id_raises_key_error() -> None:
    svc = BonusService()
    with pytest.raises(KeyError):
        svc.check_wagering_progress("p-1", "non-existent")


def test_active_bonuses_for_returns_only_active() -> None:
    svc = BonusService()
    a1 = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    a2 = svc.allocate_bonus("p-1", Decimal("5"), "reload")
    svc.forfeit_bonus("p-1", a2.bonus_id)
    active = svc.active_bonuses_for("p-1")
    assert len(active) == 1
    assert active[0].bonus_id == a1.bonus_id


def test_progress_pct_capped_at_100() -> None:
    svc = BonusService(default_wagering_multiplier=Decimal("1"))
    alloc = svc.allocate_bonus("p-1", Decimal("10"), "welcome")
    svc.apply_bonus_to_round("p-1", "r-1", alloc.bonus_id, Decimal("100"))
    progress = svc.check_wagering_progress("p-1", alloc.bonus_id)
    assert progress.progress_pct == Decimal("100")
