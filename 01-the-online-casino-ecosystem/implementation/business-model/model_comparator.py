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
iGaming Business Model Comparator - B2C vs B2B vs Hybrid

Compares business models for online gambling operators with financial projections,
risk analysis, and strategic fit scoring. Produces 5-year P&L projections for each
model type with sensitivity analysis on key variables.

Usage:
    python model_comparator.py --model all
    python model_comparator.py --model b2c --jurisdiction UK --initial-investment 5000000
    python model_comparator.py --model hybrid --format json
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class ModelType(Enum):
    B2C = "b2c"
    B2B = "b2b"
    HYBRID = "hybrid"


@dataclass
class ModelAssumptions:
    """Financial assumptions for a business model."""
    model: ModelType
    initial_investment_eur: float = 5_000_000
    # Revenue drivers
    year1_active_players: int = 0
    player_growth_rate_pct: float = 0.0
    avg_revenue_per_user_eur: float = 0.0
    # For B2B: number of operator clients
    year1_clients: int = 0
    client_growth_rate: float = 0.0
    avg_revenue_per_client_eur: float = 0.0
    # Cost structure
    platform_cost_pct: float = 0.0  # % of revenue
    content_cost_pct: float = 0.0   # game royalties
    payment_cost_pct: float = 0.0
    marketing_cost_pct: float = 0.0
    staff_cost_eur: float = 0.0
    staff_growth_rate_pct: float = 0.0
    regulatory_cost_eur: float = 0.0
    hosting_cost_eur: float = 0.0
    tax_rate_pct: float = 0.0
    # Licensing
    license_cost_eur: float = 0.0
    license_annual_fee_eur: float = 0.0


# ---------------------------------------------------------------------------
# Pre-configured model templates
# ---------------------------------------------------------------------------
B2C_DEFAULT = ModelAssumptions(
    model=ModelType.B2C,
    initial_investment_eur=5_000_000,
    year1_active_players=8_000,
    player_growth_rate_pct=40.0,
    avg_revenue_per_user_eur=420.0,  # annual GGR per active player
    platform_cost_pct=8.0,
    content_cost_pct=12.0,   # game provider royalties (slots, live casino)
    payment_cost_pct=3.5,
    marketing_cost_pct=35.0,  # high CAC in competitive markets
    staff_cost_eur=1_200_000,
    staff_growth_rate_pct=20.0,
    regulatory_cost_eur=200_000,
    hosting_cost_eur=180_000,
    tax_rate_pct=18.0,  # GGR tax (varies by jurisdiction)
    license_cost_eur=150_000,
    license_annual_fee_eur=50_000,
)

B2B_DEFAULT = ModelAssumptions(
    model=ModelType.B2B,
    initial_investment_eur=3_000_000,
    year1_clients=5,
    client_growth_rate=60.0,
    avg_revenue_per_client_eur=360_000,  # annual per operator client
    platform_cost_pct=15.0,  # higher dev/support costs
    content_cost_pct=5.0,
    payment_cost_pct=1.0,    # clients handle payments
    marketing_cost_pct=8.0,  # B2B marketing is cheaper
    staff_cost_eur=900_000,
    staff_growth_rate_pct=25.0,
    regulatory_cost_eur=120_000,
    hosting_cost_eur=250_000,  # multi-tenant infrastructure
    tax_rate_pct=5.0,  # typically Malta B2B license
    license_cost_eur=25_000,
    license_annual_fee_eur=15_000,
)

HYBRID_DEFAULT = ModelAssumptions(
    model=ModelType.HYBRID,
    initial_investment_eur=7_000_000,
    # B2C component
    year1_active_players=5_000,
    player_growth_rate_pct=35.0,
    avg_revenue_per_user_eur=400.0,
    # B2B component
    year1_clients=3,
    client_growth_rate=50.0,
    avg_revenue_per_client_eur=300_000,
    platform_cost_pct=10.0,
    content_cost_pct=10.0,
    payment_cost_pct=2.5,
    marketing_cost_pct=22.0,
    staff_cost_eur=1_500_000,
    staff_growth_rate_pct=22.0,
    regulatory_cost_eur=280_000,  # multiple licenses
    hosting_cost_eur=300_000,
    tax_rate_pct=12.0,  # blended rate
    license_cost_eur=200_000,
    license_annual_fee_eur=70_000,
)


@dataclass
class YearlyProjection:
    year: int
    revenue_eur: float
    b2c_revenue_eur: float
    b2b_revenue_eur: float
    platform_cost_eur: float
    content_cost_eur: float
    payment_cost_eur: float
    marketing_cost_eur: float
    staff_cost_eur: float
    regulatory_cost_eur: float
    hosting_cost_eur: float
    license_fee_eur: float
    total_costs_eur: float
    ebitda_eur: float
    ebitda_margin_pct: float
    tax_eur: float
    net_income_eur: float
    cumulative_cashflow_eur: float
    active_players: int
    b2b_clients: int


class ModelComparator:
    """Compare B2C, B2B, and hybrid iGaming business models."""

    def __init__(self, projection_years: int = 5):
        self.years = projection_years

    def project(self, assumptions: ModelAssumptions) -> list[YearlyProjection]:
        """Generate yearly P&L projection."""
        projections = []
        cumulative_cf = -assumptions.initial_investment_eur - assumptions.license_cost_eur
        players = 0
        clients = 0

        for year in range(1, self.years + 1):
            # Player/client counts
            if year == 1:
                players = assumptions.year1_active_players
                clients = assumptions.year1_clients
            else:
                players = int(players * (1 + assumptions.player_growth_rate_pct / 100))
                clients = int(max(clients * (1 + assumptions.client_growth_rate / 100), clients + 1))

            # Revenue
            b2c_rev = players * assumptions.avg_revenue_per_user_eur
            b2b_rev = clients * assumptions.avg_revenue_per_client_eur
            total_rev = b2c_rev + b2b_rev

            # Variable costs (% of revenue)
            platform_cost = total_rev * assumptions.platform_cost_pct / 100
            content_cost = total_rev * assumptions.content_cost_pct / 100
            payment_cost = total_rev * assumptions.payment_cost_pct / 100
            marketing_cost = total_rev * assumptions.marketing_cost_pct / 100

            # Fixed costs (grow with staff)
            growth_factor = (1 + assumptions.staff_growth_rate_pct / 100) ** (year - 1)
            staff_cost = assumptions.staff_cost_eur * growth_factor
            regulatory_cost = assumptions.regulatory_cost_eur * (1.05 ** (year - 1))
            hosting_cost = assumptions.hosting_cost_eur * (1 + 0.15 * (year - 1))

            total_costs = (platform_cost + content_cost + payment_cost +
                           marketing_cost + staff_cost + regulatory_cost +
                           hosting_cost + assumptions.license_annual_fee_eur)

            ebitda = total_rev - total_costs
            ebitda_margin = (ebitda / total_rev * 100) if total_rev > 0 else 0
            tax = max(0, ebitda * assumptions.tax_rate_pct / 100)
            net_income = ebitda - tax
            cumulative_cf += net_income

            projections.append(YearlyProjection(
                year=year,
                revenue_eur=round(total_rev),
                b2c_revenue_eur=round(b2c_rev),
                b2b_revenue_eur=round(b2b_rev),
                platform_cost_eur=round(platform_cost),
                content_cost_eur=round(content_cost),
                payment_cost_eur=round(payment_cost),
                marketing_cost_eur=round(marketing_cost),
                staff_cost_eur=round(staff_cost),
                regulatory_cost_eur=round(regulatory_cost),
                hosting_cost_eur=round(hosting_cost),
                license_fee_eur=round(assumptions.license_annual_fee_eur),
                total_costs_eur=round(total_costs),
                ebitda_eur=round(ebitda),
                ebitda_margin_pct=round(ebitda_margin, 1),
                tax_eur=round(tax),
                net_income_eur=round(net_income),
                cumulative_cashflow_eur=round(cumulative_cf),
                active_players=players,
                b2b_clients=clients,
            ))

        return projections

    def compare_models(self) -> dict:
        """Compare all three models side by side."""
        models = {
            "b2c": B2C_DEFAULT,
            "b2b": B2B_DEFAULT,
            "hybrid": HYBRID_DEFAULT,
        }

        comparison = {}
        for name, assumptions in models.items():
            projections = self.project(assumptions)
            yr5 = projections[-1]

            breakeven_year = None
            for p in projections:
                if p.cumulative_cashflow_eur > 0 and breakeven_year is None:
                    breakeven_year = p.year

            comparison[name] = {
                "model_type": name.upper(),
                "initial_investment_eur": assumptions.initial_investment_eur,
                "license_cost_eur": assumptions.license_cost_eur,
                "total_upfront_eur": assumptions.initial_investment_eur + assumptions.license_cost_eur,
                "year5_revenue_eur": yr5.revenue_eur,
                "year5_ebitda_eur": yr5.ebitda_eur,
                "year5_ebitda_margin_pct": yr5.ebitda_margin_pct,
                "year5_net_income_eur": yr5.net_income_eur,
                "cumulative_net_income_5yr_eur": sum(p.net_income_eur for p in projections),
                "breakeven_year": breakeven_year or ">5",
                "year5_cumulative_cashflow_eur": yr5.cumulative_cashflow_eur,
                "roi_5yr_pct": round(
                    yr5.cumulative_cashflow_eur / (assumptions.initial_investment_eur + assumptions.license_cost_eur) * 100, 1
                ),
                "year5_players": yr5.active_players,
                "year5_b2b_clients": yr5.b2b_clients,
                "yearly_projections": [
                    {
                        "year": p.year,
                        "revenue": p.revenue_eur,
                        "ebitda": p.ebitda_eur,
                        "margin_pct": p.ebitda_margin_pct,
                        "net_income": p.net_income_eur,
                        "cumulative_cf": p.cumulative_cashflow_eur,
                    }
                    for p in projections
                ],
            }

        return comparison

    def risk_assessment(self) -> dict:
        """Qualitative risk comparison across models."""
        return {
            "b2c": {
                "model": "B2C (Direct to Consumer)",
                "capital_requirement": "HIGH (EUR 5-10M)",
                "time_to_revenue": "6-12 months",
                "time_to_profitability": "18-36 months",
                "key_risks": [
                    "High customer acquisition cost (EUR 200-500 per depositing player)",
                    "Regulatory compliance burden per jurisdiction",
                    "Player liability and responsible gambling obligations",
                    "Marketing dependency and retention challenges",
                    "Payment processing complexity and fraud exposure",
                ],
                "key_advantages": [
                    "Direct player relationships and data ownership",
                    "Higher revenue per player (full GGR retention)",
                    "Brand equity and long-term asset value",
                    "Flexibility in product and market strategy",
                    "Potential for higher exit multiples (6-10x revenue)",
                ],
                "ideal_for": "Teams with strong marketing expertise and capital access",
                "scalability": "LINEAR - each new market requires new license, local compliance",
                "exit_multiple_range": "6-10x annual revenue",
            },
            "b2b": {
                "model": "B2B (Platform/Content Provider)",
                "capital_requirement": "MODERATE (EUR 2-5M)",
                "time_to_revenue": "12-18 months (longer sales cycle)",
                "time_to_profitability": "12-24 months",
                "key_risks": [
                    "Long B2B sales cycles (6-18 months per deal)",
                    "Client concentration risk",
                    "Technology commoditization pressure",
                    "Dependency on client success for revenue share models",
                    "Limited brand visibility to end consumers",
                ],
                "key_advantages": [
                    "Lower marketing costs (8-12% vs 30-40% B2C)",
                    "Recurring revenue from platform fees",
                    "Scalable across jurisdictions via clients",
                    "Less direct regulatory burden per market",
                    "Technology moat and switching costs",
                ],
                "ideal_for": "Teams with strong technology and enterprise sales capabilities",
                "scalability": "EXPONENTIAL - platform serves multiple operators simultaneously",
                "exit_multiple_range": "8-15x annual revenue (SaaS multiples)",
            },
            "hybrid": {
                "model": "Hybrid (B2C + B2B Platform)",
                "capital_requirement": "HIGH (EUR 7-15M)",
                "time_to_revenue": "6-12 months (B2C first)",
                "time_to_profitability": "24-42 months",
                "key_risks": [
                    "Organizational complexity (two businesses in one)",
                    "Channel conflict (competing with own B2B clients)",
                    "Higher capital requirements",
                    "Diluted management focus",
                    "Harder to attract B2B clients who see you as competitor",
                ],
                "key_advantages": [
                    "Diversified revenue streams reduce single-point failure",
                    "B2C operations validate the B2B platform quality",
                    "Data from B2C improves B2B product (dogfooding)",
                    "Revenue smoothing across business cycles",
                    "Strategic optionality for pivot or spin-off",
                ],
                "ideal_for": "Well-funded teams with both marketing and technology strengths",
                "scalability": "MODERATE - benefits of both but complexity overhead",
                "exit_multiple_range": "5-12x annual revenue (blended)",
            },
        }

    def strategic_fit_scoring(self, team_size: int = 20,
                              capital_eur: float = 5_000_000,
                              tech_strength: int = 7,
                              marketing_strength: int = 5,
                              regulatory_experience: int = 5) -> dict:
        """Score model fit based on team capabilities (1-10 scale inputs)."""
        scores = {}
        for model_name, weights in {
            "b2c": {"capital": 0.25, "marketing": 0.30, "tech": 0.15, "regulatory": 0.20, "team": 0.10},
            "b2b": {"capital": 0.15, "marketing": 0.10, "tech": 0.35, "regulatory": 0.15, "team": 0.25},
            "hybrid": {"capital": 0.25, "marketing": 0.20, "tech": 0.25, "regulatory": 0.15, "team": 0.15},
        }.items():
            capital_score = min(10, capital_eur / 1_000_000)
            team_score = min(10, team_size / 5)

            raw_scores = {
                "capital": capital_score,
                "marketing": marketing_strength,
                "tech": tech_strength,
                "regulatory": regulatory_experience,
                "team": team_score,
            }

            total = sum(raw_scores[k] * weights[k] for k in weights)
            scores[model_name] = {
                "model": model_name.upper(),
                "fit_score": round(total * 10, 1),  # scale to 100
                "component_scores": {k: round(v, 1) for k, v in raw_scores.items()},
                "weights": weights,
                "recommendation": (
                    "STRONG FIT" if total > 7 else
                    "GOOD FIT" if total > 5.5 else
                    "MODERATE FIT" if total > 4 else
                    "POOR FIT"
                ),
            }

        best = max(scores.values(), key=lambda x: x["fit_score"])
        return {
            "inputs": {
                "team_size": team_size,
                "capital_eur": capital_eur,
                "tech_strength": tech_strength,
                "marketing_strength": marketing_strength,
                "regulatory_experience": regulatory_experience,
            },
            "scores": scores,
            "recommended_model": best["model"],
            "recommended_score": best["fit_score"],
        }

    def print_comparison(self, comparison: dict):
        """Print formatted comparison table."""
        print(f"\n{'=' * 100}")
        print(f"  iGAMING BUSINESS MODEL COMPARISON - 5-YEAR FINANCIAL PROJECTION")
        print(f"{'=' * 100}")

        metrics = [
            ("Initial Investment", "total_upfront_eur", "EUR"),
            ("Year 5 Revenue", "year5_revenue_eur", "EUR"),
            ("Year 5 EBITDA", "year5_ebitda_eur", "EUR"),
            ("Year 5 EBITDA Margin", "year5_ebitda_margin_pct", "%"),
            ("Breakeven Year", "breakeven_year", ""),
            ("5-Year Cumulative CF", "year5_cumulative_cashflow_eur", "EUR"),
            ("5-Year ROI", "roi_5yr_pct", "%"),
            ("Year 5 Players", "year5_players", ""),
            ("Year 5 B2B Clients", "year5_b2b_clients", ""),
        ]

        header = f"  {'Metric':<30} {'B2C':>20} {'B2B':>20} {'HYBRID':>20}"
        print(header)
        print(f"  {'-' * 90}")

        for label, key, unit in metrics:
            vals = []
            for model in ["b2c", "b2b", "hybrid"]:
                v = comparison[model][key]
                if unit == "EUR" and isinstance(v, (int, float)):
                    vals.append(f"EUR {v:>12,.0f}")
                elif unit == "%":
                    vals.append(f"{v:>12.1f}%")
                else:
                    vals.append(f"{v:>13}")
            print(f"  {label:<30} {vals[0]:>20} {vals[1]:>20} {vals[2]:>20}")

        # Yearly breakdown
        for model_name in ["b2c", "b2b", "hybrid"]:
            data = comparison[model_name]
            print(f"\n  --- {data['model_type']} Yearly P&L ---")
            print(f"  {'Year':>6} {'Revenue':>15} {'EBITDA':>15} {'Margin':>8} {'Net Income':>15} {'Cum. CF':>15}")
            for yr in data["yearly_projections"]:
                print(f"  {yr['year']:>6} {yr['revenue']:>15,.0f} {yr['ebitda']:>15,.0f} "
                      f"{yr['margin_pct']:>7.1f}% {yr['net_income']:>15,.0f} {yr['cumulative_cf']:>15,.0f}")


def main():
    parser = argparse.ArgumentParser(description="iGaming Business Model Comparator")
    parser.add_argument("--model", choices=["b2c", "b2b", "hybrid", "all"], default="all")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--years", type=int, default=5, help="Projection years (default: 5)")
    parser.add_argument("--initial-investment", type=float, help="Override initial investment EUR")
    parser.add_argument("--team-size", type=int, default=20, help="Team size for fit scoring")
    parser.add_argument("--capital", type=float, default=5_000_000, help="Available capital EUR")
    parser.add_argument("--tech-strength", type=int, default=7, choices=range(1, 11),
                        help="Technology capability (1-10)")
    parser.add_argument("--marketing-strength", type=int, default=5, choices=range(1, 11),
                        help="Marketing capability (1-10)")
    parser.add_argument("--regulatory-exp", type=int, default=5, choices=range(1, 11),
                        help="Regulatory experience (1-10)")
    args = parser.parse_args()

    comparator = ModelComparator(projection_years=args.years)

    comparison = comparator.compare_models()
    risks = comparator.risk_assessment()
    fit = comparator.strategic_fit_scoring(
        team_size=args.team_size,
        capital_eur=args.capital,
        tech_strength=args.tech_strength,
        marketing_strength=args.marketing_strength,
        regulatory_experience=args.regulatory_exp,
    )

    if args.format == "json":
        output = {
            "financial_comparison": comparison,
            "risk_assessment": risks,
            "strategic_fit": fit,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        comparator.print_comparison(comparison)

        print(f"\n{'=' * 100}")
        print(f"  STRATEGIC FIT SCORING")
        print(f"{'=' * 100}")
        print(f"  Team Size: {fit['inputs']['team_size']} | Capital: EUR {fit['inputs']['capital_eur']:,.0f} | "
              f"Tech: {fit['inputs']['tech_strength']}/10 | Marketing: {fit['inputs']['marketing_strength']}/10 | "
              f"Regulatory: {fit['inputs']['regulatory_experience']}/10")
        print()
        for model, score_data in fit["scores"].items():
            print(f"  {score_data['model']:>8}: {score_data['fit_score']:>5.1f}/100 - {score_data['recommendation']}")
        print(f"\n  RECOMMENDED MODEL: {fit['recommended_model']} (Score: {fit['recommended_score']}/100)")

        print(f"\n{'=' * 100}")
        print(f"  RISK ASSESSMENT SUMMARY")
        print(f"{'=' * 100}")
        for model_name in ["b2c", "b2b", "hybrid"]:
            r = risks[model_name]
            print(f"\n  {r['model']}")
            print(f"    Capital: {r['capital_requirement']} | Time to Profit: {r['time_to_profitability']}")
            print(f"    Scalability: {r['scalability']}")
            print(f"    Exit Multiple: {r['exit_multiple_range']}")
            print(f"    Top Risk: {r['key_risks'][0]}")
            print(f"    Top Advantage: {r['key_advantages'][0]}")


if __name__ == "__main__":
    main()
