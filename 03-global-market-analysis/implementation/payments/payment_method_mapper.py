#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 03, Global Market Analysis.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 21: Global Market Analysis
Payment Method Availability & Preference per Market

Maps payment method availability, popularity, and integration requirements
per gambling jurisdiction:
- Payment method popularity ranking per country
- Integration complexity and cost estimation
- PSP (Payment Service Provider) recommendations
- Regulatory restrictions on payment methods for gambling
- Mobile payment and crypto adoption tracking
"""

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class PaymentCategory(Enum):
    CARD = "card"
    BANK_TRANSFER = "bank_transfer"
    E_WALLET = "e_wallet"
    MOBILE = "mobile_payment"
    CRYPTO = "cryptocurrency"
    PREPAID = "prepaid"
    LOCAL = "local_method"

@dataclass
class PaymentMethod:
    name: str
    category: PaymentCategory
    popularity_pct: float       # % of deposits using this method
    avg_deposit_usd: float
    processing_fee_pct: float
    settlement_days: float
    gambling_restricted: bool = False
    integration_complexity: str = "medium"  # low, medium, high
    notes: str = ""

MARKET_PAYMENTS = {
    "uk": {
        "currency": "GBP",
        "credit_card_banned": True,
        "methods": [
            PaymentMethod("Visa Debit", PaymentCategory.CARD, 35, 50, 1.5, 0, notes="Credit cards banned for gambling since 2020"),
            PaymentMethod("PayPal", PaymentCategory.E_WALLET, 20, 40, 2.9, 0),
            PaymentMethod("Apple Pay", PaymentCategory.MOBILE, 12, 35, 1.8, 0),
            PaymentMethod("Paysafecard", PaymentCategory.PREPAID, 8, 25, 3.0, 0),
            PaymentMethod("Skrill", PaymentCategory.E_WALLET, 7, 60, 2.5, 0),
            PaymentMethod("Neteller", PaymentCategory.E_WALLET, 5, 80, 2.5, 0),
            PaymentMethod("Bank Transfer", PaymentCategory.BANK_TRANSFER, 5, 200, 0.5, 1),
            PaymentMethod("Trustly", PaymentCategory.BANK_TRANSFER, 4, 100, 1.0, 0),
            PaymentMethod("Crypto (BTC/ETH)", PaymentCategory.CRYPTO, 2, 150, 1.0, 0, notes="Growing but niche"),
            PaymentMethod("Google Pay", PaymentCategory.MOBILE, 2, 30, 1.8, 0),
        ],
    },
    "brazil": {
        "currency": "BRL",
        "credit_card_banned": False,
        "methods": [
            PaymentMethod("PIX", PaymentCategory.LOCAL, 65, 30, 0.0, 0, integration_complexity="medium", notes="Instant, free, 24/7. Dominant in Brazil"),
            PaymentMethod("Boleto Bancario", PaymentCategory.LOCAL, 10, 50, 1.5, 1, notes="Cash-based, older demographic"),
            PaymentMethod("Credit Card", PaymentCategory.CARD, 8, 80, 3.5, 0, notes="Installments common"),
            PaymentMethod("Bank Transfer (TED)", PaymentCategory.BANK_TRANSFER, 5, 200, 0.5, 0),
            PaymentMethod("PicPay", PaymentCategory.MOBILE, 4, 25, 2.0, 0),
            PaymentMethod("Mercado Pago", PaymentCategory.E_WALLET, 3, 40, 2.5, 0),
            PaymentMethod("Crypto (USDT)", PaymentCategory.CRYPTO, 3, 100, 1.0, 0, notes="Growing fast"),
            PaymentMethod("Astropay", PaymentCategory.PREPAID, 2, 30, 3.0, 0),
        ],
    },
    "india": {
        "currency": "INR",
        "credit_card_banned": False,
        "methods": [
            PaymentMethod("UPI (GPay/PhonePe)", PaymentCategory.MOBILE, 45, 15, 0.0, 0, notes="Dominant, instant, free"),
            PaymentMethod("Paytm", PaymentCategory.E_WALLET, 15, 12, 1.5, 0),
            PaymentMethod("Net Banking", PaymentCategory.BANK_TRANSFER, 12, 50, 1.0, 0),
            PaymentMethod("Debit Card", PaymentCategory.CARD, 10, 30, 2.0, 0, gambling_restricted=True, notes="Many banks block gambling txns"),
            PaymentMethod("Crypto (USDT)", PaymentCategory.CRYPTO, 8, 80, 1.0, 0, notes="Used to bypass banking restrictions"),
            PaymentMethod("AstroPay", PaymentCategory.PREPAID, 5, 20, 2.5, 0),
            PaymentMethod("Cash deposit", PaymentCategory.LOCAL, 5, 10, 3.0, 0),
        ],
    },
    "nigeria": {
        "currency": "NGN",
        "credit_card_banned": False,
        "methods": [
            PaymentMethod("Bank Transfer", PaymentCategory.BANK_TRANSFER, 30, 15, 1.0, 0),
            PaymentMethod("USSD Banking", PaymentCategory.MOBILE, 25, 8, 0.5, 0, notes="Works without internet"),
            PaymentMethod("Opay", PaymentCategory.MOBILE, 15, 10, 1.5, 0),
            PaymentMethod("Debit Card", PaymentCategory.CARD, 12, 20, 2.5, 0),
            PaymentMethod("Flutterwave", PaymentCategory.LOCAL, 8, 15, 1.5, 0),
            PaymentMethod("Paystack", PaymentCategory.LOCAL, 5, 12, 1.5, 0),
            PaymentMethod("Crypto", PaymentCategory.CRYPTO, 3, 50, 1.0, 0, notes="P2P dominant after CBN ban"),
            PaymentMethod("Agent banking", PaymentCategory.LOCAL, 2, 5, 2.0, 0),
        ],
    },
    "germany": {
        "currency": "EUR",
        "credit_card_banned": False,
        "methods": [
            PaymentMethod("PayPal", PaymentCategory.E_WALLET, 25, 50, 2.9, 0),
            PaymentMethod("Klarna/Sofort", PaymentCategory.BANK_TRANSFER, 20, 80, 1.5, 0),
            PaymentMethod("Giropay", PaymentCategory.BANK_TRANSFER, 15, 60, 1.0, 0),
            PaymentMethod("Visa/Mastercard", PaymentCategory.CARD, 12, 50, 2.0, 0),
            PaymentMethod("Paysafecard", PaymentCategory.PREPAID, 10, 25, 3.0, 0),
            PaymentMethod("Trustly", PaymentCategory.BANK_TRANSFER, 8, 100, 1.0, 0),
            PaymentMethod("Apple Pay", PaymentCategory.MOBILE, 5, 40, 1.8, 0),
            PaymentMethod("Skrill/Neteller", PaymentCategory.E_WALLET, 5, 70, 2.5, 0),
        ],
    },
}

class PaymentMethodMapper:
    """Maps payment method availability and recommends integration priorities."""

    def get_market_payments(self, market: str) -> dict:
        if market not in MARKET_PAYMENTS:
            return {"error": f"No payment data for {market}"}

        data = MARKET_PAYMENTS[market]
        methods = data["methods"]

        # Priority tiers
        tier_1 = [m for m in methods if m.popularity_pct >= 15]  # ty:ignore[not-iterable, possibly-missing-attribute]
        tier_2 = [m for m in methods if 5 <= m.popularity_pct < 15]  # ty:ignore[not-iterable, possibly-missing-attribute]
        tier_3 = [m for m in methods if m.popularity_pct < 5]  # ty:ignore[not-iterable, possibly-missing-attribute]

        # Cost analysis
        weighted_fee = sum(m.processing_fee_pct * m.popularity_pct / 100 for m in methods)  # ty:ignore[not-iterable, possibly-missing-attribute]

        return {
            "market": market,
            "currency": data["currency"],
            "credit_card_banned": data["credit_card_banned"],
            "total_methods": len(methods),  # ty:ignore[invalid-argument-type]
            "weighted_avg_fee_pct": round(weighted_fee, 2),
            "tier_1_must_have": [
                {"name": m.name, "share_pct": m.popularity_pct, "category": m.category.value,  # ty:ignore[possibly-missing-attribute]
                 "fee_pct": m.processing_fee_pct, "notes": m.notes}  # ty:ignore[possibly-missing-attribute]
                for m in tier_1
            ],
            "tier_2_recommended": [
                {"name": m.name, "share_pct": m.popularity_pct, "category": m.category.value}  # ty:ignore[possibly-missing-attribute]
                for m in tier_2
            ],
            "tier_3_nice_to_have": [m.name for m in tier_3],  # ty:ignore[possibly-missing-attribute]
            "gambling_restrictions": [
                {"method": m.name, "notes": m.notes}  # ty:ignore[possibly-missing-attribute]
                for m in methods if m.gambling_restricted  # ty:ignore[not-iterable, possibly-missing-attribute]
            ],
            "integration_estimate": {
                "tier_1_weeks": len(tier_1) * 2,
                "tier_2_weeks": len(tier_2) * 1.5,
                "total_weeks": len(tier_1) * 2 + len(tier_2) * 1.5,
            },
        }

    def compare_markets(self, markets: list[str]) -> dict:
        results = {}
        for market in markets:
            data = self.get_market_payments(market)
            if "error" not in data:
                results[market] = {
                    "currency": data["currency"],
                    "methods": data["total_methods"],
                    "avg_fee": data["weighted_avg_fee_pct"],
                    "top_method": data["tier_1_must_have"][0]["name"] if data["tier_1_must_have"] else "N/A",
                    "top_share": data["tier_1_must_have"][0]["share_pct"] if data["tier_1_must_have"] else 0,
                }
        return results

if __name__ == "__main__":
    mapper = PaymentMethodMapper()
    print("=" * 70)
    print("PAYMENT METHOD MAPPER - iGaming Markets")
    print("=" * 70)

    for market in ["uk", "brazil", "india", "nigeria", "germany"]:
        data = mapper.get_market_payments(market)
        print(f"\n--- {market.upper()} ({data['currency']}) ---")
        print(f"  Avg processing fee: {data['weighted_avg_fee_pct']:.2f}%")
        if data["credit_card_banned"]:
            print(f"  *** Credit cards BANNED for gambling ***")
        print(f"  Must-have (Tier 1):")
        for m in data["tier_1_must_have"]:
            note = f" - {m['notes']}" if m["notes"] else ""
            print(f"    {m['name']:>25}: {m['share_pct']:>5.1f}% | fee: {m['fee_pct']:.1f}%{note}")
        print(f"  Integration estimate: ~{data['integration_estimate']['total_weeks']:.0f} weeks")

    # Comparison
    print(f"\n{'=' * 70}")
    print("MARKET COMPARISON")
    print(f"{'=' * 70}")
    comparison = mapper.compare_markets(["uk", "brazil", "india", "nigeria", "germany"])
    print(f"{'Market':>10} {'Currency':>8} {'Methods':>8} {'Avg Fee':>8} {'Top Method':>20} {'Share':>6}")
    for market, data in comparison.items():
        print(f"{market:>10} {data['currency']:>8} {data['methods']:>8} {data['avg_fee']:>7.2f}% {data['top_method']:>20} {data['top_share']:>5.1f}%")
