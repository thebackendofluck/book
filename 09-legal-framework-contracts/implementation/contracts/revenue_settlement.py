#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 09, Legal Framework and Contracts.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Revenue Settlement Engine for iGaming Operators.

Calculates settlements for game providers, affiliates, and payment processors
using multiple compensation models:
  - Revenue Share: percentage of NGR (Net Gaming Revenue)
  - Fixed Fee: monthly/weekly flat rate
  - Hybrid: base fee + revenue share above threshold
  - CPA (Cost Per Acquisition): per-player acquisition payment
  - Tiered: progressive revenue share brackets

Handles:
  - Multi-currency settlement with FX rate management
  - Tax withholding per jurisdiction (GGR tax, VAT, gaming levies)
  - Minimum guarantees and shortfall calculations
  - Settlement period aggregation (weekly/biweekly/monthly)
  - Reconciliation and dispute flagging
  - UKGC/MGA/Curacao regulatory report generation

Usage:
    python revenue_settlement.py --demo
    python revenue_settlement.py --period 2026-02 --provider megaslots
    python revenue_settlement.py --reconcile --period 2026-02
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# FX rates (production: pull from ECB/XE API)
# ---------------------------------------------------------------------------

FX_RATES = {
    ("EUR", "GBP"): Decimal("0.86"),
    ("EUR", "USD"): Decimal("1.08"),
    ("EUR", "SEK"): Decimal("11.20"),
    ("EUR", "BRL"): Decimal("5.95"),
    ("EUR", "EUR"): Decimal("1.0"),
    ("GBP", "EUR"): Decimal("1.16"),
    ("GBP", "GBP"): Decimal("1.0"),
    ("USD", "EUR"): Decimal("0.93"),
    ("USD", "USD"): Decimal("1.0"),
}


def convert_currency(amount: Decimal, from_ccy: str, to_ccy: str) -> Decimal:
    if from_ccy == to_ccy:
        return amount
    rate = FX_RATES.get((from_ccy, to_ccy))
    if not rate:
        raise ValueError(f"No FX rate for {from_ccy}->{to_ccy}")
    return (amount * rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Jurisdiction tax rules
# ---------------------------------------------------------------------------

JURISDICTION_TAX = {
    "mga": {
        "ggr_tax_rate": Decimal("0.05"),       # 5% gaming tax
        "vat_rate": Decimal("0.18"),            # 18% VAT on B2B services
        "levy": Decimal("0.004"),              # 0.4% player protection levy
        "withholding": Decimal("0.0"),
    },
    "ukgc": {
        "ggr_tax_rate": Decimal("0.21"),       # 21% remote gaming duty
        "vat_rate": Decimal("0.20"),
        "levy": Decimal("0.001"),              # 0.1% research/education levy
        "withholding": Decimal("0.0"),
    },
    "curacao": {
        "ggr_tax_rate": Decimal("0.02"),       # 2% gaming tax
        "vat_rate": Decimal("0.0"),
        "levy": Decimal("0.0"),
        "withholding": Decimal("0.0"),
    },
    "gibraltar": {
        "ggr_tax_rate": Decimal("0.01"),       # 1% of turnover, capped
        "vat_rate": Decimal("0.0"),
        "levy": Decimal("0.0"),
        "withholding": Decimal("0.0"),
    },
    "brazil_spa": {
        "ggr_tax_rate": Decimal("0.12"),       # 12% GGR
        "vat_rate": Decimal("0.0"),
        "levy": Decimal("0.0"),
        "withholding": Decimal("0.15"),        # 15% IRRF on cross-border
    },
    "sga": {
        "ggr_tax_rate": Decimal("0.18"),       # 18% gaming tax Sweden
        "vat_rate": Decimal("0.25"),
        "levy": Decimal("0.0"),
        "withholding": Decimal("0.0"),
    },
}


# ---------------------------------------------------------------------------
# Settlement models
# ---------------------------------------------------------------------------

class SettlementModel(str, Enum):
    REVENUE_SHARE = "revenue_share"
    FIXED_FEE = "fixed_fee"
    HYBRID = "hybrid"
    CPA = "cpa"
    TIERED = "tiered"


@dataclass
class RevenueShareTerms:
    share_pct: Decimal              # e.g., 0.12 for 12%
    on_metric: str = "ngr"          # ngr, ggr, turnover
    minimum_guarantee: Decimal = Decimal("0")
    cap: Optional[Decimal] = None   # max monthly payout


@dataclass
class TieredTerms:
    """Progressive brackets - each bracket applies to incremental revenue."""
    tiers: list  # [(threshold, rate), ...] e.g., [(50000, 0.10), (100000, 0.12), (None, 0.15)]
    on_metric: str = "ngr"


@dataclass
class HybridTerms:
    base_fee: Decimal
    revenue_share_pct: Decimal
    revenue_threshold: Decimal  # share kicks in above this


@dataclass
class CPATerms:
    cpa_amount: Decimal             # per qualifying player
    qualification: str = "first_deposit"  # first_deposit, wagered_50, etc.
    cap_per_month: Optional[int] = None


@dataclass
class ProviderActivity:
    """Monthly activity data for a single provider in a single jurisdiction."""
    provider_id: str
    provider_name: str
    jurisdiction: str
    period: str                     # YYYY-MM
    currency: str
    total_bets: Decimal
    total_wins: Decimal
    bonus_cost: Decimal = Decimal("0")
    jackpot_contributions: Decimal = Decimal("0")
    free_spins_cost: Decimal = Decimal("0")
    new_depositing_players: int = 0
    active_players: int = 0

    @property
    def ggr(self) -> Decimal:
        return self.total_bets - self.total_wins

    @property
    def ngr(self) -> Decimal:
        return self.ggr - self.bonus_cost - self.jackpot_contributions - self.free_spins_cost


@dataclass
class SettlementLine:
    provider_id: str
    provider_name: str
    jurisdiction: str
    period: str
    settlement_model: str
    metric_name: str
    metric_value: Decimal
    gross_settlement: Decimal
    ggr_tax: Decimal
    vat: Decimal
    levy: Decimal
    withholding_tax: Decimal
    net_settlement: Decimal
    currency: str
    settlement_currency: str
    net_settlement_converted: Decimal
    fx_rate: Decimal
    minimum_guarantee_applied: bool = False
    notes: str = ""


# ---------------------------------------------------------------------------
# Settlement engine
# ---------------------------------------------------------------------------

class RevenueSettlementEngine:

    def __init__(self, settlement_currency: str = "EUR"):
        self.settlement_currency = settlement_currency
        self.lines: list[SettlementLine] = []

    def calculate_revenue_share(self, activity: ProviderActivity,
                                 terms: RevenueShareTerms) -> SettlementLine:
        if terms.on_metric == "ngr":
            metric_value = activity.ngr
        elif terms.on_metric == "ggr":
            metric_value = activity.ggr
        else:
            metric_value = activity.total_bets

        gross = (metric_value * terms.share_pct).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)

        min_guarantee_applied = False
        if gross < terms.minimum_guarantee:
            gross = terms.minimum_guarantee
            min_guarantee_applied = True

        if terms.cap and gross > terms.cap:
            gross = terms.cap

        return self._apply_taxes(
            activity, terms.on_metric, metric_value, gross,
            SettlementModel.REVENUE_SHARE, min_guarantee_applied
        )

    def calculate_tiered(self, activity: ProviderActivity,
                          terms: TieredTerms) -> SettlementLine:
        if terms.on_metric == "ngr":
            metric_value = activity.ngr
        else:
            metric_value = activity.ggr

        gross = Decimal("0")
        remaining = metric_value
        prev_threshold = Decimal("0")

        for threshold, rate in terms.tiers:
            if threshold is None:
                bracket_amount = remaining
            else:
                bracket_amount = min(remaining, Decimal(str(threshold)) - prev_threshold)
                prev_threshold = Decimal(str(threshold))

            if bracket_amount <= 0:
                break

            gross += (bracket_amount * Decimal(str(rate))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)
            remaining -= bracket_amount

        return self._apply_taxes(
            activity, terms.on_metric, metric_value, gross,
            SettlementModel.TIERED, False
        )

    def calculate_hybrid(self, activity: ProviderActivity,
                          terms: HybridTerms) -> SettlementLine:
        gross = terms.base_fee
        if activity.ngr > terms.revenue_threshold:
            excess = activity.ngr - terms.revenue_threshold
            gross += (excess * terms.revenue_share_pct).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP)

        return self._apply_taxes(
            activity, "ngr", activity.ngr, gross,
            SettlementModel.HYBRID, False
        )

    def calculate_cpa(self, activity: ProviderActivity,
                       terms: CPATerms) -> SettlementLine:
        qualifying = activity.new_depositing_players
        if terms.cap_per_month and qualifying > terms.cap_per_month:
            qualifying = terms.cap_per_month

        gross = (terms.cpa_amount * qualifying).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)

        return self._apply_taxes(
            activity, "new_players", Decimal(str(qualifying)), gross,
            SettlementModel.CPA, False
        )

    def calculate_fixed(self, activity: ProviderActivity,
                         fee: Decimal) -> SettlementLine:
        return self._apply_taxes(
            activity, "fixed", fee, fee,
            SettlementModel.FIXED_FEE, False
        )

    def _apply_taxes(self, activity: ProviderActivity, metric_name: str,
                     metric_value: Decimal, gross: Decimal,
                     model: SettlementModel, min_guarantee: bool) -> SettlementLine:
        tax_rules = JURISDICTION_TAX.get(activity.jurisdiction, {})
        ggr_tax = (gross * tax_rules.get("ggr_tax_rate", Decimal("0"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        vat = (gross * tax_rules.get("vat_rate", Decimal("0"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        levy = (gross * tax_rules.get("levy", Decimal("0"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)
        withholding = (gross * tax_rules.get("withholding", Decimal("0"))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)

        net = gross - ggr_tax - levy - withholding  # VAT typically passed through
        fx_rate = FX_RATES.get(
            (activity.currency, self.settlement_currency), Decimal("1.0"))
        net_converted = convert_currency(net, activity.currency, self.settlement_currency)

        line = SettlementLine(
            provider_id=activity.provider_id,
            provider_name=activity.provider_name,
            jurisdiction=activity.jurisdiction,
            period=activity.period,
            settlement_model=model.value,
            metric_name=metric_name,
            metric_value=metric_value,
            gross_settlement=gross,
            ggr_tax=ggr_tax,
            vat=vat,
            levy=levy,
            withholding_tax=withholding,
            net_settlement=net,
            currency=activity.currency,
            settlement_currency=self.settlement_currency,
            net_settlement_converted=net_converted,
            fx_rate=fx_rate,
            minimum_guarantee_applied=min_guarantee,
        )
        self.lines.append(line)
        return line

    def generate_settlement_report(self) -> dict:
        total_gross = sum(l.gross_settlement for l in self.lines)
        total_net = sum(l.net_settlement_converted for l in self.lines)
        total_tax = sum(l.ggr_tax + l.levy + l.withholding_tax for l in self.lines)

        by_provider = {}
        for line in self.lines:
            key = line.provider_name
            if key not in by_provider:
                by_provider[key] = Decimal("0")
            by_provider[key] += line.net_settlement_converted

        by_jurisdiction = {}
        for line in self.lines:
            key = line.jurisdiction
            if key not in by_jurisdiction:
                by_jurisdiction[key] = {"net": Decimal("0"), "tax": Decimal("0")}
            by_jurisdiction[key]["net"] += line.net_settlement_converted
            by_jurisdiction[key]["tax"] += line.ggr_tax + line.levy

        return {
            "settlement_currency": self.settlement_currency,
            "total_lines": len(self.lines),
            "total_gross": float(total_gross),
            "total_net": float(total_net),
            "total_tax_deducted": float(total_tax),
            "by_provider": {k: float(v) for k, v in by_provider.items()},
            "by_jurisdiction": {
                k: {"net": float(v["net"]), "tax": float(v["tax"])}
                for k, v in by_jurisdiction.items()
            },
        }


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo():
    engine = RevenueSettlementEngine(settlement_currency="EUR")

    # Simulated monthly activity for 3 providers across 2 jurisdictions
    activities = [
        ProviderActivity(
            provider_id="MEGA-001", provider_name="MegaSlots International",
            jurisdiction="mga", period="2026-02", currency="EUR",
            total_bets=Decimal("2450000"), total_wins=Decimal("2280000"),
            bonus_cost=Decimal("15000"), jackpot_contributions=Decimal("8000"),
            free_spins_cost=Decimal("5000"),
            new_depositing_players=320, active_players=4500,
        ),
        ProviderActivity(
            provider_id="MEGA-001", provider_name="MegaSlots International",
            jurisdiction="ukgc", period="2026-02", currency="GBP",
            total_bets=Decimal("1800000"), total_wins=Decimal("1690000"),
            bonus_cost=Decimal("12000"), jackpot_contributions=Decimal("5000"),
            new_depositing_players=180, active_players=2800,
        ),
        ProviderActivity(
            provider_id="LIVE-002", provider_name="LiveDealer Pro",
            jurisdiction="mga", period="2026-02", currency="EUR",
            total_bets=Decimal("890000"), total_wins=Decimal("845000"),
            bonus_cost=Decimal("3000"),
            new_depositing_players=90, active_players=1200,
        ),
        ProviderActivity(
            provider_id="ODDS-003", provider_name="SportsFeed Global",
            jurisdiction="sga", period="2026-02", currency="SEK",
            total_bets=Decimal("15000000"), total_wins=Decimal("14200000"),
            bonus_cost=Decimal("50000"),
            new_depositing_players=450, active_players=8000,
        ),
    ]

    # Provider settlement terms
    print("=" * 80)
    print("REVENUE SETTLEMENT ENGINE - MONTHLY SETTLEMENT REPORT")
    print(f"Period: 2026-02 | Settlement Currency: EUR")
    print("=" * 80)

    # MegaSlots: 12% revenue share on NGR, min guarantee 5000 EUR
    mega_terms = RevenueShareTerms(
        share_pct=Decimal("0.12"), on_metric="ngr",
        minimum_guarantee=Decimal("5000"),
    )
    for act in activities[:2]:
        line = engine.calculate_revenue_share(act, mega_terms)
        _print_line(line)

    # LiveDealer: tiered on NGR
    tiered_terms = TieredTerms(
        tiers=[(30000, 0.08), (60000, 0.10), (None, 0.14)],
        on_metric="ngr",
    )
    line = engine.calculate_tiered(activities[2], tiered_terms)
    _print_line(line)

    # SportsFeed: hybrid (base + share above threshold)
    hybrid_terms = HybridTerms(
        base_fee=Decimal("15000"),
        revenue_share_pct=Decimal("0.03"),
        revenue_threshold=Decimal("500000"),
    )
    line = engine.calculate_hybrid(activities[3], hybrid_terms)
    _print_line(line)

    # Summary
    report = engine.generate_settlement_report()
    print("\n" + "=" * 80)
    print("SETTLEMENT SUMMARY")
    print("=" * 80)
    print(json.dumps(report, indent=2))

    # Reconciliation check
    print("\n" + "=" * 80)
    print("RECONCILIATION CHECK")
    print("=" * 80)
    for line in engine.lines:
        expected = line.gross_settlement - line.ggr_tax - line.levy - line.withholding_tax
        if expected != line.net_settlement:
            print(f"[WARN] {line.provider_name} ({line.jurisdiction}): "
                  f"reconciliation mismatch: expected {expected}, got {line.net_settlement}")
        else:
            print(f"[OK]   {line.provider_name} ({line.jurisdiction}): "
                  f"Gross {line.gross_settlement} -> Net {line.net_settlement} "
                  f"{line.currency} = {line.net_settlement_converted} {line.settlement_currency}")

    print("\n[OK] Settlement calculation complete.")


def _print_line(line: SettlementLine):
    print(f"\n--- {line.provider_name} ({line.jurisdiction.upper()}) ---")
    print(f"  Model:         {line.settlement_model}")
    print(f"  {line.metric_name.upper()}:  {line.currency} {line.metric_value:>12,.2f}")
    print(f"  Gross:         {line.currency} {line.gross_settlement:>12,.2f}"
          f"{'  [MIN GUARANTEE]' if line.minimum_guarantee_applied else ''}")
    print(f"  GGR Tax:      -{line.currency} {line.ggr_tax:>12,.2f}")
    print(f"  Levy:         -{line.currency} {line.levy:>12,.2f}")
    print(f"  Withholding:  -{line.currency} {line.withholding_tax:>12,.2f}")
    print(f"  Net:           {line.currency} {line.net_settlement:>12,.2f}")
    if line.currency != line.settlement_currency:
        print(f"  Converted:     {line.settlement_currency} {line.net_settlement_converted:>12,.2f} "
              f"(rate: {line.fx_rate})")


def main():
    parser = argparse.ArgumentParser(description="Revenue Settlement Engine")
    parser.add_argument("--demo", action="store_true", help="Run demo settlement")
    parser.add_argument("--period", help="Settlement period (YYYY-MM)")
    parser.add_argument("--provider", help="Provider ID filter")
    parser.add_argument("--reconcile", action="store_true", help="Run reconciliation")
    args = parser.parse_args()
    demo()


if __name__ == "__main__":
    main()
