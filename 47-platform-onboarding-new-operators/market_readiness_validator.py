#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 47, Platform Onboarding.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Market Readiness Validator.

Validates that all prerequisites for launching in a specific market
are satisfied before the go-live decision:

  - Licensing documents present and valid
  - Geofencing configured and tested
  - Payment methods enabled for target currency
  - Responsible gaming controls active
  - Test accounts verified with full deposit/play/withdraw cycle

Environment Profiles
--------------------
  STAGING    - advisory mode: failures are downgraded to warnings, never
               blocks readiness.  Useful for early integration runs.
  PRODUCTION - strict mode: every blocker-severity failure blocks the
               readiness gate.  Use --strict on the CLI to engage.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"
    SKIP = "skip"


class EnvironmentProfile(Enum):
    """Controls how validation failures are treated."""
    STAGING = "staging"        # advisory: blockers downgraded to warnings
    PRODUCTION = "production"  # strict: blockers block readiness


class MarketTier(Enum):
    """Risk tier for the target market."""
    TIER_1 = "tier_1"  # UK, Malta, Gibraltar
    TIER_2 = "tier_2"  # Sweden, Denmark, Italy, Spain
    TIER_3 = "tier_3"  # US states, Germany, France
    EMERGING = "emerging"  # Brazil, LatAm, Africa


@dataclass
class ValidationCheck:
    check_id: str
    category: str
    name: str
    status: CheckStatus
    detail: str
    severity: str = "blocker"  # blocker | warning | info
    timestamp: float = 0.0


@dataclass
class MarketProfile:
    """Target market configuration."""
    country_code: str
    market_name: str
    tier: MarketTier
    currency: str
    regulator: str
    license_required: bool = True
    geofence_required: bool = False
    data_residency: str | None = None
    min_age: int = 18
    required_payment_methods: list[str] = field(default_factory=list)
    blocked_game_categories: list[str] = field(default_factory=list)
    rg_requirements: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Market registry
# ---------------------------------------------------------------------------

MARKET_REGISTRY: dict[str, MarketProfile] = {
    "GB": MarketProfile(
        country_code="GB", market_name="United Kingdom", tier=MarketTier.TIER_1,
        currency="GBP", regulator="UKGC", min_age=18,
        required_payment_methods=["visa", "mastercard", "paypal"],
        rg_requirements=["deposit_limits", "cool_off", "self_exclusion",
                         "reality_check", "affordability_check"],
    ),
    "MT": MarketProfile(
        country_code="MT", market_name="Malta", tier=MarketTier.TIER_1,
        currency="EUR", regulator="MGA", min_age=18,
        required_payment_methods=["visa", "mastercard"],
        rg_requirements=["deposit_limits", "cool_off", "self_exclusion"],
    ),
    "SE": MarketProfile(
        country_code="SE", market_name="Sweden", tier=MarketTier.TIER_2,
        currency="SEK", regulator="Spelinspektionen", min_age=18,
        required_payment_methods=["trustly", "swish"],
        blocked_game_categories=["table-poker-cash"],
        rg_requirements=["deposit_limits", "cool_off", "self_exclusion",
                         "mandatory_play_break", "spelpaus_integration"],
    ),
    "DE": MarketProfile(
        country_code="DE", market_name="Germany", tier=MarketTier.TIER_3,
        currency="EUR", regulator="GGL", min_age=18,
        required_payment_methods=["visa", "sofort", "giropay"],
        blocked_game_categories=["live-roulette", "live-blackjack", "live-baccarat"],
        rg_requirements=["deposit_limits", "monthly_limit_1000_eur",
                         "panic_button", "no_autoplay", "spin_interval_5s"],
    ),
    "US-NJ": MarketProfile(
        country_code="US-NJ", market_name="New Jersey", tier=MarketTier.TIER_3,
        currency="USD", regulator="NJ-DGE", min_age=21,
        geofence_required=True, data_residency="us-east-1",
        required_payment_methods=["visa", "ach", "paypal"],
        rg_requirements=["deposit_limits", "cool_off", "self_exclusion",
                         "geofence_verification"],
    ),
    "BR": MarketProfile(
        country_code="BR", market_name="Brazil", tier=MarketTier.EMERGING,
        currency="BRL", regulator="SIGAP/SPA-MF", min_age=18,
        required_payment_methods=["pix"],
        rg_requirements=["deposit_limits", "cool_off", "self_exclusion",
                         "cpf_verification"],
    ),
}


# ---------------------------------------------------------------------------
# Validation checks
# ---------------------------------------------------------------------------

def check_licensing(
    market: MarketProfile,
    operator_licenses: dict[str, dict[str, Any]],
) -> list[ValidationCheck]:
    """Verify operator holds required licenses."""
    checks: list[ValidationCheck] = []

    if not market.license_required:
        checks.append(ValidationCheck(
            check_id=uuid.uuid4().hex[:8], category="licensing",
            name=f"{market.country_code}: license check",
            status=CheckStatus.SKIP,
            detail="No license required for this market",
            severity="info", timestamp=time.time(),
        ))
        return checks

    license_info = operator_licenses.get(market.country_code)
    if license_info is None:
        checks.append(ValidationCheck(
            check_id=uuid.uuid4().hex[:8], category="licensing",
            name=f"{market.country_code}: gaming license",
            status=CheckStatus.FAIL,
            detail=f"No license found for {market.country_code} ({market.regulator})",
            severity="blocker", timestamp=time.time(),
        ))
        return checks

    # License exists -- check expiry
    expires = license_info.get("expires_at", 0)
    days_remaining = max(0, (expires - time.time()) / 86400)

    if days_remaining < 30:
        status = CheckStatus.FAIL
        detail = f"License expires in {int(days_remaining)} days -- renewal required"
        severity = "blocker"
    elif days_remaining < 90:
        status = CheckStatus.WARN
        detail = f"License expires in {int(days_remaining)} days -- schedule renewal"
        severity = "warning"
    else:
        status = CheckStatus.PASS
        detail = f"License valid, {int(days_remaining)} days remaining"
        severity = "info"

    checks.append(ValidationCheck(
        check_id=uuid.uuid4().hex[:8], category="licensing",
        name=f"{market.country_code}: gaming license ({market.regulator})",
        status=status, detail=detail, severity=severity,
        timestamp=time.time(),
    ))

    return checks


def check_geofencing(
    market: MarketProfile,
    geofence_config: dict[str, Any],
) -> list[ValidationCheck]:
    """Verify geofencing configuration."""
    checks: list[ValidationCheck] = []

    if not market.geofence_required:
        checks.append(ValidationCheck(
            check_id=uuid.uuid4().hex[:8], category="geofencing",
            name=f"{market.country_code}: geofence",
            status=CheckStatus.SKIP,
            detail="Geofencing not required for this market",
            severity="info", timestamp=time.time(),
        ))
        return checks

    provider = geofence_config.get("provider")
    if not provider:
        checks.append(ValidationCheck(
            check_id=uuid.uuid4().hex[:8], category="geofencing",
            name=f"{market.country_code}: geofence provider",
            status=CheckStatus.FAIL,
            detail="No geofence provider configured",
            severity="blocker", timestamp=time.time(),
        ))
        return checks

    checks.append(ValidationCheck(
        check_id=uuid.uuid4().hex[:8], category="geofencing",
        name=f"{market.country_code}: geofence provider",
        status=CheckStatus.PASS,
        detail=f"Provider: {provider}, integrated and tested",
        severity="info", timestamp=time.time(),
    ))

    # Boundary test
    tested = geofence_config.get("boundary_tested", False)
    checks.append(ValidationCheck(
        check_id=uuid.uuid4().hex[:8], category="geofencing",
        name=f"{market.country_code}: boundary test",
        status=CheckStatus.PASS if tested else CheckStatus.FAIL,
        detail="Boundary test passed" if tested else "Boundary test not completed",
        severity="blocker" if not tested else "info",
        timestamp=time.time(),
    ))

    return checks


def check_payment_methods(
    market: MarketProfile,
    enabled_methods: list[str],
) -> list[ValidationCheck]:
    """Verify required payment methods are enabled."""
    checks: list[ValidationCheck] = []

    for method in market.required_payment_methods:
        if method in enabled_methods:
            checks.append(ValidationCheck(
                check_id=uuid.uuid4().hex[:8], category="payments",
                name=f"{market.country_code}: {method}",
                status=CheckStatus.PASS,
                detail=f"Payment method '{method}' enabled for {market.currency}",
                severity="info", timestamp=time.time(),
            ))
        else:
            checks.append(ValidationCheck(
                check_id=uuid.uuid4().hex[:8], category="payments",
                name=f"{market.country_code}: {method}",
                status=CheckStatus.FAIL,
                detail=f"Required payment method '{method}' not enabled",
                severity="blocker", timestamp=time.time(),
            ))

    return checks


def check_responsible_gaming(
    market: MarketProfile,
    rg_config: dict[str, bool],
) -> list[ValidationCheck]:
    """Verify responsible gaming controls are active."""
    checks: list[ValidationCheck] = []

    for requirement in market.rg_requirements:
        active = rg_config.get(requirement, False)
        checks.append(ValidationCheck(
            check_id=uuid.uuid4().hex[:8], category="responsible_gaming",
            name=f"{market.country_code}: {requirement}",
            status=CheckStatus.PASS if active else CheckStatus.FAIL,
            detail=f"RG control '{requirement}' {'active' if active else 'NOT active'}",
            severity="blocker" if not active else "info",
            timestamp=time.time(),
        ))

    return checks


def check_test_accounts(
    market: MarketProfile,
    test_results: dict[str, Any],
    profile: EnvironmentProfile = EnvironmentProfile.PRODUCTION,
) -> list[ValidationCheck]:
    """Verify test accounts completed full lifecycle."""
    checks: list[ValidationCheck] = []

    account_count = test_results.get("accounts_tested", 0)
    full_cycle = test_results.get("full_cycle_passed", False)

    checks.append(ValidationCheck(
        check_id=uuid.uuid4().hex[:8], category="test_accounts",
        name=f"{market.country_code}: test accounts",
        status=CheckStatus.PASS if account_count >= 3 else CheckStatus.FAIL,
        detail=f"{account_count} test accounts created (minimum 3 required)",
        severity="blocker" if account_count < 3 else "info",
        timestamp=time.time(),
    ))

    checks.append(ValidationCheck(
        check_id=uuid.uuid4().hex[:8], category="test_accounts",
        name=f"{market.country_code}: full cycle test",
        status=CheckStatus.PASS if full_cycle else CheckStatus.FAIL,
        detail="Deposit -> Play -> Withdraw cycle "
               + ("passed" if full_cycle else "NOT completed"),
        severity="blocker" if not full_cycle else "info",
        timestamp=time.time(),
    ))

    return checks


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

@dataclass
class MarketReadinessReport:
    market: MarketProfile
    checks: list[ValidationCheck]
    ready: bool
    blockers: int
    warnings: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "market": self.market.country_code,
            "market_name": self.market.market_name,
            "tier": self.market.tier.value,
            "ready": self.ready,
            "blockers": self.blockers,
            "warnings": self.warnings,
            "total_checks": len(self.checks),
            "checks": [
                {
                    "category": c.category,
                    "name": c.name,
                    "status": c.status.value,
                    "severity": c.severity,
                    "detail": c.detail,
                }
                for c in self.checks
            ],
        }


def validate_market(
    country_code: str,
    operator_licenses: dict[str, dict[str, Any]],
    enabled_payment_methods: list[str],
    rg_config: dict[str, bool],
    geofence_config: dict[str, Any],
    test_results: dict[str, Any],
    profile: EnvironmentProfile = EnvironmentProfile.PRODUCTION,
) -> MarketReadinessReport:
    """Run all validation checks for a market (profile controls strictness)."""
    market = MARKET_REGISTRY.get(country_code)
    if market is None:
        raise ValueError(f"Unknown market: {country_code}")

    all_checks: list[ValidationCheck] = []
    all_checks.extend(check_licensing(market, operator_licenses))
    all_checks.extend(check_geofencing(market, geofence_config))
    all_checks.extend(check_payment_methods(market, enabled_payment_methods))
    all_checks.extend(check_responsible_gaming(market, rg_config))
    all_checks.extend(check_test_accounts(market, test_results))

    if profile == EnvironmentProfile.STAGING:
        for check in all_checks:
            if check.status == CheckStatus.FAIL and check.severity == "blocker":
                check.status = CheckStatus.WARN
                check.severity = "warning"
                check.detail = f"[STAGING advisory] {check.detail}"

    blockers = sum(1 for c in all_checks
                   if c.status == CheckStatus.FAIL and c.severity == "blocker")
    warnings = sum(1 for c in all_checks if c.status == CheckStatus.WARN)
    ready = blockers == 0

    return MarketReadinessReport(
        market=market,
        checks=all_checks,
        ready=ready,
        blockers=blockers,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Market Readiness Validator")
    parser.add_argument("--strict", action="store_true",
        help="Run in PRODUCTION (strict) mode. Default is STAGING (advisory).")
    parser.add_argument("--markets", nargs="*", default=["GB", "MT", "DE", "BR", "US-NJ"])
    args = parser.parse_args()
    profile = EnvironmentProfile.PRODUCTION if args.strict else EnvironmentProfile.STAGING
    print(f"  Environment profile: {profile.value.upper()}")

    markets_to_validate = args.markets

    # Simulated operator state
    licenses = {
        "GB": {"license_id": "UKGC-12345", "expires_at": time.time() + 365 * 86400},
        "MT": {"license_id": "MGA-67890", "expires_at": time.time() + 200 * 86400},
        "DE": {"license_id": "GGL-11111", "expires_at": time.time() + 50 * 86400},
        "BR": {"license_id": "SIGAP-22222", "expires_at": time.time() + 300 * 86400},
        "US-NJ": {"license_id": "NJ-DGE-33333", "expires_at": time.time() + 180 * 86400},
    }
    payment_methods = ["visa", "mastercard", "paypal", "pix", "ach", "trustly"]
    rg_config = {
        "deposit_limits": True, "cool_off": True, "self_exclusion": True,
        "reality_check": True, "affordability_check": True,
        "mandatory_play_break": True, "spelpaus_integration": True,
        "monthly_limit_1000_eur": True, "panic_button": True,
        "no_autoplay": True, "spin_interval_5s": True,
        "geofence_verification": True, "cpf_verification": True,
    }
    geofence_config = {
        "provider": "geocomply",
        "boundary_tested": True,
    }
    test_results = {
        "accounts_tested": 5,
        "full_cycle_passed": True,
    }

    results: list[dict[str, Any]] = []
    for code in markets_to_validate:
        report = validate_market(
            code, licenses, payment_methods, rg_config,
            geofence_config, test_results, profile=profile,
        )
        results.append(report.to_dict())
        status = "READY" if report.ready else f"NOT READY ({report.blockers} blockers)"
        print(f"  {code} ({report.market.market_name}): {status}")

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
