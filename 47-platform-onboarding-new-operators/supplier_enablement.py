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
Supplier Enablement Service.

Automates the process of enabling game suppliers for a new operator:

  1. Credential setup (API key, secret, stored in vault)
  2. Callback URL configuration and verification
  3. Test round execution (bet, win, settle)
  4. Production readiness sign-off

Each supplier goes through a four-stage pipeline.  A supplier cannot
reach production status until all four stages pass.

Environments
------------
  staging  - mock supplier APIs, instant responses
  uat      - sandbox supplier APIs, real HTTP calls
  prod     - production supplier APIs
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

class EnablementStatus(Enum):
    NOT_STARTED = "not_started"
    CREDENTIALS_SET = "credentials_set"
    CALLBACKS_CONFIGURED = "callbacks_configured"
    TEST_ROUND_PASSED = "test_round_passed"
    PRODUCTION_READY = "production_ready"
    FAILED = "failed"
    DISABLED = "disabled"


class IntegrationType(Enum):
    SEAMLESS = "seamless"   # Real-time wallet integration
    TRANSFER = "transfer"   # Pre-funded wallet at supplier


@dataclass
class SupplierCredentials:
    supplier_id: str
    api_key: str
    api_secret: str
    environment: str
    created_at: float
    vault_path: str


@dataclass
class CallbackConfig:
    supplier_id: str
    operator_id: str
    callback_url: str
    auth_token: str
    verified: bool = False
    verified_at: float = 0.0


@dataclass
class TestRoundResult:
    round_id: str
    supplier_id: str
    game_id: str
    bet_amount: float
    win_amount: float
    currency: str
    settled: bool
    balance_correct: bool
    latency_ms: float
    timestamp: float


@dataclass
class SupplierEnablement:
    """Tracks the enablement state for one supplier-operator pair."""
    supplier_id: str
    operator_id: str
    supplier_name: str
    integration_type: IntegrationType
    status: EnablementStatus = EnablementStatus.NOT_STARTED
    credentials: SupplierCredentials | None = None
    callback: CallbackConfig | None = None
    test_rounds: list[TestRoundResult] = field(default_factory=list)
    sign_off_by: str | None = None
    sign_off_at: float = 0.0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Supplier catalogue
# ---------------------------------------------------------------------------

SUPPLIER_CATALOGUE: dict[str, dict[str, Any]] = {
    "pragmatic-play": {
        "name": "Pragmatic Play",
        "integration_type": IntegrationType.SEAMLESS,
        "test_games": ["gates-of-olympus", "sweet-bonanza", "wolf-gold"],
        "supported_currencies": ["EUR", "USD", "GBP", "BRL", "SEK"],
        "min_bet": 0.20,
        "max_bet": 100.00,
        "rtp_range": (94.0, 96.5),
        "callback_required": True,
    },
    "evolution": {
        "name": "Evolution Gaming",
        "integration_type": IntegrationType.SEAMLESS,
        "test_games": ["lightning-roulette", "crazy-time", "mega-ball"],
        "supported_currencies": ["EUR", "USD", "GBP", "BRL"],
        "min_bet": 1.00,
        "max_bet": 10000.00,
        "rtp_range": (95.0, 97.3),
        "callback_required": True,
    },
    "netent": {
        "name": "NetEnt",
        "integration_type": IntegrationType.SEAMLESS,
        "test_games": ["starburst", "dead-or-alive-2", "gonzos-quest"],
        "supported_currencies": ["EUR", "USD", "GBP", "SEK", "NOK"],
        "min_bet": 0.10,
        "max_bet": 200.00,
        "rtp_range": (95.0, 96.1),
        "callback_required": True,
    },
    "play-n-go": {
        "name": "Play'n GO",
        "integration_type": IntegrationType.SEAMLESS,
        "test_games": ["book-of-dead", "reactoonz", "fire-joker"],
        "supported_currencies": ["EUR", "USD", "GBP", "BRL"],
        "min_bet": 0.10,
        "max_bet": 100.00,
        "rtp_range": (94.0, 96.2),
        "callback_required": True,
    },
    "novomatic": {
        "name": "Novomatic",
        "integration_type": IntegrationType.TRANSFER,
        "test_games": ["book-of-ra", "lucky-ladys-charm"],
        "supported_currencies": ["EUR", "USD", "GBP"],
        "min_bet": 0.50,
        "max_bet": 50.00,
        "rtp_range": (92.0, 95.0),
        "callback_required": False,
    },
}


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def setup_credentials(
    enablement: SupplierEnablement,
    environment: str = "staging",
) -> SupplierEnablement:
    """Stage 1: Generate and store API credentials."""
    api_key = f"ak_{enablement.supplier_id}_{uuid.uuid4().hex[:8]}"
    api_secret = f"sk_{uuid.uuid4().hex[:16]}"
    vault_path = (f"secret/operators/{enablement.operator_id}"
                  f"/suppliers/{enablement.supplier_id}")

    enablement.credentials = SupplierCredentials(
        supplier_id=enablement.supplier_id,
        api_key=api_key,
        api_secret=api_secret,
        environment=environment,
        created_at=time.time(),
        vault_path=vault_path,
    )
    enablement.status = EnablementStatus.CREDENTIALS_SET
    return enablement


def configure_callbacks(
    enablement: SupplierEnablement,
    base_domain: str,
) -> SupplierEnablement:
    """Stage 2: Configure and verify callback URLs."""
    catalogue = SUPPLIER_CATALOGUE.get(enablement.supplier_id, {})
    if not catalogue.get("callback_required", False):
        # Transfer wallets don't need callbacks
        enablement.status = EnablementStatus.CALLBACKS_CONFIGURED
        return enablement

    callback_url = (f"https://api.{base_domain}/suppliers"
                    f"/{enablement.supplier_id}/callback")
    auth_token = f"cb_{uuid.uuid4().hex[:16]}"

    enablement.callback = CallbackConfig(
        supplier_id=enablement.supplier_id,
        operator_id=enablement.operator_id,
        callback_url=callback_url,
        auth_token=auth_token,
        verified=True,  # simulated verification
        verified_at=time.time(),
    )
    enablement.status = EnablementStatus.CALLBACKS_CONFIGURED
    return enablement


def execute_test_rounds(
    enablement: SupplierEnablement,
    currency: str = "EUR",
) -> SupplierEnablement:
    """Stage 3: Execute test rounds on supplier's test games."""
    catalogue = SUPPLIER_CATALOGUE.get(enablement.supplier_id, {})
    test_games = catalogue.get("test_games", [])

    for game_id in test_games:
        bet_amount = catalogue.get("min_bet", 1.00)
        win_amount = round(bet_amount * 0.5, 2)  # simulated 50% return

        result = TestRoundResult(
            round_id=uuid.uuid4().hex[:8],
            supplier_id=enablement.supplier_id,
            game_id=game_id,
            bet_amount=bet_amount,
            win_amount=win_amount,
            currency=currency,
            settled=True,
            balance_correct=True,
            latency_ms=45.0,  # simulated
            timestamp=time.time(),
        )
        enablement.test_rounds.append(result)

    all_passed = all(r.settled and r.balance_correct for r in enablement.test_rounds)
    if all_passed:
        enablement.status = EnablementStatus.TEST_ROUND_PASSED
    else:
        enablement.status = EnablementStatus.FAILED
        enablement.errors.append("One or more test rounds failed settlement")

    return enablement


def production_sign_off(
    enablement: SupplierEnablement,
    signed_off_by: str,
) -> SupplierEnablement:
    """Stage 4: Production readiness sign-off."""
    if enablement.status != EnablementStatus.TEST_ROUND_PASSED:
        enablement.errors.append(
            f"Cannot sign off: current status is {enablement.status.value}"
        )
        return enablement

    # Verify all prerequisites
    prerequisites: list[tuple[str, bool]] = [
        ("credentials_set", enablement.credentials is not None),
        ("test_rounds_passed", len(enablement.test_rounds) > 0),
        ("all_rounds_settled", all(r.settled for r in enablement.test_rounds)),
        ("all_balances_correct", all(r.balance_correct for r in enablement.test_rounds)),
    ]

    catalogue = SUPPLIER_CATALOGUE.get(enablement.supplier_id, {})
    if catalogue.get("callback_required", False):
        prerequisites.append(
            ("callback_verified",
             enablement.callback is not None and enablement.callback.verified)
        )

    failed = [name for name, ok in prerequisites if not ok]
    if failed:
        enablement.errors.append(f"Prerequisites not met: {failed}")
        enablement.status = EnablementStatus.FAILED
        return enablement

    enablement.sign_off_by = signed_off_by
    enablement.sign_off_at = time.time()
    enablement.status = EnablementStatus.PRODUCTION_READY
    return enablement


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def enable_supplier_for_operator(
    supplier_id: str,
    operator_id: str,
    base_domain: str,
    currency: str = "EUR",
    environment: str = "staging",
    signed_off_by: str = "integration-team",
) -> SupplierEnablement:
    """Execute the full supplier enablement pipeline."""
    catalogue = SUPPLIER_CATALOGUE.get(supplier_id)
    if catalogue is None:
        enablement = SupplierEnablement(
            supplier_id=supplier_id,
            operator_id=operator_id,
            supplier_name=supplier_id,
            integration_type=IntegrationType.SEAMLESS,
            status=EnablementStatus.FAILED,
        )
        enablement.errors.append(f"Unknown supplier: {supplier_id}")
        return enablement

    enablement = SupplierEnablement(
        supplier_id=supplier_id,
        operator_id=operator_id,
        supplier_name=catalogue["name"],
        integration_type=catalogue["integration_type"],
    )

    # Pipeline
    enablement = setup_credentials(enablement, environment)
    enablement = configure_callbacks(enablement, base_domain)
    enablement = execute_test_rounds(enablement, currency)
    enablement = production_sign_off(enablement, signed_off_by)

    return enablement


def enablement_report(enablement: SupplierEnablement) -> dict[str, Any]:
    """Generate a JSON-serialisable report."""
    return {
        "supplier_id": enablement.supplier_id,
        "supplier_name": enablement.supplier_name,
        "operator_id": enablement.operator_id,
        "integration_type": enablement.integration_type.value,
        "status": enablement.status.value,
        "credentials_set": enablement.credentials is not None,
        "callback_configured": enablement.callback is not None,
        "callback_verified": (enablement.callback.verified
                              if enablement.callback else False),
        "test_rounds": [
            {
                "game": r.game_id,
                "bet": r.bet_amount,
                "win": r.win_amount,
                "settled": r.settled,
                "balance_ok": r.balance_correct,
                "latency_ms": r.latency_ms,
            }
            for r in enablement.test_rounds
        ],
        "sign_off_by": enablement.sign_off_by,
        "errors": enablement.errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    suppliers = ["pragmatic-play", "evolution", "netent", "play-n-go", "novomatic"]
    operator_id = "acmetocasino"
    domain = "acmetocasino.com"

    results: list[dict[str, Any]] = []
    for supplier_id in suppliers:
        enablement = enable_supplier_for_operator(
            supplier_id=supplier_id,
            operator_id=operator_id,
            base_domain=domain,
            currency="EUR",
            environment="staging",
            signed_off_by="integration-lead",
        )
        report = enablement_report(enablement)
        results.append(report)
        status = enablement.status.value
        print(f"  {enablement.supplier_name}: {status}")

    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
