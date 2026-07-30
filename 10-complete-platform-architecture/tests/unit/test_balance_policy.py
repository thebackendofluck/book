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
Unit tests for BalancePolicy: fund usage ordering and wagering contributions.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts.balance_policy import BalancePolicy, UsageOrderPolicy
from acmetocasino.gameservice.models.enums import FundSource


def test_cash_first_default_order() -> None:
    policy = BalancePolicy()
    order = policy.determine_usage_order("p-1", Decimal("10"))
    assert order[0] == FundSource.CASH
    assert order[1] == FundSource.BONUS


def test_bonus_first_order() -> None:
    policy = BalancePolicy(usage_order=UsageOrderPolicy.BONUS_FIRST)
    order = policy.determine_usage_order("p-1", Decimal("10"))
    assert order[0] == FundSource.BONUS
    assert order[1] == FundSource.CASH


def test_proportional_returns_both() -> None:
    policy = BalancePolicy(usage_order=UsageOrderPolicy.PROPORTIONAL)
    order = policy.determine_usage_order("p-1", Decimal("10"))
    assert FundSource.CASH in order
    assert FundSource.BONUS in order


def test_player_override_takes_precedence() -> None:
    policy = BalancePolicy(usage_order=UsageOrderPolicy.CASH_FIRST)
    policy.set_player_policy("p-vip", UsageOrderPolicy.BONUS_FIRST)
    assert policy.determine_usage_order("p-vip", Decimal("10"))[0] == FundSource.BONUS
    assert policy.determine_usage_order("p-regular", Decimal("10"))[0] == FundSource.CASH


def test_slots_contribution_100_pct() -> None:
    policy = BalancePolicy()
    contribution = policy.apply_wagering_contribution("netent", "slots", Decimal("10.00"))
    assert contribution == Decimal("10.00")


def test_live_casino_contribution_10_pct() -> None:
    policy = BalancePolicy()
    contribution = policy.apply_wagering_contribution("evolution", "live_casino", Decimal("10.00"))
    assert contribution == Decimal("1.00")


def test_sportsbook_contribution_0_pct() -> None:
    policy = BalancePolicy()
    contribution = policy.apply_wagering_contribution("kambi", "sportsbook", Decimal("50.00"))
    assert contribution == Decimal("0.00")


def test_virtual_sports_contribution_50_pct() -> None:
    policy = BalancePolicy()
    contribution = policy.apply_wagering_contribution("nyx", "virtual_sports", Decimal("10.00"))
    assert contribution == Decimal("5.00")


def test_unknown_game_type_falls_back_to_wildcard() -> None:
    policy = BalancePolicy()
    contribution = policy.apply_wagering_contribution("unknown_supplier", "unknown_type", Decimal("10.00"))
    # Falls back to ("*","*") = 100%
    assert contribution == Decimal("10.00")


def test_custom_contribution_rate_overrides_default() -> None:
    policy = BalancePolicy()
    policy.set_contribution_rate("slots", Decimal("0.50"), supplier_id="testprovider")
    contribution = policy.apply_wagering_contribution("testprovider", "slots", Decimal("10.00"))
    assert contribution == Decimal("5.00")


def test_set_contribution_rate_out_of_range_raises() -> None:
    policy = BalancePolicy()
    with pytest.raises(ValueError):
        policy.set_contribution_rate("slots", Decimal("1.50"))


def test_set_contribution_rate_negative_raises() -> None:
    policy = BalancePolicy()
    with pytest.raises(ValueError):
        policy.set_contribution_rate("slots", Decimal("-0.01"))


def test_contribution_quantized_to_2_decimals() -> None:
    policy = BalancePolicy()
    result = policy.apply_wagering_contribution("*", "slots", Decimal("3.333"))
    # 3.333 * 1.00 = 3.33 (quantized)
    assert result == Decimal("3.33")


def test_constructor_contribution_rates_override_defaults() -> None:
    custom = {("mysupp", "slots"): Decimal("0.25")}
    policy = BalancePolicy(contribution_rates=custom)
    result = policy.apply_wagering_contribution("mysupp", "slots", Decimal("100.00"))
    assert result == Decimal("25.00")
