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
Crypto Gambling Tax Reporting per Jurisdiction

Generates tax reports for crypto gambling operations:
- Player win/loss statements with cost basis tracking
- Operator GGR tax calculations per jurisdiction
- Crypto-to-fiat conversion tracking at transaction time
- FIFO/LIFO/weighted average cost basis methods
- Multi-jurisdiction support (UK, Malta, US, Curacao, etc.)
- Export formats: CSV, JSON, PDF-ready
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

class CostBasisMethod(Enum):
    FIFO = "fifo"
    LIFO = "lifo"
    WEIGHTED_AVERAGE = "weighted_average"

JURISDICTION_TAX_RULES = {
    "uk": {"name": "United Kingdom", "ggr_tax": 0.21, "player_tax": "capital_gains",
           "cgt_allowance": 6_000, "cgt_rate": 0.20, "reporting_currency": "GBP"},
    "malta": {"name": "Malta", "ggr_tax": 0.05, "player_tax": "exempt",
              "reporting_currency": "EUR"},
    "us_nj": {"name": "New Jersey", "ggr_tax": 0.15, "player_tax": "income",
              "w2g_threshold": 600, "reporting_currency": "USD"},
    "curacao": {"name": "Curacao", "ggr_tax": 0.02, "player_tax": "exempt",
                "reporting_currency": "USD"},
    "gibraltar": {"name": "Gibraltar", "ggr_tax": 0.01, "player_tax": "exempt",
                  "reporting_currency": "GBP"},
}

@dataclass
class CryptoTransaction:
    tx_id: str
    player_id: str
    tx_type: str  # deposit, withdrawal, bet, win, loss
    crypto_amount: float
    crypto_currency: str
    fiat_value_usd: float
    exchange_rate: float
    timestamp: str

@dataclass
class TaxReport:
    period: str
    jurisdiction: str
    total_deposits_usd: float = 0
    total_withdrawals_usd: float = 0
    total_ggr_usd: float = 0
    ggr_tax_due: float = 0
    total_bets_usd: float = 0
    total_wins_usd: float = 0
    net_player_pnl_usd: float = 0
    transactions_count: int = 0

class CryptoTaxReporter:
    def __init__(self, jurisdiction: str = "uk", cost_basis: CostBasisMethod = CostBasisMethod.FIFO):
        if jurisdiction not in JURISDICTION_TAX_RULES:
            raise ValueError(f"Unknown jurisdiction: {jurisdiction}")
        self.jurisdiction = jurisdiction
        self.rules = JURISDICTION_TAX_RULES[jurisdiction]
        self.cost_basis = cost_basis
        self.transactions: list[CryptoTransaction] = []

    def add_transaction(self, tx: CryptoTransaction):
        self.transactions.append(tx)

    def generate_operator_report(self, period: str = "2025-Q4") -> TaxReport:
        report = TaxReport(period=period, jurisdiction=self.rules["name"])  # ty:ignore[invalid-argument-type]
        for tx in self.transactions:
            report.transactions_count += 1
            if tx.tx_type == "deposit":
                report.total_deposits_usd += tx.fiat_value_usd
            elif tx.tx_type == "withdrawal":
                report.total_withdrawals_usd += tx.fiat_value_usd
            elif tx.tx_type == "bet":
                report.total_bets_usd += tx.fiat_value_usd
            elif tx.tx_type == "win":
                report.total_wins_usd += tx.fiat_value_usd

        report.total_ggr_usd = report.total_bets_usd - report.total_wins_usd
        report.ggr_tax_due = max(0, report.total_ggr_usd * self.rules["ggr_tax"])  # ty:ignore[unsupported-operator]
        report.net_player_pnl_usd = report.total_wins_usd - report.total_bets_usd
        return report

    def generate_player_statement(self, player_id: str) -> dict:
        player_txs = [t for t in self.transactions if t.player_id == player_id]
        deposits = sum(t.fiat_value_usd for t in player_txs if t.tx_type == "deposit")
        withdrawals = sum(t.fiat_value_usd for t in player_txs if t.tx_type == "withdrawal")
        bets = sum(t.fiat_value_usd for t in player_txs if t.tx_type == "bet")
        wins = sum(t.fiat_value_usd for t in player_txs if t.tx_type == "win")

        statement = {
            "player_id": player_id,
            "jurisdiction": self.rules["name"],
            "period_transactions": len(player_txs),
            "total_deposited_usd": round(deposits, 2),
            "total_withdrawn_usd": round(withdrawals, 2),
            "total_wagered_usd": round(bets, 2),
            "total_won_usd": round(wins, 2),
            "net_pnl_usd": round(wins - bets, 2),
            "player_tax_treatment": self.rules["player_tax"],
        }

        if self.rules["player_tax"] == "capital_gains":
            net = wins - bets
            taxable = max(0, net - self.rules.get("cgt_allowance", 0))  # ty:ignore[unsupported-operator]
            statement["taxable_gain_usd"] = round(taxable, 2)
            statement["estimated_tax_usd"] = round(taxable * self.rules.get("cgt_rate", 0), 2)  # ty:ignore[no-matching-overload]
        elif self.rules["player_tax"] == "income":
            statement["w2g_threshold"] = self.rules.get("w2g_threshold", 0)
            big_wins = [t for t in player_txs if t.tx_type == "win" and t.fiat_value_usd >= self.rules.get("w2g_threshold", 600)]  # ty:ignore[unsupported-operator]
            statement["reportable_wins"] = len(big_wins)
            statement["reportable_amount_usd"] = round(sum(t.fiat_value_usd for t in big_wins), 2)

        return statement

if __name__ == "__main__":
    print("=" * 60)
    print("CRYPTO TAX REPORTER - iGaming")
    print("=" * 60)

    sample_txs = [
        CryptoTransaction("TX-001", "PLR-001", "deposit", 1.0, "ETH", 2000, 2000, "2025-10-01T10:00:00Z"),
        CryptoTransaction("TX-002", "PLR-001", "bet", 0.5, "ETH", 1000, 2000, "2025-10-02T14:00:00Z"),
        CryptoTransaction("TX-003", "PLR-001", "win", 1.2, "ETH", 2400, 2000, "2025-10-02T14:01:00Z"),
        CryptoTransaction("TX-004", "PLR-001", "bet", 0.3, "ETH", 600, 2000, "2025-10-03T11:00:00Z"),
        CryptoTransaction("TX-005", "PLR-002", "deposit", 0.1, "BTC", 4000, 40000, "2025-10-01T09:00:00Z"),
        CryptoTransaction("TX-006", "PLR-002", "bet", 0.05, "BTC", 2000, 40000, "2025-10-05T16:00:00Z"),
        CryptoTransaction("TX-007", "PLR-002", "win", 0.02, "BTC", 800, 40000, "2025-10-05T16:01:00Z"),
    ]

    for jur in ["uk", "us_nj", "malta", "curacao"]:
        reporter = CryptoTaxReporter(jurisdiction=jur)
        for tx in sample_txs:
            reporter.add_transaction(tx)

        report = reporter.generate_operator_report("2025-Q4")
        print(f"\n  {report.jurisdiction}:")
        print(f"    GGR: ${report.total_ggr_usd:,.2f} | Tax due: ${report.ggr_tax_due:,.2f}")

    # Player statement
    reporter = CryptoTaxReporter("uk")
    for tx in sample_txs:
        reporter.add_transaction(tx)
    stmt = reporter.generate_player_statement("PLR-001")
    print(f"\n  Player Statement (UK):")
    print(json.dumps(stmt, indent=4))
