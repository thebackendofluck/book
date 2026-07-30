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
capability_matrix.py
--------------------
Capability Matrix Service for the Supplier Integration Control Plane.

The capability matrix declares what each supplier supports across multiple
dimensions: game types, currencies, jurisdictions, RTP ranges, and feature
flags. This service provides query and comparison operations used by:

  - Game Aggregation Layer: routing a game launch to the right supplier
  - Compliance Engine: ensuring a supplier is licensed for a jurisdiction
  - Bonus Engine: checking if a supplier supports free spins / bonus buys
  - Backoffice UI: displaying a comparison grid of supplier capabilities

Feature flags
-------------
Each supplier can declare support for optional features like:
  - free_spins: Supplier-managed free spin rounds
  - bonus_buy:  Players can purchase direct access to bonus rounds
  - jackpot:    Supplier contributes to shared or local jackpot pools
  - tournament: Supplier supports tournament mode / leaderboards
  - cashback:   Supplier supports cashback on losses

These flags drive runtime behaviour — e.g., the bonus engine will not
attempt to award free spins through a supplier that lacks support.

Usage:
    service = CapabilityMatrixService(registry=registry)
    # Find suppliers licensed for GB that support EUR and free spins
    matches = service.find_suppliers(
        jurisdiction="GB", currency="EUR", features={"free_spins"},
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from models import SupplierCapabilityMatrix, SupplierStatus, WalletModel
from registry import SupplierRegistry, registry as default_registry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Extended capability model
# ---------------------------------------------------------------------------


class GameCategory(str, Enum):
    SLOTS = "slots"
    TABLE_GAMES = "table_games"
    LIVE_CASINO = "live_casino"
    CRASH = "crash"
    VIRTUAL_SPORTS = "virtual_sports"
    SCRATCH_CARDS = "scratch_cards"
    BINGO = "bingo"
    LOTTERY = "lottery"


@dataclass
class SupplierFeatureFlags:
    """
    Optional feature support per supplier.

    Each flag indicates whether the supplier's API supports a given
    platform feature. Defaults to False (opt-in).
    """
    supplier_id: str
    free_spins: bool = False
    bonus_buy: bool = False
    jackpot: bool = False
    tournament: bool = False
    cashback: bool = False
    responsible_gaming_api: bool = False
    reality_check: bool = False
    session_limits: bool = False

    def supports(self, feature: str) -> bool:
        """Check if a named feature is supported."""
        return getattr(self, feature, False)

    def enabled_features(self) -> set[str]:
        """Return the set of enabled feature names."""
        return {
            name
            for name in (
                "free_spins", "bonus_buy", "jackpot", "tournament",
                "cashback", "responsible_gaming_api", "reality_check",
                "session_limits",
            )
            if getattr(self, name, False)
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "supplier_id": self.supplier_id,
            "free_spins": self.free_spins,
            "bonus_buy": self.bonus_buy,
            "jackpot": self.jackpot,
            "tournament": self.tournament,
            "cashback": self.cashback,
            "responsible_gaming_api": self.responsible_gaming_api,
            "reality_check": self.reality_check,
            "session_limits": self.session_limits,
        }


@dataclass
class RTPRange:
    """Min/max RTP for a supplier's game catalogue."""
    supplier_id: str
    min_rtp: float = 0.0    # e.g. 0.92 (92%)
    max_rtp: float = 0.0    # e.g. 0.97 (97%)

    def is_compliant(self, min_required: float) -> bool:
        """Check if the supplier's min RTP meets a regulatory minimum."""
        return self.min_rtp >= min_required


@dataclass
class SupplierRateLimits:
    """Rate limits for API calls to a supplier."""
    supplier_id: str
    max_requests_per_second: int = 100
    max_requests_per_minute: int = 5000
    max_concurrent_sessions: int = 10000
    burst_limit: int = 200

    def allows_request(self, current_rps: int) -> bool:
        return current_rps < self.max_requests_per_second


# ---------------------------------------------------------------------------
# Capability Matrix Service
# ---------------------------------------------------------------------------


class CapabilityMatrixService:
    """
    Query and comparison service for supplier capabilities.

    Wraps the basic SupplierCapabilityMatrix stored on each SupplierRecord
    and adds feature flags, RTP ranges, and rate limit metadata.

    Parameters
    ----------
    registry: SupplierRegistry for supplier lookups.
    """

    def __init__(self, registry: SupplierRegistry = default_registry) -> None:
        self._registry = registry
        self._features: dict[str, SupplierFeatureFlags] = {}
        self._rtp_ranges: dict[str, RTPRange] = {}
        self._rate_limits: dict[str, SupplierRateLimits] = {}

    # ------------------------------------------------------------------
    # Feature flag management
    # ------------------------------------------------------------------

    def set_features(self, flags: SupplierFeatureFlags) -> None:
        """Register feature flags for a supplier."""
        self._features[flags.supplier_id] = flags

    def get_features(self, supplier_id: str) -> SupplierFeatureFlags:
        """Return feature flags for a supplier (defaults to all-False)."""
        return self._features.get(
            supplier_id,
            SupplierFeatureFlags(supplier_id=supplier_id),
        )

    # ------------------------------------------------------------------
    # RTP management
    # ------------------------------------------------------------------

    def set_rtp_range(self, rtp: RTPRange) -> None:
        self._rtp_ranges[rtp.supplier_id] = rtp

    def get_rtp_range(self, supplier_id: str) -> RTPRange:
        return self._rtp_ranges.get(
            supplier_id,
            RTPRange(supplier_id=supplier_id),
        )

    # ------------------------------------------------------------------
    # Rate limit management
    # ------------------------------------------------------------------

    def set_rate_limits(self, limits: SupplierRateLimits) -> None:
        self._rate_limits[limits.supplier_id] = limits

    def get_rate_limits(self, supplier_id: str) -> SupplierRateLimits:
        return self._rate_limits.get(
            supplier_id,
            SupplierRateLimits(supplier_id=supplier_id),
        )

    # ------------------------------------------------------------------
    # Query operations
    # ------------------------------------------------------------------

    def find_suppliers(
        self,
        jurisdiction: Optional[str] = None,
        currency: Optional[str] = None,
        game: Optional[str] = None,
        features: Optional[set[str]] = None,
        min_rtp: Optional[float] = None,
        wallet_model: Optional[WalletModel] = None,
        active_only: bool = True,
    ) -> list[str]:
        """
        Find supplier IDs matching all specified criteria.

        All parameters are optional; unset parameters are not filtered.
        Returns a list of matching supplier IDs.
        """
        results: list[str] = []

        for record in self._registry.list_suppliers():
            if active_only and record.status != SupplierStatus.ACTIVE:
                continue

            cap = record.capabilities
            if cap is None:
                continue

            if jurisdiction and not cap.supports_jurisdiction(jurisdiction):
                continue
            if currency and not cap.supports_currency(currency):
                continue
            if game and game not in cap.games:
                continue
            if wallet_model and cap.wallet_model != wallet_model:
                continue

            # Feature flag check
            if features:
                supplier_features = self.get_features(record.id)
                if not all(supplier_features.supports(f) for f in features):
                    continue

            # RTP check
            if min_rtp is not None:
                rtp_range = self.get_rtp_range(record.id)
                if not rtp_range.is_compliant(min_rtp):
                    continue

            results.append(record.id)

        return results

    def compare_suppliers(
        self,
        supplier_ids: list[str],
    ) -> list[dict[str, Any]]:
        """
        Generate a comparison matrix for the given suppliers.

        Returns a list of dicts, one per supplier, with all capability
        dimensions populated.
        """
        comparison: list[dict[str, Any]] = []

        for sid in supplier_ids:
            try:
                record = self._registry.get_supplier(sid)
            except KeyError:
                continue

            cap = record.capabilities
            features = self.get_features(sid)
            rtp = self.get_rtp_range(sid)
            rate_limits = self.get_rate_limits(sid)

            entry: dict[str, Any] = {
                "supplier_id": sid,
                "name": record.name,
                "status": record.status.value,
                "type": record.type.value,
            }

            if cap:
                entry["games"] = sorted(cap.games)
                entry["currencies"] = sorted(cap.currencies)
                entry["jurisdictions"] = sorted(cap.jurisdictions)
                entry["wallet_model"] = cap.wallet_model.value
                entry["rtp_certified"] = cap.rtp_certified
                entry["max_bet_usd"] = cap.max_bet_usd
            else:
                entry["games"] = []
                entry["currencies"] = []
                entry["jurisdictions"] = []

            entry["features"] = features.to_dict()
            entry["rtp_range"] = {"min": rtp.min_rtp, "max": rtp.max_rtp}
            entry["rate_limits"] = {
                "max_rps": rate_limits.max_requests_per_second,
                "max_rpm": rate_limits.max_requests_per_minute,
                "max_concurrent": rate_limits.max_concurrent_sessions,
            }

            comparison.append(entry)

        return comparison

    def get_jurisdiction_coverage(self) -> dict[str, list[str]]:
        """
        Return a map of jurisdiction -> list of supplier IDs licensed there.

        Useful for compliance dashboards showing which jurisdictions have
        redundant supplier coverage.
        """
        coverage: dict[str, list[str]] = {}

        for record in self._registry.list_suppliers():
            if record.status == SupplierStatus.DISABLED:
                continue
            cap = record.capabilities
            if cap is None:
                continue
            for jur in cap.jurisdictions:
                coverage.setdefault(jur, []).append(record.id)

        return coverage

    def get_currency_coverage(self) -> dict[str, list[str]]:
        """Return a map of currency -> list of supplier IDs supporting it."""
        coverage: dict[str, list[str]] = {}

        for record in self._registry.list_suppliers():
            if record.status == SupplierStatus.DISABLED:
                continue
            cap = record.capabilities
            if cap is None:
                continue
            for cur in cap.currencies:
                coverage.setdefault(cur, []).append(record.id)

        return coverage

    def get_game_coverage(self) -> dict[str, list[str]]:
        """Return a map of game slug -> list of supplier IDs offering it."""
        coverage: dict[str, list[str]] = {}

        for record in self._registry.list_suppliers():
            if record.status == SupplierStatus.DISABLED:
                continue
            cap = record.capabilities
            if cap is None:
                continue
            for game in cap.games:
                coverage.setdefault(game, []).append(record.id)

        return coverage


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

capability_service = CapabilityMatrixService()
