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
Market Expansion Roadmap Generator for iGaming Operators
==========================================================

Generates a sequenced market expansion plan based on:
  - ROI potential per jurisdiction
  - Regulatory timeline and licensing cost
  - Market size and growth trajectory
  - Operational complexity and dependencies
  - Budget allocation across phases

Usage:
    python expansion_roadmap.py --budget 5000000 --years 3
    python expansion_roadmap.py --budget 10000000 --years 5 --products sports,casino
    python expansion_roadmap.py --budget 2000000 --priority tax_efficiency
    python expansion_roadmap.py --scenario aggressive --format json
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Jurisdiction entry profiles
# ---------------------------------------------------------------------------

@dataclass
class EntryProfile:
    """Everything needed to evaluate a market for expansion planning."""
    code: str
    name: str
    region: str

    # Costs
    license_cost_usd: float          # total first-year licensing cost
    setup_cost_usd: float            # legal, technical, compliance setup
    ongoing_annual_cost_usd: float   # annual compliance + operations
    marketing_launch_usd: float      # marketing budget for market entry

    # Timeline
    licensing_months: int
    setup_months: int  # post-license tech/ops setup
    total_months_to_revenue: int

    # Revenue potential
    estimated_tam_usd: float
    realistic_year1_ggr_usd: float
    realistic_year3_ggr_usd: float
    ggr_tax_pct: float
    corporate_tax_pct: float
    market_growth_pct: float

    # Operational factors
    local_entity_required: bool
    local_staff_needed: int
    language_localization: list = field(default_factory=list)
    payment_integration_complexity: str = "medium"  # low, medium, high
    technical_requirements: list = field(default_factory=list)

    # Strategic value
    passport_markets: list = field(default_factory=list)  # other markets accessible
    brand_halo_effect: float = 0  # 0-1, how much this license helps elsewhere
    strategic_notes: str = ""

    # Dependencies
    prerequisites: list = field(default_factory=list)  # codes of markets needed first
    difficulty: str = "medium"  # easy, medium, hard, very_hard


ENTRY_PROFILES = [
    EntryProfile(
        code="MGA", name="Malta (MGA)", region="europe",
        license_cost_usd=180000, setup_cost_usd=150000,
        ongoing_annual_cost_usd=235000, marketing_launch_usd=200000,
        licensing_months=6, setup_months=3, total_months_to_revenue=9,
        estimated_tam_usd=500000000000,  # EU-wide via passporting
        realistic_year1_ggr_usd=5000000, realistic_year3_ggr_usd=25000000,
        ggr_tax_pct=5, corporate_tax_pct=5,  # effective with refund system
        market_growth_pct=8,
        local_entity_required=True, local_staff_needed=5,
        language_localization=["en", "de", "fi", "no", "pt"],
        payment_integration_complexity="medium",
        technical_requirements=["RNG certification", "Player protection systems",
                                "AML/KYC compliance", "Source code escrow"],
        passport_markets=["DE", "FI", "AT", "IE", "NL"],
        brand_halo_effect=0.8,
        strategic_notes="Gateway to EU. Most operators start here. "
                        "EU/EEA freedom of services enables multi-market reach.",
        difficulty="medium",
    ),
    EntryProfile(
        code="UKGC", name="United Kingdom", region="europe",
        license_cost_usd=160000, setup_cost_usd=300000,
        ongoing_annual_cost_usd=620000, marketing_launch_usd=2000000,
        licensing_months=8, setup_months=4, total_months_to_revenue=12,
        estimated_tam_usd=18000000000,
        realistic_year1_ggr_usd=8000000, realistic_year3_ggr_usd=40000000,
        ggr_tax_pct=21, corporate_tax_pct=25, market_growth_pct=3.2,
        local_entity_required=False, local_staff_needed=3,
        language_localization=["en"],
        payment_integration_complexity="medium",
        technical_requirements=["GAMSTOP integration", "Affordability checks",
                                "Source of funds procedures", "AML Level 3"],
        passport_markets=[],
        brand_halo_effect=0.9,
        strategic_notes="Largest single regulated market. UKGC license is gold "
                        "standard credential. Very competitive but massive TAM.",
        difficulty="hard",
    ),
    EntryProfile(
        code="CUR", name="Curacao", region="caribbean",
        license_cost_usd=68000, setup_cost_usd=50000,
        ongoing_annual_cost_usd=98000, marketing_launch_usd=100000,
        licensing_months=3, setup_months=2, total_months_to_revenue=5,
        estimated_tam_usd=5000000000,
        realistic_year1_ggr_usd=2000000, realistic_year3_ggr_usd=8000000,
        ggr_tax_pct=0, corporate_tax_pct=22, market_growth_pct=5,
        local_entity_required=True, local_staff_needed=2,
        language_localization=["en", "es", "pt"],
        payment_integration_complexity="low",
        technical_requirements=["RNG testing", "Basic KYC", "Player fund segregation"],
        passport_markets=[],
        brand_halo_effect=0.2,
        strategic_notes="Fast and cost-effective entry. Good for testing "
                        "new markets. Lower reputation but reformed from 2024.",
        difficulty="easy",
    ),
    EntryProfile(
        code="IOM", name="Isle of Man", region="europe",
        license_cost_usd=100000, setup_cost_usd=200000,
        ongoing_annual_cost_usd=300000, marketing_launch_usd=300000,
        licensing_months=4, setup_months=3, total_months_to_revenue=7,
        estimated_tam_usd=2000000000,
        realistic_year1_ggr_usd=3000000, realistic_year3_ggr_usd=15000000,
        ggr_tax_pct=1.5, corporate_tax_pct=0, market_growth_pct=5,
        local_entity_required=True, local_staff_needed=4,
        language_localization=["en"],
        payment_integration_complexity="medium",
        technical_requirements=["GLI/BMM testing", "AML framework", "Player protection"],
        passport_markets=[],
        brand_halo_effect=0.7,
        strategic_notes="Premium jurisdiction with 0% corporate tax. White list "
                        "status. Excellent for B2B and B2C operations.",
        difficulty="medium",
    ),
    EntryProfile(
        code="BRA", name="Brazil", region="latin_america",
        license_cost_usd=6000000, setup_cost_usd=1000000,
        ongoing_annual_cost_usd=400000, marketing_launch_usd=3000000,
        licensing_months=6, setup_months=6, total_months_to_revenue=12,
        estimated_tam_usd=8000000000,
        realistic_year1_ggr_usd=15000000, realistic_year3_ggr_usd=80000000,
        ggr_tax_pct=12, corporate_tax_pct=34, market_growth_pct=45,
        local_entity_required=True, local_staff_needed=15,
        language_localization=["pt-BR"],
        payment_integration_complexity="medium",
        technical_requirements=["SIGAP reporting integration", "Local servers",
                                "PIX payment integration", "CPF verification"],
        passport_markets=[],
        brand_halo_effect=0.3,
        strategic_notes="Massive emerging market. Very high entry cost (R$30M license) "
                        "but enormous revenue potential. PIX-first payments.",
        difficulty="hard",
    ),
    EntryProfile(
        code="ONT", name="Ontario, Canada", region="north_america",
        license_cost_usd=200000, setup_cost_usd=400000,
        ongoing_annual_cost_usd=600000, marketing_launch_usd=1500000,
        licensing_months=9, setup_months=3, total_months_to_revenue=12,
        estimated_tam_usd=5000000000,
        realistic_year1_ggr_usd=6000000, realistic_year3_ggr_usd=30000000,
        ggr_tax_pct=20, corporate_tax_pct=26.5, market_growth_pct=15,
        local_entity_required=False, local_staff_needed=3,
        language_localization=["en", "fr"],
        payment_integration_complexity="medium",
        technical_requirements=["iGO integration", "Interac payments",
                                "PlaySmart self-exclusion", "GLI testing"],
        passport_markets=[],
        brand_halo_effect=0.4,
        strategic_notes="Largest Canadian province. Revenue share with iGO. "
                        "Growing rapidly since 2022 launch. Gateway to Canada.",
        difficulty="medium",
    ),
    EntryProfile(
        code="SWE", name="Sweden", region="europe",
        license_cost_usd=55000, setup_cost_usd=150000,
        ongoing_annual_cost_usd=200000, marketing_launch_usd=500000,
        licensing_months=6, setup_months=3, total_months_to_revenue=9,
        estimated_tam_usd=3200000000,
        realistic_year1_ggr_usd=3000000, realistic_year3_ggr_usd=15000000,
        ggr_tax_pct=18, corporate_tax_pct=20.6, market_growth_pct=4.5,
        local_entity_required=False, local_staff_needed=2,
        language_localization=["sv"],
        payment_integration_complexity="medium",
        technical_requirements=["Spelpaus integration", "Swish payments",
                                "Trustly Open Banking", "Bonus restrictions compliance"],
        passport_markets=[],
        brand_halo_effect=0.5,
        strategic_notes="Mature Nordic market. Welcome bonus only restriction. "
                        "High ARPU but strict responsible gambling requirements.",
        difficulty="medium",
    ),
    EntryProfile(
        code="GIB", name="Gibraltar", region="europe",
        license_cost_usd=120000, setup_cost_usd=250000,
        ongoing_annual_cost_usd=450000, marketing_launch_usd=500000,
        licensing_months=6, setup_months=4, total_months_to_revenue=10,
        estimated_tam_usd=2000000000,
        realistic_year1_ggr_usd=4000000, realistic_year3_ggr_usd=20000000,
        ggr_tax_pct=1, corporate_tax_pct=12.5, market_growth_pct=5,
        local_entity_required=True, local_staff_needed=5,
        language_localization=["en"],
        payment_integration_complexity="medium",
        technical_requirements=["GLI testing", "AML framework", "Data protection"],
        passport_markets=[],
        brand_halo_effect=0.85,
        strategic_notes="Premium Tier 1 jurisdiction. Home to major operators. "
                        "Very selective - new licenses rare. Low effective tax.",
        difficulty="very_hard",
    ),
    EntryProfile(
        code="PH", name="Philippines (PAGCOR)", region="asia_pacific",
        license_cost_usd=150000, setup_cost_usd=200000,
        ongoing_annual_cost_usd=250000, marketing_launch_usd=500000,
        licensing_months=4, setup_months=3, total_months_to_revenue=7,
        estimated_tam_usd=2800000000,
        realistic_year1_ggr_usd=3000000, realistic_year3_ggr_usd=12000000,
        ggr_tax_pct=5, corporate_tax_pct=25, market_growth_pct=18,
        local_entity_required=True, local_staff_needed=8,
        language_localization=["en", "fil"],
        payment_integration_complexity="high",
        technical_requirements=["PAGCOR compliance", "GCash integration",
                                "Local data hosting"],
        passport_markets=[],
        brand_halo_effect=0.3,
        strategic_notes="Growing Asian market. PAGCOR dual role (regulator + operator). "
                        "POGO crackdown affected B2B but domestic market growing.",
        difficulty="medium",
    ),
    EntryProfile(
        code="NG", name="Nigeria", region="africa",
        license_cost_usd=50000, setup_cost_usd=100000,
        ongoing_annual_cost_usd=120000, marketing_launch_usd=300000,
        licensing_months=3, setup_months=3, total_months_to_revenue=6,
        estimated_tam_usd=1100000000,
        realistic_year1_ggr_usd=2000000, realistic_year3_ggr_usd=10000000,
        ggr_tax_pct=5, corporate_tax_pct=30, market_growth_pct=35,
        local_entity_required=True, local_staff_needed=10,
        language_localization=["en"],
        payment_integration_complexity="high",
        technical_requirements=["Mobile money integration", "USSD platform",
                                "Agent network setup", "Low-bandwidth optimization"],
        passport_markets=[],
        brand_halo_effect=0.15,
        strategic_notes="Africa's largest market. Mobile-first. Agent network "
                        "critical for distribution. Young, sports-mad population.",
        difficulty="medium",
    ),
]


# ---------------------------------------------------------------------------
# ROI and scoring models
# ---------------------------------------------------------------------------

class ExpansionPlanner:
    """Generate market expansion roadmaps."""

    PRIORITY_WEIGHTS = {
        "balanced": {
            "roi": 0.25, "market_size": 0.20, "time_to_revenue": 0.15,
            "strategic_value": 0.15, "risk": 0.15, "cost": 0.10,
        },
        "roi_focused": {
            "roi": 0.40, "market_size": 0.15, "time_to_revenue": 0.10,
            "strategic_value": 0.10, "risk": 0.15, "cost": 0.10,
        },
        "speed": {
            "roi": 0.15, "market_size": 0.10, "time_to_revenue": 0.35,
            "strategic_value": 0.10, "risk": 0.15, "cost": 0.15,
        },
        "tax_efficiency": {
            "roi": 0.20, "market_size": 0.15, "time_to_revenue": 0.10,
            "strategic_value": 0.10, "risk": 0.10, "cost": 0.35,
        },
        "strategic": {
            "roi": 0.15, "market_size": 0.15, "time_to_revenue": 0.10,
            "strategic_value": 0.35, "risk": 0.15, "cost": 0.10,
        },
    }

    def __init__(self, priority: str = "balanced"):
        self.weights = self.PRIORITY_WEIGHTS.get(priority, self.PRIORITY_WEIGHTS["balanced"])
        self.priority_name = priority
        self.profiles = {p.code: p for p in ENTRY_PROFILES}

    def score_jurisdiction(self, p: EntryProfile) -> dict:
        """Score a jurisdiction for expansion prioritization."""
        scores = {}

        # ROI Score
        total_investment = (p.license_cost_usd + p.setup_cost_usd +
                            p.marketing_launch_usd)
        year3_net = p.realistic_year3_ggr_usd * (1 - p.ggr_tax_pct / 100)
        three_year_revenue = (p.realistic_year1_ggr_usd +
                               (p.realistic_year1_ggr_usd + p.realistic_year3_ggr_usd) / 2 +
                               p.realistic_year3_ggr_usd) * (1 - p.ggr_tax_pct / 100)
        three_year_cost = total_investment + p.ongoing_annual_cost_usd * 3
        roi_3yr = (three_year_revenue - three_year_cost) / three_year_cost if three_year_cost else 0
        scores["roi"] = min(100, max(0, roi_3yr * 30 + 50))

        # Market Size
        tam = p.estimated_tam_usd
        if tam > 10_000_000_000:
            scores["market_size"] = 95
        elif tam > 5_000_000_000:
            scores["market_size"] = 80
        elif tam > 1_000_000_000:
            scores["market_size"] = 65
        elif tam > 500_000_000:
            scores["market_size"] = 50
        else:
            scores["market_size"] = 30

        # Growth bonus
        if p.market_growth_pct > 20:
            scores["market_size"] = min(100, scores["market_size"] + 15)

        # Time to Revenue
        months = p.total_months_to_revenue
        if months <= 5:
            scores["time_to_revenue"] = 95
        elif months <= 7:
            scores["time_to_revenue"] = 80
        elif months <= 9:
            scores["time_to_revenue"] = 65
        elif months <= 12:
            scores["time_to_revenue"] = 50
        else:
            scores["time_to_revenue"] = 30

        # Strategic Value
        strat = 50
        strat += len(p.passport_markets) * 5
        strat += p.brand_halo_effect * 30
        scores["strategic_value"] = min(100, strat)

        # Risk (inverted: lower risk = higher score)
        diff_scores = {"easy": 85, "medium": 65, "hard": 40, "very_hard": 20}
        scores["risk"] = diff_scores.get(p.difficulty, 50)

        # Cost efficiency
        if total_investment < 200000:
            scores["cost"] = 95
        elif total_investment < 500000:
            scores["cost"] = 80
        elif total_investment < 1000000:
            scores["cost"] = 65
        elif total_investment < 3000000:
            scores["cost"] = 45
        elif total_investment < 7000000:
            scores["cost"] = 25
        else:
            scores["cost"] = 10

        overall = sum(scores[k] * self.weights.get(k, 0) for k in scores)

        return {
            "code": p.code,
            "name": p.name,
            "region": p.region,
            "overall_score": round(overall, 1),
            "dimension_scores": scores,
            "financials": {
                "total_entry_cost_usd": total_investment,
                "ongoing_annual_usd": p.ongoing_annual_cost_usd,
                "year1_ggr_usd": p.realistic_year1_ggr_usd,
                "year3_ggr_usd": p.realistic_year3_ggr_usd,
                "year3_net_ggr_usd": round(year3_net),
                "three_year_roi_pct": round(roi_3yr * 100, 1),
                "ggr_tax_pct": p.ggr_tax_pct,
                "effective_total_tax_pct": round(
                    p.ggr_tax_pct + p.corporate_tax_pct * (1 - p.ggr_tax_pct / 100) * 0.3, 1),
            },
            "timeline": {
                "licensing_months": p.licensing_months,
                "setup_months": p.setup_months,
                "total_to_revenue_months": p.total_months_to_revenue,
            },
            "operational": {
                "local_entity": p.local_entity_required,
                "local_staff": p.local_staff_needed,
                "languages": p.language_localization,
                "payment_complexity": p.payment_integration_complexity,
                "tech_requirements": p.technical_requirements,
            },
            "strategic": {
                "passport_markets": p.passport_markets,
                "brand_halo": p.brand_halo_effect,
                "difficulty": p.difficulty,
                "notes": p.strategic_notes,
            },
        }

    def generate_roadmap(self, budget_usd: float, years: int,
                          products: Optional[list] = None,
                          exclude: Optional[list] = None) -> dict:
        """Generate a phased expansion roadmap."""

        # Score and rank all jurisdictions
        scored = []
        for code, profile in self.profiles.items():
            if exclude and code in [e.upper() for e in exclude]:
                continue
            scored.append(self.score_jurisdiction(profile))

        scored.sort(key=lambda x: x["overall_score"], reverse=True)

        # Allocate to phases
        phases = []
        remaining_budget = budget_usd
        months_elapsed = 0
        total_months = years * 12
        selected_codes = set()

        for phase_num in range(1, years + 1):
            phase_markets = []
            phase_budget = remaining_budget * (0.4 if phase_num == 1 else 0.3)

            for s in scored:
                code = s["code"]
                if code in selected_codes:
                    continue

                entry_cost = s["financials"]["total_entry_cost_usd"]
                time_needed = s["timeline"]["total_to_revenue_months"]

                # Check prerequisites
                profile = self.profiles[code]
                prereqs_met = all(p in selected_codes for p in profile.prerequisites)
                if not prereqs_met:
                    continue

                # Check budget and timeline
                if entry_cost <= phase_budget and months_elapsed + time_needed <= total_months:
                    phase_markets.append({
                        "jurisdiction": s["name"],
                        "code": code,
                        "score": s["overall_score"],
                        "entry_cost_usd": entry_cost,
                        "months_to_revenue": time_needed,
                        "year1_ggr_usd": s["financials"]["year1_ggr_usd"],
                        "year3_ggr_usd": s["financials"]["year3_ggr_usd"],
                        "roi_3yr_pct": s["financials"]["three_year_roi_pct"],
                        "difficulty": s["strategic"]["difficulty"],
                        "rationale": s["strategic"]["notes"][:120],
                    })
                    selected_codes.add(code)
                    phase_budget -= entry_cost
                    remaining_budget -= entry_cost

                    if len(phase_markets) >= 3:  # max 3 markets per phase
                        break

            if phase_markets:
                phase_cost = sum(m["entry_cost_usd"] for m in phase_markets)
                phases.append({
                    "phase": phase_num,
                    "label": f"Year {phase_num}" + (" (Launch)" if phase_num == 1 else ""),
                    "start_month": months_elapsed,
                    "markets": phase_markets,
                    "phase_investment_usd": phase_cost,
                    "cumulative_markets": len(selected_codes),
                })
                months_elapsed += 12

        # Financial summary
        total_invested = sum(p["phase_investment_usd"] for p in phases)
        all_y3_ggr = sum(
            m["year3_ggr_usd"]
            for p in phases for m in p["markets"]
        )

        return {
            "generated": datetime.now(timezone.utc).isoformat(),
            "parameters": {
                "budget_usd": budget_usd,
                "years": years,
                "priority": self.priority_name,
                "products": products,
            },
            "summary": {
                "total_markets": len(selected_codes),
                "total_invested_usd": total_invested,
                "remaining_budget_usd": remaining_budget,
                "projected_year3_ggr_usd": all_y3_ggr,
                "estimated_portfolio_roi_3yr_pct": round(
                    (all_y3_ggr * 3 - total_invested) / total_invested * 100, 1
                ) if total_invested else 0,
            },
            "phases": phases,
            "markets_not_selected": [
                {"code": s["code"], "name": s["name"],
                 "reason": "budget" if s["financials"]["total_entry_cost_usd"] > remaining_budget
                 else "timeline",
                 "score": s["overall_score"]}
                for s in scored if s["code"] not in selected_codes
            ],
        }

    def compare_strategies(self, budget_usd: float, years: int) -> dict:
        """Compare different priority strategies."""
        strategies = {}
        for priority in self.PRIORITY_WEIGHTS:
            planner = ExpansionPlanner(priority=priority)
            roadmap = planner.generate_roadmap(budget_usd, years)
            strategies[priority] = {
                "markets_entered": roadmap["summary"]["total_markets"],
                "total_invested": roadmap["summary"]["total_invested_usd"],
                "projected_y3_ggr": roadmap["summary"]["projected_year3_ggr_usd"],
                "portfolio_roi_3yr": roadmap["summary"]["estimated_portfolio_roi_3yr_pct"],
                "phase_1_markets": [
                    m["code"] for p in roadmap["phases"]
                    if p["phase"] == 1 for m in p["markets"]
                ],
            }
        return {
            "budget_usd": budget_usd,
            "years": years,
            "strategy_comparison": strategies,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="iGaming Market Expansion Roadmap Generator")
    parser.add_argument("--budget", type=float, default=5000000,
                        help="Total expansion budget in USD")
    parser.add_argument("--years", type=int, default=3,
                        help="Expansion horizon in years")
    parser.add_argument("--priority", choices=list(ExpansionPlanner.PRIORITY_WEIGHTS.keys()),
                        default="balanced")
    parser.add_argument("--products", type=str,
                        help="Comma-separated product focus (sports,casino)")
    parser.add_argument("--exclude", type=str,
                        help="Comma-separated jurisdictions to exclude")
    parser.add_argument("--scenario", choices=["conservative", "balanced", "aggressive"],
                        help="Predefined scenario")
    parser.add_argument("--compare-strategies", action="store_true",
                        help="Compare all priority strategies")
    parser.add_argument("--rank", action="store_true",
                        help="Just rank jurisdictions without roadmap")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    # Handle predefined scenarios
    if args.scenario == "conservative":
        args.budget = args.budget or 2000000
        args.priority = "risk"
    elif args.scenario == "aggressive":
        args.budget = args.budget or 15000000
        args.priority = "roi_focused"

    products = args.products.split(",") if args.products else None
    exclude = args.exclude.split(",") if args.exclude else None

    planner = ExpansionPlanner(priority=args.priority)

    if args.compare_strategies:
        result = planner.compare_strategies(args.budget, args.years)
        print(json.dumps(result, indent=2))
        return

    if args.rank:
        scored = []
        for code, profile in planner.profiles.items():
            scored.append(planner.score_jurisdiction(profile))
        scored.sort(key=lambda x: x["overall_score"], reverse=True)

        if args.format == "json":
            print(json.dumps(scored, indent=2))
        else:
            print(f"=== Jurisdiction Ranking ({args.priority}) ===\n")
            print(f"{'#':<3} {'Jurisdiction':<25} {'Score':>6} {'Entry Cost':>15} "
                  f"{'Months':>7} {'Y3 GGR':>15} {'3yr ROI':>8} {'Difficulty':<10}")
            print("-" * 100)
            for i, s in enumerate(scored, 1):
                f = s["financials"]
                print(f"{i:<3} {s['name']:<25} {s['overall_score']:>5.1f} "
                      f"${f['total_entry_cost_usd']:>13,.0f} "
                      f"{s['timeline']['total_to_revenue_months']:>6} "
                      f"${f['year3_ggr_usd']:>13,.0f} "
                      f"{f['three_year_roi_pct']:>7.0f}% "
                      f"{s['strategic']['difficulty']:<10}")
        return

    roadmap = planner.generate_roadmap(args.budget, args.years, products, exclude)

    if args.format == "json":
        print(json.dumps(roadmap, indent=2))
    else:
        _print_roadmap(roadmap)


def _print_roadmap(roadmap: dict):
    """Pretty-print the expansion roadmap."""
    params = roadmap["parameters"]
    summary = roadmap["summary"]

    print("=" * 80)
    print(f"  MARKET EXPANSION ROADMAP")
    print(f"  Budget: ${params['budget_usd']:,.0f} | "
          f"Horizon: {params['years']} years | Priority: {params['priority']}")
    print("=" * 80)

    print(f"\n--- Summary ---")
    print(f"  Markets to enter: {summary['total_markets']}")
    print(f"  Total investment: ${summary['total_invested_usd']:,.0f}")
    print(f"  Remaining budget: ${summary['remaining_budget_usd']:,.0f}")
    print(f"  Projected Year 3 GGR: ${summary['projected_year3_ggr_usd']:,.0f}")
    print(f"  Portfolio 3-year ROI: {summary['estimated_portfolio_roi_3yr_pct']:.0f}%")

    for phase in roadmap["phases"]:
        print(f"\n{'='*60}")
        print(f"  PHASE {phase['phase']}: {phase['label']}")
        print(f"  Investment: ${phase['phase_investment_usd']:,.0f} | "
              f"Cumulative markets: {phase['cumulative_markets']}")
        print(f"{'='*60}")

        for m in phase["markets"]:
            print(f"\n  [{m['code']}] {m['jurisdiction']} "
                  f"(Score: {m['score']:.1f}, {m['difficulty']})")
            print(f"    Entry cost: ${m['entry_cost_usd']:,.0f}")
            print(f"    Time to revenue: {m['months_to_revenue']} months")
            print(f"    Y1 GGR: ${m['year1_ggr_usd']:,.0f}")
            print(f"    Y3 GGR: ${m['year3_ggr_usd']:,.0f}")
            print(f"    3-year ROI: {m['roi_3yr_pct']:.0f}%")
            if m["rationale"]:
                print(f"    Note: {m['rationale']}")

    if roadmap["markets_not_selected"]:
        print(f"\n--- Markets Deferred ---")
        for m in roadmap["markets_not_selected"]:
            print(f"  {m['code']}: {m['name']} (Score: {m['score']:.1f}, "
                  f"Reason: {m['reason']})")


if __name__ == "__main__":
    main()
