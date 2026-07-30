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
Unit tests for LimitsService: deposit/loss/wager/session limits.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from acmetocasino.gameservice.accounts.limits_service import LimitsService


def test_no_limit_set_always_allowed() -> None:
    svc = LimitsService()
    result = svc.check_deposit_limit("p-1", Decimal("9999"))
    assert result.allowed is True


def test_deposit_limit_within_budget_allowed() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "deposit", Decimal("100"), 86400)
    result = svc.check_deposit_limit("p-1", Decimal("50"))
    assert result.allowed is True
    assert result.remaining == Decimal("100")


def test_deposit_limit_exceeded_blocked() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "deposit", Decimal("100"), 86400)
    svc.record_usage("p-1", "deposit", Decimal("90"))
    result = svc.check_deposit_limit("p-1", Decimal("20"))
    assert result.allowed is False
    assert result.reason is not None
    assert "deposit" in result.reason.lower()


def test_deposit_limit_exact_amount_allowed() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "deposit", Decimal("100"), 86400)
    svc.record_usage("p-1", "deposit", Decimal("50"))
    result = svc.check_deposit_limit("p-1", Decimal("50"))
    assert result.allowed is True


def test_loss_limit_blocks_excess() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "loss", Decimal("200"), 86400)
    svc.record_usage("p-1", "loss", Decimal("190"))
    result = svc.check_loss_limit("p-1", Decimal("20"))
    assert result.allowed is False


def test_wager_limit_blocks_excess() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "wager", Decimal("10"), 86400)
    result = svc.check_wager_limit("p-1", Decimal("15"))
    assert result.allowed is False


def test_session_duration_no_session_start_is_ok() -> None:
    svc = LimitsService(default_session_duration_seconds=3600)
    result = svc.check_session_duration("p-1")
    assert result.allowed is True


def test_session_duration_within_limit_is_ok() -> None:
    svc = LimitsService(default_session_duration_seconds=3600)
    svc.record_session_start("p-1")
    result = svc.check_session_duration("p-1")
    assert result.allowed is True


def test_session_duration_no_limit_configured_is_ok() -> None:
    svc = LimitsService()  # default_session_duration_seconds=0 means disabled
    svc.record_session_start("p-1")
    result = svc.check_session_duration("p-1")
    assert result.allowed is True


def test_wager_limit_type_in_result() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "wager", Decimal("5"), 3600)
    result = svc.check_wager_limit("p-1", Decimal("3"))
    assert result.limit_type == "wager"


def test_record_usage_accumulates() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "deposit", Decimal("100"), 86400)
    svc.record_usage("p-1", "deposit", Decimal("30"))
    svc.record_usage("p-1", "deposit", Decimal("30"))
    result = svc.check_deposit_limit("p-1", Decimal("41"))
    assert result.allowed is False


def test_record_usage_no_op_when_no_limit_set() -> None:
    svc = LimitsService()
    # Should not raise even with no limit registered
    svc.record_usage("p-1", "deposit", Decimal("999"))


def test_multiple_players_independent_limits() -> None:
    svc = LimitsService()
    svc.set_limit("p-1", "deposit", Decimal("100"), 86400)
    svc.set_limit("p-2", "deposit", Decimal("50"), 86400)
    svc.record_usage("p-1", "deposit", Decimal("80"))
    svc.record_usage("p-2", "deposit", Decimal("10"))
    assert svc.check_deposit_limit("p-1", Decimal("30")).allowed is False
    assert svc.check_deposit_limit("p-2", Decimal("30")).allowed is True
