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
tests/test_capability_matrix.py
--------------------------------
Test suite for the Capability Matrix Service.

Covers:
  - Feature flag management and querying
  - RTP range compliance
  - Rate limit configuration
  - Supplier search with multi-dimension filters
  - Comparison matrix generation
  - Jurisdiction, currency, and game coverage maps
"""

from __future__ import annotations

import os
import sys

import pytest

_HERE = os.path.dirname(__file__)
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from capability_matrix import (
    CapabilityMatrixService,
    GameCategory,
    RTPRange,
    SupplierFeatureFlags,
    SupplierRateLimits,
)
from models import (
    SupplierCapabilityMatrix,
    SupplierRecord,
    SupplierStatus,
    SupplierType,
    WalletModel,
)
from registry import SupplierRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_registry_with_suppliers():
    reg = SupplierRegistry()

    evo = SupplierRecord(
        id="evolution",
        name="Evolution Gaming",
        type=SupplierType.CASINO,
        status=SupplierStatus.ACTIVE,
        capabilities=SupplierCapabilityMatrix(
            supplier_id="evolution",
            games={"blackjack", "roulette", "baccarat"},
            currencies={"EUR", "GBP", "USD"},
            jurisdictions={"GB", "MT", "SE"},
            wallet_model=WalletModel.SEAMLESS,
            rtp_certified=True,
            max_bet_usd=10000,
        ),
    )
    reg.register_supplier(evo)

    pragmatic = SupplierRecord(
        id="pragmatic",
        name="Pragmatic Play",
        type=SupplierType.CASINO,
        status=SupplierStatus.ACTIVE,
        capabilities=SupplierCapabilityMatrix(
            supplier_id="pragmatic",
            games={"sweet-bonanza", "gates-of-olympus", "roulette"},
            currencies={"EUR", "BRL", "USD"},
            jurisdictions={"GB", "MT", "BR"},
            wallet_model=WalletModel.SEAMLESS,
            rtp_certified=True,
        ),
    )
    reg.register_supplier(pragmatic)

    netent = SupplierRecord(
        id="netent",
        name="NetEnt",
        type=SupplierType.CASINO,
        status=SupplierStatus.ACTIVE,
        capabilities=SupplierCapabilityMatrix(
            supplier_id="netent",
            games={"starburst", "dead-or-alive", "gonzos-quest"},
            currencies={"EUR", "GBP", "SEK"},
            jurisdictions={"GB", "SE", "DK"},
            wallet_model=WalletModel.TRANSFER,
            rtp_certified=True,
        ),
    )
    reg.register_supplier(netent)

    disabled = SupplierRecord(
        id="old-supplier",
        name="Old Supplier",
        type=SupplierType.CASINO,
        status=SupplierStatus.DISABLED,
        capabilities=SupplierCapabilityMatrix(
            supplier_id="old-supplier",
            games={"slots-classic"},
            currencies={"EUR"},
            jurisdictions={"MT"},
        ),
    )
    reg.register_supplier(disabled)

    return reg


def _make_service():
    reg = _make_registry_with_suppliers()
    svc = CapabilityMatrixService(registry=reg)

    # Set feature flags
    svc.set_features(SupplierFeatureFlags(
        supplier_id="evolution",
        free_spins=False,
        bonus_buy=False,
        jackpot=True,
        tournament=True,
    ))
    svc.set_features(SupplierFeatureFlags(
        supplier_id="pragmatic",
        free_spins=True,
        bonus_buy=True,
        jackpot=False,
        tournament=False,
    ))
    svc.set_features(SupplierFeatureFlags(
        supplier_id="netent",
        free_spins=True,
        bonus_buy=False,
        jackpot=True,
    ))

    # Set RTP ranges
    svc.set_rtp_range(RTPRange("evolution", min_rtp=0.95, max_rtp=0.99))
    svc.set_rtp_range(RTPRange("pragmatic", min_rtp=0.94, max_rtp=0.97))
    svc.set_rtp_range(RTPRange("netent", min_rtp=0.96, max_rtp=0.98))

    # Set rate limits
    svc.set_rate_limits(SupplierRateLimits("evolution", max_requests_per_second=200))
    svc.set_rate_limits(SupplierRateLimits("pragmatic", max_requests_per_second=150))

    return svc


# ===========================================================================
# 1. Feature Flags
# ===========================================================================


class TestFeatureFlags:
    def test_set_and_get_features(self):
        svc = _make_service()
        features = svc.get_features("evolution")
        assert features.jackpot is True
        assert features.free_spins is False

    def test_default_features_all_false(self):
        svc = _make_service()
        features = svc.get_features("unknown-supplier")
        assert features.free_spins is False
        assert features.jackpot is False

    def test_supports_named_feature(self):
        flags = SupplierFeatureFlags(
            supplier_id="test",
            free_spins=True,
            bonus_buy=False,
        )
        assert flags.supports("free_spins") is True
        assert flags.supports("bonus_buy") is False
        assert flags.supports("nonexistent") is False

    def test_enabled_features_set(self):
        flags = SupplierFeatureFlags(
            supplier_id="test",
            free_spins=True,
            jackpot=True,
            tournament=True,
        )
        enabled = flags.enabled_features()
        assert enabled == {"free_spins", "jackpot", "tournament"}

    def test_feature_flags_to_dict(self):
        flags = SupplierFeatureFlags(supplier_id="test", free_spins=True)
        d = flags.to_dict()
        assert d["supplier_id"] == "test"
        assert d["free_spins"] is True
        assert d["bonus_buy"] is False


# ===========================================================================
# 2. RTP Range
# ===========================================================================


class TestRTPRange:
    def test_is_compliant_meets_minimum(self):
        rtp = RTPRange("evo", min_rtp=0.95, max_rtp=0.99)
        assert rtp.is_compliant(0.94) is True

    def test_is_compliant_exactly_at_minimum(self):
        rtp = RTPRange("evo", min_rtp=0.95, max_rtp=0.99)
        assert rtp.is_compliant(0.95) is True

    def test_is_not_compliant_below_minimum(self):
        rtp = RTPRange("evo", min_rtp=0.93, max_rtp=0.97)
        assert rtp.is_compliant(0.94) is False

    def test_get_rtp_range(self):
        svc = _make_service()
        rtp = svc.get_rtp_range("evolution")
        assert rtp.min_rtp == 0.95
        assert rtp.max_rtp == 0.99


# ===========================================================================
# 3. Rate Limits
# ===========================================================================


class TestRateLimits:
    def test_get_rate_limits(self):
        svc = _make_service()
        limits = svc.get_rate_limits("evolution")
        assert limits.max_requests_per_second == 200

    def test_default_rate_limits(self):
        svc = _make_service()
        limits = svc.get_rate_limits("unknown")
        assert limits.max_requests_per_second == 100  # default

    def test_allows_request_under_limit(self):
        limits = SupplierRateLimits("test", max_requests_per_second=100)
        assert limits.allows_request(50) is True

    def test_denies_request_over_limit(self):
        limits = SupplierRateLimits("test", max_requests_per_second=100)
        assert limits.allows_request(100) is False


# ===========================================================================
# 4. Supplier Search
# ===========================================================================


class TestFindSuppliers:
    def test_find_all_active(self):
        svc = _make_service()
        results = svc.find_suppliers()
        assert "evolution" in results
        assert "pragmatic" in results
        assert "netent" in results
        assert "old-supplier" not in results

    def test_find_by_jurisdiction(self):
        svc = _make_service()
        results = svc.find_suppliers(jurisdiction="BR")
        assert results == ["pragmatic"]

    def test_find_by_currency(self):
        svc = _make_service()
        results = svc.find_suppliers(currency="SEK")
        assert results == ["netent"]

    def test_find_by_game(self):
        svc = _make_service()
        results = svc.find_suppliers(game="roulette")
        assert set(results) == {"evolution", "pragmatic"}

    def test_find_by_feature_free_spins(self):
        svc = _make_service()
        results = svc.find_suppliers(features={"free_spins"})
        assert set(results) == {"pragmatic", "netent"}

    def test_find_by_multiple_features(self):
        svc = _make_service()
        results = svc.find_suppliers(features={"free_spins", "bonus_buy"})
        assert results == ["pragmatic"]

    def test_find_by_min_rtp(self):
        svc = _make_service()
        results = svc.find_suppliers(min_rtp=0.95)
        assert set(results) == {"evolution", "netent"}

    def test_find_by_wallet_model(self):
        svc = _make_service()
        results = svc.find_suppliers(wallet_model=WalletModel.TRANSFER)
        assert results == ["netent"]

    def test_find_combined_filters(self):
        svc = _make_service()
        results = svc.find_suppliers(
            jurisdiction="GB",
            currency="EUR",
            features={"jackpot"},
        )
        assert set(results) == {"evolution", "netent"}

    def test_find_no_matches(self):
        svc = _make_service()
        results = svc.find_suppliers(jurisdiction="JP")
        assert results == []


# ===========================================================================
# 5. Comparison Matrix
# ===========================================================================


class TestCompareSuppliers:
    def test_compare_two_suppliers(self):
        svc = _make_service()
        comparison = svc.compare_suppliers(["evolution", "pragmatic"])
        assert len(comparison) == 2
        evo = comparison[0]
        assert evo["supplier_id"] == "evolution"
        assert "EUR" in evo["currencies"]
        assert evo["features"]["jackpot"] is True

    def test_compare_unknown_supplier_skipped(self):
        svc = _make_service()
        comparison = svc.compare_suppliers(["evolution", "nonexistent"])
        assert len(comparison) == 1

    def test_comparison_includes_rate_limits(self):
        svc = _make_service()
        comparison = svc.compare_suppliers(["evolution"])
        assert comparison[0]["rate_limits"]["max_rps"] == 200

    def test_comparison_includes_rtp(self):
        svc = _make_service()
        comparison = svc.compare_suppliers(["evolution"])
        assert comparison[0]["rtp_range"]["min"] == 0.95


# ===========================================================================
# 6. Coverage Maps
# ===========================================================================


class TestCoverageMaps:
    def test_jurisdiction_coverage(self):
        svc = _make_service()
        coverage = svc.get_jurisdiction_coverage()
        assert "GB" in coverage
        assert set(coverage["GB"]) == {"evolution", "pragmatic", "netent"}
        assert "BR" in coverage
        assert coverage["BR"] == ["pragmatic"]

    def test_currency_coverage(self):
        svc = _make_service()
        coverage = svc.get_currency_coverage()
        assert "EUR" in coverage
        assert len(coverage["EUR"]) == 3  # evo, pragmatic, netent

    def test_game_coverage(self):
        svc = _make_service()
        coverage = svc.get_game_coverage()
        assert "roulette" in coverage
        assert set(coverage["roulette"]) == {"evolution", "pragmatic"}

    def test_disabled_supplier_excluded_from_coverage(self):
        svc = _make_service()
        coverage = svc.get_game_coverage()
        assert "slots-classic" not in coverage
