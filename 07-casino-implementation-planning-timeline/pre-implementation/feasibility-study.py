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
Casino Platform Feasibility Study Calculator

Generates comprehensive financial feasibility analysis for online casino launches
with market-specific inputs including GGR projections, licensing costs, operating
expenses, and break-even analysis.

Usage:
    python3 feasibility-study.py --market uk --players 50000 --avg-deposit 75
    python3 feasibility-study.py --interactive
    python3 feasibility-study.py --market malta --export report.json
"""

import argparse
import json
import math
import sys
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Market-specific reference data
# ---------------------------------------------------------------------------

MARKET_DATA = {
    "uk": {
        "name": "United Kingdom",
        "regulator": "UK Gambling Commission (UKGC)",
        "license_application_fee": 16_142,       # GBP converted approx
        "annual_license_fee": 35_610,
        "gaming_tax_rate": 0.21,                  # 21% Remote Gaming Duty
        "avg_player_value_monthly": 62.0,         # GBP avg monthly GGR per player
        "player_acquisition_cost": 180.0,         # CPA
        "market_size_estimate": 5_700_000,        # active online gambling accounts
        "currency": "GBP",
        "vat_rate": 0.20,
        "compliance_cost_monthly": 45_000,
        "required_reserve_months": 6,
        "estimated_license_timeline_months": 6,
        "responsible_gambling_levy": 0.01,        # 1% of GGR
        "aml_compliance_annual": 120_000,
        "technical_standards": "RTS",
        "data_protection": "UK GDPR / ICO",
    },
    "malta": {
        "name": "Malta",
        "regulator": "Malta Gaming Authority (MGA)",
        "license_application_fee": 25_000,
        "annual_license_fee": 25_000,
        "gaming_tax_rate": 0.05,                  # 5% on GGR
        "avg_player_value_monthly": 48.0,
        "player_acquisition_cost": 120.0,
        "market_size_estimate": 800_000,
        "currency": "EUR",
        "vat_rate": 0.18,
        "compliance_cost_monthly": 30_000,
        "required_reserve_months": 3,
        "estimated_license_timeline_months": 4,
        "responsible_gambling_levy": 0.004,
        "aml_compliance_annual": 80_000,
        "technical_standards": "MGA Technical Standards",
        "data_protection": "GDPR",
    },
    "ontario": {
        "name": "Ontario, Canada",
        "regulator": "Alcohol and Gaming Commission of Ontario (AGCO) / iGO",
        "license_application_fee": 100_000,
        "annual_license_fee": 100_000,
        "gaming_tax_rate": 0.20,                  # 20% revenue share to iGO
        "avg_player_value_monthly": 55.0,
        "player_acquisition_cost": 200.0,
        "market_size_estimate": 1_500_000,
        "currency": "CAD",
        "vat_rate": 0.13,
        "compliance_cost_monthly": 40_000,
        "required_reserve_months": 3,
        "estimated_license_timeline_months": 8,
        "responsible_gambling_levy": 0.005,
        "aml_compliance_annual": 100_000,
        "technical_standards": "iGO Standards",
        "data_protection": "PIPEDA",
    },
    "new_jersey": {
        "name": "New Jersey, USA",
        "regulator": "NJ Division of Gaming Enforcement (DGE)",
        "license_application_fee": 400_000,
        "annual_license_fee": 250_000,
        "gaming_tax_rate": 0.175,                 # 17.5% internet gaming tax + 2.5% community
        "avg_player_value_monthly": 70.0,
        "player_acquisition_cost": 350.0,
        "market_size_estimate": 1_200_000,
        "currency": "USD",
        "vat_rate": 0.0,
        "compliance_cost_monthly": 60_000,
        "required_reserve_months": 6,
        "estimated_license_timeline_months": 12,
        "responsible_gambling_levy": 0.0,
        "aml_compliance_annual": 200_000,
        "technical_standards": "NJ DGE Technical Standards",
        "data_protection": "State Privacy Laws",
    },
    "sweden": {
        "name": "Sweden",
        "regulator": "Spelinspektionen",
        "license_application_fee": 40_000,
        "annual_license_fee": 40_000,
        "gaming_tax_rate": 0.18,
        "avg_player_value_monthly": 45.0,
        "player_acquisition_cost": 160.0,
        "market_size_estimate": 900_000,
        "currency": "SEK",
        "vat_rate": 0.25,
        "compliance_cost_monthly": 35_000,
        "required_reserve_months": 3,
        "estimated_license_timeline_months": 5,
        "responsible_gambling_levy": 0.007,
        "aml_compliance_annual": 90_000,
        "technical_standards": "SPER Standards",
        "data_protection": "GDPR",
    },
    "brazil": {
        "name": "Brazil",
        "regulator": "Secretaria de Premios e Apostas (SPA)",
        "license_application_fee": 6_000_000,     # R$30M converted approx
        "annual_license_fee": 1_000_000,
        "gaming_tax_rate": 0.12,
        "avg_player_value_monthly": 25.0,
        "player_acquisition_cost": 40.0,
        "market_size_estimate": 30_000_000,
        "currency": "BRL",
        "vat_rate": 0.0,
        "compliance_cost_monthly": 50_000,
        "required_reserve_months": 6,
        "estimated_license_timeline_months": 10,
        "responsible_gambling_levy": 0.005,
        "aml_compliance_annual": 60_000,
        "technical_standards": "SPA Technical Standards / SIGAP",
        "data_protection": "LGPD",
    },
}


@dataclass
class FeasibilityInputs:
    """All inputs required for a feasibility study."""
    market: str
    target_players_year1: int = 50_000
    avg_monthly_deposit: float = 75.0
    house_edge: float = 0.04               # 4% average across all games
    monthly_player_growth_rate: float = 0.08  # 8% month-over-month growth
    churn_rate_monthly: float = 0.05        # 5% monthly churn
    num_game_providers: int = 5
    game_provider_revenue_share: float = 0.12  # 12% of GGR to game providers
    platform_dev_cost: float = 800_000.0
    team_monthly_cost: float = 120_000.0
    marketing_budget_year1: float = 500_000.0
    hosting_monthly_base: float = 15_000.0
    payment_processing_rate: float = 0.025  # 2.5% of deposits
    funding_buffer: float = 0.30            # 30% buffer
    projection_months: int = 36


@dataclass
class MonthlyProjection:
    """Single month financial projection."""
    month: int
    active_players: int
    new_players: int
    churned_players: int
    total_deposits: float
    total_withdrawals: float
    ggr: float
    ngr: float
    gaming_tax: float
    game_provider_cost: float
    payment_processing: float
    team_cost: float
    hosting_cost: float
    marketing_spend: float
    compliance_cost: float
    total_revenue: float
    total_costs: float
    monthly_profit: float
    cumulative_profit: float
    cash_position: float


@dataclass
class FeasibilityReport:
    """Complete feasibility study output."""
    generated_at: str
    market: dict
    inputs: dict
    initial_investment: float
    total_funding_required: float
    funding_with_buffer: float
    break_even_month: Optional[int]
    roi_36_months: Optional[float]
    monthly_projections: list
    risk_assessment: dict
    recommendations: list


def calculate_projections(inputs: FeasibilityInputs) -> FeasibilityReport:
    """Run the full feasibility calculation."""
    market = MARKET_DATA.get(inputs.market)
    if market is None:
        logger.error(f"Unknown market: {inputs.market}. Available: {list(MARKET_DATA.keys())}")
        sys.exit(1)

    assert market is not None
    logger.info(f"Calculating feasibility for {market['name']} market")

    # --- Initial investment calculation ---
    initial_investment = (
        market["license_application_fee"]
        + inputs.platform_dev_cost  # ty:ignore[unsupported-operator]
        + (inputs.team_monthly_cost * market["required_reserve_months"])  # ty:ignore[unsupported-operator]
        + (inputs.hosting_monthly_base * 3)  # 3 months pre-launch hosting
        + market["aml_compliance_annual"]
        + 50_000  # legal and consultancy
        + 30_000  # RNG certification and penetration testing
    )

    # Monthly projections
    projections = []
    cumulative_profit = -initial_investment
    active_players = 0
    monthly_marketing = inputs.marketing_budget_year1 / 12

    for month in range(1, inputs.projection_months + 1):
        # Player acquisition model
        if month <= 12:
            marketing = monthly_marketing
        elif month <= 24:
            marketing = monthly_marketing * 0.7  # reduce spend as brand grows
        else:
            marketing = monthly_marketing * 0.5

        # New players from marketing spend
        cpa = market["player_acquisition_cost"]
        new_players = int(marketing / cpa) if cpa > 0 else 0  # ty:ignore[unsupported-operator]

        # Organic growth after month 6
        if month > 6:
            organic = int(active_players * 0.02)  # 2% organic growth
            new_players += organic

        # Churn
        churned = int(active_players * inputs.churn_rate_monthly)

        # Active player count
        active_players = max(0, active_players + new_players - churned)

        # Revenue model
        avg_deposits = inputs.avg_monthly_deposit * active_players
        avg_withdrawals = avg_deposits * (1 - inputs.house_edge) * 0.85  # not all losses withdrawn
        ggr = avg_deposits * inputs.house_edge * active_players / max(active_players, 1)
        # Simplified: GGR = deposits * house_edge
        ggr = avg_deposits * inputs.house_edge

        # Deductions from GGR -> NGR
        gaming_tax = ggr * market["gaming_tax_rate"]  # ty:ignore[unsupported-operator]
        rg_levy = ggr * market["responsible_gambling_levy"]  # ty:ignore[unsupported-operator]
        game_provider_cost = ggr * inputs.game_provider_revenue_share
        ngr = ggr - gaming_tax - rg_levy - game_provider_cost

        # Operating costs
        payment_processing = avg_deposits * inputs.payment_processing_rate
        team_cost = inputs.team_monthly_cost
        if month > 12:
            team_cost *= 1.15  # team grows ~15% in year 2+

        hosting = inputs.hosting_monthly_base
        if active_players > 20_000:
            hosting *= 1.5
        if active_players > 50_000:
            hosting *= 2.0

        compliance = market["compliance_cost_monthly"]
        annual_license = market["annual_license_fee"] / 12  # ty:ignore[unsupported-operator]

        total_revenue = ngr
        total_costs = (
            payment_processing
            + team_cost
            + hosting
            + marketing
            + compliance  # ty:ignore[unsupported-operator]
            + annual_license
        )

        monthly_profit = total_revenue - total_costs
        cumulative_profit += monthly_profit

        proj = MonthlyProjection(
            month=month,
            active_players=active_players,
            new_players=new_players,
            churned_players=churned,
            total_deposits=round(avg_deposits, 2),
            total_withdrawals=round(avg_withdrawals, 2),
            ggr=round(ggr, 2),
            ngr=round(ngr, 2),
            gaming_tax=round(gaming_tax, 2),
            game_provider_cost=round(game_provider_cost, 2),
            payment_processing=round(payment_processing, 2),
            team_cost=round(team_cost, 2),
            hosting_cost=round(hosting, 2),
            marketing_spend=round(marketing, 2),
            compliance_cost=round(compliance, 2),  # ty:ignore[no-matching-overload]
            total_revenue=round(total_revenue, 2),
            total_costs=round(total_costs, 2),
            monthly_profit=round(monthly_profit, 2),
            cumulative_profit=round(cumulative_profit, 2),
            cash_position=round(cumulative_profit + initial_investment, 2),
        )
        projections.append(proj)

    # Break-even analysis
    break_even_month = None
    for p in projections:
        if p.cumulative_profit >= 0:
            break_even_month = p.month
            break

    # Total funding required (sum of all negative months)
    max_negative = min(p.cumulative_profit for p in projections)
    total_funding = abs(max_negative) if max_negative < 0 else initial_investment
    total_funding = max(total_funding, initial_investment)
    funding_with_buffer = total_funding * (1 + inputs.funding_buffer)

    # ROI at 36 months
    final_cumulative = projections[-1].cumulative_profit if projections else 0
    roi_36 = (final_cumulative / funding_with_buffer) * 100 if funding_with_buffer > 0 else None

    # Risk assessment
    risk = _assess_risks(inputs, market, break_even_month, funding_with_buffer)

    # Recommendations
    recommendations = _generate_recommendations(inputs, market, break_even_month, projections)

    report = FeasibilityReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        market=market,
        inputs=asdict(inputs),
        initial_investment=round(initial_investment, 2),
        total_funding_required=round(total_funding, 2),
        funding_with_buffer=round(funding_with_buffer, 2),
        break_even_month=break_even_month,
        roi_36_months=round(roi_36, 2) if roi_36 else None,
        monthly_projections=[asdict(p) for p in projections],
        risk_assessment=risk,
        recommendations=recommendations,
    )
    return report


def _assess_risks(inputs, market, break_even, funding):
    """Generate risk assessment based on inputs."""
    risks = []

    if break_even is None or break_even > 24:
        risks.append({
            "category": "Financial",
            "severity": "HIGH",
            "description": "Break-even exceeds 24 months - high cash burn risk",
            "mitigation": "Reduce initial scope, negotiate better game provider terms, "
                          "or increase marketing efficiency",
        })

    if funding > 5_000_000:
        risks.append({
            "category": "Financial",
            "severity": "HIGH",
            "description": f"Total funding requirement ({funding:,.0f}) is substantial",
            "mitigation": "Consider phased market entry or strategic partnership",
        })

    if market["estimated_license_timeline_months"] > 6:
        risks.append({
            "category": "Regulatory",
            "severity": "MEDIUM",
            "description": f"License timeline ({market['estimated_license_timeline_months']} months) "
                           "creates extended pre-revenue period",
            "mitigation": "Begin application immediately; consider interim B2B partnership",
        })

    if market["gaming_tax_rate"] > 0.15:
        risks.append({
            "category": "Tax",
            "severity": "MEDIUM",
            "description": f"Gaming tax rate ({market['gaming_tax_rate']:.0%}) significantly "
                           "impacts net gaming revenue",
            "mitigation": "Model scenarios with higher player volumes to offset tax burden",
        })

    if inputs.churn_rate_monthly > 0.07:
        risks.append({
            "category": "Operational",
            "severity": "MEDIUM",
            "description": "Monthly churn rate above 7% will erode player base quickly",
            "mitigation": "Invest in retention programs, loyalty schemes, and player engagement",
        })

    risks.append({
        "category": "Technical",
        "severity": "LOW",
        "description": "Platform development delay risk",
        "mitigation": "Use proven platform components, maintain 20% schedule buffer",
    })

    return {
        "overall_risk": "HIGH" if any(r["severity"] == "HIGH" for r in risks) else "MEDIUM",
        "risks": risks,
    }


def _generate_recommendations(inputs, market, break_even, projections):
    """Generate actionable recommendations."""
    recs = []

    if break_even and break_even > 18:
        recs.append(
            "Consider launching with a smaller game portfolio (top 200 games) to reduce "
            "provider costs while building player base."
        )

    if market["player_acquisition_cost"] > 200:
        recs.append(
            f"Player acquisition cost ({market['player_acquisition_cost']}) is high for "
            f"{market['name']}. Invest in SEO and content marketing to build organic channels."
        )

    if inputs.num_game_providers > 4:
        recs.append(
            "Start with 3 tier-1 game providers (e.g., Evolution, Pragmatic Play, NetEnt) "
            "and add more post-launch based on player demand data."
        )

    year1_end = projections[11] if len(projections) >= 12 else projections[-1]
    if year1_end.active_players < inputs.target_players_year1 * 0.5:
        recs.append(
            "Year-1 player target may be aggressive. Build conservative and optimistic "
            "scenarios to stress-test the financial model."
        )

    recs.append(
        f"Secure {inputs.funding_buffer:.0%} funding buffer above projected costs. "
        f"Gambling startups frequently exceed budget by 20-40% in the first year."
    )

    recs.append(
        "Engage regulatory counsel in the target jurisdiction before submitting the "
        "license application to avoid costly resubmissions."
    )

    return recs


def print_summary(report: FeasibilityReport):
    """Print a human-readable summary to stdout."""
    m = report.market
    print("\n" + "=" * 72)
    print(f"  FEASIBILITY STUDY - {m['name'].upper()}")
    print(f"  Generated: {report.generated_at}")
    print("=" * 72)

    print(f"\n  Regulator:            {m['regulator']}")
    print(f"  Gaming Tax Rate:      {m['gaming_tax_rate']:.1%}")
    print(f"  License Application:  {m['currency']} {m['license_application_fee']:>12,.0f}")
    print(f"  Annual License Fee:   {m['currency']} {m['annual_license_fee']:>12,.0f}")

    print(f"\n  INVESTMENT SUMMARY")
    print(f"  {'-' * 50}")
    print(f"  Initial Investment:   {m['currency']} {report.initial_investment:>12,.0f}")
    print(f"  Total Funding Needed: {m['currency']} {report.total_funding_required:>12,.0f}")
    print(f"  With 30% Buffer:      {m['currency']} {report.funding_with_buffer:>12,.0f}")

    if report.break_even_month:
        print(f"\n  Break-Even Month:     Month {report.break_even_month}")
    else:
        print(f"\n  Break-Even Month:     NOT REACHED in {report.inputs['projection_months']} months")

    if report.roi_36_months is not None:
        print(f"  36-Month ROI:         {report.roi_36_months:+.1f}%")

    # Quarterly summary table
    print(f"\n  QUARTERLY PROJECTIONS ({m['currency']})")
    print(f"  {'Quarter':<10} {'Players':>10} {'GGR':>14} {'NGR':>14} {'Profit':>14} {'Cumulative':>14}")
    print(f"  {'-' * 76}")

    for i, p in enumerate(report.monthly_projections):
        if (i + 1) % 3 == 0:  # quarterly
            q = (i + 1) // 3
            proj = report.monthly_projections[i]
            print(
                f"  Q{q:<9} {proj['active_players']:>10,} "
                f"{proj['ggr']:>14,.0f} {proj['ngr']:>14,.0f} "
                f"{proj['monthly_profit']:>14,.0f} {proj['cumulative_profit']:>14,.0f}"
            )

    # Risk assessment
    print(f"\n  RISK ASSESSMENT: {report.risk_assessment['overall_risk']}")
    print(f"  {'-' * 50}")
    for r in report.risk_assessment["risks"]:
        print(f"  [{r['severity']:>6}] {r['category']}: {r['description']}")
        print(f"          -> {r['mitigation']}")

    # Recommendations
    print(f"\n  RECOMMENDATIONS")
    print(f"  {'-' * 50}")
    for i, rec in enumerate(report.recommendations, 1):
        print(f"  {i}. {rec}")

    print("\n" + "=" * 72)


def interactive_mode():
    """Run in interactive mode, prompting the user for inputs."""
    print("\n=== Casino Platform Feasibility Study ===\n")
    print("Available markets:", ", ".join(MARKET_DATA.keys()))

    market = input("Select market [uk]: ").strip().lower() or "uk"
    if market not in MARKET_DATA:
        print(f"Unknown market '{market}'. Using 'uk'.")
        market = "uk"

    try:
        players = int(input("Target players year 1 [50000]: ").strip() or 50000)
    except ValueError:
        players = 50000

    try:
        deposit = float(input("Avg monthly deposit per player [75]: ").strip() or 75)
    except ValueError:
        deposit = 75.0

    try:
        dev_cost = float(input("Platform development cost [800000]: ").strip() or 800000)
    except ValueError:
        dev_cost = 800000.0

    try:
        team_cost = float(input("Monthly team cost [120000]: ").strip() or 120000)
    except ValueError:
        team_cost = 120000.0

    inputs = FeasibilityInputs(
        market=market,
        target_players_year1=players,
        avg_monthly_deposit=deposit,
        platform_dev_cost=dev_cost,
        team_monthly_cost=team_cost,
    )
    return inputs


def main():
    parser = argparse.ArgumentParser(description="Casino Platform Feasibility Study")
    parser.add_argument("--market", type=str, default="uk",
                        help=f"Target market ({', '.join(MARKET_DATA.keys())})")
    parser.add_argument("--players", type=int, default=50000,
                        help="Target active players at end of year 1")
    parser.add_argument("--avg-deposit", type=float, default=75.0,
                        help="Average monthly deposit per player")
    parser.add_argument("--dev-cost", type=float, default=800000,
                        help="Platform development cost")
    parser.add_argument("--team-cost", type=float, default=120000,
                        help="Monthly team cost")
    parser.add_argument("--marketing", type=float, default=500000,
                        help="Year 1 marketing budget")
    parser.add_argument("--months", type=int, default=36,
                        help="Projection period in months")
    parser.add_argument("--export", type=str, default=None,
                        help="Export full report to JSON file")
    parser.add_argument("--interactive", action="store_true",
                        help="Run in interactive mode")
    args = parser.parse_args()

    if args.interactive:
        inputs = interactive_mode()
    else:
        inputs = FeasibilityInputs(
            market=args.market,
            target_players_year1=args.players,
            avg_monthly_deposit=args.avg_deposit,
            platform_dev_cost=args.dev_cost,
            team_monthly_cost=args.team_cost,
            marketing_budget_year1=args.marketing,
            projection_months=args.months,
        )

    report = calculate_projections(inputs)
    print_summary(report)

    if args.export:
        with open(args.export, "w") as f:
            json.dump(asdict(report) if hasattr(report, "__dataclass_fields__") else {
                "generated_at": report.generated_at,
                "market": report.market,
                "inputs": report.inputs,
                "initial_investment": report.initial_investment,
                "total_funding_required": report.total_funding_required,
                "funding_with_buffer": report.funding_with_buffer,
                "break_even_month": report.break_even_month,
                "roi_36_months": report.roi_36_months,
                "monthly_projections": report.monthly_projections,
                "risk_assessment": report.risk_assessment,
                "recommendations": report.recommendations,
            }, f, indent=2, default=str)
        logger.info(f"Full report exported to {args.export}")


if __name__ == "__main__":
    main()
