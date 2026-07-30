#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 07, Casino Implementation Planning and Timeline.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Payment Method Router for Multi-Market Casino Operations

Routes deposits and withdrawals through the optimal payment method
based on market, player preferences, transaction type, and provider
availability. Supports 3+ payment methods per market as required
by most gambling regulators.

Features:
- Market-specific payment method configuration
- Automatic failover between payment providers
- Transaction fee optimization
- Velocity checks and fraud scoring
- PCI DSS compliant (no raw card data handling)
- Regulatory reporting hooks

Usage:
    from payment_router import PaymentRouter
    router = PaymentRouter()
    result = router.process_deposit("player-123", 100.00, "GBP", "uk", method="card")
    result = router.process_withdrawal("player-123", 50.00, "GBP", "uk")

    # CLI demo
    python3 payment_router.py --demo
"""

import json
import logging
import argparse
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Transaction types and statuses
# ---------------------------------------------------------------------------

class TransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"


class TransactionStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERSED = "reversed"
    CANCELLED = "cancelled"
    ON_HOLD = "on_hold"          # AML review
    AWAITING_KYC = "awaiting_kyc"


class PaymentMethodType(Enum):
    CARD = "card"                 # Visa/Mastercard (tokenized)
    BANK_TRANSFER = "bank_transfer"
    E_WALLET = "e_wallet"        # PayPal, Skrill, Neteller
    CRYPTO = "crypto"            # Where legal
    PREPAID = "prepaid"          # Paysafecard, Neosurf
    MOBILE = "mobile"            # Apple Pay, Google Pay
    PIX = "pix"                  # Brazil-specific
    OPEN_BANKING = "open_banking"  # Trustly, direct bank
    INTERAC = "interac"          # Canada-specific


# ---------------------------------------------------------------------------
# Market-specific payment configuration
# ---------------------------------------------------------------------------

MARKET_PAYMENT_CONFIG = {
    "uk": {
        "currency": "GBP",
        "methods": {
            "card": {
                "provider": "Worldpay",
                "fallback_provider": "Adyen",
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 5000.0,
                "withdrawal_min": 10.0,
                "withdrawal_max": 10000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "1-3 business days",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 2000.0,
            },
            "e_wallet_paypal": {
                "provider": "PayPal",
                "fallback_provider": None,
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 5000.0,
                "withdrawal_min": 10.0,
                "withdrawal_max": 5500.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "24 hours",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 2000.0,
            },
            "open_banking": {
                "provider": "Trustly",
                "fallback_provider": "TrueLayer",
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 25000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 25000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "1-2 business days",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 2000.0,
            },
            "prepaid_paysafecard": {
                "provider": "Paysafe",
                "fallback_provider": None,
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 1000.0,
                "withdrawal_min": 0,
                "withdrawal_max": 0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "N/A",
                "supports_deposit": True,
                "supports_withdrawal": False,
                "kyc_required_above": 250.0,
            },
        },
        "credit_cards_banned": True,  # UK banned credit card gambling 2020
        "aml_threshold": 2000.0,
        "enhanced_dd_threshold": 15000.0,
        "withdrawal_to_deposit_method": True,  # Must withdraw to same method used for deposit
    },
    "malta": {
        "currency": "EUR",
        "methods": {
            "card": {
                "provider": "Adyen",
                "fallback_provider": "Stripe",
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 10000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 10000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "2-5 business days",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 2000.0,
            },
            "e_wallet_skrill": {
                "provider": "Skrill",
                "fallback_provider": "Neteller",
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 10000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 10000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "24 hours",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 2000.0,
            },
            "bank_transfer": {
                "provider": "Trustly",
                "fallback_provider": None,
                "enabled": True,
                "deposit_min": 20.0,
                "deposit_max": 50000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 50000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "1-3 business days",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 2000.0,
            },
        },
        "credit_cards_banned": False,
        "aml_threshold": 2000.0,
        "enhanced_dd_threshold": 15000.0,
        "withdrawal_to_deposit_method": True,
    },
    "ontario": {
        "currency": "CAD",
        "methods": {
            "card": {
                "provider": "Worldpay",
                "fallback_provider": "Nuvei",
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 10000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 10000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "3-5 business days",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 3000.0,
            },
            "interac": {
                "provider": "iDebit",
                "fallback_provider": "InstaDebit",
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 10000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 10000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "24 hours",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 3000.0,
            },
            "e_wallet_paypal": {
                "provider": "PayPal",
                "fallback_provider": None,
                "enabled": True,
                "deposit_min": 10.0,
                "deposit_max": 5000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 5000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "24 hours",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 3000.0,
            },
        },
        "credit_cards_banned": False,
        "aml_threshold": 3000.0,
        "enhanced_dd_threshold": 10000.0,
        "withdrawal_to_deposit_method": True,
    },
    "brazil": {
        "currency": "BRL",
        "methods": {
            "pix": {
                "provider": "Pagamentos Brasil",
                "fallback_provider": "PicPay",
                "enabled": True,
                "deposit_min": 20.0,
                "deposit_max": 50000.0,
                "withdrawal_min": 20.0,
                "withdrawal_max": 50000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant (< 10 seconds)",
                "processing_time_withdrawal": "instant (< 10 seconds)",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 1000.0,
            },
            "bank_transfer": {
                "provider": "Banco do Brasil API",
                "fallback_provider": None,
                "enabled": True,
                "deposit_min": 50.0,
                "deposit_max": 100000.0,
                "withdrawal_min": 50.0,
                "withdrawal_max": 100000.0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.01,
                "processing_time_deposit": "1-2 business days",
                "processing_time_withdrawal": "1-3 business days",
                "supports_deposit": True,
                "supports_withdrawal": True,
                "kyc_required_above": 1000.0,
            },
            "card": {
                "provider": "Nuvei",
                "fallback_provider": "Adyen",
                "enabled": True,
                "deposit_min": 20.0,
                "deposit_max": 20000.0,
                "withdrawal_min": 0,
                "withdrawal_max": 0,
                "deposit_fee_pct": 0.0,
                "withdrawal_fee_pct": 0.0,
                "processing_time_deposit": "instant",
                "processing_time_withdrawal": "N/A",
                "supports_deposit": True,
                "supports_withdrawal": False,
                "kyc_required_above": 1000.0,
            },
        },
        "credit_cards_banned": True,  # Brazil banned credit cards for betting
        "aml_threshold": 1000.0,
        "enhanced_dd_threshold": 10000.0,
        "withdrawal_to_deposit_method": False,  # PIX is universal in Brazil
    },
}


@dataclass
class Transaction:
    """A single payment transaction."""
    transaction_id: str
    player_id: str
    type: str
    status: str
    amount: float
    currency: str
    market: str
    payment_method: str
    provider: str
    provider_reference: Optional[str]
    fee_amount: float
    net_amount: float
    created_at: str
    completed_at: Optional[str]
    failure_reason: Optional[str]
    aml_flagged: bool
    kyc_required: bool
    metadata: dict = field(default_factory=dict)


class PaymentRouter:
    """
    Routes payment transactions through the optimal payment provider
    based on market, method, and availability.
    """

    def __init__(self):
        self._transactions: dict = {}
        self._player_history: dict = defaultdict(list)
        self._provider_health: dict = defaultdict(lambda: {"healthy": True, "last_check": None})

    def process_deposit(
        self,
        player_id: str,
        amount: float,
        currency: str,
        market: str,
        method: str,
        card_token: Optional[str] = None,
    ) -> Transaction:
        """
        Process a deposit request.

        Validates the request, applies velocity checks, routes to the
        appropriate provider, and records the transaction.
        """
        config = MARKET_PAYMENT_CONFIG.get(market)
        if not config:
            raise ValueError(f"Unsupported market: {market}")

        if currency != config["currency"]:
            raise ValueError(f"Currency mismatch: expected {config['currency']}, got {currency}")

        # Find the payment method configuration
        method_config = config["methods"].get(method)  # ty:ignore[possibly-missing-attribute]
        if not method_config:
            available = [m for m, c in config["methods"].items() if c["supports_deposit"]]  # ty:ignore[possibly-missing-attribute]
            raise ValueError(f"Payment method '{method}' not available in {market}. "
                             f"Available: {available}")

        if not method_config["enabled"] or not method_config["supports_deposit"]:
            raise ValueError(f"Payment method '{method}' is not enabled for deposits in {market}")

        # Credit card ban check
        if config.get("credit_cards_banned") and method == "card":
            # In production, check if the card is credit vs debit via BIN lookup
            logger.warning("Credit card check required - market bans credit card gambling")

        # Amount validation
        if amount < method_config["deposit_min"]:  # ty:ignore[unsupported-operator]
            raise ValueError(f"Minimum deposit: {config['currency']} {method_config['deposit_min']}")
        if amount > method_config["deposit_max"]:  # ty:ignore[unsupported-operator]
            raise ValueError(f"Maximum deposit: {config['currency']} {method_config['deposit_max']}")

        # Velocity checks
        velocity_ok, velocity_msg = self._check_velocity(player_id, amount, "deposit")
        if not velocity_ok:
            raise ValueError(f"Velocity check failed: {velocity_msg}")

        # AML threshold check
        aml_flagged = amount >= config.get("aml_threshold", float("inf"))  # ty:ignore[unsupported-operator]
        kyc_required = amount >= method_config.get("kyc_required_above", float("inf"))  # ty:ignore[unsupported-operator]

        # Calculate fees
        fee = amount * method_config["deposit_fee_pct"]  # ty:ignore[unsupported-operator]
        net_amount = amount - fee

        # Select provider (with failover)
        provider = self._select_provider(method_config)

        # Create transaction
        txn_id = f"dep-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        txn = Transaction(
            transaction_id=txn_id,
            player_id=player_id,
            type=TransactionType.DEPOSIT.value,
            status=TransactionStatus.PROCESSING.value,
            amount=amount,
            currency=currency,
            market=market,
            payment_method=method,
            provider=provider,
            provider_reference=None,
            fee_amount=round(fee, 2),
            net_amount=round(net_amount, 2),
            created_at=now.isoformat(),
            completed_at=None,
            failure_reason=None,
            aml_flagged=aml_flagged,
            kyc_required=kyc_required,
            metadata={"card_token": card_token} if card_token else {},
        )

        # Simulate provider call
        success, provider_ref = self._call_provider(provider, txn)

        if success:
            txn.status = TransactionStatus.COMPLETED.value
            txn.provider_reference = provider_ref
            txn.completed_at = datetime.now(timezone.utc).isoformat()
            logger.info(f"Deposit completed: {txn_id} | {currency} {amount:.2f} | {provider}")
        else:
            # Try fallback provider
            fallback = method_config.get("fallback_provider")
            if fallback:
                logger.warning(f"Primary provider {provider} failed, trying {fallback}")
                success2, ref2 = self._call_provider(fallback, txn)  # ty:ignore[invalid-argument-type]
                if success2:
                    txn.status = TransactionStatus.COMPLETED.value
                    txn.provider = fallback  # ty:ignore[invalid-assignment]
                    txn.provider_reference = ref2
                    txn.completed_at = datetime.now(timezone.utc).isoformat()
                    logger.info(f"Deposit completed via fallback: {txn_id} | {fallback}")
                else:
                    txn.status = TransactionStatus.FAILED.value
                    txn.failure_reason = "All payment providers failed"
                    logger.error(f"Deposit failed: {txn_id} - all providers unavailable")
            else:
                txn.status = TransactionStatus.FAILED.value
                txn.failure_reason = f"Provider {provider} failed, no fallback configured"

        if aml_flagged:
            txn.status = TransactionStatus.ON_HOLD.value
            logger.warning(f"Transaction {txn_id} flagged for AML review (amount: {amount:.2f})")

        self._transactions[txn_id] = txn
        self._player_history[player_id].append(txn_id)

        return txn

    def process_withdrawal(
        self,
        player_id: str,
        amount: float,
        currency: str,
        market: str,
        method: Optional[str] = None,
    ) -> Transaction:
        """
        Process a withdrawal request.

        If no method specified, uses the same method as the last deposit
        (regulatory requirement in most jurisdictions).
        """
        config = MARKET_PAYMENT_CONFIG.get(market)
        if not config:
            raise ValueError(f"Unsupported market: {market}")

        # Determine withdrawal method
        if method is None and config.get("withdrawal_to_deposit_method"):
            method = self._get_last_deposit_method(player_id)
            if not method:
                raise ValueError("No deposit history found. Cannot determine withdrawal method.")
            logger.info(f"Using last deposit method for withdrawal: {method}")

        if method is None:
            # Use first available withdrawal method
            for m, mc in config["methods"].items():  # ty:ignore[possibly-missing-attribute]
                if mc["supports_withdrawal"] and mc["enabled"]:
                    method = m
                    break
            if method is None:
                raise ValueError(f"No withdrawal methods available in {market}")

        assert method is not None
        method_config = config["methods"].get(method)  # ty:ignore[possibly-missing-attribute]
        if not method_config or not method_config["supports_withdrawal"]:
            raise ValueError(f"Method '{method}' does not support withdrawals in {market}")

        # Amount validation
        if amount < method_config["withdrawal_min"]:  # ty:ignore[unsupported-operator]
            raise ValueError(f"Minimum withdrawal: {currency} {method_config['withdrawal_min']}")
        if 0 < method_config["withdrawal_max"] < amount:  # ty:ignore[unsupported-operator]
            raise ValueError(f"Maximum withdrawal: {currency} {method_config['withdrawal_max']}")

        # KYC check for larger amounts
        kyc_required = amount >= method_config.get("kyc_required_above", float("inf"))  # ty:ignore[unsupported-operator]
        aml_flagged = amount >= config.get("aml_threshold", float("inf"))  # ty:ignore[unsupported-operator]

        # Calculate fees
        fee = amount * method_config["withdrawal_fee_pct"]  # ty:ignore[unsupported-operator]
        net_amount = amount - fee

        provider = self._select_provider(method_config)

        txn_id = f"wdr-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        txn = Transaction(
            transaction_id=txn_id,
            player_id=player_id,
            type=TransactionType.WITHDRAWAL.value,
            status=TransactionStatus.PENDING.value if kyc_required else TransactionStatus.PROCESSING.value,
            amount=amount,
            currency=currency,
            market=market,
            payment_method=method,
            provider=provider,
            provider_reference=None,
            fee_amount=round(fee, 2),
            net_amount=round(net_amount, 2),
            created_at=now.isoformat(),
            completed_at=None,
            failure_reason=None,
            aml_flagged=aml_flagged,
            kyc_required=kyc_required,
        )

        if kyc_required:
            txn.status = TransactionStatus.AWAITING_KYC.value
            logger.info(f"Withdrawal {txn_id} awaiting KYC verification (amount: {amount:.2f})")
        elif aml_flagged:
            txn.status = TransactionStatus.ON_HOLD.value
            logger.warning(f"Withdrawal {txn_id} held for AML review")
        else:
            # Process immediately
            success, ref = self._call_provider(provider, txn)
            if success:
                txn.status = TransactionStatus.PROCESSING.value
                txn.provider_reference = ref
                logger.info(f"Withdrawal processing: {txn_id} | {currency} {amount:.2f} | {provider}")
                logger.info(f"  Estimated time: {method_config['processing_time_withdrawal']}")
            else:
                txn.status = TransactionStatus.FAILED.value
                txn.failure_reason = f"Provider {provider} rejected withdrawal"

        self._transactions[txn_id] = txn
        self._player_history[player_id].append(txn_id)

        return txn

    def get_available_methods(self, market: str, txn_type: str = "deposit") -> list:
        """Get available payment methods for a market."""
        config = MARKET_PAYMENT_CONFIG.get(market, {})
        methods = []

        for method_key, method_config in config.get("methods", {}).items():  # ty:ignore[possibly-missing-attribute]
            if not method_config["enabled"]:
                continue

            if txn_type == "deposit" and method_config["supports_deposit"]:
                methods.append({
                    "method": method_key,
                    "provider": method_config["provider"],
                    "min": method_config["deposit_min"],
                    "max": method_config["deposit_max"],
                    "fee_pct": method_config["deposit_fee_pct"],
                    "processing_time": method_config["processing_time_deposit"],
                })
            elif txn_type == "withdrawal" and method_config["supports_withdrawal"]:
                methods.append({
                    "method": method_key,
                    "provider": method_config["provider"],
                    "min": method_config["withdrawal_min"],
                    "max": method_config["withdrawal_max"],
                    "fee_pct": method_config["withdrawal_fee_pct"],
                    "processing_time": method_config["processing_time_withdrawal"],
                })

        return methods

    def get_transaction(self, txn_id: str) -> Optional[Transaction]:
        """Get a transaction by ID."""
        return self._transactions.get(txn_id)

    def get_player_transactions(self, player_id: str) -> list:
        """Get all transactions for a player."""
        txn_ids = self._player_history.get(player_id, [])
        return [self._transactions[tid] for tid in txn_ids if tid in self._transactions]

    # -----------------------------------------------------------------------
    # Private methods
    # -----------------------------------------------------------------------

    def _check_velocity(self, player_id: str, amount: float, txn_type: str) -> tuple:
        """
        Check transaction velocity limits.

        Prevents rapid-fire deposits/withdrawals that may indicate
        fraud or money laundering.
        """
        recent_txns = self.get_player_transactions(player_id)
        now = datetime.now(timezone.utc)

        # Last hour limits
        hour_ago = now - timedelta(hours=1)
        recent_count = 0
        recent_total = 0.0

        for txn in recent_txns:
            txn_time = datetime.fromisoformat(txn.created_at)
            if txn_time > hour_ago and txn.type == txn_type:
                recent_count += 1
                recent_total += txn.amount

        if recent_count >= 10:
            return False, f"Too many {txn_type}s in the last hour ({recent_count})"

        if recent_total + amount > 50000:
            return False, f"Hourly {txn_type} limit exceeded ({recent_total + amount:.2f})"

        return True, "OK"

    def _select_provider(self, method_config: dict) -> str:
        """Select the best available provider for a payment method."""
        primary = method_config["provider"]
        fallback = method_config.get("fallback_provider")

        health = self._provider_health[primary]
        if health["healthy"]:
            return primary

        if fallback:
            logger.warning(f"Primary provider {primary} unhealthy, using {fallback}")
            return fallback

        # Return primary anyway and let the call fail
        return primary

    def _call_provider(self, provider: str, txn: Transaction) -> tuple:
        """
        Call the payment provider API.

        In production, this would make HTTP calls to Worldpay/Adyen/PayPal etc.
        Returns (success, provider_reference).
        """
        # Simulated provider call
        ref = f"{provider.upper().replace(' ', '-')}-{uuid.uuid4().hex[:8].upper()}"
        logger.info(f"  Calling provider: {provider} for {txn.currency} {txn.amount:.2f}")

        # Simulate 95% success rate
        import random
        if random.random() < 0.95:
            return True, ref
        else:
            logger.warning(f"  Provider {provider} returned error (simulated)")
            return False, None

    def _get_last_deposit_method(self, player_id: str) -> Optional[str]:
        """Get the last payment method used for a deposit."""
        txns = self.get_player_transactions(player_id)
        deposits = [t for t in txns if t.type == TransactionType.DEPOSIT.value
                    and t.status == TransactionStatus.COMPLETED.value]
        if deposits:
            return deposits[-1].payment_method
        return None


def run_demo():
    """Run a demonstration of the payment router."""
    router = PaymentRouter()

    print("\n" + "=" * 70)
    print("  PAYMENT ROUTER DEMO")
    print("=" * 70)

    # Demo 1: List available methods
    for market in ["uk", "brazil", "ontario"]:
        print(f"\n--- Available Deposit Methods: {market.upper()} ---")
        methods = router.get_available_methods(market, "deposit")
        for m in methods:
            print(f"  {m['method']:<25} {m['provider']:<20} "
                  f"Min: {m['min']:<10} Max: {m['max']:<10} {m['processing_time']}")

    # Demo 2: UK deposit via card
    print("\n--- Demo: UK Card Deposit ---")
    txn1 = router.process_deposit("player-001", 100.00, "GBP", "uk", "card",
                                  card_token="tok_visa_debit_xxx")
    print(f"  Transaction: {txn1.transaction_id}")
    print(f"  Status: {txn1.status}")
    print(f"  Provider: {txn1.provider} (ref: {txn1.provider_reference})")

    # Demo 3: Brazil PIX deposit
    print("\n--- Demo: Brazil PIX Deposit ---")
    txn2 = router.process_deposit("player-002", 500.00, "BRL", "brazil", "pix")
    print(f"  Transaction: {txn2.transaction_id}")
    print(f"  Status: {txn2.status}")
    print(f"  Provider: {txn2.provider}")

    # Demo 4: UK withdrawal (auto-selects card since last deposit was card)
    print("\n--- Demo: UK Withdrawal (auto-method selection) ---")
    txn3 = router.process_withdrawal("player-001", 50.00, "GBP", "uk")
    print(f"  Transaction: {txn3.transaction_id}")
    print(f"  Status: {txn3.status}")
    print(f"  Method: {txn3.payment_method} (matched to deposit method)")

    # Demo 5: Large deposit triggering AML hold
    print("\n--- Demo: Large Deposit (AML Threshold) ---")
    txn4 = router.process_deposit("player-003", 3000.00, "GBP", "uk", "open_banking")
    print(f"  Transaction: {txn4.transaction_id}")
    print(f"  Status: {txn4.status}")
    print(f"  AML Flagged: {txn4.aml_flagged}")

    # Demo 6: Transaction history
    print("\n--- Demo: Player Transaction History ---")
    history = router.get_player_transactions("player-001")
    for h in history:
        print(f"  {h.transaction_id} | {h.type:<10} | {h.currency} {h.amount:>10.2f} | {h.status}")

    print("\n" + "=" * 70)


def main():
    parser = argparse.ArgumentParser(description="Payment Router Demo")
    parser.add_argument("--demo", action="store_true", help="Run demo")
    parser.add_argument("--list-markets", action="store_true", help="List supported markets")

    args = parser.parse_args()

    if args.list_markets:
        print("\nSupported Markets:")
        for market, config in MARKET_PAYMENT_CONFIG.items():
            methods = len(config["methods"])  # ty:ignore[invalid-argument-type]
            print(f"  {market:<12} {config['currency']:<5} {methods} payment methods")
            for m, mc in config["methods"].items():  # ty:ignore[possibly-missing-attribute]
                dep = "D" if mc["supports_deposit"] else "-"
                wdr = "W" if mc["supports_withdrawal"] else "-"
                print(f"    [{dep}{wdr}] {m:<25} via {mc['provider']}")
        print()
        return

    run_demo()


if __name__ == "__main__":
    main()
