# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Tests for PIX Payment Gateway Service
======================================
Comprehensive pytest test suite covering:
  - PIX deposit creation flow
  - Payment state machine transitions
  - Webhook processing
  - Fraud check enforcement
  - Rate limiting
  - Withdrawal processing
  - Reconciliation engine
  - PSP adapter error handling
  - CPF validator (shared with KYC)

Reference implementation for Chapter 46: Brazilian Betting Platform.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Import modules under test
# ---------------------------------------------------------------------------
# Adjust the import path if running from the project root
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from pix_payment_gateway import (
    CelcoinAdapter,
    DuplicateTransactionError,
    FraudCheckFailedError,
    InsufficientFundsError,
    InvalidPixKeyError,
    PaymentRecord,
    PaymentState,
    PaymentStateMachine,
    PaymentStore,
    PixDepositRequest,
    PixFraudChecker,
    PixGatewayError,
    PixKeyType,
    PixPaymentGateway,
    PixWithdrawalRequest,
    PSPConnectionError,
    PSPCredentials,
    PSPProvider,
    QRCodeType,
    RateLimiter,
    ReconciliationResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def payment_store():
    return PaymentStore()


@pytest.fixture
def mock_redis():
    """Redis mock that tracks call counts."""
    redis = AsyncMock()
    counters: Dict[str, int] = {}

    async def incr(key):
        counters[key] = counters.get(key, 0) + 1
        return counters[key]

    async def expire(key, ttl):
        pass

    redis.incr = incr
    redis.expire = expire
    return redis, counters


@pytest.fixture
def fraud_checker(mock_redis):
    redis, _ = mock_redis
    return PixFraudChecker(redis_client=redis)


@pytest.fixture
def rate_limiter(mock_redis):
    redis, _ = mock_redis
    return RateLimiter(redis_client=redis, max_per_minute=100)


@pytest.fixture
def mock_celcoin_adapter():
    """CelcoinAdapter with all IO mocked."""
    adapter = AsyncMock(spec=CelcoinAdapter)
    adapter.provider = PSPProvider.CELCOIN

    async def generate_qr_code(*args, **kwargs):
        return "00020101021226880014br.gov.bcb.pix...", f"E{uuid.uuid4().hex[:28]}"

    async def process_payout(*args, **kwargs):
        return f"E{uuid.uuid4().hex[:28]}", "APPROVED"

    async def query_payment_status(e2e_id):
        return {"status": "CONFIRMED", "amount": 100.0}

    async def list_transactions(start, end):
        return []

    def verify_webhook_signature(payload, signature):
        return signature == "valid_sig"

    adapter.generate_qr_code = generate_qr_code
    adapter.process_payout = process_payout
    adapter.query_payment_status = query_payment_status
    adapter.list_transactions = list_transactions
    adapter.verify_webhook_signature = verify_webhook_signature
    return adapter


@pytest.fixture
def gateway(payment_store, fraud_checker, rate_limiter, mock_celcoin_adapter):
    return PixPaymentGateway(
        adapters={PSPProvider.CELCOIN: mock_celcoin_adapter},
        store=payment_store,
        fraud_checker=fraud_checker,
        rate_limiter=rate_limiter,
        primary_psp=PSPProvider.CELCOIN,
    )


def make_deposit_req(**kwargs) -> PixDepositRequest:
    defaults = dict(
        player_id="player_test_001",
        amount_brl=100.00,
        description="Depósito de teste",
        expiration_seconds=3600,
    )
    defaults.update(kwargs)
    return PixDepositRequest(**defaults)


def make_withdrawal_req(**kwargs) -> PixWithdrawalRequest:
    defaults = dict(
        player_id="player_test_001",
        amount_brl=50.00,
        pix_key="11122233344",
        pix_key_type=PixKeyType.CPF,
        recipient_name="João da Silva",
        recipient_cpf_cnpj="11122233344",
        description="Saque de teste",
    )
    defaults.update(kwargs)
    return PixWithdrawalRequest(**defaults)


# ---------------------------------------------------------------------------
# State Machine Tests
# ---------------------------------------------------------------------------


class TestPaymentStateMachine:
    def _make_record(self, state: PaymentState) -> PaymentRecord:
        now = datetime.now(timezone.utc)
        return PaymentRecord(
            payment_id=str(uuid.uuid4()),
            player_id="p001",
            amount_brl=100.0,
            direction="deposit",
            state=state,
            psp_provider=PSPProvider.CELCOIN,
            e2e_id=None,
            qr_code=None,
            qr_code_type=QRCodeType.DYNAMIC,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
            settled_at=None,
        )

    def test_pending_to_processing(self):
        record = self._make_record(PaymentState.PENDING)
        PaymentStateMachine.transition(record, PaymentState.PROCESSING)
        assert record.state == PaymentState.PROCESSING
        assert len(record.audit_trail) == 1

    def test_processing_to_confirmed(self):
        record = self._make_record(PaymentState.PROCESSING)
        PaymentStateMachine.transition(record, PaymentState.CONFIRMED)
        assert record.state == PaymentState.CONFIRMED

    def test_confirmed_to_settled(self):
        record = self._make_record(PaymentState.CONFIRMED)
        PaymentStateMachine.transition(record, PaymentState.SETTLED)
        assert record.state == PaymentState.SETTLED
        assert record.settled_at is not None

    def test_settled_is_terminal(self):
        record = self._make_record(PaymentState.SETTLED)
        with pytest.raises(PixGatewayError):
            PaymentStateMachine.transition(record, PaymentState.CONFIRMED)

    def test_invalid_transition_raises(self):
        record = self._make_record(PaymentState.PENDING)
        with pytest.raises(PixGatewayError, match="Invalid transition"):
            PaymentStateMachine.transition(record, PaymentState.SETTLED)

    def test_failed_to_refunded(self):
        record = self._make_record(PaymentState.FAILED)
        PaymentStateMachine.transition(record, PaymentState.REFUNDED)
        assert record.state == PaymentState.REFUNDED

    def test_audit_trail_appended(self):
        record = self._make_record(PaymentState.PENDING)
        PaymentStateMachine.transition(record, PaymentState.PROCESSING, actor="test", reason="unit-test")
        assert record.audit_trail[-1]["actor"] == "test"
        assert record.audit_trail[-1]["reason"] == "unit-test"
        assert record.audit_trail[-1]["from"] == "pending"
        assert record.audit_trail[-1]["to"] == "processing"

    def test_full_happy_path(self):
        record = self._make_record(PaymentState.PENDING)
        for state in [
            PaymentState.PROCESSING,
            PaymentState.CONFIRMED,
            PaymentState.SETTLED,
        ]:
            PaymentStateMachine.transition(record, state)
        assert record.state == PaymentState.SETTLED
        assert len(record.audit_trail) == 3

    def test_refund_path(self):
        record = self._make_record(PaymentState.CONFIRMED)
        PaymentStateMachine.transition(record, PaymentState.REFUNDED)
        assert record.state == PaymentState.REFUNDED


# ---------------------------------------------------------------------------
# Fraud Checker Tests
# ---------------------------------------------------------------------------


class TestPixFraudChecker:
    @pytest.mark.asyncio
    async def test_clean_transaction_low_score(self, fraud_checker):
        score = await fraud_checker.score_deposit(
            player_id="p001",
            amount_brl=50.0,
            ip_address="203.0.113.1",
        )
        assert score < PixFraudChecker.HIGH_RISK_SCORE_THRESHOLD

    @pytest.mark.asyncio
    async def test_known_vpn_ip_raises_score(self, fraud_checker):
        score = await fraud_checker.score_deposit(
            player_id="p001",
            amount_brl=50.0,
            ip_address="185.220.100.1",  # known VPN prefix
        )
        assert score > 0.30

    @pytest.mark.asyncio
    async def test_very_high_amount_raises_score(self, fraud_checker):
        score = await fraud_checker.score_deposit(
            player_id="p001",
            amount_brl=25_000.0,
            ip_address="203.0.113.1",
        )
        assert score > 0.20

    @pytest.mark.asyncio
    async def test_velocity_high_count_raises_score(self, fraud_checker, mock_redis):
        redis, counters = mock_redis
        # Pre-fill the daily counter above the limit
        daily_key = f"pix:deposits:daily:p_velocity:{datetime.now(timezone.utc).date()}"
        counters[daily_key] = PixFraudChecker.MAX_DAILY_DEPOSITS + 1

        score = await fraud_checker.score_deposit(
            player_id="p_velocity",
            amount_brl=10.0,
            ip_address="203.0.113.1",
        )
        # Score should be elevated due to velocity
        assert score >= 0.40

    @pytest.mark.asyncio
    async def test_score_capped_at_one(self, fraud_checker, mock_redis):
        redis, counters = mock_redis
        # Saturate both daily and hourly counters
        daily_key = f"pix:deposits:daily:p_max:{datetime.now(timezone.utc).date()}"
        hour_key = f"pix:deposits:hourly:p_max:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        counters[daily_key] = 100
        counters[hour_key] = 100

        score = await fraud_checker.score_deposit(
            player_id="p_max",
            amount_brl=25_000.0,
            ip_address="185.220.100.1",
        )
        assert score <= 1.0


# ---------------------------------------------------------------------------
# Payment Store Tests
# ---------------------------------------------------------------------------


class TestPaymentStore:
    def _make_record(
        self,
        player_id: str = "p001",
        e2e_id: Optional[str] = None,
        state: PaymentState = PaymentState.PENDING,
    ) -> PaymentRecord:
        now = datetime.now(timezone.utc)
        return PaymentRecord(
            payment_id=str(uuid.uuid4()),
            player_id=player_id,
            amount_brl=100.0,
            direction="deposit",
            state=state,
            psp_provider=PSPProvider.CELCOIN,
            e2e_id=e2e_id or str(uuid.uuid4()),
            qr_code="qr_code_string",
            qr_code_type=QRCodeType.DYNAMIC,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
            settled_at=None,
        )

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self, payment_store):
        record = self._make_record()
        await payment_store.save(record)
        retrieved = await payment_store.get(record.payment_id)
        assert retrieved is not None
        assert retrieved.payment_id == record.payment_id

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, payment_store):
        result = await payment_store.get("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_e2e(self, payment_store):
        e2e = "Etest1234"
        record = self._make_record(e2e_id=e2e)
        await payment_store.save(record)
        found = await payment_store.get_by_e2e(e2e)
        assert found is not None
        assert found.e2e_id == e2e

    @pytest.mark.asyncio
    async def test_list_by_period(self, payment_store):
        now = datetime.now(timezone.utc)
        for i in range(5):
            r = self._make_record()
            r.created_at = now - timedelta(hours=i)
            await payment_store.save(r)

        results = await payment_store.list_by_period(
            now - timedelta(hours=3),
            now + timedelta(minutes=1),
        )
        assert len(results) == 4  # hours 0,1,2,3

    @pytest.mark.asyncio
    async def test_update_existing(self, payment_store):
        record = self._make_record()
        await payment_store.save(record)
        record.state = PaymentState.CONFIRMED
        await payment_store.save(record)
        updated = await payment_store.get(record.payment_id)
        assert updated.state == PaymentState.CONFIRMED


# ---------------------------------------------------------------------------
# Deposit Flow Tests
# ---------------------------------------------------------------------------


class TestPixDeposit:
    @pytest.mark.asyncio
    async def test_successful_deposit_returns_qr_code(self, gateway):
        req = make_deposit_req()
        record = await gateway.create_deposit(req, ip_address="203.0.113.1")
        assert record.qr_code is not None
        assert record.e2e_id is not None
        assert record.state == PaymentState.PROCESSING

    @pytest.mark.asyncio
    async def test_deposit_stored_in_store(self, gateway, payment_store):
        req = make_deposit_req()
        record = await gateway.create_deposit(req)
        stored = await payment_store.get(record.payment_id)
        assert stored is not None
        assert stored.player_id == req.player_id

    @pytest.mark.asyncio
    async def test_deposit_amount_rounded(self, gateway):
        req = make_deposit_req(amount_brl=10.999)
        # Pydantic rounds to 2 decimal places
        assert req.amount_brl == round(10.999, 2)

    @pytest.mark.asyncio
    async def test_deposit_fraud_blocked(self, gateway, mock_redis):
        """Saturate velocity counters to trigger fraud block."""
        redis, counters = mock_redis

        # Override fraud checker to always return high score
        original_score = gateway.fraud_checker.score_deposit

        async def always_high(*args, **kwargs):
            return 0.99

        gateway.fraud_checker.score_deposit = always_high
        req = make_deposit_req()

        with pytest.raises(FraudCheckFailedError):
            await gateway.create_deposit(req, ip_address="185.220.100.1")

        # Restore
        gateway.fraud_checker.score_deposit = original_score

    @pytest.mark.asyncio
    async def test_deposit_psp_failure_transitions_to_failed(
        self, payment_store, fraud_checker, rate_limiter
    ):
        failing_adapter = AsyncMock()
        failing_adapter.provider = PSPProvider.CELCOIN
        failing_adapter.generate_qr_code.side_effect = PSPConnectionError("Celcoin down")

        gw = PixPaymentGateway(
            adapters={PSPProvider.CELCOIN: failing_adapter},
            store=payment_store,
            fraud_checker=fraud_checker,
            rate_limiter=rate_limiter,
        )
        req = make_deposit_req()
        with pytest.raises(PSPConnectionError):
            await gw.create_deposit(req)

        # Find the stored record and check it's FAILED
        all_records = list(payment_store._records.values())
        assert len(all_records) == 1
        assert all_records[0].state == PaymentState.FAILED

    @pytest.mark.asyncio
    async def test_deposit_has_audit_trail(self, gateway):
        req = make_deposit_req()
        record = await gateway.create_deposit(req, ip_address="203.0.113.1")
        assert len(record.audit_trail) >= 1
        initiation = record.audit_trail[0]
        assert initiation["event"] == "deposit_initiated"
        assert initiation["player_id"] == req.player_id

    @pytest.mark.asyncio
    async def test_deposit_sets_expiration(self, gateway):
        expiry_secs = 1800
        req = make_deposit_req(expiration_seconds=expiry_secs)
        record = await gateway.create_deposit(req)
        assert record.expires_at is not None
        delta = (record.expires_at - record.created_at).total_seconds()
        assert abs(delta - expiry_secs) < 10  # within 10 seconds tolerance

    @pytest.mark.asyncio
    async def test_multiple_deposits_for_same_player(self, gateway):
        req = make_deposit_req(player_id="multi_player")
        r1 = await gateway.create_deposit(req)
        r2 = await gateway.create_deposit(req)
        assert r1.payment_id != r2.payment_id


# ---------------------------------------------------------------------------
# Withdrawal Flow Tests
# ---------------------------------------------------------------------------


class TestPixWithdrawal:
    @pytest.mark.asyncio
    async def test_successful_withdrawal(self, gateway):
        req = make_withdrawal_req()
        record = await gateway.process_withdrawal(req)
        assert record.state == PaymentState.SETTLED
        assert record.e2e_id is not None
        assert record.settled_at is not None

    @pytest.mark.asyncio
    async def test_withdrawal_stored(self, gateway, payment_store):
        req = make_withdrawal_req()
        record = await gateway.process_withdrawal(req)
        stored = await payment_store.get(record.payment_id)
        assert stored is not None
        assert stored.direction == "withdrawal"

    @pytest.mark.asyncio
    async def test_withdrawal_psp_failure(
        self, payment_store, fraud_checker, rate_limiter
    ):
        failing_adapter = AsyncMock()
        failing_adapter.provider = PSPProvider.CELCOIN
        failing_adapter.process_payout.side_effect = PSPConnectionError("timeout")

        gw = PixPaymentGateway(
            adapters={PSPProvider.CELCOIN: failing_adapter},
            store=payment_store,
            fraud_checker=fraud_checker,
            rate_limiter=rate_limiter,
        )
        req = make_withdrawal_req()
        with pytest.raises(PSPConnectionError):
            await gw.process_withdrawal(req)

        records = list(payment_store._records.values())
        assert records[0].state == PaymentState.FAILED

    @pytest.mark.asyncio
    async def test_withdrawal_logs_audit_trail(self, gateway):
        req = make_withdrawal_req()
        record = await gateway.process_withdrawal(req)
        # At minimum: transitions should be logged in state machine
        assert len(record.audit_trail) >= 2


# ---------------------------------------------------------------------------
# Webhook Tests
# ---------------------------------------------------------------------------


class TestWebhookHandling:
    def _make_webhook_payload(
        self,
        e2e_id: str,
        status: str = "CONFIRMED",
        amount: float = 100.0,
    ) -> bytes:
        payload = {
            "e2eId": e2e_id,
            "status": status,
            "amount": amount,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return json.dumps(payload).encode()

    @pytest.mark.asyncio
    async def test_confirmed_webhook_settles_payment(self, gateway, payment_store):
        # Create a payment in PROCESSING state
        req = make_deposit_req()
        record = await gateway.create_deposit(req)
        e2e_id = record.e2e_id

        payload = self._make_webhook_payload(e2e_id, "CONFIRMED")
        await gateway.handle_webhook(payload, "valid_sig", PSPProvider.CELCOIN)

        updated = await payment_store.get(record.payment_id)
        assert updated.state == PaymentState.SETTLED

    @pytest.mark.asyncio
    async def test_failed_webhook_transitions_to_failed(self, gateway, payment_store):
        req = make_deposit_req()
        record = await gateway.create_deposit(req)

        payload = self._make_webhook_payload(record.e2e_id, "FAILED")
        await gateway.handle_webhook(payload, "valid_sig", PSPProvider.CELCOIN)

        updated = await payment_store.get(record.payment_id)
        assert updated.state == PaymentState.FAILED

    @pytest.mark.asyncio
    async def test_invalid_signature_raises_http_exception(self, gateway):
        payload = self._make_webhook_payload("some_e2e_id")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await gateway.handle_webhook(payload, "wrong_sig", PSPProvider.CELCOIN)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_unknown_e2e_id_is_ignored(self, gateway):
        """Webhook for unknown transaction should not raise, just log warning."""
        payload = self._make_webhook_payload("EUNKNOWN12345")
        # Should complete without raising
        await gateway.handle_webhook(payload, "valid_sig", PSPProvider.CELCOIN)

    @pytest.mark.asyncio
    async def test_webhook_appends_audit_entry(self, gateway, payment_store):
        req = make_deposit_req()
        record = await gateway.create_deposit(req)
        payload = self._make_webhook_payload(record.e2e_id, "CONFIRMED")
        await gateway.handle_webhook(payload, "valid_sig", PSPProvider.CELCOIN)

        updated = await payment_store.get(record.payment_id)
        events = [e["event"] for e in updated.audit_trail]
        assert "webhook_received" in events


# ---------------------------------------------------------------------------
# Rate Limiter Tests
# ---------------------------------------------------------------------------


class TestRateLimiter:
    @pytest.mark.asyncio
    async def test_allows_under_limit(self, rate_limiter):
        allowed = await rate_limiter.check("test_key")
        assert allowed is True

    @pytest.mark.asyncio
    async def test_blocks_over_limit(self, mock_redis):
        redis, counters = mock_redis
        # Pre-fill counter above limit
        bucket_key = f"rate_limit:test_rl:{int(time.time() // 60)}"
        counters[bucket_key] = 1000  # far above any limit

        limiter = RateLimiter(redis_client=redis, max_per_minute=10)
        allowed = await limiter.check("test_rl")
        assert allowed is False


# ---------------------------------------------------------------------------
# Reconciliation Tests
# ---------------------------------------------------------------------------


class TestReconciliation:
    @pytest.mark.asyncio
    async def test_balanced_reconciliation(
        self, gateway, payment_store, mock_celcoin_adapter
    ):
        # Add a settled record
        now = datetime.now(timezone.utc)
        e2e = f"E{uuid.uuid4().hex[:28]}"
        record = PaymentRecord(
            payment_id=str(uuid.uuid4()),
            player_id="p001",
            amount_brl=100.0,
            direction="deposit",
            state=PaymentState.SETTLED,
            psp_provider=PSPProvider.CELCOIN,
            e2e_id=e2e,
            qr_code="qr",
            qr_code_type=QRCodeType.DYNAMIC,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
            settled_at=now,
        )
        await payment_store.save(record)

        # Mock PSP to return the matching transaction
        mock_celcoin_adapter.list_transactions = AsyncMock(
            return_value=[{"e2eId": e2e, "amount": 100.0}]
        )

        result = await gateway.reconcile(PSPProvider.CELCOIN, period_hours=1)
        assert result.discrepancy_brl < 0.01
        assert result.status == "balanced"

    @pytest.mark.asyncio
    async def test_discrepancy_detected(self, gateway, payment_store, mock_celcoin_adapter):
        now = datetime.now(timezone.utc)
        e2e = f"E{uuid.uuid4().hex[:28]}"
        record = PaymentRecord(
            payment_id=str(uuid.uuid4()),
            player_id="p001",
            amount_brl=100.0,
            direction="deposit",
            state=PaymentState.SETTLED,
            psp_provider=PSPProvider.CELCOIN,
            e2e_id=e2e,
            qr_code="qr",
            qr_code_type=QRCodeType.DYNAMIC,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(hours=1),
            settled_at=now,
        )
        await payment_store.save(record)

        # PSP reports different amount
        mock_celcoin_adapter.list_transactions = AsyncMock(
            return_value=[{"e2eId": e2e, "amount": 95.0}]  # R$5 discrepancy
        )

        result = await gateway.reconcile(PSPProvider.CELCOIN, period_hours=1)
        assert result.discrepancy_brl == pytest.approx(5.0, abs=0.01)
        assert result.status == "discrepancy"

    @pytest.mark.asyncio
    async def test_unmatched_psp_transaction_detected(
        self, gateway, mock_celcoin_adapter
    ):
        # PSP has a transaction not in internal store
        mock_celcoin_adapter.list_transactions = AsyncMock(
            return_value=[{"e2eId": "EPSP_ONLY_123", "amount": 200.0}]
        )
        result = await gateway.reconcile(PSPProvider.CELCOIN, period_hours=1)
        assert "EPSP_ONLY_123" in result.unmatched_psp

    @pytest.mark.asyncio
    async def test_result_has_required_fields(self, gateway, mock_celcoin_adapter):
        mock_celcoin_adapter.list_transactions.return_value = []
        result = await gateway.reconcile(PSPProvider.CELCOIN)
        assert result.run_id
        assert result.period_start
        assert result.period_end
        assert result.psp_provider == PSPProvider.CELCOIN


# ---------------------------------------------------------------------------
# Input Validation Tests
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_deposit_amount_must_be_positive(self):
        with pytest.raises(Exception):
            PixDepositRequest(player_id="p", amount_brl=-1.0)

    def test_deposit_amount_max_limit(self):
        with pytest.raises(Exception):
            PixDepositRequest(player_id="p", amount_brl=600_000.0)

    def test_withdrawal_amount_positive(self):
        with pytest.raises(Exception):
            PixWithdrawalRequest(
                player_id="p",
                amount_brl=0.0,
                pix_key="key",
                pix_key_type=PixKeyType.CPF,
                recipient_name="Test",
                recipient_cpf_cnpj="11122233344",
            )

    def test_deposit_expiration_minimum(self):
        with pytest.raises(Exception):
            PixDepositRequest(player_id="p", amount_brl=10.0, expiration_seconds=30)

    def test_deposit_amount_rounds_to_cents(self):
        req = PixDepositRequest(player_id="p", amount_brl=10.999)
        assert req.amount_brl == round(10.999, 2)

    def test_empty_player_id_rejected(self):
        with pytest.raises(Exception):
            PixDepositRequest(player_id="", amount_brl=10.0)


# ---------------------------------------------------------------------------
# PixKeyType Tests
# ---------------------------------------------------------------------------


class TestPixKeyTypes:
    def test_all_key_types_accepted(self):
        for key_type in PixKeyType:
            req = PixWithdrawalRequest(
                player_id="p",
                amount_brl=10.0,
                pix_key="some_key",
                pix_key_type=key_type,
                recipient_name="Test",
                recipient_cpf_cnpj="11122233344",
            )
            assert req.pix_key_type == key_type
