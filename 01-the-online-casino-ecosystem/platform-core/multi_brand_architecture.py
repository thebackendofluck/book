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
# Multi-Tenant / Multi-Brand Architecture Patterns
# Source: Production casino platform (sanitized)
# Chapter 1 - The Online Casino Ecosystem
#
# Casino platforms typically serve multiple brands (white-label operators)
# from a single codebase. This file shows the three key patterns that
# make this possible: Platform instance types, BrandSettings hierarchy,
# and SupplierSettings per-supplier/per-brand configuration.
# =============================================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Generic, Optional, TypeVar

T = TypeVar("T")

# ---------------------------------------------------------------------------
# 1. PLATFORM INSTANCE TYPES (Hub/Spoke/Standalone)
# ---------------------------------------------------------------------------
# A single codebase supports three deployment modes:
# - STANDALONE: Everything on one instance (small operators)
# - HUB: Central instance for user registration, cross-jurisdiction ops
# - SPOKE: Jurisdiction-specific instance for localized operations


PLATFORM_DIR = Path("c:/platform") if os.name == "nt" else Path("/opt/platform")


class EnvType(str, Enum):
    PROD = "prod"
    STAGE = "stage"
    DEV = "dev"
    LOCAL = "local"


class InstanceType(str, Enum):
    STANDALONE = "standalone"
    HUB = "hub"
    SPOKE = "spoke"


def get_instance_name(instance_type: InstanceType, fixed_jurisdiction: Optional[str] = None) -> str:
    """
    The instance name doubles as:
    1. Hazelcast/cluster name (for distributed caching)
    2. Kafka consumer group name (for event processing)

    Changing this without migrating message_topic_offsets
    will replay ALL Kafka events -- learned the hard way.
    """
    if instance_type == InstanceType.STANDALONE:
        return "platform"
    if instance_type == InstanceType.HUB:
        return "hub"
    if instance_type == InstanceType.SPOKE:
        if fixed_jurisdiction is None:
            raise ValueError("SPOKE instance requires a fixed jurisdiction")
        return f"spoke-{fixed_jurisdiction}"
    raise ValueError(f"Unknown instance type: {instance_type}")


# ---------------------------------------------------------------------------
# 2. BRAND SETTINGS: Hierarchical Configuration
# ---------------------------------------------------------------------------
# Settings cascade through a priority chain, allowing fine-grained
# overrides at every level of the brand/jurisdiction/country hierarchy.


@dataclass(frozen=True)
class BrandAndJurisdiction:
    brand: Optional[int] = None
    jurisdiction: Optional[str] = None
    country: Optional[str] = None
    currency: Optional[str] = None


class BrandSettings:
    """
    Settings resolution chain (priority order, highest to lowest):

    1. Brand + Jurisdiction + Country + Currency  (most specific)
    2. Brand + Country + Currency
    3. Brand + Country
    4. Brand + Jurisdiction
    5. Brand only
    6. Country only
    7. Jurisdiction only
    8. Global default                             (least specific)

    This means: a setting defined for "Brand 42 in Sweden" overrides
    the same setting defined for "all Swedish brands", which in turn
    overrides the global default.
    """

    # Example settings with defaults
    SHOW_RESPONSIBLE_GAMBLING_BANNER = "responsible-gambling-banner-enabled"
    KYC_ON_SIGNUP_BONUS = "kyc-on-signup-bonus"
    KYC_APPROVAL_MODE = "kyc-approval-mode"
    REALITY_CHECK_FREQUENCY = "reality-check-frequency"

    DEFAULTS: dict[str, Any] = {
        SHOW_RESPONSIBLE_GAMBLING_BANNER: True,
        KYC_ON_SIGNUP_BONUS: False,
        KYC_APPROVAL_MODE: "KYC_NOT_IMMEDIATELY_NEEDED",
        REALITY_CHECK_FREQUENCY: None,
    }

    def __init__(self, store: dict[BrandAndJurisdiction, dict[str, Any]]) -> None:
        self._store = store

    def _resolve_chain(self, key: BrandAndJurisdiction) -> list[BrandAndJurisdiction]:
        """Build the lookup chain from most-specific to least-specific."""
        return [
            key,  # All fields
            BrandAndJurisdiction(key.brand, None, key.country, key.currency),  # Brand+Country+Currency
            BrandAndJurisdiction(key.brand, None, key.country, None),          # Brand+Country
            BrandAndJurisdiction(key.brand, key.jurisdiction, None, None),     # Brand+Jurisdiction
            BrandAndJurisdiction(key.brand, None, None, None),                 # Brand only
            BrandAndJurisdiction(None, None, key.country, None),               # Country only
            BrandAndJurisdiction(None, key.jurisdiction, None, None),          # Jurisdiction only
            BrandAndJurisdiction(None, None, None, None),                      # Global default
        ]

    def get(self, key: BrandAndJurisdiction, setting: str) -> Any:
        for candidate in self._resolve_chain(key):
            if candidate in self._store and setting in self._store[candidate]:
                return self._store[candidate][setting]
        return self.DEFAULTS.get(setting)

    def value_for_brand(self, brand: int, setting: str) -> Any:
        return self.get(BrandAndJurisdiction(brand=brand), setting)

    def value_for_brand_and_jurisdiction(self, brand: int, jurisdiction: str, setting: str) -> Any:
        return self.get(BrandAndJurisdiction(brand=brand, jurisdiction=jurisdiction), setting)

    def value_for_jurisdiction(self, jurisdiction: str, setting: str) -> Any:
        return self.get(BrandAndJurisdiction(jurisdiction=jurisdiction), setting)


# ---------------------------------------------------------------------------
# 3. SUPPLIER SETTINGS: Per-Supplier, Per-Brand Configuration
# ---------------------------------------------------------------------------
# Game suppliers also need per-brand/per-jurisdiction settings
# (e.g., different Evolution table limits for UK vs Sweden brands).


@dataclass(frozen=True)
class SupplierBrandAndJurisdiction:
    supplier: int
    brand: Optional[int] = None
    jurisdiction: Optional[str] = None


class SupplierSettings:
    """
    Resolution chain: Supplier+Brand+Jurisdiction -> Supplier+Brand
                      -> Supplier+Jurisdiction    -> Supplier only
    """

    LAUNCH_TOKEN_LIFETIME = "launch-token-lifetime"
    ONE_OFF_LAUNCH_TOKEN = "one-off-launch-token"
    RISK_FREE_BETS = "risk-free-bet-support"

    DEFAULTS: dict[str, Any] = {
        LAUNCH_TOKEN_LIFETIME: 60 * 60 * 12,  # 12 hours
        ONE_OFF_LAUNCH_TOKEN: False,
        RISK_FREE_BETS: False,
    }

    def __init__(self, store: dict[SupplierBrandAndJurisdiction, dict[str, Any]]) -> None:
        self._store = store

    def _resolve_chain(self, key: SupplierBrandAndJurisdiction) -> list[SupplierBrandAndJurisdiction]:
        return [
            key,
            SupplierBrandAndJurisdiction(key.supplier, key.brand, None),
            SupplierBrandAndJurisdiction(key.supplier, None, key.jurisdiction),
            SupplierBrandAndJurisdiction(key.supplier, None, None),
        ]

    def get(self, key: SupplierBrandAndJurisdiction, setting: str) -> Any:
        for candidate in self._resolve_chain(key):
            if candidate in self._store and setting in self._store[candidate]:
                return self._store[candidate][setting]
        return self.DEFAULTS.get(setting)

    def settings_for_brand_and_jurisdiction(
        self, supplier: int, brand: int, jurisdiction: str
    ) -> dict[str, Any]:
        key = SupplierBrandAndJurisdiction(supplier, brand, jurisdiction)
        return {setting: self.get(key, setting) for setting in self.DEFAULTS}


# ---------------------------------------------------------------------------
# WHY MULTI-BRAND MATTERS FOR THE ECOSYSTEM
# ---------------------------------------------------------------------------
#
# The package structure and configuration hierarchy shown here is what
# enables the B2B white-label model described in Chapter 1:
#
# - A SINGLE PLATFORM INSTANCE serves 20+ casino brands
# - Each brand can have different KYC rules, bonus policies, and
#   responsible gambling settings
# - Each jurisdiction (UK, Sweden, Germany, Malta) enforces its own
#   regulatory requirements through the settings chain
# - Game suppliers are configured per-brand: Brand A might offer
#   Evolution live casino while Brand B does not
# - The Hub/Spoke architecture allows a platform to operate across
#   multiple jurisdictions while keeping player data localized
#   (critical for GDPR and data residency requirements)
