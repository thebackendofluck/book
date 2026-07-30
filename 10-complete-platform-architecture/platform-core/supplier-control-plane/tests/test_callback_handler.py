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
tests/test_callback_handler.py
-------------------------------
Test suite for the Callback Handler.

Covers:
  - Signature validation (HMAC-SHA256, HMAC-SHA512, none)
  - Idempotency store (dedup, TTL expiry, eviction)
  - Callback routing to domain handlers
  - Dead letter queue lifecycle
  - Metrics tracking
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys

import pytest
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(__file__)
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from callback_handler import (
    CallbackHandler,
    CallbackResult,
    CallbackStatus,
    CallbackType,
    CrossTenantViolation,
    DeadLetterEntry,
    DomainHandler,
    IdempotencyStore,
    InMemoryPlayerBrandRegistry,
    SignatureMethod,
    SignatureValidator,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_registry_and_creds():
    """Create a registry with one supplier and matching credentials."""
    reg = SupplierRegistry()
    record = SupplierRecord(
        id="evolution",
        name="Evolution Gaming",
        type=SupplierType.CASINO,
        status=SupplierStatus.ACTIVE,
        capabilities=SupplierCapabilityMatrix(
            supplier_id="evolution",
            games={"blackjack", "roulette"},
            currencies={"EUR"},
            jurisdictions={"GB"},
            wallet_model=WalletModel.SEAMLESS,
        ),
    )
    reg.register_supplier(record)

    backend = InMemorySecretBackend()
    cred_mgr = CredentialManager(registry=reg, backend=backend)
    creds = Credentials(
        supplier_id="evolution",
        brand_id="brand1",
        jurisdiction="GB",
        api_key="TESTKEY123",
        api_secret="TESTSECRET456",
        operator_id="OP1",
    )
    cred_mgr.add_credentials(creds)

    return reg, cred_mgr


def _sign_payload(payload: dict, secret: str) -> str:
    """Generate HMAC-SHA256 signature for a payload."""
    body = json.dumps(payload, sort_keys=True)
    return hmac.new(
        secret.encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ===========================================================================
# 1. Signature Validator
# ===========================================================================


class TestSignatureValidator:
    def test_hmac_sha256_valid(self):
        validator = SignatureValidator()
        body = '{"key": "value"}'
        secret = "mysecret"
        sig = hmac.new(
            secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        assert validator.validate_hmac_sha256(body, secret, sig) is True

    def test_hmac_sha256_invalid(self):
        validator = SignatureValidator()
        assert validator.validate_hmac_sha256("body", "secret", "wrong") is False

    def test_hmac_sha512_valid(self):
        validator = SignatureValidator()
        body = '{"key": "value"}'
        secret = "mysecret"
        sig = hmac.new(
            secret.encode(), body.encode(), hashlib.sha512
        ).hexdigest()
        assert validator.validate_hmac_sha512(body, secret, sig) is True

    def test_none_method_always_passes(self):
        validator = SignatureValidator()
        assert validator.validate(SignatureMethod.NONE, "", "", "") is True

    def test_validate_dispatches_to_hmac256(self):
        validator = SignatureValidator()
        body = "test"
        secret = "sec"
        sig = hmac.new(
            secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        assert validator.validate(
            SignatureMethod.HMAC_SHA256, body, secret, sig,
        ) is True


# ===========================================================================
# 1b. RSA-SHA256 Signature Validation
# ===========================================================================

cryptography = pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402


def _make_rsa_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return private_key, public_pem


def _rsa_sign(private_key, body: str) -> str:
    signature = private_key.sign(
        body.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


class TestRSASignatureValidation:
    def test_validate_accepts_valid_rsa_signature(self):
        validator = SignatureValidator()
        private_key, public_pem = _make_rsa_keypair()
        body = '{"transaction_id": "TXN-RSA-1"}'
        sig_b64 = _rsa_sign(private_key, body)

        assert validator.validate(
            SignatureMethod.RSA_SHA256, body, public_pem, sig_b64,
        ) is True

    def test_validate_rejects_forged_rsa_signature(self):
        validator = SignatureValidator()
        _, public_pem = _make_rsa_keypair()
        forger_private_key, _ = _make_rsa_keypair()  # a different keypair
        body = '{"transaction_id": "TXN-RSA-2"}'
        forged_sig_b64 = _rsa_sign(forger_private_key, body)

        assert validator.validate(
            SignatureMethod.RSA_SHA256, body, public_pem, forged_sig_b64,
        ) is False

    def test_validate_rejects_tampered_body_with_valid_signature(self):
        validator = SignatureValidator()
        private_key, public_pem = _make_rsa_keypair()
        sig_b64 = _rsa_sign(private_key, '{"amount": 10}')

        assert validator.validate(
            SignatureMethod.RSA_SHA256, '{"amount": 999999}', public_pem, sig_b64,
        ) is False

    def test_validate_fails_closed_when_no_public_key_configured(self):
        validator = SignatureValidator()
        assert validator.validate(
            SignatureMethod.RSA_SHA256, "body", "", "anysig",
        ) is False

    def test_process_callback_accepts_valid_rsa_signed_wallet_callback(self):
        """End-to-end: process_callback() must actually verify RSA, not rubber-stamp it."""
        private_key, public_pem = _make_rsa_keypair()

        reg = SupplierRegistry()
        record = SupplierRecord(
            id="rsa-supplier",
            name="RSA Supplier",
            type=SupplierType.CASINO,
            status=SupplierStatus.ACTIVE,
            capabilities=SupplierCapabilityMatrix(
                supplier_id="rsa-supplier",
                wallet_model=WalletModel.SEAMLESS,
            ),
        )
        reg.register_supplier(record)
        backend = InMemorySecretBackend()
        cred_mgr = CredentialManager(registry=reg, backend=backend)
        cred_mgr.add_credentials(Credentials(
            supplier_id="rsa-supplier",
            brand_id="brand1",
            jurisdiction="GB",
            api_key="unused",
            api_secret=public_pem,  # RSA public key travels in api_secret
            operator_id="OP1",
        ))

        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.RSA_SHA256,
        )
        payload = {
            "transaction_id": "TXN-RSA-OK",
            "callback_type": "wallet",
            "action": "credit",
            "player_id": "P-1",
            "amount": 25.0,
        }
        body = json.dumps(payload, sort_keys=True)
        sig_b64 = _rsa_sign(private_key, body)

        result = handler.process_callback(
            supplier_id="rsa-supplier",
            brand_id="brand1",
            jurisdiction="GB",
            headers={"X-Signature": sig_b64},
            payload=payload,
            raw_body=body,
        )
        assert result.status == CallbackStatus.ACCEPTED

    def test_process_callback_rejects_forged_rsa_signed_wallet_callback(self):
        """A callback signed with the wrong key must be rejected, not accepted."""
        _, public_pem = _make_rsa_keypair()
        forger_private_key, _ = _make_rsa_keypair()

        reg = SupplierRegistry()
        record = SupplierRecord(
            id="rsa-supplier",
            name="RSA Supplier",
            type=SupplierType.CASINO,
            status=SupplierStatus.ACTIVE,
            capabilities=SupplierCapabilityMatrix(
                supplier_id="rsa-supplier",
                wallet_model=WalletModel.SEAMLESS,
            ),
        )
        reg.register_supplier(record)
        backend = InMemorySecretBackend()
        cred_mgr = CredentialManager(registry=reg, backend=backend)
        cred_mgr.add_credentials(Credentials(
            supplier_id="rsa-supplier",
            brand_id="brand1",
            jurisdiction="GB",
            api_key="unused",
            api_secret=public_pem,
            operator_id="OP1",
        ))

        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.RSA_SHA256,
        )
        payload = {
            "transaction_id": "TXN-RSA-FORGED",
            "callback_type": "wallet",
            "action": "credit",
            "player_id": "P-1",
            "amount": 999999.0,
        }
        body = json.dumps(payload, sort_keys=True)
        forged_sig_b64 = _rsa_sign(forger_private_key, body)

        result = handler.process_callback(
            supplier_id="rsa-supplier",
            brand_id="brand1",
            jurisdiction="GB",
            headers={"X-Signature": forged_sig_b64},
            payload=payload,
            raw_body=body,
        )
        assert result.status == CallbackStatus.REJECTED
        assert "Invalid callback signature" in result.error_message


# ===========================================================================
# 2. Idempotency Store
# ===========================================================================


class TestIdempotencyStore:
    def test_new_transaction_not_seen(self):
        store = IdempotencyStore()
        assert store.has_seen("evolution", "brand1", "TXN-001") is False

    def test_recorded_transaction_is_seen(self):
        store = IdempotencyStore()
        result = CallbackResult(
            transaction_id="TXN-001",
            status=CallbackStatus.ACCEPTED,
            supplier_id="evo",
            callback_type="game_round",
        )
        store.record("evolution", "brand1", "TXN-001", result)
        assert store.has_seen("evolution", "brand1", "TXN-001") is True

    def test_get_previous_result(self):
        store = IdempotencyStore()
        result = CallbackResult(
            transaction_id="TXN-001",
            status=CallbackStatus.ACCEPTED,
            supplier_id="evo",
            callback_type="game_round",
            response_payload={"balance": 100.0},
        )
        store.record("evolution", "brand1", "TXN-001", result)
        prev = store.get_previous_result("evolution", "brand1", "TXN-001")
        assert prev is not None
        assert prev.response_payload == {"balance": 100.0}

    def test_get_previous_result_missing(self):
        store = IdempotencyStore()
        assert store.get_previous_result("evolution", "brand1", "TXN-999") is None

    def test_size_tracking(self):
        store = IdempotencyStore()
        assert store.size() == 0
        result = CallbackResult(
            transaction_id="T1",
            status=CallbackStatus.ACCEPTED,
            supplier_id="evo",
            callback_type="game_round",
        )
        store.record("evolution", "brand1", "T1", result)
        assert store.size() == 1

    def test_clear(self):
        store = IdempotencyStore()
        result = CallbackResult(
            transaction_id="T1",
            status=CallbackStatus.ACCEPTED,
            supplier_id="evo",
            callback_type="game_round",
        )
        store.record("evolution", "brand1", "T1", result)
        store.clear()
        assert store.size() == 0
        assert store.has_seen("evolution", "brand1", "T1") is False

    def test_eviction_on_max_entries(self):
        store = IdempotencyStore(max_entries=3)
        for i in range(5):
            result = CallbackResult(
                transaction_id=f"T{i}",
                status=CallbackStatus.ACCEPTED,
                supplier_id="evo",
                callback_type="game_round",
            )
            store.record("evolution", "brand1", f"T{i}", result)
        assert store.size() <= 3

    def test_same_transaction_id_different_brands_do_not_collide(self):
        """
        Two brands on the same supplier reusing a transaction_id must be
        treated as independent events, not a duplicate/cross-tenant leak.
        """
        store = IdempotencyStore()
        result_a = CallbackResult(
            transaction_id="TXN-SHARED",
            status=CallbackStatus.ACCEPTED,
            supplier_id="evolution",
            callback_type="wallet",
            response_payload={"brand": "brand-a"},
        )
        store.record("evolution", "brand-a", "TXN-SHARED", result_a)

        # brand-b has never seen this transaction_id, despite the string match
        assert store.has_seen("evolution", "brand-b", "TXN-SHARED") is False
        assert store.get_previous_result("evolution", "brand-b", "TXN-SHARED") is None

        result_b = CallbackResult(
            transaction_id="TXN-SHARED",
            status=CallbackStatus.ACCEPTED,
            supplier_id="evolution",
            callback_type="wallet",
            response_payload={"brand": "brand-b"},
        )
        store.record("evolution", "brand-b", "TXN-SHARED", result_b)

        # Each brand still gets back its own cached response
        assert store.get_previous_result("evolution", "brand-a", "TXN-SHARED").response_payload == {"brand": "brand-a"}
        assert store.get_previous_result("evolution", "brand-b", "TXN-SHARED").response_payload == {"brand": "brand-b"}


# ===========================================================================
# 3. Callback Handler — Core Processing
# ===========================================================================


class TestCallbackHandler:
    def _make_handler(self, sig_method=SignatureMethod.NONE):
        reg, cred_mgr = _make_registry_and_creds()
        return CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=sig_method,
        )

    def test_process_valid_game_round(self):
        handler = self._make_handler()
        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-001",
                "callback_type": "game_round",
                "action": "bet",
                "round_id": "R-100",
                "player_id": "P-1",
                "amount": 10.0,
            },
        )
        assert result.status == CallbackStatus.ACCEPTED
        assert result.transaction_id == "TXN-001"
        assert result.is_success() is True

    def test_process_valid_wallet_callback(self):
        handler = self._make_handler()
        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-002",
                "callback_type": "wallet",
                "action": "credit",
                "player_id": "P-1",
                "amount": 50.0,
            },
        )
        assert result.status == CallbackStatus.ACCEPTED

    def test_process_valid_bonus_callback(self):
        handler = self._make_handler()
        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-003",
                "callback_type": "bonus",
                "action": "award",
                "bonus_id": "B-1",
            },
        )
        assert result.status == CallbackStatus.ACCEPTED

    def test_missing_transaction_id_rejected(self):
        handler = self._make_handler()
        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={"callback_type": "game_round"},
        )
        assert result.status == CallbackStatus.REJECTED
        assert "Missing transaction_id" in result.error_message

    def test_unknown_callback_type_rejected(self):
        handler = self._make_handler()
        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-004",
                "callback_type": "unknown_type",
            },
        )
        assert result.status == CallbackStatus.REJECTED
        assert "Unknown callback_type" in result.error_message

    def test_duplicate_callback_returns_duplicate(self):
        handler = self._make_handler()
        payload = {
            "transaction_id": "TXN-005",
            "callback_type": "game_round",
            "action": "bet",
        }
        result1 = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload=payload,
        )
        assert result1.status == CallbackStatus.ACCEPTED

        result2 = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload=payload,
        )
        assert result2.status == CallbackStatus.DUPLICATE
        assert result2.is_success() is True

    def test_signature_validation_hmac_sha256(self):
        reg, cred_mgr = _make_registry_and_creds()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.HMAC_SHA256,
        )
        payload = {
            "transaction_id": "TXN-006",
            "callback_type": "game_round",
            "action": "result",
        }
        sig = _sign_payload(payload, "TESTSECRET456")
        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={"X-Signature": sig},
            payload=payload,
        )
        assert result.status == CallbackStatus.ACCEPTED

    def test_invalid_signature_rejected(self):
        reg, cred_mgr = _make_registry_and_creds()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.HMAC_SHA256,
        )
        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={"X-Signature": "invalid_signature"},
            payload={
                "transaction_id": "TXN-007",
                "callback_type": "game_round",
            },
        )
        assert result.status == CallbackStatus.REJECTED
        assert "Invalid callback signature" in result.error_message

    def test_metrics_tracking(self):
        handler = self._make_handler()
        handler.reset_metrics()

        handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={"transaction_id": "T1", "callback_type": "game_round"},
        )
        handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={"transaction_id": "T1", "callback_type": "game_round"},
        )
        handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={"callback_type": "game_round"},  # no txn id
        )

        metrics = handler.get_metrics()
        assert metrics["total_received"] == 3
        assert metrics["accepted"] == 1
        assert metrics["duplicates"] == 1
        assert metrics["rejected"] == 1


# ===========================================================================
# 4. Dead Letter Queue
# ===========================================================================


class TestDeadLetterQueue:
    def test_failed_handler_sends_to_dlq(self):
        reg, cred_mgr = _make_registry_and_creds()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
        )

        # Register a handler that always fails
        class FailingHandler(DomainHandler):
            def handle(self, supplier_id, brand_id, jurisdiction, payload):
                raise RuntimeError("Simulated processing failure")

        handler.register_handler("game_round", FailingHandler())

        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-DLQ-001",
                "callback_type": "game_round",
            },
        )
        assert result.status == CallbackStatus.DEAD_LETTERED
        assert handler.get_dead_letter_count() == 1

    def test_replay_dead_letter(self):
        reg, cred_mgr = _make_registry_and_creds()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
        )

        # First, use a failing handler
        call_count = {"n": 0}

        class SometimesFailingHandler(DomainHandler):
            def handle(self, supplier_id, brand_id, jurisdiction, payload):
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise RuntimeError("First attempt fails")
                return {"status": "ok"}

        handler.register_handler("game_round", SometimesFailingHandler())

        handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-DLQ-002",
                "callback_type": "game_round",
            },
        )
        assert handler.get_dead_letter_count() == 1

        # Replay should succeed on second attempt
        dlq = handler.get_dead_letter_queue()
        replay_result = handler.replay_dead_letter(dlq[0].id)
        assert replay_result is not None
        assert replay_result.status == CallbackStatus.ACCEPTED
        assert handler.get_dead_letter_count() == 0

    def test_clear_dead_letter_queue(self):
        reg, cred_mgr = _make_registry_and_creds()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
        )

        class FailingHandler(DomainHandler):
            def handle(self, supplier_id, brand_id, jurisdiction, payload):
                raise RuntimeError("fail")

        handler.register_handler("game_round", FailingHandler())

        for i in range(3):
            handler.process_callback(
                supplier_id="evolution",
                brand_id="brand1",
                jurisdiction="GB",
                headers={},
                payload={
                    "transaction_id": f"TXN-DLQ-{i}",
                    "callback_type": "game_round",
                },
            )

        count = handler.clear_dead_letter_queue()
        assert count == 3
        assert handler.get_dead_letter_count() == 0

    def test_replay_nonexistent_entry(self):
        handler = CallbackHandler(
            signature_method=SignatureMethod.NONE,
        )
        assert handler.replay_dead_letter("nonexistent-id") is None


# ===========================================================================
# 5. Custom Domain Handler Registration
# ===========================================================================


class TestCustomHandler:
    def test_register_custom_handler(self):
        reg, cred_mgr = _make_registry_and_creds()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
        )

        class JackpotHandler(DomainHandler):
            def handle(self, supplier_id, brand_id, jurisdiction, payload):
                return {"jackpot_won": True, "amount": payload.get("amount", 0)}

        handler.register_handler("jackpot", JackpotHandler())

        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand1",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-JP-001",
                "callback_type": "jackpot",
                "amount": 50000,
            },
        )
        assert result.status == CallbackStatus.ACCEPTED
        assert result.response_payload["jackpot_won"] is True


# ===========================================================================
# 6. Multi-tenant isolation (namespaced idempotency + cross-tenant rejection)
# ===========================================================================


class TestMultiTenantIsolation:
    def _make_two_brand_handler(self):
        reg = SupplierRegistry()
        record = SupplierRecord(
            id="evolution",
            name="Evolution Gaming",
            type=SupplierType.CASINO,
            status=SupplierStatus.ACTIVE,
            capabilities=SupplierCapabilityMatrix(
                supplier_id="evolution",
                wallet_model=WalletModel.SEAMLESS,
            ),
        )
        reg.register_supplier(record)

        backend = InMemorySecretBackend()
        cred_mgr = CredentialManager(registry=reg, backend=backend)
        for brand in ("brand-a", "brand-b"):
            cred_mgr.add_credentials(Credentials(
                supplier_id="evolution",
                brand_id=brand,
                jurisdiction="GB",
                api_key=f"KEY-{brand}",
                api_secret=f"SECRET-{brand}",
                operator_id="OP1",
            ))
        return reg, cred_mgr

    def test_same_transaction_id_across_brands_both_processed(self):
        """
        Two brands sharing a supplier and reusing a transaction_id must
        each be processed independently — the second brand's callback
        must not be short-circuited as a duplicate of the first brand's.
        """
        reg, cred_mgr = self._make_two_brand_handler()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
        )
        payload = {
            "transaction_id": "TXN-COLLIDE",
            "callback_type": "wallet",
            "action": "credit",
            "player_id": "P-BRAND-A",
            "amount": 10.0,
        }
        result_a = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand-a",
            jurisdiction="GB",
            headers={},
            payload=payload,
        )
        assert result_a.status == CallbackStatus.ACCEPTED

        payload_b = dict(payload, player_id="P-BRAND-B")
        result_b = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand-b",
            jurisdiction="GB",
            headers={},
            payload=payload_b,
        )
        # Must be freshly ACCEPTED, not DUPLICATE of brand-a's response
        assert result_b.status == CallbackStatus.ACCEPTED

        metrics = handler.get_metrics()
        assert metrics["duplicates"] == 0

    def test_repeat_callback_within_same_brand_is_still_deduped(self):
        reg, cred_mgr = self._make_two_brand_handler()
        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
        )
        payload = {
            "transaction_id": "TXN-REPEAT",
            "callback_type": "wallet",
            "action": "credit",
            "player_id": "P-1",
            "amount": 10.0,
        }
        first = handler.process_callback(
            supplier_id="evolution", brand_id="brand-a", jurisdiction="GB",
            headers={}, payload=payload,
        )
        assert first.status == CallbackStatus.ACCEPTED

        second = handler.process_callback(
            supplier_id="evolution", brand_id="brand-a", jurisdiction="GB",
            headers={}, payload=payload,
        )
        assert second.status == CallbackStatus.DUPLICATE

    def test_cross_tenant_player_rejected(self):
        """A callback whose player_id is known to belong to a different
        brand than the one that signed the request must be rejected."""
        reg, cred_mgr = self._make_two_brand_handler()
        player_registry = InMemoryPlayerBrandRegistry()
        player_registry.assign("P-1", "brand-a")

        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
            player_brand_registry=player_registry,
        )

        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand-b",  # wrong brand for P-1
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-XT-001",
                "callback_type": "wallet",
                "action": "credit",
                "player_id": "P-1",
                "amount": 500.0,
            },
        )
        assert result.status == CallbackStatus.REJECTED
        assert "brand" in result.error_message

        # Must be a clean rejection, never routed to the DLQ for retry
        assert handler.get_dead_letter_count() == 0

    def test_cross_tenant_player_allowed_for_correct_brand(self):
        reg, cred_mgr = self._make_two_brand_handler()
        player_registry = InMemoryPlayerBrandRegistry()
        player_registry.assign("P-1", "brand-a")

        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
            player_brand_registry=player_registry,
        )

        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand-a",  # correct brand for P-1
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-XT-002",
                "callback_type": "wallet",
                "action": "credit",
                "player_id": "P-1",
                "amount": 500.0,
            },
        )
        assert result.status == CallbackStatus.ACCEPTED

    def test_unknown_player_not_blocked(self):
        """A player the registry has never seen is allowed through (first
        callback for a not-yet-provisioned player), not rejected."""
        reg, cred_mgr = self._make_two_brand_handler()
        player_registry = InMemoryPlayerBrandRegistry()  # empty

        handler = CallbackHandler(
            registry=reg,
            credential_manager=cred_mgr,
            signature_method=SignatureMethod.NONE,
            player_brand_registry=player_registry,
        )

        result = handler.process_callback(
            supplier_id="evolution",
            brand_id="brand-a",
            jurisdiction="GB",
            headers={},
            payload={
                "transaction_id": "TXN-XT-003",
                "callback_type": "wallet",
                "action": "credit",
                "player_id": "P-NEW",
                "amount": 10.0,
            },
        )
        assert result.status == CallbackStatus.ACCEPTED
