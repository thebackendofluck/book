#!/usr/bin/env python3
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
Release Gate: Supplier Callback Replay
=======================================

Replays recorded supplier callbacks against the callback handler and verifies:
  1. Idempotency — replaying the same callback twice produces DUPLICATE status
  2. Correctness — all valid callbacks produce ACCEPTED status on first attempt
  3. Signature integrity — callbacks with tampered signatures are REJECTED
  4. Dead letter — callbacks that fail processing are routed to DLQ

This script is designed to run as part of a CI/CD release gate. It loads
a fixture set of callbacks, replays them through the CallbackHandler, and
asserts invariants that must hold before any release to production.

Usage:
    python supplier_callback_replay.py                  # Run all checks
    python supplier_callback_replay.py --verbose        # Detailed output
    python supplier_callback_replay.py --json           # JSON report

Exit codes:
    0 — All checks passed
    1 — One or more checks failed
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTROL_PLANE = os.path.join(
    os.path.dirname(_HERE), "platform-core", "supplier-control-plane",
)
if _CONTROL_PLANE not in sys.path:
    sys.path.insert(0, _CONTROL_PLANE)

from callback_handler import (
    CallbackHandler,
    CallbackStatus,
    DomainHandler,
    SignatureMethod,
)
from models import (
    Credentials,
    SupplierCapabilityMatrix,
    SupplierRecord,
    SupplierStatus,
    SupplierType,
    WalletModel,
)
from registry import SupplierRegistry
from credential_manager import CredentialManager, InMemorySecretBackend

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("supplier-callback-replay")


# ---------------------------------------------------------------------------
# Test fixture data
# ---------------------------------------------------------------------------


FIXTURE_CALLBACKS = [
    {
        "supplier_id": "evolution",
        "transaction_id": "TXN-REPLAY-001",
        "callback_type": "game_round",
        "action": "bet",
        "round_id": "R-1001",
        "player_id": "PLR-42",
        "amount": 25.00,
    },
    {
        "supplier_id": "evolution",
        "transaction_id": "TXN-REPLAY-002",
        "callback_type": "game_round",
        "action": "result",
        "round_id": "R-1001",
        "player_id": "PLR-42",
        "amount": 50.00,
    },
    {
        "supplier_id": "pragmatic",
        "transaction_id": "TXN-REPLAY-003",
        "callback_type": "wallet",
        "action": "credit",
        "player_id": "PLR-99",
        "amount": 100.00,
    },
    {
        "supplier_id": "pragmatic",
        "transaction_id": "TXN-REPLAY-004",
        "callback_type": "bonus",
        "action": "award",
        "bonus_id": "BONUS-555",
    },
    {
        "supplier_id": "evolution",
        "transaction_id": "TXN-REPLAY-005",
        "callback_type": "game_round",
        "action": "refund",
        "round_id": "R-1002",
        "player_id": "PLR-42",
        "amount": 10.00,
    },
]


# ---------------------------------------------------------------------------
# Check results
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str = ""
    duration_ms: float = 0.0


@dataclass
class ReplayReport:
    checks: list[CheckResult] = field(default_factory=list)
    total_callbacks: int = 0
    passed: int = 0
    failed: int = 0

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)
        if result.passed:
            self.passed += 1
        else:
            self.failed += 1

    @property
    def all_passed(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        return {
            "total_callbacks": self.total_callbacks,
            "checks_passed": self.passed,
            "checks_failed": self.failed,
            "all_passed": self.all_passed,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "message": c.message,
                    "duration_ms": round(c.duration_ms, 2),
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------


def _setup_environment():
    """Create a fresh registry, credential manager, and callback handler."""
    reg = SupplierRegistry()

    for sid in ("evolution", "pragmatic"):
        record = SupplierRecord(
            id=sid,
            name=f"{sid.title()} Gaming",
            type=SupplierType.CASINO,
            status=SupplierStatus.ACTIVE,
            capabilities=SupplierCapabilityMatrix(
                supplier_id=sid,
                games={"blackjack", "roulette"},
                currencies={"EUR"},
                jurisdictions={"GB"},
                wallet_model=WalletModel.SEAMLESS,
            ),
        )
        reg.register_supplier(record)

    backend = InMemorySecretBackend()
    cred_mgr = CredentialManager(registry=reg, backend=backend)

    for sid, secret in [("evolution", "EVO_SECRET"), ("pragmatic", "PRAG_SECRET")]:
        creds = Credentials(
            supplier_id=sid,
            brand_id="brand1",
            jurisdiction="GB",
            api_key=f"{sid.upper()}_KEY",
            api_secret=secret,
            operator_id="OP1",
        )
        cred_mgr.add_credentials(creds)

    handler = CallbackHandler(
        registry=reg,
        credential_manager=cred_mgr,
        signature_method=SignatureMethod.NONE,
    )

    return reg, cred_mgr, handler


def _sign(payload: dict, secret: str) -> str:
    body = json.dumps(payload, sort_keys=True)
    return hmac.new(
        secret.encode(), body.encode(), hashlib.sha256,
    ).hexdigest()


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------


def check_first_pass_accepted(handler: CallbackHandler, report: ReplayReport) -> None:
    """All fixture callbacks should be ACCEPTED on first pass."""
    t0 = time.monotonic()
    all_ok = True
    for cb in FIXTURE_CALLBACKS:
        result = handler.process_callback(
            supplier_id=cb["supplier_id"],
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload=cb,
        )
        if result.status != CallbackStatus.ACCEPTED:
            all_ok = False
            report.add(CheckResult(
                name=f"first_pass_{cb['transaction_id']}",
                passed=False,
                message=f"Expected ACCEPTED, got {result.status.value}",
            ))
    if all_ok:
        report.add(CheckResult(
            name="first_pass_all_accepted",
            passed=True,
            message=f"All {len(FIXTURE_CALLBACKS)} callbacks accepted",
            duration_ms=(time.monotonic() - t0) * 1000,
        ))
    report.total_callbacks += len(FIXTURE_CALLBACKS)


def check_idempotency(handler: CallbackHandler, report: ReplayReport) -> None:
    """Replaying the same callbacks should produce DUPLICATE status."""
    t0 = time.monotonic()
    all_ok = True
    for cb in FIXTURE_CALLBACKS:
        result = handler.process_callback(
            supplier_id=cb["supplier_id"],
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload=cb,
        )
        if result.status != CallbackStatus.DUPLICATE:
            all_ok = False
            report.add(CheckResult(
                name=f"idempotency_{cb['transaction_id']}",
                passed=False,
                message=f"Expected DUPLICATE, got {result.status.value}",
            ))
    if all_ok:
        report.add(CheckResult(
            name="idempotency_all_duplicate",
            passed=True,
            message=f"All {len(FIXTURE_CALLBACKS)} replays correctly deduplicated",
            duration_ms=(time.monotonic() - t0) * 1000,
        ))
    report.total_callbacks += len(FIXTURE_CALLBACKS)


def check_signature_validation(
    cred_mgr: CredentialManager,
    reg: SupplierRegistry,
    report: ReplayReport,
) -> None:
    """Callbacks with invalid signatures should be REJECTED."""
    t0 = time.monotonic()

    # Create a handler with HMAC validation enabled
    signed_handler = CallbackHandler(
        registry=reg,
        credential_manager=cred_mgr,
        signature_method=SignatureMethod.HMAC_SHA256,
    )

    payload = {
        "transaction_id": "TXN-SIG-001",
        "callback_type": "game_round",
        "action": "bet",
    }

    # Valid signature
    valid_sig = _sign(payload, "EVO_SECRET")
    result_valid = signed_handler.process_callback(
        supplier_id="evolution",
        brand_id="brand1",
        jurisdiction="GB",
        headers={"X-Signature": valid_sig},
        payload=payload,
    )

    # Invalid signature
    payload_bad = {
        "transaction_id": "TXN-SIG-002",
        "callback_type": "game_round",
        "action": "bet",
    }
    result_invalid = signed_handler.process_callback(
        supplier_id="evolution",
        brand_id="brand1",
        jurisdiction="GB",
        headers={"X-Signature": "tampered_signature"},
        payload=payload_bad,
    )

    valid_ok = result_valid.status == CallbackStatus.ACCEPTED
    invalid_ok = result_invalid.status == CallbackStatus.REJECTED

    report.add(CheckResult(
        name="signature_valid_accepted",
        passed=valid_ok,
        message="Valid signature accepted" if valid_ok else f"Got {result_valid.status.value}",
    ))
    report.add(CheckResult(
        name="signature_invalid_rejected",
        passed=invalid_ok,
        message="Invalid signature rejected" if invalid_ok else f"Got {result_invalid.status.value}",
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


def check_dead_letter_queue(reg: SupplierRegistry, cred_mgr: CredentialManager, report: ReplayReport) -> None:
    """Callbacks that fail processing should land in the DLQ."""
    t0 = time.monotonic()

    dlq_handler = CallbackHandler(
        registry=reg,
        credential_manager=cred_mgr,
        signature_method=SignatureMethod.NONE,
    )

    class FailingHandler(DomainHandler):
        def handle(self, supplier_id, payload):
            raise RuntimeError("Simulated processing failure")

    dlq_handler.register_handler("game_round", FailingHandler())

    result = dlq_handler.process_callback(
        supplier_id="evolution",
        brand_id="brand1",
        jurisdiction="GB",
        headers={},
        payload={
            "transaction_id": "TXN-DLQ-GATE-001",
            "callback_type": "game_round",
        },
    )

    dlq_ok = result.status == CallbackStatus.DEAD_LETTERED
    count_ok = dlq_handler.get_dead_letter_count() == 1

    report.add(CheckResult(
        name="dead_letter_routing",
        passed=dlq_ok and count_ok,
        message=(
            "Failed callback routed to DLQ"
            if dlq_ok and count_ok
            else f"status={result.status.value} dlq_count={dlq_handler.get_dead_letter_count()}"
        ),
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


def check_missing_transaction_id(handler: CallbackHandler, report: ReplayReport) -> None:
    """Callbacks without transaction_id should be rejected."""
    t0 = time.monotonic()
    result = handler.process_callback(
        supplier_id="evolution",
        brand_id="brand1",
        jurisdiction="GB",
        headers={},
        payload={"callback_type": "game_round", "action": "bet"},
    )

    passed = result.status == CallbackStatus.REJECTED
    report.add(CheckResult(
        name="missing_transaction_id_rejected",
        passed=passed,
        message="Missing txn_id correctly rejected" if passed else f"Got {result.status.value}",
        duration_ms=(time.monotonic() - t0) * 1000,
    ))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run_replay(verbose: bool = False, as_json: bool = False) -> bool:
    report = ReplayReport()
    reg, cred_mgr, handler = _setup_environment()

    logger.info("=== Supplier Callback Replay Gate ===")

    check_first_pass_accepted(handler, report)
    check_idempotency(handler, report)
    check_signature_validation(cred_mgr, reg, report)
    check_dead_letter_queue(reg, cred_mgr, report)
    check_missing_transaction_id(handler, report)

    if as_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Supplier Callback Replay Report")
        print(f"{'='*60}")
        print(f"Total callbacks replayed: {report.total_callbacks}")
        print(f"Checks passed:  {report.passed}")
        print(f"Checks failed:  {report.failed}")
        print(f"{'='*60}")
        for check in report.checks:
            icon = "PASS" if check.passed else "FAIL"
            print(f"  [{icon}] {check.name}: {check.message}")
        print(f"{'='*60}")
        print(f"Result: {'ALL PASSED' if report.all_passed else 'FAILED'}")

    return report.all_passed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Supplier Callback Replay Gate")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    success = run_replay(verbose=args.verbose, as_json=args.json)
    sys.exit(0 if success else 1)
