#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 43, Future Technology & Innovation in iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Tests for the jurisdiction/product gate (chapter 43c)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jurisdiction_gate import (  # noqa: E402
    AccessMode,
    JurisdictionGate,
    UnknownJurisdiction,
)
from market_lifecycle import MarketCategory  # noqa: E402


@pytest.fixture
def gate():
    return JurisdictionGate()


class TestDirectMarket:
    def test_gibraltar_football_direct(self, gate):
        d = gate.evaluate("GI", MarketCategory.FOOTBALL)
        assert d.allowed is True
        assert d.mode == AccessMode.DIRECT
        assert d.partner is None

    def test_gibraltar_politics_allowed_post_expansion(self, gate):
        d = gate.evaluate("gi", MarketCategory.POLITICS)  # case-insensitive
        assert d.allowed is True

    def test_gibraltar_finance_not_yet_approved(self, gate):
        d = gate.evaluate("GI", MarketCategory.FINANCE)
        assert d.allowed is False
        assert any("FINANCE" in r for r in d.reasons)


class TestPartnerEmbedded:
    def test_brazil_sports_only_via_matchbook(self, gate):
        d = gate.evaluate("BR", MarketCategory.FOOTBALL)
        assert d.allowed is True
        assert d.mode == AccessMode.PARTNER_EMBEDDED
        assert d.partner == "matchbook"

    def test_brazil_politics_denied(self, gate):
        d = gate.evaluate("BR", MarketCategory.POLITICS)
        assert d.allowed is False
        assert d.partner is None

    def test_us_route_via_fanatics(self, gate):
        d = gate.evaluate("US", MarketCategory.FOOTBALL)
        assert d.allowed is True
        assert d.partner == "fanatics-markets"

    def test_uk_and_ireland_via_matchbook(self, gate):
        for code in ("GB", "IE"):
            d = gate.evaluate(code, MarketCategory.OTHER_SPORTS)
            assert d.allowed is True
            assert d.partner == "matchbook"


class TestBlockedAndPending:
    @pytest.mark.parametrize("code", ["DE", "FR", "NL", "NZ"])
    def test_blocked_jurisdictions(self, gate, code):
        d = gate.evaluate(code, MarketCategory.FOOTBALL)
        assert d.allowed is False
        assert d.mode == AccessMode.BLOCKED

    def test_malta_pending_framework(self, gate):
        d = gate.evaluate("MT", MarketCategory.FOOTBALL)
        assert d.allowed is False
        assert d.mode == AccessMode.PENDING_FRAMEWORK

    def test_unknown_jurisdiction_default_deny(self, gate):
        with pytest.raises(UnknownJurisdiction):
            gate.evaluate("XX", MarketCategory.FOOTBALL)


class TestAuditAndQueries:
    def test_every_evaluation_is_logged(self, gate):
        gate.evaluate("GI", MarketCategory.FOOTBALL)
        gate.evaluate("DE", MarketCategory.FOOTBALL)
        assert len(gate.audit_log) == 2
        assert gate.audit_log[1].allowed is False

    def test_decisions_carry_legal_basis(self, gate):
        d = gate.evaluate("BR", MarketCategory.FOOTBALL)
        assert any("14.790" in r for r in d.reasons)

    def test_reachable_jurisdictions_for_football(self, gate):
        reachable = gate.reachable_jurisdictions(MarketCategory.FOOTBALL)
        codes = {d.jurisdiction for d in reachable}
        assert codes == {"GI", "US", "GB", "IE", "BR"}

    def test_reachable_jurisdictions_for_politics(self, gate):
        reachable = gate.reachable_jurisdictions(MarketCategory.POLITICS)
        assert {d.jurisdiction for d in reachable} == {"GI"}
