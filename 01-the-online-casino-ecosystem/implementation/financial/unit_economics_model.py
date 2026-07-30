#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 01, The Online Casino Ecosystem.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
iGaming Unit Economics Model - CAC, LTV, and Sensitivity Analysis

Models unit economics for online gambling operators with detailed customer
acquisition cost (CAC), lifetime value (LTV), and contribution margin analysis.
Includes Monte Carlo sensitivity analysis for key variables.

Usage:
    python unit_economics_model.py --vertical casino
    python unit_economics_model.py --vertical sports --jurisdiction UK --format json
    python unit_economics_model.py --sensitivity --iterations 5000
"""

import argparse
import json
import random
import sys
from dataclasses import dataclass
from typing import Optional


@dataclass
class PlayerCohortAssumptions:
    """Assumptions for a player cohort unit economics model."""
    vertical: str  # casino, sports, combined
    jurisdiction: str

    # Acquisition
    marketing_spend_per_ftd_eur: float  # cost per First Time Depositor
    registration_to_ftd_rate_pct: float  # % of registrations that deposit
    ftd_to_active_rate_pct: float  # % of FTDs that become active (30-day)

    # Player behavior
    avg_first_deposit_eur: float
    avg_monthly_deposits_eur: float
    avg_monthly_bets_eur: float
    avg_house_edge_pct: float
    avg_monthly_ggr_per_player_eur: float
    avg_bonus_cost_pct_of_ggr: float

    # Retention
    month1_retention_pct: float
    month3_retention_pct: float
    month6_retention_pct: float
    month12_retention_pct: float
    avg_lifetime_months: float

    # Costs (per player, variable)
    payment_processing_pct: float
    game_content_royalty_pct: float
    ggr_tax_pct: float
    platform_cost_per_player_eur: float
    support_cost_per_player_eur: float


# ---------------------------------------------------------------------------
# Default assumptions by vertical (based on industry benchmarks)
# ---------------------------------------------------------------------------
CASINO_UK = PlayerCohortAssumptions(
    vertical="casino", jurisdiction="UK",
    marketing_spend_per_ftd_eur=280,
    registration_to_ftd_rate_pct=35,
    ftd_to_active_rate_pct=60,
    avg_first_deposit_eur=45,
    avg_monthly_deposits_eur=120,
    avg_monthly_bets_eur=850,
    avg_house_edge_pct=4.2,
    avg_monthly_ggr_per_player_eur=35.7,
    avg_bonus_cost_pct_of_ggr=18,
    month1_retention_pct=55,
    month3_retention_pct=35,
    month6_retention_pct=22,
    month12_retention_pct=15,
    avg_lifetime_months=8.5,
    payment_processing_pct=3.2,
    game_content_royalty_pct=12,
    ggr_tax_pct=21,
    platform_cost_per_player_eur=2.5,
    support_cost_per_player_eur=1.8,
)

SPORTS_UK = PlayerCohortAssumptions(
    vertical="sports", jurisdiction="UK",
    marketing_spend_per_ftd_eur=350,
    registration_to_ftd_rate_pct=40,
    ftd_to_active_rate_pct=55,
    avg_first_deposit_eur=35,
    avg_monthly_deposits_eur=95,
    avg_monthly_bets_eur=420,
    avg_house_edge_pct=7.5,
    avg_monthly_ggr_per_player_eur=31.5,
    avg_bonus_cost_pct_of_ggr=22,
    month1_retention_pct=50,
    month3_retention_pct=32,
    month6_retention_pct=20,
    month12_retention_pct=14,
    avg_lifetime_months=10.0,
    payment_processing_pct=2.8,
    game_content_royalty_pct=2,
    ggr_tax_pct=21,
    platform_cost_per_player_eur=3.0,
    support_cost_per_player_eur=2.2,
)

CASINO_MALTA = PlayerCohortAssumptions(
    vertical="casino", jurisdiction="Malta_MGA",
    marketing_spend_per_ftd_eur=180,
    registration_to_ftd_rate_pct=30,
    ftd_to_active_rate_pct=55,
    avg_first_deposit_eur=55,
    avg_monthly_deposits_eur=140,
    avg_monthly_bets_eur=950,
    avg_house_edge_pct=4.0,
    avg_monthly_ggr_per_player_eur=38,
    avg_bonus_cost_pct_of_ggr=20,
    month1_retention_pct=50,
    month3_retention_pct=30,
    month6_retention_pct=18,
    month12_retention_pct=12,
    avg_lifetime_months=7.0,
    payment_processing_pct=3.5,
    game_content_royalty_pct=12,
    ggr_tax_pct=5,
    platform_cost_per_player_eur=2.0,
    support_cost_per_player_eur=1.5,
)

CASINO_BR = PlayerCohortAssumptions(
    vertical="casino", jurisdiction="Brazil",
    marketing_spend_per_ftd_eur=85,
    registration_to_ftd_rate_pct=45,
    ftd_to_active_rate_pct=65,
    avg_first_deposit_eur=20,
    avg_monthly_deposits_eur=65,
    avg_monthly_bets_eur=500,
    avg_house_edge_pct=4.5,
    avg_monthly_ggr_per_player_eur=22.5,
    avg_bonus_cost_pct_of_ggr=25,
    month1_retention_pct=60,
    month3_retention_pct=38,
    month6_retention_pct=25,
    month12_retention_pct=18,
    avg_lifetime_months=9.0,
    payment_processing_pct=1.5,  # PIX is very cheap
    game_content_royalty_pct=12,
    ggr_tax_pct=12,
    platform_cost_per_player_eur=1.5,
    support_cost_per_player_eur=1.0,
)

PRESETS = {
    "casino_uk": CASINO_UK,
    "sports_uk": SPORTS_UK,
    "casino_malta": CASINO_MALTA,
    "casino_brazil": CASINO_BR,
}


class UnitEconomicsModel:
    """Calculate and analyze iGaming unit economics."""

    def __init__(self, assumptions: PlayerCohortAssumptions):
        self.a = assumptions

    def calculate_cac(self) -> dict:
        """Calculate Customer Acquisition Cost breakdown."""
        effective_cac = self.a.marketing_spend_per_ftd_eur
        cac_per_registration = effective_cac * (self.a.registration_to_ftd_rate_pct / 100)
        cac_per_active = effective_cac / (self.a.ftd_to_active_rate_pct / 100)

        return {
            "cost_per_ftd_eur": round(effective_cac, 2),
            "cost_per_registration_eur": round(cac_per_registration, 2),
            "cost_per_active_player_eur": round(cac_per_active, 2),
            "registration_to_ftd_rate_pct": self.a.registration_to_ftd_rate_pct,
            "ftd_to_active_rate_pct": self.a.ftd_to_active_rate_pct,
            "effective_funnel_rate_pct": round(
                self.a.registration_to_ftd_rate_pct * self.a.ftd_to_active_rate_pct / 100, 1
            ),
        }

    def calculate_ltv(self) -> dict:
        """Calculate Lifetime Value with monthly cohort decay."""
        monthly_ggr = self.a.avg_monthly_ggr_per_player_eur
        bonus_cost = monthly_ggr * self.a.avg_bonus_cost_pct_of_ggr / 100
        net_ggr = monthly_ggr - bonus_cost

        # Variable costs per player per month
        payment_cost = self.a.avg_monthly_deposits_eur * self.a.payment_processing_pct / 100
        content_royalty = monthly_ggr * self.a.game_content_royalty_pct / 100
        tax = monthly_ggr * self.a.ggr_tax_pct / 100
        platform_cost = self.a.platform_cost_per_player_eur
        support_cost = self.a.support_cost_per_player_eur

        total_variable_cost = payment_cost + content_royalty + tax + platform_cost + support_cost
        monthly_contribution = net_ggr - total_variable_cost

        # Calculate LTV using cohort retention curve
        retention_curve = self._build_retention_curve()
        ltv_gross = sum(monthly_ggr * ret for ret in retention_curve)
        ltv_net = sum(monthly_contribution * ret for ret in retention_curve)

        return {
            "monthly_ggr_per_player_eur": round(monthly_ggr, 2),
            "monthly_bonus_cost_eur": round(bonus_cost, 2),
            "monthly_net_ggr_eur": round(net_ggr, 2),
            "monthly_variable_costs_eur": {
                "payment_processing": round(payment_cost, 2),
                "content_royalty": round(content_royalty, 2),
                "ggr_tax": round(tax, 2),
                "platform": round(platform_cost, 2),
                "support": round(support_cost, 2),
                "total": round(total_variable_cost, 2),
            },
            "monthly_contribution_margin_eur": round(monthly_contribution, 2),
            "contribution_margin_pct": round(monthly_contribution / monthly_ggr * 100, 1) if monthly_ggr > 0 else 0,
            "avg_lifetime_months": self.a.avg_lifetime_months,
            "ltv_gross_eur": round(ltv_gross, 2),
            "ltv_net_eur": round(ltv_net, 2),
            "retention_curve_24m": [round(r * 100, 1) for r in retention_curve[:24]],
        }

    def _build_retention_curve(self, months: int = 36) -> list[float]:
        """Build monthly retention curve using known data points with exponential interpolation."""
        known = {
            0: 1.0,
            1: self.a.month1_retention_pct / 100,
            3: self.a.month3_retention_pct / 100,
            6: self.a.month6_retention_pct / 100,
            12: self.a.month12_retention_pct / 100,
        }
        curve = []
        for m in range(months):
            if m in known:
                curve.append(known[m])
            elif m < 1:
                curve.append(1.0)
            elif m <= 3:
                # Linear interpolation between month 1 and 3
                t = (m - 1) / 2
                curve.append(known[1] + t * (known[3] - known[1]))
            elif m <= 6:
                t = (m - 3) / 3
                curve.append(known[3] + t * (known[6] - known[3]))
            elif m <= 12:
                t = (m - 6) / 6
                curve.append(known[6] + t * (known[12] - known[6]))
            else:
                # Exponential decay after month 12
                decay_rate = 0.92  # 8% monthly churn after year 1
                months_after_12 = m - 12
                curve.append(known[12] * (decay_rate ** months_after_12))
        return curve

    def calculate_ratios(self) -> dict:
        """Calculate key unit economics ratios."""
        cac = self.calculate_cac()
        ltv = self.calculate_ltv()

        ltv_net = ltv["ltv_net_eur"]
        cac_val = cac["cost_per_active_player_eur"]

        ltv_cac_ratio = ltv_net / cac_val if cac_val > 0 else 0
        months_to_payback = cac_val / ltv["monthly_contribution_margin_eur"] if ltv["monthly_contribution_margin_eur"] > 0 else float('inf')

        return {
            "ltv_cac_ratio": round(ltv_cac_ratio, 2),
            "payback_period_months": round(months_to_payback, 1),
            "cac_per_active_eur": round(cac_val, 2),
            "ltv_net_eur": round(ltv_net, 2),
            "monthly_contribution_eur": round(ltv["monthly_contribution_margin_eur"], 2),
            "health_assessment": self._assess_health(ltv_cac_ratio, months_to_payback),
            "benchmarks": {
                "target_ltv_cac_ratio": ">3.0",
                "healthy_payback_months": "<12",
                "good_contribution_margin_pct": ">25%",
            },
        }

    def _assess_health(self, ltv_cac: float, payback: float) -> str:
        if ltv_cac >= 4 and payback <= 8:
            return "EXCELLENT - Strong unit economics, scale aggressively"
        elif ltv_cac >= 3 and payback <= 12:
            return "HEALTHY - Sustainable model, optimize for growth"
        elif ltv_cac >= 2 and payback <= 18:
            return "ADEQUATE - Viable but needs improvement in CAC or retention"
        elif ltv_cac >= 1:
            return "WARNING - Marginal profitability, reduce CAC or improve retention"
        return "CRITICAL - Unsustainable, major changes needed before scaling"

    def sensitivity_analysis(self, iterations: int = 3000) -> dict:
        """Monte Carlo simulation varying key inputs."""
        random.seed(42)  # reproducible results
        results = []

        for _ in range(iterations):
            # Vary key assumptions within realistic ranges
            varied = PlayerCohortAssumptions(
                vertical=self.a.vertical,
                jurisdiction=self.a.jurisdiction,
                marketing_spend_per_ftd_eur=self.a.marketing_spend_per_ftd_eur * random.uniform(0.7, 1.5),
                registration_to_ftd_rate_pct=self.a.registration_to_ftd_rate_pct * random.uniform(0.8, 1.2),
                ftd_to_active_rate_pct=self.a.ftd_to_active_rate_pct * random.uniform(0.8, 1.2),
                avg_first_deposit_eur=self.a.avg_first_deposit_eur * random.uniform(0.7, 1.4),
                avg_monthly_deposits_eur=self.a.avg_monthly_deposits_eur * random.uniform(0.7, 1.4),
                avg_monthly_bets_eur=self.a.avg_monthly_bets_eur * random.uniform(0.7, 1.4),
                avg_house_edge_pct=self.a.avg_house_edge_pct * random.uniform(0.9, 1.1),
                avg_monthly_ggr_per_player_eur=self.a.avg_monthly_ggr_per_player_eur * random.uniform(0.6, 1.5),
                avg_bonus_cost_pct_of_ggr=self.a.avg_bonus_cost_pct_of_ggr * random.uniform(0.7, 1.4),
                month1_retention_pct=min(100, self.a.month1_retention_pct * random.uniform(0.85, 1.15)),
                month3_retention_pct=min(100, self.a.month3_retention_pct * random.uniform(0.8, 1.2)),
                month6_retention_pct=min(100, self.a.month6_retention_pct * random.uniform(0.75, 1.25)),
                month12_retention_pct=min(100, self.a.month12_retention_pct * random.uniform(0.7, 1.3)),
                avg_lifetime_months=self.a.avg_lifetime_months * random.uniform(0.7, 1.3),
                payment_processing_pct=self.a.payment_processing_pct * random.uniform(0.8, 1.2),
                game_content_royalty_pct=self.a.game_content_royalty_pct * random.uniform(0.9, 1.1),
                ggr_tax_pct=self.a.ggr_tax_pct,  # tax rate is fixed
                platform_cost_per_player_eur=self.a.platform_cost_per_player_eur * random.uniform(0.8, 1.3),
                support_cost_per_player_eur=self.a.support_cost_per_player_eur * random.uniform(0.8, 1.3),
            )

            model = UnitEconomicsModel(varied)
            ratios = model.calculate_ratios()
            results.append({
                "ltv_cac_ratio": ratios["ltv_cac_ratio"],
                "payback_months": ratios["payback_period_months"],
                "ltv_net_eur": ratios["ltv_net_eur"],
                "cac_eur": ratios["cac_per_active_eur"],
            })

        # Statistical summary
        ltv_cac_values = sorted(r["ltv_cac_ratio"] for r in results)
        payback_values = sorted(r["payback_months"] for r in results if r["payback_months"] < 100)
        ltv_values = sorted(r["ltv_net_eur"] for r in results)

        def percentile(data, pct):
            idx = int(len(data) * pct / 100)
            return data[min(idx, len(data) - 1)]

        return {
            "iterations": iterations,
            "ltv_cac_ratio": {
                "mean": round(sum(ltv_cac_values) / len(ltv_cac_values), 2),
                "p10": round(percentile(ltv_cac_values, 10), 2),
                "p25": round(percentile(ltv_cac_values, 25), 2),
                "median": round(percentile(ltv_cac_values, 50), 2),
                "p75": round(percentile(ltv_cac_values, 75), 2),
                "p90": round(percentile(ltv_cac_values, 90), 2),
                "pct_above_3": round(sum(1 for v in ltv_cac_values if v >= 3) / len(ltv_cac_values) * 100, 1),
                "pct_below_1": round(sum(1 for v in ltv_cac_values if v < 1) / len(ltv_cac_values) * 100, 1),
            },
            "payback_months": {
                "mean": round(sum(payback_values) / len(payback_values), 1) if payback_values else None,
                "median": round(percentile(payback_values, 50), 1) if payback_values else None,
                "p90": round(percentile(payback_values, 90), 1) if payback_values else None,
            },
            "ltv_net_eur": {
                "mean": round(sum(ltv_values) / len(ltv_values), 2),
                "p10": round(percentile(ltv_values, 10), 2),
                "median": round(percentile(ltv_values, 50), 2),
                "p90": round(percentile(ltv_values, 90), 2),
            },
            "risk_summary": {
                "probability_unprofitable_pct": round(
                    sum(1 for v in ltv_cac_values if v < 1) / len(ltv_cac_values) * 100, 1
                ),
                "probability_healthy_pct": round(
                    sum(1 for v in ltv_cac_values if v >= 3) / len(ltv_cac_values) * 100, 1
                ),
            },
        }

    def full_report(self) -> dict:
        """Generate comprehensive unit economics report."""
        return {
            "vertical": self.a.vertical,
            "jurisdiction": self.a.jurisdiction,
            "customer_acquisition": self.calculate_cac(),
            "lifetime_value": self.calculate_ltv(),
            "key_ratios": self.calculate_ratios(),
        }

    def print_report(self):
        """Print formatted unit economics report."""
        report = self.full_report()
        cac = report["customer_acquisition"]
        ltv = report["lifetime_value"]
        ratios = report["key_ratios"]

        print(f"\n{'=' * 80}")
        print(f"  UNIT ECONOMICS: {self.a.vertical.upper()} - {self.a.jurisdiction}")
        print(f"{'=' * 80}")

        print(f"\n  CUSTOMER ACQUISITION COST (CAC)")
        print(f"  {'Cost per FTD:':<40} EUR {cac['cost_per_ftd_eur']:>10,.2f}")
        print(f"  {'Cost per Active Player:':<40} EUR {cac['cost_per_active_player_eur']:>10,.2f}")
        print(f"  {'Registration → FTD Rate:':<40} {cac['registration_to_ftd_rate_pct']:>10.1f}%")
        print(f"  {'FTD → Active Rate:':<40} {cac['ftd_to_active_rate_pct']:>10.1f}%")

        print(f"\n  LIFETIME VALUE (LTV)")
        print(f"  {'Monthly GGR/player:':<40} EUR {ltv['monthly_ggr_per_player_eur']:>10,.2f}")
        print(f"  {'Monthly Bonus Cost:':<40} EUR {ltv['monthly_bonus_cost_eur']:>10,.2f}")
        print(f"  {'Monthly Net GGR:':<40} EUR {ltv['monthly_net_ggr_eur']:>10,.2f}")
        costs = ltv["monthly_variable_costs_eur"]
        print(f"  {'  - Payment Processing:':<40} EUR {costs['payment_processing']:>10,.2f}")
        print(f"  {'  - Content Royalty:':<40} EUR {costs['content_royalty']:>10,.2f}")
        print(f"  {'  - GGR Tax:':<40} EUR {costs['ggr_tax']:>10,.2f}")
        print(f"  {'  - Platform:':<40} EUR {costs['platform']:>10,.2f}")
        print(f"  {'  - Support:':<40} EUR {costs['support']:>10,.2f}")
        print(f"  {'Monthly Contribution Margin:':<40} EUR {ltv['monthly_contribution_margin_eur']:>10,.2f}")
        print(f"  {'Contribution Margin %:':<40} {ltv['contribution_margin_pct']:>10.1f}%")
        print(f"  {'Average Lifetime:':<40} {ltv['avg_lifetime_months']:>10.1f} months")
        print(f"  {'Gross LTV:':<40} EUR {ltv['ltv_gross_eur']:>10,.2f}")
        print(f"  {'Net LTV:':<40} EUR {ltv['ltv_net_eur']:>10,.2f}")

        print(f"\n  KEY RATIOS")
        print(f"  {'LTV:CAC Ratio:':<40} {ratios['ltv_cac_ratio']:>10.2f}x")
        print(f"  {'Payback Period:':<40} {ratios['payback_period_months']:>10.1f} months")
        print(f"  {'Assessment:':<40} {ratios['health_assessment']}")

        print(f"\n  RETENTION CURVE (first 12 months):")
        curve = ltv["retention_curve_24m"][:12]
        bar_width = 40
        for i, pct in enumerate(curve):
            bar = "#" * int(pct / 100 * bar_width)
            print(f"  Month {i:>2}: {bar:<{bar_width}} {pct:>5.1f}%")


def main():
    parser = argparse.ArgumentParser(description="iGaming Unit Economics Model")
    parser.add_argument("--preset", choices=list(PRESETS.keys()),
                        help="Use preset assumptions")
    parser.add_argument("--vertical", choices=["casino", "sports"],
                        help="Product vertical")
    parser.add_argument("--jurisdiction", type=str, default="UK",
                        help="Jurisdiction for tax rates")
    parser.add_argument("--sensitivity", action="store_true",
                        help="Run Monte Carlo sensitivity analysis")
    parser.add_argument("--iterations", type=int, default=3000,
                        help="Sensitivity analysis iterations")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--compare-all", action="store_true",
                        help="Compare all presets")
    args = parser.parse_args()

    if args.compare_all:
        print(f"\n{'=' * 100}")
        print(f"  UNIT ECONOMICS COMPARISON - ALL PRESETS")
        print(f"{'=' * 100}")
        header = f"  {'Preset':<20} {'CAC':>10} {'LTV':>10} {'LTV:CAC':>10} {'Payback':>10} {'Margin%':>10}"
        print(header)
        print(f"  {'-' * 70}")
        for name, assumptions in PRESETS.items():
            model = UnitEconomicsModel(assumptions)
            ratios = model.calculate_ratios()
            ltv = model.calculate_ltv()
            print(f"  {name:<20} "
                  f"EUR {ratios['cac_per_active_eur']:>7,.0f} "
                  f"EUR {ratios['ltv_net_eur']:>7,.0f} "
                  f"{ratios['ltv_cac_ratio']:>9.2f}x "
                  f"{ratios['payback_period_months']:>8.1f}m "
                  f"{ltv['contribution_margin_pct']:>9.1f}%")
        return

    # Select preset or build from vertical/jurisdiction
    if args.preset:
        assumptions = PRESETS[args.preset]
    elif args.vertical == "sports":
        assumptions = SPORTS_UK
    else:
        assumptions = CASINO_UK

    model = UnitEconomicsModel(assumptions)

    if args.sensitivity:
        sensitivity = model.sensitivity_analysis(iterations=args.iterations)
        if args.format == "json":
            print(json.dumps(sensitivity, indent=2))
        else:
            print(f"\n{'=' * 80}")
            print(f"  SENSITIVITY ANALYSIS ({sensitivity['iterations']} iterations)")
            print(f"{'=' * 80}")
            ltv_cac = sensitivity["ltv_cac_ratio"]
            print(f"\n  LTV:CAC Ratio Distribution:")
            print(f"    P10:    {ltv_cac['p10']:>6.2f}x")
            print(f"    P25:    {ltv_cac['p25']:>6.2f}x")
            print(f"    Median: {ltv_cac['median']:>6.2f}x")
            print(f"    Mean:   {ltv_cac['mean']:>6.2f}x")
            print(f"    P75:    {ltv_cac['p75']:>6.2f}x")
            print(f"    P90:    {ltv_cac['p90']:>6.2f}x")
            print(f"\n  Risk Assessment:")
            print(f"    Probability of loss (LTV:CAC < 1): {sensitivity['risk_summary']['probability_unprofitable_pct']}%")
            print(f"    Probability of healthy (LTV:CAC > 3): {sensitivity['risk_summary']['probability_healthy_pct']}%")
        return

    if args.format == "json":
        print(json.dumps(model.full_report(), indent=2, default=str))
    else:
        model.print_report()


if __name__ == "__main__":
    main()
