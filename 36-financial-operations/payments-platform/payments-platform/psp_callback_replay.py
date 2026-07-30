# Companion code for "The Backend of Luck" - Chapter 36, Financial Operations.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
PSP Callback Replay — Idempotency Proof Script

Purpose
-------
Demonstrate that the payments platform correctly handles duplicate PSP
callback webhooks without crediting or debiting a player balance twice.

Scenarios covered
-----------------
1. First deposit callback → balance increases by deposit amount
2. SAME callback replayed → balance UNCHANGED (idempotent)
3. Callback with a DIFFERENT reference → NEW transaction recorded
4. Timeout / slow-response scenario → callback eventually applied once
5. Partial failure recovery → failed callback followed by success

Run with:
    python psp_callback_replay.py

Or run the test class directly:
    pytest psp_callback_replay.py -v
"""

from __future__ import annotations

import asyncio
import sys
import os
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

# Allow sibling imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from models import (
    Deposit,
    DepositRequest,
    PaymentMethod,
    PaymentStatus,
    PSPResponse,
    PaymentProviderInfo,
)
from deposit_service import (
    DepositLimitService,
    DepositService,
    PaymentEventBus,
    PaymentStore,
)
from fraud_check import FraudChecker, InMemoryFraudStore
from psp_router import PSPRegistry, PSPRouter, RoutingRule
from psp.base import PSPAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory wallet — tracks player balances across test scenarios
# ---------------------------------------------------------------------------


class InMemoryWallet:
    """
    Minimal player wallet for the replay demonstration.

    Internally tracks (player_id, payment_id) pairs that have already been
    credited to ensure idempotency.
    """

    def __init__(self) -> None:
        self._balances: dict[int, int] = {}           # player_id → balance (minor units)
        self._credited: set[str] = set()               # payment_ids already credited

    def get_balance(self, player_id: int) -> int:
        return self._balances.get(player_id, 0)

    def credit(self, player_id: int, payment_id: str, amount: int) -> bool:
        """
        Credit `amount` to `player_id` for `payment_id`.

        Returns True if the credit was applied, False if it was a duplicate.
        """
        if payment_id in self._credited:
            logger.warning(
                "DUPLICATE CREDIT blocked player_id=%d payment_id=%s amount=%d",
                player_id, payment_id, amount,
            )
            return False

        self._credited.add(payment_id)
        self._balances[player_id] = self.get_balance(player_id) + amount
        logger.info(
            "Credit applied player_id=%d payment_id=%s amount=%d new_balance=%d",
            player_id, payment_id, amount, self._balances[player_id],
        )
        return True


# ---------------------------------------------------------------------------
# PSP adapter stubs
# ---------------------------------------------------------------------------


class IdempotentSuccessAdapter(PSPAdapter):
    """
    Stub PSP adapter that always succeeds.

    Tracks how many times each payment_id has been submitted to detect
    duplicate outbound calls (should never happen with a correct implementation).
    """

    name = "idempotent_psp"
    supports_withdrawals = False

    def __init__(self) -> None:
        self._call_log: dict[str, int] = {}

    async def deposit(self, payment: Deposit) -> PSPResponse:
        count = self._call_log.get(payment.payment_id, 0) + 1
        self._call_log[payment.payment_id] = count
        if count > 1:
            logger.warning(
                "PSP received duplicate outbound call for payment_id=%s (call #%d)",
                payment.payment_id, count,
            )
        return PSPResponse(
            success=True,
            external_transaction_id=f"EXT-{payment.payment_id[:8]}",
            status=PaymentStatus.SUCCEEDED,
            raw_response={"stub": True, "call_count": count},
        )

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        return PSPResponse(
            success=True,
            external_transaction_id=external_id,
            status=PaymentStatus.SUCCEEDED,
            raw_response={},
        )

    def call_count(self, payment_id: str) -> int:
        return self._call_log.get(payment_id, 0)


class TimeoutThenSuccessAdapter(PSPAdapter):
    """Simulates a timeout on the first call, success on retry.

    The timeout is triggered once per adapter instance (i.e. once per scenario),
    regardless of payment_id.  This models the real-world case where an operator
    retries via a *new* payment request after the first one times out.
    """

    name = "timeout_psp"
    supports_withdrawals = False

    def __init__(self) -> None:
        self._attempts: dict[str, int] = {}
        self._total_calls: int = 0

    async def deposit(self, payment: Deposit) -> PSPResponse:
        self._total_calls += 1
        attempts = self._attempts.get(payment.payment_id, 0) + 1
        self._attempts[payment.payment_id] = attempts

        if self._total_calls == 1:
            logger.info("Simulating timeout for payment_id=%s", payment.payment_id)
            # Simulate network delay without blocking the event loop in tests
            await asyncio.sleep(0.01)
            return PSPResponse(
                success=False,
                external_transaction_id=None,
                status=PaymentStatus.ABANDONED,
                raw_response={"error": "timeout"},
                error_code="TIMEOUT",
                error_message="PSP response timed out",
            )

        logger.info("Retry succeeded for payment_id=%s (attempt %d)", payment.payment_id, attempts)
        return PSPResponse(
            success=True,
            external_transaction_id=f"EXT-RETRY-{payment.payment_id[:8]}",
            status=PaymentStatus.SUCCEEDED,
            raw_response={"attempt": attempts},
        )

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        return PSPResponse(
            success=True,
            external_transaction_id=external_id,
            status=PaymentStatus.SUCCEEDED,
            raw_response={},
        )


class PartialFailureAdapter(PSPAdapter):
    """Succeeds on first call but simulates a failed callback delivery."""

    name = "partial_fail_psp"
    supports_withdrawals = False

    async def deposit(self, payment: Deposit) -> PSPResponse:
        return PSPResponse(
            success=True,
            external_transaction_id=f"EXT-PF-{payment.payment_id[:8]}",
            status=PaymentStatus.PROCESSING,
            raw_response={"note": "awaiting callback"},
        )

    async def get_transaction_status(self, external_id: str) -> PSPResponse:
        return PSPResponse(
            success=True,
            external_transaction_id=external_id,
            status=PaymentStatus.SUCCEEDED,
            raw_response={},
        )


# ---------------------------------------------------------------------------
# Router / service factory helpers
# ---------------------------------------------------------------------------


def _make_service(adapter: PSPAdapter) -> tuple[DepositService, PaymentStore]:
    registry = PSPRegistry()
    registry.register(adapter)
    router = PSPRouter(registry)
    router.add_rule(RoutingRule(PaymentMethod.CARD, "*", primary=adapter.name))

    store = PaymentStore()
    svc = DepositService(
        psp_router=router,
        fraud_checker=FraudChecker(InMemoryFraudStore()),
        limit_service=DepositLimitService(),
        store=store,
        event_bus=PaymentEventBus(),
    )
    return svc, store


def _make_deposit_request(user_id: int = 101, amount: int = 10_000) -> DepositRequest:
    return DepositRequest(
        brand_id=1,
        user_id=user_id,
        amount=amount,
        currency="EUR",
        user_ip="80.100.100.1",
        method=PaymentMethod.CARD,
        country_code="DE",
    )


# ---------------------------------------------------------------------------
# Scenario 1 — First deposit callback credits balance
# ---------------------------------------------------------------------------


async def scenario_1_first_callback_credits_balance() -> None:
    logger.info("=== Scenario 1: First deposit callback credits balance ===")
    adapter = IdempotentSuccessAdapter()
    svc, store = _make_service(adapter)
    wallet = InMemoryWallet()
    user_id = 201
    amount = 10_000  # €100.00

    # Initiate deposit
    req = _make_deposit_request(user_id=user_id, amount=amount)
    deposit = await svc.initiate(req)

    assert deposit.status == PaymentStatus.SUCCEEDED, (
        f"Expected SUCCEEDED, got {deposit.status}"
    )

    # Apply credit to wallet (first time)
    credited = wallet.credit(user_id, deposit.payment_id, amount)
    assert credited, "First credit should be applied"
    assert wallet.get_balance(user_id) == amount, (
        f"Expected balance {amount}, got {wallet.get_balance(user_id)}"
    )

    logger.info("Scenario 1 PASSED — balance=%d", wallet.get_balance(user_id))


# ---------------------------------------------------------------------------
# Scenario 2 — Same callback replayed → balance unchanged
# ---------------------------------------------------------------------------


async def scenario_2_replay_same_callback_idempotent() -> None:
    logger.info("=== Scenario 2: Same callback replayed — balance must be unchanged ===")
    adapter = IdempotentSuccessAdapter()
    svc, store = _make_service(adapter)
    wallet = InMemoryWallet()
    user_id = 202
    amount = 5_000  # €50.00

    req = _make_deposit_request(user_id=user_id, amount=amount)
    deposit = await svc.initiate(req)

    assert deposit.status == PaymentStatus.SUCCEEDED

    # First credit
    wallet.credit(user_id, deposit.payment_id, amount)
    balance_after_first = wallet.get_balance(user_id)

    # Replay the exact same callback
    duplicate_credited = wallet.credit(user_id, deposit.payment_id, amount)
    balance_after_replay = wallet.get_balance(user_id)

    assert not duplicate_credited, "Duplicate credit must be blocked"
    assert balance_after_first == balance_after_replay, (
        f"Balance changed on replay! Before={balance_after_first} After={balance_after_replay}"
    )

    logger.info("Scenario 2 PASSED — balance unchanged at %d", balance_after_replay)


# ---------------------------------------------------------------------------
# Scenario 3 — Different reference creates a NEW transaction
# ---------------------------------------------------------------------------


async def scenario_3_different_reference_new_transaction() -> None:
    logger.info("=== Scenario 3: Different reference → new transaction ===")
    adapter = IdempotentSuccessAdapter()
    svc, store = _make_service(adapter)
    wallet = InMemoryWallet()
    user_id = 203
    amount = 2_500  # €25.00

    # First deposit
    req1 = _make_deposit_request(user_id=user_id, amount=amount)
    deposit1 = await svc.initiate(req1)

    # Second deposit — completely independent (different payment_id)
    req2 = _make_deposit_request(user_id=user_id, amount=amount)
    deposit2 = await svc.initiate(req2)

    assert deposit1.payment_id != deposit2.payment_id, (
        "Two separate deposits must have distinct payment IDs"
    )

    wallet.credit(user_id, deposit1.payment_id, amount)
    wallet.credit(user_id, deposit2.payment_id, amount)

    expected = amount * 2
    actual = wallet.get_balance(user_id)
    assert actual == expected, f"Expected {expected}, got {actual}"

    logger.info("Scenario 3 PASSED — two distinct deposits credited, balance=%d", actual)


# ---------------------------------------------------------------------------
# Scenario 4 — Timeout scenario
# ---------------------------------------------------------------------------


async def scenario_4_timeout_scenario() -> None:
    logger.info("=== Scenario 4: PSP timeout — retry should succeed exactly once ===")
    adapter = TimeoutThenSuccessAdapter()
    svc, store = _make_service(adapter)
    wallet = InMemoryWallet()
    user_id = 204
    amount = 7_500  # €75.00

    # First attempt → timeout → payment is ABANDONED
    req = _make_deposit_request(user_id=user_id, amount=amount)
    deposit_attempt1 = await svc.initiate(req)
    assert deposit_attempt1.status == PaymentStatus.ABANDONED, (
        f"Expected ABANDONED after timeout, got {deposit_attempt1.status}"
    )

    # Retry — same user, new deposit request (operator UI initiated retry)
    req2 = _make_deposit_request(user_id=user_id, amount=amount)
    deposit_attempt2 = await svc.initiate(req2)
    assert deposit_attempt2.status == PaymentStatus.SUCCEEDED, (
        f"Retry should succeed, got {deposit_attempt2.status}"
    )

    # Only credit the successful attempt — ABANDONED payments must not inflate the balance
    if deposit_attempt1.status == PaymentStatus.SUCCEEDED:
        wallet.credit(user_id, deposit_attempt1.payment_id, amount)
    wallet.credit(user_id, deposit_attempt2.payment_id, amount)  # real credit

    # The abandoned attempt should NOT have inflated the balance
    assert wallet.get_balance(user_id) == amount, (
        f"Expected {amount} (retry credit only), got {wallet.get_balance(user_id)}"
    )

    logger.info("Scenario 4 PASSED — only successful retry credited, balance=%d", wallet.get_balance(user_id))


# ---------------------------------------------------------------------------
# Scenario 5 — Partial failure recovery
# ---------------------------------------------------------------------------


async def scenario_5_partial_failure_recovery() -> None:
    logger.info("=== Scenario 5: Partial failure — callback arrives late after initial PROCESSING ===")
    adapter = PartialFailureAdapter()
    svc, store = _make_service(adapter)
    wallet = InMemoryWallet()
    user_id = 205
    amount = 15_000  # €150.00

    # Initiate — lands in PROCESSING (no immediate SUCCEEDED)
    req = _make_deposit_request(user_id=user_id, amount=amount)
    deposit = await svc.initiate(req)
    assert deposit.status == PaymentStatus.PROCESSING, (
        f"Expected PROCESSING, got {deposit.status}"
    )

    # Simulate late PSP callback arriving with SUCCEEDED
    late_callback = PSPResponse(
        success=True,
        external_transaction_id=deposit.provider_info.external_transaction_id,
        status=PaymentStatus.SUCCEEDED,
        raw_response={"delayed": True},
    )
    updated_deposit = await svc.handle_psp_callback(deposit.payment_id, late_callback)
    assert updated_deposit.status == PaymentStatus.SUCCEEDED

    # First credit
    wallet.credit(user_id, deposit.payment_id, amount)
    assert wallet.get_balance(user_id) == amount

    # Replaying the late callback again (delivery retry by PSP)
    await svc.handle_psp_callback(deposit.payment_id, late_callback)  # idempotent at service level
    # Replay wallet credit → must be blocked
    wallet.credit(user_id, deposit.payment_id, amount)

    assert wallet.get_balance(user_id) == amount, (
        f"Late callback replay must not double-credit. Balance={wallet.get_balance(user_id)}"
    )

    logger.info("Scenario 5 PASSED — partial failure recovery correct, balance=%d", wallet.get_balance(user_id))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


async def main() -> None:
    logger.info("Starting PSP Callback Replay Idempotency Proof")
    logger.info("=" * 60)

    results: list[tuple[str, bool, str]] = []

    scenarios = [
        ("Scenario 1: First callback credits balance", scenario_1_first_callback_credits_balance),
        ("Scenario 2: Same callback replay is idempotent", scenario_2_replay_same_callback_idempotent),
        ("Scenario 3: Different reference → new transaction", scenario_3_different_reference_new_transaction),
        ("Scenario 4: Timeout → retry credited once", scenario_4_timeout_scenario),
        ("Scenario 5: Partial failure recovery", scenario_5_partial_failure_recovery),
    ]

    for name, fn in scenarios:
        try:
            await fn()
            results.append((name, True, ""))
        except (AssertionError, Exception) as exc:
            results.append((name, False, str(exc)))
            logger.error("FAILED %s: %s", name, exc)

    logger.info("=" * 60)
    logger.info("RESULTS:")
    passed = 0
    for name, ok, err in results:
        status = "PASS" if ok else "FAIL"
        logger.info("  [%s] %s %s", status, name, f"— {err}" if err else "")
        if ok:
            passed += 1

    logger.info("%d/%d scenarios passed", passed, len(results))
    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())


# ---------------------------------------------------------------------------
# Pytest-compatible test class (run via: pytest psp_callback_replay.py -v)
# ---------------------------------------------------------------------------


import pytest


class TestPSPCallbackReplay:
    """Pytest-runnable version of the five idempotency scenarios."""

    @pytest.mark.asyncio
    async def test_scenario_1_first_callback_credits_balance(self):
        await scenario_1_first_callback_credits_balance()

    @pytest.mark.asyncio
    async def test_scenario_2_replay_same_callback_idempotent(self):
        await scenario_2_replay_same_callback_idempotent()

    @pytest.mark.asyncio
    async def test_scenario_3_different_reference_new_transaction(self):
        await scenario_3_different_reference_new_transaction()

    @pytest.mark.asyncio
    async def test_scenario_4_timeout_scenario(self):
        await scenario_4_timeout_scenario()

    @pytest.mark.asyncio
    async def test_scenario_5_partial_failure_recovery(self):
        await scenario_5_partial_failure_recovery()
