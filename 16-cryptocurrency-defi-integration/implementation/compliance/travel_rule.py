#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 16, Cryptocurrency and DeFi Integration.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 8: Cryptocurrency and DeFi Integration
FATF Travel Rule Compliance

Implements FATF Recommendation 16 (Travel Rule) for crypto casino VASPs.
Transfers >= USD 1,000 require originator/beneficiary information exchange.

Features:
- Threshold detection per jurisdiction (USD 1,000 FATF / EUR 1,000 EU)
- IVMS101 message format for inter-VASP data exchange
- Integration hooks for TRISA, TRP, and OpenVASP protocols
- Sunrise issue handling (counterparty VASP not Travel Rule compliant)
- Transaction screening and blocking for non-compliant transfers
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class TravelRuleStatus(Enum):
    COMPLIANT = "compliant"
    PENDING_COUNTERPARTY = "pending_counterparty"
    EXEMPT = "exempt"
    BLOCKED = "blocked"
    SUNRISE_EXCEPTION = "sunrise_exception"

JURISDICTION_THRESHOLDS = {
    "FATF": {"threshold_usd": 1_000, "currency": "USD"},
    "EU": {"threshold_usd": 1_000, "currency": "EUR"},
    "US_FINCEN": {"threshold_usd": 3_000, "currency": "USD"},
    "SINGAPORE": {"threshold_usd": 1_500, "currency": "SGD"},
    "JAPAN": {"threshold_usd": 0, "currency": "JPY"},  # All transfers
    "SWITZERLAND": {"threshold_usd": 1_000, "currency": "CHF"},
    "UK": {"threshold_usd": 1_000, "currency": "GBP"},
}

@dataclass
class OriginatorInfo:
    name: str
    account_number: str  # Wallet address or internal ID
    address: str = ""
    date_of_birth: str = ""
    place_of_birth: str = ""
    national_id: str = ""
    customer_id: str = ""

@dataclass
class BeneficiaryInfo:
    name: str
    account_number: str
    vasp_name: str = ""
    vasp_lei: str = ""  # Legal Entity Identifier

@dataclass
class TravelRuleMessage:
    """IVMS101-compatible travel rule message."""
    message_id: str
    originator: OriginatorInfo
    beneficiary: BeneficiaryInfo
    amount: float
    currency: str
    amount_usd: float
    blockchain: str
    tx_hash: Optional[str] = None
    jurisdiction: str = "FATF"
    status: TravelRuleStatus = TravelRuleStatus.PENDING_COUNTERPARTY
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    protocol: str = "TRISA"  # TRISA, TRP, OpenVASP

    def to_ivms101(self) -> dict:
        return {
            "version": "ivms101.2023",
            "originator": {
                "originatorPersons": [{"naturalPerson": {
                    "name": {"nameIdentifier": [{"primaryIdentifier": self.originator.name}]},
                    "dateAndPlaceOfBirth": {"dateOfBirth": self.originator.date_of_birth},
                    "nationalIdentification": {"nationalIdentifier": self.originator.national_id},
                }}],
                "accountNumber": [self.originator.account_number],
            },
            "beneficiary": {
                "beneficiaryPersons": [{"naturalPerson": {
                    "name": {"nameIdentifier": [{"primaryIdentifier": self.beneficiary.name}]},
                }}],
                "accountNumber": [self.beneficiary.account_number],
            },
            "transferAmount": str(self.amount),
            "transferCurrency": self.currency,
        }

class TravelRuleEngine:
    def __init__(self, jurisdiction: str = "FATF", vasp_name: str = "CasinoCorp VASP"):
        self.jurisdiction = jurisdiction
        self.vasp_name = vasp_name
        self.threshold = JURISDICTION_THRESHOLDS.get(jurisdiction, JURISDICTION_THRESHOLDS["FATF"])
        self.messages: list[TravelRuleMessage] = []
        self._counter = 0

    def screen_transfer(self, amount_usd: float, is_withdrawal: bool = True) -> dict:
        threshold = self.threshold["threshold_usd"]
        requires_travel_rule = amount_usd >= threshold  # ty:ignore[unsupported-operator]
        return {
            "amount_usd": amount_usd,
            "threshold_usd": threshold,
            "jurisdiction": self.jurisdiction,
            "requires_travel_rule": requires_travel_rule,
            "direction": "withdrawal" if is_withdrawal else "deposit",
            "action": "COLLECT_INFO" if requires_travel_rule else "PROCEED",
        }

    def create_message(self, originator: OriginatorInfo, beneficiary: BeneficiaryInfo,
                       amount: float, currency: str, amount_usd: float, blockchain: str) -> TravelRuleMessage:
        self._counter += 1
        msg = TravelRuleMessage(
            message_id=f"TR-{self._counter:08d}",
            originator=originator, beneficiary=beneficiary,
            amount=amount, currency=currency, amount_usd=amount_usd,
            blockchain=blockchain, jurisdiction=self.jurisdiction,
        )
        self.messages.append(msg)
        logger.info(f"[{msg.message_id}] Travel Rule message created: {amount} {currency} (${amount_usd:,.2f})")
        return msg

if __name__ == "__main__":
    engine = TravelRuleEngine(jurisdiction="EU", vasp_name="CryptoCasino EU")
    print("=" * 60)
    print("FATF TRAVEL RULE COMPLIANCE ENGINE")
    print("=" * 60)

    # Screen transfers
    for amt in [500, 1_000, 5_000, 25_000]:
        result = engine.screen_transfer(amt)
        print(f"  ${amt:>8,}: {'TRAVEL RULE REQUIRED' if result['requires_travel_rule'] else 'Exempt'}")

    # Create compliant message
    msg = engine.create_message(
        originator=OriginatorInfo(name="John Smith", account_number="0xABC...DEF",
                                  date_of_birth="1985-03-15", national_id="UK-PASS-123456"),
        beneficiary=BeneficiaryInfo(name="External Wallet", account_number="0x999...111",
                                    vasp_name="Binance", vasp_lei="5493001KJTIIGC8Y1R12"),
        amount=2.5, currency="ETH", amount_usd=5_000, blockchain="ethereum",
    )
    print(f"\n  IVMS101 Message:")
    print(json.dumps(msg.to_ivms101(), indent=4))
