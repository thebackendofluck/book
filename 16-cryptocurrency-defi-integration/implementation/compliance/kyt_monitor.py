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
Know Your Transaction (KYT) Blockchain Analysis

Monitors and scores blockchain transactions for AML/CFT compliance:
- Risk scoring per transaction (mixer detection, darknet, sanctions)
- Address clustering and entity resolution
- Real-time deposit screening before crediting player balance
- Integration hooks for Chainalysis, Elliptic, Crystal
- OFAC/EU sanctions list checking
- Suspicious activity report (SAR) generation
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SEVERE = "severe"
    BLOCKED = "blocked"

class RiskCategory(Enum):
    CLEAN = "clean"
    MIXER = "mixer"
    DARKNET = "darknet_market"
    RANSOMWARE = "ransomware"
    SANCTIONS = "sanctions"
    SCAM = "scam"
    GAMBLING = "gambling"
    EXCHANGE = "exchange"
    DEFI = "defi"
    P2P = "peer_to_peer"
    ATM = "crypto_atm"
    UNKNOWN = "unknown"

RISK_SCORES = {
    RiskCategory.CLEAN: 0, RiskCategory.EXCHANGE: 5, RiskCategory.DEFI: 10,
    RiskCategory.GAMBLING: 15, RiskCategory.P2P: 20, RiskCategory.ATM: 25,
    RiskCategory.UNKNOWN: 40, RiskCategory.MIXER: 70, RiskCategory.SCAM: 80,
    RiskCategory.DARKNET: 90, RiskCategory.RANSOMWARE: 95, RiskCategory.SANCTIONS: 100,
}

RISK_THRESHOLDS = {"low": 20, "medium": 40, "high": 70, "severe": 85, "blocked": 90}

@dataclass
class TransactionAnalysis:
    tx_hash: str
    address: str
    amount: float
    currency: str
    risk_score: int = 0
    risk_level: RiskLevel = RiskLevel.LOW
    categories: list[RiskCategory] = field(default_factory=list)
    source_of_funds: list[dict] = field(default_factory=list)
    action: str = "ACCEPT"  # ACCEPT, REVIEW, HOLD, BLOCK
    flags: list[str] = field(default_factory=list)
    analyzed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class KYTMonitor:
    """Blockchain transaction analysis engine for crypto casino compliance."""

    # Simulated known-risk addresses for demo
    KNOWN_ADDRESSES = {
        "0xMIXER001": RiskCategory.MIXER,
        "0xDARKNET01": RiskCategory.DARKNET,
        "0xSANCTION1": RiskCategory.SANCTIONS,
        "0xSCAM0001": RiskCategory.SCAM,
        "0xBINANCE1": RiskCategory.EXCHANGE,
        "0xUNISWAP1": RiskCategory.DEFI,
    }

    def __init__(self, auto_block_threshold: int = 85):
        self.auto_block_threshold = auto_block_threshold
        self.analyses: list[TransactionAnalysis] = []

    def analyze_transaction(self, tx_hash: str, from_address: str,
                            amount: float, currency: str) -> TransactionAnalysis:
        categories = []
        flags = []
        score = 0

        # Check known addresses
        addr_upper = from_address.upper()
        for known, cat in self.KNOWN_ADDRESSES.items():
            if known.upper() in addr_upper:
                categories.append(cat)
                score = max(score, RISK_SCORES[cat])

        if not categories:
            categories.append(RiskCategory.UNKNOWN)
            score = RISK_SCORES[RiskCategory.UNKNOWN]

        # Amount-based flags
        if amount > 10 and currency in ("ETH", "BTC"):
            flags.append("HIGH_VALUE_DEPOSIT")
            score = min(100, score + 10)
        if amount == round(amount):
            flags.append("ROUND_AMOUNT")

        # Determine risk level and action
        if score >= RISK_THRESHOLDS["blocked"]:
            risk_level, action = RiskLevel.BLOCKED, "BLOCK"
        elif score >= RISK_THRESHOLDS["severe"]:
            risk_level, action = RiskLevel.SEVERE, "HOLD"
        elif score >= RISK_THRESHOLDS["high"]:
            risk_level, action = RiskLevel.HIGH, "REVIEW"
        elif score >= RISK_THRESHOLDS["medium"]:
            risk_level, action = RiskLevel.MEDIUM, "ACCEPT"
        else:
            risk_level, action = RiskLevel.LOW, "ACCEPT"

        analysis = TransactionAnalysis(
            tx_hash=tx_hash, address=from_address, amount=amount,
            currency=currency, risk_score=score, risk_level=risk_level,
            categories=categories, action=action, flags=flags,
        )
        self.analyses.append(analysis)
        logger.info(f"KYT [{tx_hash[:16]}]: score={score} level={risk_level.value} action={action}")
        return analysis

    def generate_sar(self, analysis: TransactionAnalysis) -> dict:
        """Generate Suspicious Activity Report data."""
        return {
            "report_type": "SAR",
            "filing_date": datetime.now(timezone.utc).isoformat(),
            "transaction": {"hash": analysis.tx_hash, "amount": analysis.amount,
                           "currency": analysis.currency, "address": analysis.address},
            "risk_assessment": {"score": analysis.risk_score, "level": analysis.risk_level.value,
                               "categories": [c.value for c in analysis.categories]},
            "flags": analysis.flags,
            "recommendation": analysis.action,
        }

if __name__ == "__main__":
    monitor = KYTMonitor()
    print("=" * 60)
    print("KYT BLOCKCHAIN ANALYSIS - Crypto Casino")
    print("=" * 60)

    test_txs = [
        ("0xTX_CLEAN_001", "0xNORMAL_WALLET", 0.5, "ETH"),
        ("0xTX_MIXER_001", "0xMIXER001_abc", 2.0, "ETH"),
        ("0xTX_DARK_001", "0xDARKNET01_xyz", 1.5, "BTC"),
        ("0xTX_SANCT_001", "0xSANCTION1_aaa", 5.0, "ETH"),
        ("0xTX_EXCH_001", "0xBINANCE1_user", 10.0, "ETH"),
    ]
    for tx_hash, addr, amt, curr in test_txs:
        result = monitor.analyze_transaction(tx_hash, addr, amt, curr)
        print(f"  {addr[:20]:>20s}: score={result.risk_score:>3} | "
              f"{result.risk_level.value:>8s} | {result.action}")
        if result.risk_score >= 70:
            sar = monitor.generate_sar(result)
            print(f"    -> SAR generated: {json.dumps(sar['risk_assessment'])}")
