#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 05, Differences Between Betting Sites and Online Casinos.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Chapter 3: Betting vs Casino - Hybrid Platform Financial Model

Financial modeling tool for operators evaluating betting-only, casino-only,
or hybrid (betting + casino) platform strategies. Produces:
- GGR (Gross Gaming Revenue) projections by vertical
- NGR (Net Gaming Revenue) after taxes, bonuses, and provider fees
- Player lifetime value (LTV) per vertical
- EBITDA projections with cost structure breakdown
- Break-even analysis and ROI timeline
- Scenario modeling (base, optimistic, pessimistic)
- Market cannibalization effects for hybrid platforms

Usage:
    model = HybridPlatformModel(jurisdiction="uk")
    results = model.run_projection(months=36)
    print(results.summary())
"""

import json
import math
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── Jurisdiction Tax & Regulatory Data ────────────────────────────────

JURISDICTION_DATA = {
    "uk": {
        "name": "United Kingdom (UKGC)",
        "ggr_tax_rate": 0.21,                # 21% POC tax
        "vat_applicable": False,
        "license_cost_annual": 150_000,       # UKGC license maintenance
        "setup_cost": 500_000,
        "min_capital_requirement": 1_000_000,
        "betting_allowed": True,
        "casino_allowed": True,
        "responsible_gambling_levy": 0.01,    # 1% of GGR
    },
    "malta": {
        "name": "Malta (MGA)",
        "ggr_tax_rate": 0.05,                # 5% gaming tax
        "vat_applicable": False,
        "license_cost_annual": 25_000,
        "setup_cost": 200_000,
        "min_capital_requirement": 500_000,
        "betting_allowed": True,
        "casino_allowed": True,
        "responsible_gambling_levy": 0.004,
    },
    "curacao": {
        "name": "Curacao",
        "ggr_tax_rate": 0.02,
        "vat_applicable": False,
        "license_cost_annual": 12_000,
        "setup_cost": 50_000,
        "min_capital_requirement": 100_000,
        "betting_allowed": True,
        "casino_allowed": True,
        "responsible_gambling_levy": 0.0,
    },
    "new_jersey": {
        "name": "New Jersey (DGE)",
        "ggr_tax_rate": 0.15,               # 15% + 1.25% community investment
        "vat_applicable": False,
        "license_cost_annual": 250_000,
        "setup_cost": 1_000_000,
        "min_capital_requirement": 5_000_000,
        "betting_allowed": True,
        "casino_allowed": True,
        "responsible_gambling_levy": 0.0125,
    },
    "ontario": {
        "name": "Ontario (iGO/AGCO)",
        "ggr_tax_rate": 0.20,
        "vat_applicable": False,
        "license_cost_annual": 100_000,
        "setup_cost": 500_000,
        "min_capital_requirement": 2_000_000,
        "betting_allowed": True,
        "casino_allowed": True,
        "responsible_gambling_levy": 0.005,
    },
    "brazil": {
        "name": "Brazil (SIGAP)",
        "ggr_tax_rate": 0.12,
        "vat_applicable": True,
        "license_cost_annual": 300_000,
        "setup_cost": 1_500_000,
        "min_capital_requirement": 6_000_000,  # BRL 30M equivalent
        "betting_allowed": True,
        "casino_allowed": False,              # Casino not regulated yet
        "responsible_gambling_levy": 0.005,
    },
}


# ── Vertical-Specific Parameters ─────────────────────────────────────

@dataclass
class VerticalParams:
    """Financial parameters for a single vertical (betting or casino)."""
    name: str
    avg_ggr_per_player_monthly: float    # Average GGR per active player/month
    house_edge_avg: float                # Average theoretical house edge
    bonus_cost_pct: float                # Bonus cost as % of GGR
    provider_fee_pct: float              # Content/feed provider fees as % of GGR
    payment_processing_pct: float        # Payment processing cost as % of turnover
    avg_turnover_per_player: float       # Monthly turnover per active player
    player_acquisition_cost: float       # CAC per new player
    player_retention_rate: float         # Monthly retention rate
    avg_lifetime_months: float           # Average player lifetime
    organic_growth_rate: float           # Monthly organic player growth
    fixed_cost_monthly: float            # Platform/team fixed costs
    staff_count: int                     # FTE headcount
    avg_salary: float                    # Average monthly salary


BETTING_PARAMS = VerticalParams(
    name="Sportsbook",
    avg_ggr_per_player_monthly=85,
    house_edge_avg=0.065,                # ~6.5% average overround
    bonus_cost_pct=0.15,                 # Free bets, enhanced odds
    provider_fee_pct=0.08,               # Data feeds, odds providers
    payment_processing_pct=0.018,
    avg_turnover_per_player=1_300,
    player_acquisition_cost=180,
    player_retention_rate=0.88,
    avg_lifetime_months=14,
    organic_growth_rate=0.03,
    fixed_cost_monthly=120_000,
    staff_count=25,                      # Traders, developers, ops
    avg_salary=5_500,
)

CASINO_PARAMS = VerticalParams(
    name="Casino",
    avg_ggr_per_player_monthly=120,
    house_edge_avg=0.035,                # ~3.5% average (slots+table mix)
    bonus_cost_pct=0.12,                 # Welcome bonuses, free spins
    provider_fee_pct=0.15,               # Game provider royalties
    payment_processing_pct=0.02,
    avg_turnover_per_player=3_400,
    player_acquisition_cost=150,
    player_retention_rate=0.82,
    avg_lifetime_months=11,
    organic_growth_rate=0.04,
    fixed_cost_monthly=80_000,
    staff_count=15,
    avg_salary=5_000,
)


class Scenario(Enum):
    PESSIMISTIC = "pessimistic"
    BASE = "base"
    OPTIMISTIC = "optimistic"


SCENARIO_MULTIPLIERS = {
    Scenario.PESSIMISTIC: {
        "ggr_mult": 0.70,
        "cac_mult": 1.30,
        "retention_adj": -0.05,
        "growth_mult": 0.60,
    },
    Scenario.BASE: {
        "ggr_mult": 1.0,
        "cac_mult": 1.0,
        "retention_adj": 0.0,
        "growth_mult": 1.0,
    },
    Scenario.OPTIMISTIC: {
        "ggr_mult": 1.25,
        "cac_mult": 0.80,
        "retention_adj": 0.03,
        "growth_mult": 1.40,
    },
}


@dataclass
class MonthlyResult:
    month: int
    active_players: int
    new_players: int
    churned_players: int
    turnover: float
    ggr: float
    bonus_cost: float
    provider_fees: float
    payment_fees: float
    tax: float
    levy: float
    ngr: float
    acquisition_cost: float
    fixed_costs: float
    staff_costs: float
    ebitda: float
    cumulative_ebitda: float


@dataclass
class ProjectionResult:
    vertical: str
    jurisdiction: str
    scenario: str
    months: list[MonthlyResult]
    initial_investment: float
    break_even_month: Optional[int]
    total_ggr: float
    total_ngr: float
    total_ebitda: float
    avg_ltv: float
    roi_pct: float

    def summary(self) -> str:
        lines = [
            f"{'=' * 60}",
            f"FINANCIAL PROJECTION: {self.vertical} ({self.scenario})",
            f"Jurisdiction: {self.jurisdiction}",
            f"{'=' * 60}",
            f"Initial Investment:    ${self.initial_investment:>12,.2f}",
            f"Total GGR (period):    ${self.total_ggr:>12,.2f}",
            f"Total NGR (period):    ${self.total_ngr:>12,.2f}",
            f"Total EBITDA (period): ${self.total_ebitda:>12,.2f}",
            f"Player LTV:            ${self.avg_ltv:>12,.2f}",
            f"ROI:                   {self.roi_pct:>12.1f}%",
            f"Break-even:            Month {self.break_even_month or 'N/A'}",
            f"{'=' * 60}",
        ]
        return "\n".join(lines)


class HybridPlatformModel:
    """
    Financial projection model for iGaming platform evaluation.

    Compares betting-only, casino-only, and hybrid strategies
    across multiple scenarios and jurisdictions.
    """

    def __init__(self, jurisdiction: str = "uk"):
        if jurisdiction not in JURISDICTION_DATA:
            raise ValueError(f"Unknown jurisdiction: {jurisdiction}. Available: {list(JURISDICTION_DATA.keys())}")
        self.jurisdiction = jurisdiction
        self.jur_data = JURISDICTION_DATA[jurisdiction]

    def run_projection(
        self,
        vertical_params: VerticalParams,
        initial_players: int = 500,
        monthly_new_players: int = 200,
        months: int = 36,
        scenario: Scenario = Scenario.BASE,
    ) -> ProjectionResult:
        """Run a financial projection for a single vertical."""
        mult = SCENARIO_MULTIPLIERS[scenario]
        jur = self.jur_data

        active_players = initial_players
        cumulative_ebitda = -(jur["setup_cost"] + jur["min_capital_requirement"])  # ty:ignore[unsupported-operator]
        initial_investment = abs(cumulative_ebitda)
        results = []
        break_even_month = None

        for m in range(1, months + 1):
            # Player dynamics
            growth = max(1, int(monthly_new_players * mult["growth_mult"] * (1 + vertical_params.organic_growth_rate * m / 12)))
            retention = min(0.99, vertical_params.player_retention_rate + mult["retention_adj"])
            churned = int(active_players * (1 - retention))
            active_players = active_players - churned + growth

            # Revenue
            ggr_per_player = vertical_params.avg_ggr_per_player_monthly * mult["ggr_mult"]
            turnover = active_players * vertical_params.avg_turnover_per_player * mult["ggr_mult"]
            ggr = active_players * ggr_per_player

            # Costs
            bonus_cost = ggr * vertical_params.bonus_cost_pct
            provider_fees = ggr * vertical_params.provider_fee_pct
            payment_fees = turnover * vertical_params.payment_processing_pct
            tax = ggr * jur["ggr_tax_rate"]  # ty:ignore[unsupported-operator]
            levy = ggr * jur["responsible_gambling_levy"]  # ty:ignore[unsupported-operator]
            ngr = ggr - bonus_cost - provider_fees - payment_fees - tax - levy

            # Operating costs
            acquisition_cost = growth * vertical_params.player_acquisition_cost * mult["cac_mult"]
            fixed_costs = vertical_params.fixed_cost_monthly
            staff_costs = vertical_params.staff_count * vertical_params.avg_salary
            license_monthly = jur["license_cost_annual"] / 12  # ty:ignore[unsupported-operator]

            ebitda = ngr - acquisition_cost - fixed_costs - staff_costs - license_monthly
            cumulative_ebitda += ebitda

            if break_even_month is None and cumulative_ebitda > 0:
                break_even_month = m

            results.append(MonthlyResult(
                month=m, active_players=active_players,
                new_players=growth, churned_players=churned,
                turnover=turnover, ggr=ggr,
                bonus_cost=bonus_cost, provider_fees=provider_fees,
                payment_fees=payment_fees, tax=tax, levy=levy, ngr=ngr,
                acquisition_cost=acquisition_cost,
                fixed_costs=fixed_costs + license_monthly,
                staff_costs=staff_costs, ebitda=ebitda,
                cumulative_ebitda=cumulative_ebitda,
            ))

        # LTV calculation
        avg_ltv = (
            vertical_params.avg_ggr_per_player_monthly
            * (1 - vertical_params.bonus_cost_pct - vertical_params.provider_fee_pct - jur["ggr_tax_rate"])  # ty:ignore[unsupported-operator]
            * vertical_params.avg_lifetime_months
        )

        total_ggr = sum(r.ggr for r in results)
        total_ngr = sum(r.ngr for r in results)
        total_ebitda = sum(r.ebitda for r in results)

        return ProjectionResult(
            vertical=vertical_params.name,
            jurisdiction=jur["name"],  # ty:ignore[invalid-argument-type]
            scenario=scenario.value,
            months=results,
            initial_investment=initial_investment,
            break_even_month=break_even_month,
            total_ggr=total_ggr,
            total_ngr=total_ngr,
            total_ebitda=total_ebitda,
            avg_ltv=avg_ltv,
            roi_pct=(total_ebitda / initial_investment * 100) if initial_investment > 0 else 0,
        )

    def compare_strategies(
        self,
        months: int = 36,
        initial_players: int = 500,
        monthly_new_players: int = 200,
        scenario: Scenario = Scenario.BASE,
    ) -> dict:
        """
        Compare betting-only, casino-only, and hybrid strategies.

        For hybrid, models cross-sell effects and shared infrastructure savings.
        """
        results = {}

        # Betting only
        if self.jur_data["betting_allowed"]:
            results["betting"] = self.run_projection(
                BETTING_PARAMS, initial_players, monthly_new_players, months, scenario
            )

        # Casino only
        if self.jur_data["casino_allowed"]:
            results["casino"] = self.run_projection(
                CASINO_PARAMS, initial_players, monthly_new_players, months, scenario
            )

        # Hybrid (if both allowed)
        if self.jur_data["betting_allowed"] and self.jur_data["casino_allowed"]:
            # Hybrid benefits: shared infrastructure, cross-sell, but higher complexity
            hybrid_betting = VerticalParams(
                name="Hybrid-Betting",
                avg_ggr_per_player_monthly=BETTING_PARAMS.avg_ggr_per_player_monthly * 1.15,  # Cross-sell uplift
                house_edge_avg=BETTING_PARAMS.house_edge_avg,
                bonus_cost_pct=BETTING_PARAMS.bonus_cost_pct * 0.90,  # Shared bonus pool efficiency
                provider_fee_pct=BETTING_PARAMS.provider_fee_pct,
                payment_processing_pct=BETTING_PARAMS.payment_processing_pct * 0.85,  # Volume discount
                avg_turnover_per_player=BETTING_PARAMS.avg_turnover_per_player * 1.10,
                player_acquisition_cost=BETTING_PARAMS.player_acquisition_cost * 0.70,  # Shared CAC
                player_retention_rate=min(0.95, BETTING_PARAMS.player_retention_rate + 0.05),  # Better retention
                avg_lifetime_months=BETTING_PARAMS.avg_lifetime_months + 4,  # Longer LTV
                organic_growth_rate=BETTING_PARAMS.organic_growth_rate * 1.2,
                fixed_cost_monthly=BETTING_PARAMS.fixed_cost_monthly * 0.65,  # Shared infra
                staff_count=BETTING_PARAMS.staff_count,
                avg_salary=BETTING_PARAMS.avg_salary,
            )
            hybrid_casino = VerticalParams(
                name="Hybrid-Casino",
                avg_ggr_per_player_monthly=CASINO_PARAMS.avg_ggr_per_player_monthly * 1.10,
                house_edge_avg=CASINO_PARAMS.house_edge_avg,
                bonus_cost_pct=CASINO_PARAMS.bonus_cost_pct * 0.90,
                provider_fee_pct=CASINO_PARAMS.provider_fee_pct,
                payment_processing_pct=CASINO_PARAMS.payment_processing_pct * 0.85,
                avg_turnover_per_player=CASINO_PARAMS.avg_turnover_per_player * 1.05,
                player_acquisition_cost=CASINO_PARAMS.player_acquisition_cost * 0.60,
                player_retention_rate=min(0.95, CASINO_PARAMS.player_retention_rate + 0.06),
                avg_lifetime_months=CASINO_PARAMS.avg_lifetime_months + 5,
                organic_growth_rate=CASINO_PARAMS.organic_growth_rate * 1.2,
                fixed_cost_monthly=CASINO_PARAMS.fixed_cost_monthly * 0.55,
                staff_count=CASINO_PARAMS.staff_count,
                avg_salary=CASINO_PARAMS.avg_salary,
            )

            bet_proj = self.run_projection(hybrid_betting, initial_players, int(monthly_new_players * 0.6), months, scenario)
            cas_proj = self.run_projection(hybrid_casino, initial_players, int(monthly_new_players * 0.5), months, scenario)

            results["hybrid"] = {
                "betting_vertical": bet_proj,
                "casino_vertical": cas_proj,
                "combined_ggr": bet_proj.total_ggr + cas_proj.total_ggr,
                "combined_ngr": bet_proj.total_ngr + cas_proj.total_ngr,
                "combined_ebitda": bet_proj.total_ebitda + cas_proj.total_ebitda,
                "combined_roi": (
                    (bet_proj.total_ebitda + cas_proj.total_ebitda)
                    / (bet_proj.initial_investment + cas_proj.initial_investment) * 100
                ),
            }

        return results

    def sensitivity_analysis(self, months: int = 24) -> dict:
        """Run all three scenarios and compare outcomes."""
        analysis = {}
        for scenario in Scenario:
            comparison = self.compare_strategies(months=months, scenario=scenario)
            analysis[scenario.value] = {}
            for strategy, result in comparison.items():
                if strategy == "hybrid":
                    analysis[scenario.value][strategy] = {
                        "total_ggr": result["combined_ggr"],
                        "total_ebitda": result["combined_ebitda"],
                        "roi_pct": result["combined_roi"],
                    }
                else:
                    analysis[scenario.value][strategy] = {
                        "total_ggr": result.total_ggr,
                        "total_ebitda": result.total_ebitda,
                        "roi_pct": result.roi_pct,
                        "break_even_month": result.break_even_month,
                    }
        return analysis


# ── Demo ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 72)
    print("HYBRID PLATFORM FINANCIAL MODEL - iGaming")
    print("=" * 72)

    # Compare jurisdictions
    for jur_code in ["uk", "malta", "curacao", "new_jersey", "brazil"]:
        model = HybridPlatformModel(jurisdiction=jur_code)
        jur = JURISDICTION_DATA[jur_code]

        print(f"\n{'─' * 72}")
        print(f"Jurisdiction: {jur['name']} | GGR Tax: {jur['ggr_tax_rate']*100:.0f}%")
        print(f"{'─' * 72}")

        comparison = model.compare_strategies(months=36)

        for strategy, result in comparison.items():
            if strategy == "hybrid":
                print(f"\n  HYBRID Platform:")
                print(f"    Combined GGR:    ${result['combined_ggr']:>12,.2f}")
                print(f"    Combined NGR:    ${result['combined_ngr']:>12,.2f}")
                print(f"    Combined EBITDA: ${result['combined_ebitda']:>12,.2f}")
                print(f"    Combined ROI:    {result['combined_roi']:>12.1f}%")
            else:
                print(f"\n  {result.vertical}:")
                print(f"    Total GGR:       ${result.total_ggr:>12,.2f}")
                print(f"    Total NGR:       ${result.total_ngr:>12,.2f}")
                print(f"    Total EBITDA:    ${result.total_ebitda:>12,.2f}")
                print(f"    Player LTV:      ${result.avg_ltv:>12,.2f}")
                print(f"    ROI:             {result.roi_pct:>12.1f}%")
                print(f"    Break-even:      Month {result.break_even_month or 'N/A'}")

    # Sensitivity analysis
    print(f"\n{'=' * 72}")
    print("SENSITIVITY ANALYSIS (UK, 24 months)")
    print(f"{'=' * 72}")
    model = HybridPlatformModel("uk")
    sensitivity = model.sensitivity_analysis(months=24)
    print(json.dumps(sensitivity, indent=2, default=str))

    # Detailed monthly projection
    print(f"\n{'=' * 72}")
    print("MONTHLY PROJECTION - UK Casino (Base, 12 months)")
    print(f"{'=' * 72}")
    model = HybridPlatformModel("uk")
    proj = model.run_projection(CASINO_PARAMS, months=12)
    print(f"{'Month':>5} {'Players':>8} {'GGR':>12} {'NGR':>12} {'EBITDA':>12} {'Cumulative':>14}")
    print("-" * 72)
    for r in proj.months:
        print(f"{r.month:>5} {r.active_players:>8,} ${r.ggr:>10,.0f} ${r.ngr:>10,.0f} "
              f"${r.ebitda:>10,.0f} ${r.cumulative_ebitda:>12,.0f}")
