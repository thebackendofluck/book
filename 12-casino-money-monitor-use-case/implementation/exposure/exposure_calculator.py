#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 12, Real-Time Cash Flow Management for Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Casino Money Monitor - Exposure Calculation Engine
====================================================
Chapter 5 Implementation: Checklist Item #3

Calculates the casino's total financial exposure from:
- Open (unsettled) bets across all verticals (sports, casino, poker, etc.)
- Pending withdrawal requests in processing pipeline
- Jackpot liabilities (progressive and fixed)
- Bonus liabilities (wagering requirements not yet met)
- Tournament guarantees and overlay risk
- Payment reversals and chargeback reserves

The exposure number is critical for the cash dashboard: it determines
how much liquidity the operator must maintain at all times.

PCI DSS Compliance Notes:
- Requirement 10.2: All exposure calculations are audit-logged
- Requirement 7.1: Role-based access to financial data
- Amounts never include card numbers (Req 3.4)

Dependencies:
    pip install sqlalchemy asyncpg pydantic redis
"""

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field

logger = logging.getLogger("exposure_calculator")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPORTING_CURRENCY = "EUR"

# Exposure calculation runs on this interval (seconds)
CALCULATION_INTERVAL = 60

# Reserve multipliers for regulatory compliance
# MGA Technical Standard: operator must hold >= 100% of player funds
# UKGC LCCP 3.2.2: segregated player funds must cover all liabilities
RESERVE_MULTIPLIER_DEFAULT = Decimal("1.0")
RESERVE_MULTIPLIER_UK = Decimal("1.0")       # UKGC - 100% segregation
RESERVE_MULTIPLIER_MALTA = Decimal("1.0")     # MGA - full coverage
RESERVE_MULTIPLIER_CURACAO = Decimal("0.8")   # less strict

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

class ExposureCategory(str, Enum):
    OPEN_BETS = "open_bets"
    PENDING_WITHDRAWALS = "pending_withdrawals"
    JACKPOT_LIABILITY = "jackpot_liability"
    BONUS_LIABILITY = "bonus_liability"
    TOURNAMENT_GUARANTEE = "tournament_guarantee"
    CHARGEBACK_RESERVE = "chargeback_reserve"
    PLAYER_BALANCES = "player_balances"
    AFFILIATE_PAYABLE = "affiliate_payable"
    TAX_PROVISION = "tax_provision"


class Jurisdiction(str, Enum):
    UK = "uk"
    MALTA = "malta"
    CURACAO = "curacao"
    GIBRALTAR = "gibraltar"
    ISLE_OF_MAN = "isle_of_man"
    SWEDEN = "sweden"
    DENMARK = "denmark"


class Vertical(str, Enum):
    SPORTS = "sports"
    CASINO = "casino"
    LIVE_CASINO = "live_casino"
    POKER = "poker"
    BINGO = "bingo"
    LOTTERY = "lottery"
    ESPORTS = "esports"


class OpenBet(BaseModel):
    """Represents an unsettled bet contributing to exposure."""
    bet_id: str
    player_id: str
    vertical: Vertical
    jurisdiction: Jurisdiction
    currency: str
    stake: Decimal
    potential_payout: Decimal          # max liability to operator
    odds: Decimal
    placed_at: datetime
    event_start: Optional[datetime] = None
    market_type: str = ""              # e.g., "match_winner", "over_under"
    is_live: bool = False
    is_accumulator: bool = False
    legs: int = 1


class PendingWithdrawal(BaseModel):
    """Withdrawal request in the processing pipeline."""
    withdrawal_id: str
    player_id: str
    amount: Decimal
    currency: str
    method: str                        # bank_transfer, ewallet, crypto
    status: str                        # pending_approval, processing, awaiting_psp
    requested_at: datetime
    jurisdiction: Jurisdiction
    kyc_verified: bool = True
    aml_flagged: bool = False


class JackpotPool(BaseModel):
    """Progressive or fixed jackpot liability."""
    jackpot_id: str
    name: str
    pool_amount: Decimal
    currency: str
    jackpot_type: str                  # progressive, fixed, daily_drop
    contribution_rate: Decimal         # % of each bet added to pool
    seed_amount: Decimal               # operator-funded seed
    max_payout: Optional[Decimal] = None
    is_network: bool = False           # shared across operators


class ExposureLineItem(BaseModel):
    """Single line item in the exposure calculation."""
    category: ExposureCategory
    jurisdiction: Jurisdiction
    currency: str
    amount_local: Decimal
    amount_reporting_ccy: Decimal
    item_count: int = 0
    description: str = ""
    risk_weight: Decimal = Decimal("1.0")
    weighted_amount: Decimal = Decimal("0")


class ExposureReport(BaseModel):
    """Complete exposure calculation result."""
    report_id: str = Field(default_factory=lambda: str(uuid4()))
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    reporting_currency: str = REPORTING_CURRENCY

    # Summary
    total_exposure: Decimal = Decimal("0")
    total_weighted_exposure: Decimal = Decimal("0")
    total_player_funds_required: Decimal = Decimal("0")

    # Breakdown
    line_items: list[ExposureLineItem] = []

    # By dimension
    by_category: dict[str, Decimal] = {}
    by_jurisdiction: dict[str, Decimal] = {}
    by_vertical: dict[str, Decimal] = {}

    # Risk metrics
    largest_single_exposure: Decimal = Decimal("0")
    concentration_risk_pct: Decimal = Decimal("0")  # largest / total


# ---------------------------------------------------------------------------
# FX Conversion (simplified; use ExchangeRateService in production)
# ---------------------------------------------------------------------------

_FX_RATES_VS_EUR = {
    "EUR": Decimal("1.0"), "GBP": Decimal("0.858"), "USD": Decimal("1.087"),
    "SEK": Decimal("11.25"), "NOK": Decimal("11.58"), "DKK": Decimal("7.46"),
    "CAD": Decimal("1.475"), "AUD": Decimal("1.652"), "BRL": Decimal("5.32"),
    "BTC": Decimal("0.0000155"), "ETH": Decimal("0.000285"), "USDT": Decimal("1.087"),
}


def to_reporting_ccy(amount: Decimal, currency: str) -> Decimal:
    rate = _FX_RATES_VS_EUR.get(currency, Decimal("1"))
    return (amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Exposure Calculation Engine
# ---------------------------------------------------------------------------

class ExposureCalculator:
    """
    Core engine that computes total operator exposure.

    Exposure = Sum of all potential outflows the operator must be prepared to pay:
    1. Open bets -> potential payout if all bets win (worst case)
    2. Pending withdrawals -> cash committed to leave
    3. Jackpot pools -> could be won at any time
    4. Bonus liabilities -> outstanding wagerable bonus funds
    5. Tournament guarantees -> overlay risk
    6. Chargeback reserve -> historical chargeback rate * volume
    7. Player balances -> all deposited funds not yet wagered
    8. Affiliate payables -> commissions owed
    9. Tax provisions -> GGR/turnover tax accrued

    Risk weighting adjusts exposure based on probability:
    - Open bets: weighted by expected value (not worst case)
    - Pending withdrawals: 100% (must be paid)
    - Jackpots: weighted by actuarial probability
    """

    def __init__(self):
        self._risk_weights = {
            ExposureCategory.OPEN_BETS: Decimal("0.35"),           # ~35% expected loss
            ExposureCategory.PENDING_WITHDRAWALS: Decimal("1.0"),  # 100% committed
            ExposureCategory.JACKPOT_LIABILITY: Decimal("0.85"),   # high probability
            ExposureCategory.BONUS_LIABILITY: Decimal("0.40"),     # many bonuses expire
            ExposureCategory.TOURNAMENT_GUARANTEE: Decimal("0.25"),# overlay risk
            ExposureCategory.CHARGEBACK_RESERVE: Decimal("1.0"),   # provision already set
            ExposureCategory.PLAYER_BALANCES: Decimal("1.0"),      # 100% owed
            ExposureCategory.AFFILIATE_PAYABLE: Decimal("1.0"),    # contractual
            ExposureCategory.TAX_PROVISION: Decimal("1.0"),        # regulatory
        }

    async def calculate(
        self,
        open_bets: list[OpenBet],
        pending_withdrawals: list[PendingWithdrawal],
        jackpot_pools: list[JackpotPool],
        bonus_balance: Decimal = Decimal("0"),
        bonus_currency: str = "EUR",
        tournament_guarantees: Decimal = Decimal("0"),
        chargeback_reserve: Decimal = Decimal("0"),
        total_player_balances: Decimal = Decimal("0"),
        affiliate_payable: Decimal = Decimal("0"),
        tax_provision: Decimal = Decimal("0"),
    ) -> ExposureReport:
        """Run a full exposure calculation."""

        report = ExposureReport()
        by_vertical: dict[str, Decimal] = {}

        # --- 1. Open Bets ---
        bets_by_jurisdiction: dict[str, list[OpenBet]] = {}
        for bet in open_bets:
            bets_by_jurisdiction.setdefault(bet.jurisdiction.value, []).append(bet)
            by_vertical[bet.vertical.value] = by_vertical.get(
                bet.vertical.value, Decimal("0")
            ) + to_reporting_ccy(bet.potential_payout, bet.currency)

        for jur, bets in bets_by_jurisdiction.items():
            total_payout_local = sum(b.potential_payout for b in bets)
            # Use first bet's currency for group (simplified; production groups by currency)
            ccy = bets[0].currency if bets else "EUR"
            amount_rc = sum(to_reporting_ccy(b.potential_payout, b.currency) for b in bets)

            item = ExposureLineItem(
                category=ExposureCategory.OPEN_BETS,
                jurisdiction=Jurisdiction(jur),
                currency=ccy,
                amount_local=total_payout_local,  # ty:ignore[invalid-argument-type]
                amount_reporting_ccy=amount_rc,  # ty:ignore[invalid-argument-type]
                item_count=len(bets),
                description=f"{len(bets)} open bets, max payout {total_payout_local:,.2f} {ccy}",
                risk_weight=self._risk_weights[ExposureCategory.OPEN_BETS],
            )
            item.weighted_amount = (amount_rc * item.risk_weight).quantize(Decimal("0.01"))
            report.line_items.append(item)

        # --- 2. Pending Withdrawals ---
        wd_by_jurisdiction: dict[str, list[PendingWithdrawal]] = {}
        for wd in pending_withdrawals:
            wd_by_jurisdiction.setdefault(wd.jurisdiction.value, []).append(wd)

        for jur, wds in wd_by_jurisdiction.items():
            total_local = sum(w.amount for w in wds)
            ccy = wds[0].currency if wds else "EUR"
            amount_rc = sum(to_reporting_ccy(w.amount, w.currency) for w in wds)

            item = ExposureLineItem(
                category=ExposureCategory.PENDING_WITHDRAWALS,
                jurisdiction=Jurisdiction(jur),
                currency=ccy,
                amount_local=total_local,  # ty:ignore[invalid-argument-type]
                amount_reporting_ccy=amount_rc,  # ty:ignore[invalid-argument-type]
                item_count=len(wds),
                description=f"{len(wds)} pending withdrawals",
                risk_weight=self._risk_weights[ExposureCategory.PENDING_WITHDRAWALS],
            )
            item.weighted_amount = amount_rc  # 100%  # ty:ignore[invalid-assignment]
            report.line_items.append(item)

        # --- 3. Jackpot Pools ---
        for jp in jackpot_pools:
            amount_rc = to_reporting_ccy(jp.pool_amount, jp.currency)
            rw = self._risk_weights[ExposureCategory.JACKPOT_LIABILITY]
            if jp.is_network:
                rw *= Decimal("0.5")  # shared liability with network

            item = ExposureLineItem(
                category=ExposureCategory.JACKPOT_LIABILITY,
                jurisdiction=Jurisdiction.MALTA,  # typically centralized
                currency=jp.currency,
                amount_local=jp.pool_amount,
                amount_reporting_ccy=amount_rc,
                item_count=1,
                description=f"Jackpot: {jp.name} ({jp.jackpot_type})",
                risk_weight=rw,
            )
            item.weighted_amount = (amount_rc * rw).quantize(Decimal("0.01"))
            report.line_items.append(item)

        # --- 4-9. Other Categories (fixed amounts) ---
        fixed_items = [
            (ExposureCategory.BONUS_LIABILITY, bonus_balance, bonus_currency, "Active bonus balances"),
            (ExposureCategory.TOURNAMENT_GUARANTEE, tournament_guarantees, "EUR", "Tournament overlay risk"),
            (ExposureCategory.CHARGEBACK_RESERVE, chargeback_reserve, "EUR", "Chargeback provision"),
            (ExposureCategory.PLAYER_BALANCES, total_player_balances, "EUR", "Total player wallet balances"),
            (ExposureCategory.AFFILIATE_PAYABLE, affiliate_payable, "EUR", "Affiliate commissions due"),
            (ExposureCategory.TAX_PROVISION, tax_provision, "EUR", "Accrued gaming taxes"),
        ]

        for cat, amount, ccy, desc in fixed_items:
            if amount <= 0:
                continue
            amount_rc = to_reporting_ccy(amount, ccy)
            rw = self._risk_weights[cat]
            item = ExposureLineItem(
                category=cat,
                jurisdiction=Jurisdiction.MALTA,
                currency=ccy,
                amount_local=amount,
                amount_reporting_ccy=amount_rc,
                item_count=1,
                description=desc,
                risk_weight=rw,
            )
            item.weighted_amount = (amount_rc * rw).quantize(Decimal("0.01"))
            report.line_items.append(item)

        # --- Aggregate ---
        report.total_exposure = sum(li.amount_reporting_ccy for li in report.line_items)  # ty:ignore[invalid-assignment]
        report.total_weighted_exposure = sum(li.weighted_amount for li in report.line_items)  # ty:ignore[invalid-assignment]

        # By category
        for li in report.line_items:
            key = li.category.value
            report.by_category[key] = report.by_category.get(key, Decimal("0")) + li.amount_reporting_ccy

        # By jurisdiction
        for li in report.line_items:
            key = li.jurisdiction.value
            report.by_jurisdiction[key] = report.by_jurisdiction.get(key, Decimal("0")) + li.amount_reporting_ccy

        report.by_vertical = by_vertical

        # Concentration risk
        if report.line_items:
            report.largest_single_exposure = max(li.amount_reporting_ccy for li in report.line_items)
            if report.total_exposure > 0:
                report.concentration_risk_pct = (
                    report.largest_single_exposure / report.total_exposure * Decimal("100")
                ).quantize(Decimal("0.1"))

        # Player funds requirement
        player_cats = {
            ExposureCategory.PLAYER_BALANCES,
            ExposureCategory.PENDING_WITHDRAWALS,
            ExposureCategory.OPEN_BETS,
            ExposureCategory.BONUS_LIABILITY,
        }
        report.total_player_funds_required = sum(  # ty:ignore[invalid-assignment]
            li.amount_reporting_ccy for li in report.line_items
            if li.category in player_cats
        )

        logger.info(
            f"Exposure calculated: total={report.total_exposure:,.2f} EUR, "
            f"weighted={report.total_weighted_exposure:,.2f} EUR, "
            f"player_funds_req={report.total_player_funds_required:,.2f} EUR"
        )

        return report


# ---------------------------------------------------------------------------
# Demo Data Generator
# ---------------------------------------------------------------------------

def generate_demo_data():
    """Generate realistic casino exposure data for demonstration."""
    now = datetime.now(timezone.utc)

    open_bets = [
        # Sports betting - UK
        OpenBet(bet_id="BET-001", player_id="P-1001", vertical=Vertical.SPORTS,
                jurisdiction=Jurisdiction.UK, currency="GBP",
                stake=Decimal("50.00"), potential_payout=Decimal("450.00"),
                odds=Decimal("9.0"), placed_at=now - timedelta(hours=2),
                market_type="match_winner", is_live=False),
        OpenBet(bet_id="BET-002", player_id="P-1002", vertical=Vertical.SPORTS,
                jurisdiction=Jurisdiction.UK, currency="GBP",
                stake=Decimal("100.00"), potential_payout=Decimal("2500.00"),
                odds=Decimal("25.0"), placed_at=now - timedelta(hours=1),
                market_type="accumulator", is_accumulator=True, legs=6),
        # High roller single - UK
        OpenBet(bet_id="BET-003", player_id="P-1003", vertical=Vertical.SPORTS,
                jurisdiction=Jurisdiction.UK, currency="GBP",
                stake=Decimal("5000.00"), potential_payout=Decimal("15000.00"),
                odds=Decimal("3.0"), placed_at=now - timedelta(minutes=30),
                market_type="match_winner", is_live=True),

        # Casino - Malta
        OpenBet(bet_id="BET-004", player_id="P-2001", vertical=Vertical.CASINO,
                jurisdiction=Jurisdiction.MALTA, currency="EUR",
                stake=Decimal("200.00"), potential_payout=Decimal("50000.00"),
                odds=Decimal("250.0"), placed_at=now - timedelta(minutes=5),
                market_type="slot_spin"),
        OpenBet(bet_id="BET-005", player_id="P-2002", vertical=Vertical.LIVE_CASINO,
                jurisdiction=Jurisdiction.MALTA, currency="EUR",
                stake=Decimal("1000.00"), potential_payout=Decimal("36000.00"),
                odds=Decimal("36.0"), placed_at=now - timedelta(minutes=2),
                market_type="roulette_straight"),

        # Sports - Curacao (crypto)
        OpenBet(bet_id="BET-006", player_id="P-3001", vertical=Vertical.SPORTS,
                jurisdiction=Jurisdiction.CURACAO, currency="USD",
                stake=Decimal("500.00"), potential_payout=Decimal("3750.00"),
                odds=Decimal("7.5"), placed_at=now - timedelta(hours=4),
                market_type="outright_winner"),
        OpenBet(bet_id="BET-007", player_id="P-3002", vertical=Vertical.ESPORTS,
                jurisdiction=Jurisdiction.CURACAO, currency="USDT",
                stake=Decimal("250.00"), potential_payout=Decimal("1125.00"),
                odds=Decimal("4.5"), placed_at=now - timedelta(hours=1),
                market_type="match_winner"),
    ]

    pending_withdrawals = [
        PendingWithdrawal(withdrawal_id="WD-001", player_id="P-1001",
                          amount=Decimal("2500.00"), currency="GBP",
                          method="bank_transfer", status="processing",
                          requested_at=now - timedelta(hours=6),
                          jurisdiction=Jurisdiction.UK),
        PendingWithdrawal(withdrawal_id="WD-002", player_id="P-1050",
                          amount=Decimal("15000.00"), currency="GBP",
                          method="bank_transfer", status="pending_approval",
                          requested_at=now - timedelta(hours=2),
                          jurisdiction=Jurisdiction.UK),
        PendingWithdrawal(withdrawal_id="WD-003", player_id="P-2001",
                          amount=Decimal("800.00"), currency="EUR",
                          method="ewallet", status="processing",
                          requested_at=now - timedelta(hours=1),
                          jurisdiction=Jurisdiction.MALTA),
        PendingWithdrawal(withdrawal_id="WD-004", player_id="P-3001",
                          amount=Decimal("5000.00"), currency="USDT",
                          method="crypto", status="awaiting_psp",
                          requested_at=now - timedelta(minutes=30),
                          jurisdiction=Jurisdiction.CURACAO),
    ]

    jackpot_pools = [
        JackpotPool(jackpot_id="JP-001", name="Mega Fortune Progressive",
                    pool_amount=Decimal("2450000.00"), currency="EUR",
                    jackpot_type="progressive", contribution_rate=Decimal("0.02"),
                    seed_amount=Decimal("500000.00"), is_network=True),
        JackpotPool(jackpot_id="JP-002", name="Daily Drop Jackpot",
                    pool_amount=Decimal("25000.00"), currency="EUR",
                    jackpot_type="daily_drop", contribution_rate=Decimal("0.01"),
                    seed_amount=Decimal("10000.00"), is_network=False),
        JackpotPool(jackpot_id="JP-003", name="Local Slots Jackpot",
                    pool_amount=Decimal("180000.00"), currency="GBP",
                    jackpot_type="progressive", contribution_rate=Decimal("0.015"),
                    seed_amount=Decimal("50000.00"), is_network=False),
    ]

    return open_bets, pending_withdrawals, jackpot_pools


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    """Run exposure calculation with demo data."""
    calculator = ExposureCalculator()

    open_bets, pending_withdrawals, jackpot_pools = generate_demo_data()

    report = await calculator.calculate(
        open_bets=open_bets,
        pending_withdrawals=pending_withdrawals,
        jackpot_pools=jackpot_pools,
        bonus_balance=Decimal("450000.00"),
        bonus_currency="EUR",
        tournament_guarantees=Decimal("75000.00"),
        chargeback_reserve=Decimal("120000.00"),
        total_player_balances=Decimal("8500000.00"),
        affiliate_payable=Decimal("185000.00"),
        tax_provision=Decimal("340000.00"),
    )

    print(f"\n{'='*60}")
    print(f"EXPOSURE REPORT - {report.calculated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"{'='*60}")
    print(f"Report ID: {report.report_id}")
    print(f"\nTotal Gross Exposure:    {report.total_exposure:>14,.2f} {REPORTING_CURRENCY}")
    print(f"Total Weighted Exposure: {report.total_weighted_exposure:>14,.2f} {REPORTING_CURRENCY}")
    print(f"Player Funds Required:   {report.total_player_funds_required:>14,.2f} {REPORTING_CURRENCY}")

    print(f"\n--- By Category ---")
    for cat, amount in sorted(report.by_category.items(), key=lambda x: x[1], reverse=True):
        print(f"  {cat:<25s} {amount:>14,.2f} {REPORTING_CURRENCY}")

    print(f"\n--- By Jurisdiction ---")
    for jur, amount in sorted(report.by_jurisdiction.items(), key=lambda x: x[1], reverse=True):
        print(f"  {jur:<25s} {amount:>14,.2f} {REPORTING_CURRENCY}")

    print(f"\n--- By Vertical ---")
    for vert, amount in sorted(report.by_vertical.items(), key=lambda x: x[1], reverse=True):
        print(f"  {vert:<25s} {amount:>14,.2f} {REPORTING_CURRENCY}")

    print(f"\n--- Risk Metrics ---")
    print(f"  Largest Single Exposure: {report.largest_single_exposure:>14,.2f} {REPORTING_CURRENCY}")
    print(f"  Concentration Risk:      {report.concentration_risk_pct:>13.1f}%")
    print()


if __name__ == "__main__":
    asyncio.run(main())
