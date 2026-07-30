#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Jurisdiction Selection Tool for iGaming Licensing
====================================================

Weighted scoring system for selecting optimal licensing jurisdictions.
Evaluates cost, time-to-license, market access, tax rates, reputation,
and operational requirements.

Usage:
    python jurisdiction_selector.py --target-markets "UK,Germany,Brazil"
    python jurisdiction_selector.py --budget 500000 --time-limit 12
    python jurisdiction_selector.py --compare "MGA,UKGC,Curacao"
    python jurisdiction_selector.py --all --format json
"""

import json
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class JurisdictionProfile:
    """Complete profile of a licensing jurisdiction."""
    code: str
    name: str
    regulator: str
    regulator_website: str

    # Cost factors
    application_fee_usd: float
    annual_license_fee_usd: float
    estimated_setup_cost_usd: float    # legal, technical, compliance setup
    ongoing_annual_cost_usd: float     # compliance, reporting, local presence

    # Time factors
    application_timeline_months: int
    renewal_period_years: int

    # Tax and financial
    ggr_tax_pct: float                 # gross gaming revenue tax
    corporate_tax_pct: float
    vat_applicable: bool = False
    vat_rate_pct: float = 0

    # Market access
    population_millions: float = 0
    online_gambling_penetration_pct: float = 0
    estimated_market_size_usd: float = 0
    passport_to_eu: bool = False       # can operate in other EU/EEA states
    accepted_by_affiliates: bool = True
    payment_processor_support: str = "good"  # poor, fair, good, excellent

    # Reputation and compliance
    reputation_tier: int = 2           # 1=premium, 2=standard, 3=basic
    fatf_compliant: bool = True
    aml_framework_rating: str = "established"
    responsible_gaming_requirements: str = "standard"
    data_protection_framework: str = "GDPR"
    advertising_restrictions: str = "moderate"

    # Operational requirements
    local_director_required: bool = False
    local_office_required: bool = False
    local_server_required: bool = False
    minimum_capital_usd: float = 0
    key_person_background_checks: bool = True
    source_code_escrow: bool = False
    independent_testing_required: bool = True
    testing_lab: str = ""

    # Products allowed
    sports_betting: bool = True
    online_casino: bool = True
    live_casino: bool = True
    poker: bool = True
    bingo: bool = True
    lottery: bool = False
    virtual_sports: bool = True
    esports: bool = True
    crypto_accepted: bool = False

    # Risk factors
    political_stability: str = "stable"
    currency_risk: str = "low"
    regulatory_change_risk: str = "low"

    notes: str = ""


# ---------------------------------------------------------------------------
# Jurisdiction database (realistic data as of 2025-2026)
# ---------------------------------------------------------------------------

JURISDICTIONS = [
    JurisdictionProfile(
        code="MGA", name="Malta (MGA)",
        regulator="Malta Gaming Authority",
        regulator_website="https://www.mga.org.mt",
        application_fee_usd=28000,
        annual_license_fee_usd=35000,
        estimated_setup_cost_usd=150000,
        ongoing_annual_cost_usd=200000,
        application_timeline_months=6,
        renewal_period_years=5,
        ggr_tax_pct=5.0,
        corporate_tax_pct=35.0,  # effective ~5% with refund system
        population_millions=0.5,
        estimated_market_size_usd=200000000,
        online_gambling_penetration_pct=45,
        passport_to_eu=True,
        payment_processor_support="excellent",
        reputation_tier=1,
        aml_framework_rating="advanced",
        responsible_gaming_requirements="high",
        local_director_required=True,
        local_office_required=True,
        minimum_capital_usd=120000,
        source_code_escrow=True,
        independent_testing_required=True,
        testing_lab="GLI, BMM, eCOGRA, iTech Labs",
        crypto_accepted=True,
        notes="Premier EU jurisdiction. Single license covers all verticals. "
              "EU passporting via freedom of services.",
    ),
    JurisdictionProfile(
        code="UKGC", name="United Kingdom (UKGC)",
        regulator="UK Gambling Commission",
        regulator_website="https://www.gamblingcommission.gov.uk",
        application_fee_usd=40000,
        annual_license_fee_usd=120000,  # varies by revenue
        estimated_setup_cost_usd=300000,
        ongoing_annual_cost_usd=500000,
        application_timeline_months=8,
        renewal_period_years=5,
        ggr_tax_pct=21.0,  # Remote Gaming Duty
        corporate_tax_pct=25.0,
        vat_applicable=True,
        vat_rate_pct=20.0,
        population_millions=67,
        estimated_market_size_usd=18000000000,
        online_gambling_penetration_pct=38,
        passport_to_eu=False,
        payment_processor_support="excellent",
        reputation_tier=1,
        aml_framework_rating="advanced",
        responsible_gaming_requirements="very_high",
        advertising_restrictions="strict",
        local_director_required=True,
        local_office_required=False,
        minimum_capital_usd=0,
        source_code_escrow=False,
        independent_testing_required=True,
        testing_lab="GLI, BMM, NMi, eCOGRA",
        notes="Largest regulated market. Strict responsible gaming (GAMSTOP integration required). "
              "Affordability checks mandatory from 2024.",
    ),
    JurisdictionProfile(
        code="GIB", name="Gibraltar",
        regulator="Gibraltar Gambling Commissioner",
        regulator_website="https://www.gibraltar.gov.gi/gambling",
        application_fee_usd=20000,
        annual_license_fee_usd=100000,
        estimated_setup_cost_usd=250000,
        ongoing_annual_cost_usd=350000,
        application_timeline_months=6,
        renewal_period_years=5,
        ggr_tax_pct=1.0,
        corporate_tax_pct=12.5,
        population_millions=0.03,
        estimated_market_size_usd=100000000,
        passport_to_eu=False,
        payment_processor_support="excellent",
        reputation_tier=1,
        aml_framework_rating="advanced",
        local_director_required=True,
        local_office_required=True,
        minimum_capital_usd=100000,
        independent_testing_required=True,
        testing_lab="GLI, BMM",
        notes="Premium jurisdiction. Home to bet365, 888, Entain. "
              "Very selective — new licenses rare. Low tax rates.",
    ),
    JurisdictionProfile(
        code="IOM", name="Isle of Man",
        regulator="Isle of Man Gambling Supervision Commission",
        regulator_website="https://www.gov.im/gambling/",
        application_fee_usd=50000,
        annual_license_fee_usd=50000,
        estimated_setup_cost_usd=200000,
        ongoing_annual_cost_usd=250000,
        application_timeline_months=4,
        renewal_period_years=5,
        ggr_tax_pct=1.5,
        corporate_tax_pct=0,
        population_millions=0.085,
        passport_to_eu=False,
        payment_processor_support="good",
        reputation_tier=1,
        aml_framework_rating="advanced",
        local_director_required=True,
        local_office_required=True,
        minimum_capital_usd=100000,
        independent_testing_required=True,
        testing_lab="GLI, BMM, eCOGRA",
        notes="Very favorable tax regime (0% corporate tax). White list jurisdiction. "
              "Home to PokerStars (Flutter).",
    ),
    JurisdictionProfile(
        code="CUR", name="Curaçao",
        regulator="Curaçao Gaming Control Board",
        regulator_website="https://www.gamingcontrolcuracao.org",
        application_fee_usd=18000,
        annual_license_fee_usd=18000,
        estimated_setup_cost_usd=50000,
        ongoing_annual_cost_usd=80000,
        application_timeline_months=3,
        renewal_period_years=5,
        ggr_tax_pct=0,
        corporate_tax_pct=22.0,  # new regime from 2024
        population_millions=0.15,
        estimated_market_size_usd=50000000,
        passport_to_eu=False,
        payment_processor_support="fair",
        reputation_tier=3,
        aml_framework_rating="developing",
        responsible_gaming_requirements="basic",
        local_director_required=True,
        local_office_required=True,
        independent_testing_required=True,
        testing_lab="GLI, BMM, Gaming Labs Curaçao",
        crypto_accepted=True,
        notes="Reformed licensing regime from 2024 under new GCB. "
              "Previously lax oversight. Still cost-effective but improving standards.",
    ),
    JurisdictionProfile(
        code="SWE", name="Sweden",
        regulator="Spelinspektionen",
        regulator_website="https://www.spelinspektionen.se",
        application_fee_usd=35000,
        annual_license_fee_usd=20000,
        estimated_setup_cost_usd=150000,
        ongoing_annual_cost_usd=180000,
        application_timeline_months=6,
        renewal_period_years=5,
        ggr_tax_pct=18.0,
        corporate_tax_pct=20.6,
        population_millions=10.5,
        estimated_market_size_usd=3200000000,
        online_gambling_penetration_pct=42,
        passport_to_eu=False,  # national license required
        payment_processor_support="good",
        reputation_tier=1,
        aml_framework_rating="advanced",
        responsible_gaming_requirements="very_high",
        advertising_restrictions="strict",
        local_director_required=False,
        local_office_required=False,
        independent_testing_required=True,
        testing_lab="GLI, BMM, RISE",
        notes="Mature Nordic market. Strict bonus restrictions (welcome bonus only). "
              "Mandatory Spelpaus self-exclusion integration.",
    ),
    JurisdictionProfile(
        code="ONT", name="Ontario, Canada",
        regulator="Alcohol and Gaming Commission of Ontario (iGO)",
        regulator_website="https://igamingontario.ca",
        application_fee_usd=100000,
        annual_license_fee_usd=100000,
        estimated_setup_cost_usd=400000,
        ongoing_annual_cost_usd=500000,
        application_timeline_months=9,
        renewal_period_years=5,
        ggr_tax_pct=20.0,
        corporate_tax_pct=26.5,
        population_millions=15,
        estimated_market_size_usd=5000000000,
        online_gambling_penetration_pct=25,
        payment_processor_support="good",
        reputation_tier=1,
        aml_framework_rating="advanced",
        responsible_gaming_requirements="high",
        local_director_required=False,
        local_office_required=False,
        independent_testing_required=True,
        testing_lab="GLI, BMM, iTech Labs",
        notes="Largest Canadian provincial market. Revenue share model with iGO. "
              "Growing rapidly since 2022 launch.",
    ),
    JurisdictionProfile(
        code="BRA", name="Brazil (SPA/MF)",
        regulator="Secretaria de Prêmios e Apostas (Ministry of Finance)",
        regulator_website="https://www.gov.br/fazenda",
        application_fee_usd=6000000,  # R$30M
        annual_license_fee_usd=0,     # included in 5-year fee
        estimated_setup_cost_usd=1000000,
        ongoing_annual_cost_usd=400000,
        application_timeline_months=6,
        renewal_period_years=5,
        ggr_tax_pct=12.0,
        corporate_tax_pct=34.0,
        population_millions=215,
        estimated_market_size_usd=8000000000,
        online_gambling_penetration_pct=15,
        passport_to_eu=False,
        payment_processor_support="good",
        reputation_tier=2,
        aml_framework_rating="established",
        responsible_gaming_requirements="standard",
        local_director_required=True,
        local_office_required=True,
        local_server_required=True,
        minimum_capital_usd=12000000,
        independent_testing_required=True,
        testing_lab="GLI, BMM, SBC Labs",
        crypto_accepted=False,
        notes="Massive emerging market. Regulated from Jan 2025. "
              "High entry cost (R$30M license). SIGAP reporting system. PIX payments dominant.",
    ),
]


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

DEFAULT_WEIGHTS = {
    "cost": 0.20,
    "time_to_market": 0.10,
    "market_size": 0.20,
    "tax_efficiency": 0.15,
    "reputation": 0.15,
    "operational_ease": 0.10,
    "product_flexibility": 0.10,
}


# ---------------------------------------------------------------------------
# Jurisdiction selector engine
# ---------------------------------------------------------------------------

class JurisdictionSelector:
    """Select optimal licensing jurisdictions based on weighted criteria."""

    def __init__(self, weights: Optional[dict] = None):
        self.jurisdictions = {j.code: j for j in JURISDICTIONS}
        self.weights = weights or DEFAULT_WEIGHTS
        self._normalize_weights()

    def _normalize_weights(self):
        total = sum(self.weights.values())
        if total > 0:
            self.weights = {k: v / total for k, v in self.weights.items()}

    def score_jurisdiction(self, j: JurisdictionProfile, budget_usd: Optional[float] = None,
                           time_limit_months: Optional[int] = None) -> dict:
        """Score a jurisdiction on all dimensions (0-100 each)."""
        scores = {}

        # Cost score (lower = better)
        total_first_year = j.application_fee_usd + j.annual_license_fee_usd + j.estimated_setup_cost_usd
        if total_first_year < 100000:
            scores["cost"] = 95
        elif total_first_year < 300000:
            scores["cost"] = 80
        elif total_first_year < 500000:
            scores["cost"] = 65
        elif total_first_year < 1000000:
            scores["cost"] = 50
        elif total_first_year < 3000000:
            scores["cost"] = 30
        else:
            scores["cost"] = 15

        if budget_usd and total_first_year > budget_usd:
            scores["cost"] = max(0, scores["cost"] - 30)

        # Time to market (faster = better)
        months = j.application_timeline_months
        if months <= 3:
            scores["time_to_market"] = 95
        elif months <= 6:
            scores["time_to_market"] = 75
        elif months <= 9:
            scores["time_to_market"] = 55
        elif months <= 12:
            scores["time_to_market"] = 35
        else:
            scores["time_to_market"] = 15

        if time_limit_months and months > time_limit_months:
            scores["time_to_market"] = max(0, scores["time_to_market"] - 40)

        # Market size
        market = j.estimated_market_size_usd
        if market > 10000000000:
            scores["market_size"] = 95
        elif market > 5000000000:
            scores["market_size"] = 85
        elif market > 1000000000:
            scores["market_size"] = 70
        elif market > 500000000:
            scores["market_size"] = 55
        elif market > 100000000:
            scores["market_size"] = 40
        else:
            scores["market_size"] = 20

        if j.passport_to_eu:
            scores["market_size"] = min(100, scores["market_size"] + 15)

        # Tax efficiency (lower total tax = better)
        effective_tax = j.ggr_tax_pct + (j.corporate_tax_pct * 0.3)
        if effective_tax < 10:
            scores["tax_efficiency"] = 95
        elif effective_tax < 20:
            scores["tax_efficiency"] = 75
        elif effective_tax < 30:
            scores["tax_efficiency"] = 55
        elif effective_tax < 40:
            scores["tax_efficiency"] = 35
        else:
            scores["tax_efficiency"] = 20

        # Reputation
        reputation_scores = {1: 90, 2: 60, 3: 30}
        scores["reputation"] = reputation_scores.get(j.reputation_tier, 50)
        if j.fatf_compliant:
            scores["reputation"] = min(100, scores["reputation"] + 5)
        if j.payment_processor_support == "excellent":
            scores["reputation"] = min(100, scores["reputation"] + 5)

        # Operational ease
        op_score = 80
        if j.local_office_required:
            op_score -= 15
        if j.local_director_required:
            op_score -= 10
        if j.local_server_required:
            op_score -= 10
        if j.minimum_capital_usd > 500000:
            op_score -= 10
        if j.source_code_escrow:
            op_score -= 5
        scores["operational_ease"] = max(0, op_score)

        # Product flexibility
        product_count = sum([j.sports_betting, j.online_casino, j.live_casino, j.poker,
                             j.bingo, j.lottery, j.virtual_sports, j.esports])
        scores["product_flexibility"] = min(100, product_count * 12)
        if j.crypto_accepted:
            scores["product_flexibility"] = min(100, scores["product_flexibility"] + 10)

        # Overall weighted score
        overall = sum(scores[k] * self.weights.get(k, 0) for k in scores)

        return {
            "jurisdiction": j.name,
            "code": j.code,
            "overall_score": round(overall, 1),
            "dimension_scores": scores,
            "first_year_cost_usd": total_first_year,
            "ongoing_annual_usd": j.ongoing_annual_cost_usd + j.annual_license_fee_usd,
            "timeline_months": j.application_timeline_months,
            "ggr_tax_pct": j.ggr_tax_pct,
            "reputation_tier": j.reputation_tier,
            "passport_to_eu": j.passport_to_eu,
            "within_budget": total_first_year <= budget_usd if budget_usd else True,
            "within_timeline": j.application_timeline_months <= time_limit_months if time_limit_months else True,
        }

    def rank_jurisdictions(self, budget_usd: Optional[float] = None,
                           time_limit_months: Optional[int] = None,
                           required_products: Optional[list] = None) -> list[dict]:
        """Rank all jurisdictions by weighted score."""
        results = []
        for j in self.jurisdictions.values():
            # Filter by required products
            if required_products:
                product_map = {
                    "sports": j.sports_betting, "casino": j.online_casino,
                    "live_casino": j.live_casino, "poker": j.poker,
                    "bingo": j.bingo, "esports": j.esports,
                }
                if not all(product_map.get(p, True) for p in required_products):
                    continue

            scored = self.score_jurisdiction(j, budget_usd, time_limit_months)
            results.append(scored)

        results.sort(key=lambda x: x["overall_score"], reverse=True)
        return results

    def compare_jurisdictions(self, codes: list[str]) -> dict:
        """Side-by-side comparison of specific jurisdictions."""
        results = []
        for code in codes:
            j = self.jurisdictions.get(code.upper())
            if j:
                scored = self.score_jurisdiction(j)
                scored["details"] = asdict(j)
                results.append(scored)

        results.sort(key=lambda x: x["overall_score"], reverse=True)

        return {
            "comparison_date": datetime.now(timezone.utc).isoformat(),
            "jurisdictions_compared": len(results),
            "weights_used": self.weights,
            "results": results,
            "recommendation": results[0]["jurisdiction"] if results else None,
        }

    def multi_jurisdiction_strategy(self, budget_usd: float,
                                     target_regions: Optional[list] = None) -> dict:
        """Recommend a multi-jurisdiction licensing strategy."""
        all_ranked = self.rank_jurisdictions(budget_usd=budget_usd)

        phases: list[dict] = []
        remaining_budget = budget_usd
        selected: list[dict] = []

        # Phase 1: Primary license (highest scored within budget)
        for j in all_ranked:
            if j["first_year_cost_usd"] <= remaining_budget and j["reputation_tier"] <= 2:
                selected.append(j)
                remaining_budget -= j["first_year_cost_usd"]
                phases.append({
                    "phase": 1,
                    "jurisdiction": j["jurisdiction"],
                    "cost_usd": j["first_year_cost_usd"],
                    "timeline_months": j["timeline_months"],
                    "rationale": "Primary license — best overall score within budget",
                })
                break

        # Phase 2: Market expansion (add complementary jurisdictions)
        for j in all_ranked:
            if (j["code"] not in [s["code"] for s in selected]
                    and j["first_year_cost_usd"] <= remaining_budget
                    and j["overall_score"] > 50):
                selected.append(j)
                remaining_budget -= j["first_year_cost_usd"]
                phases.append({
                    "phase": 2,
                    "jurisdiction": j["jurisdiction"],
                    "cost_usd": j["first_year_cost_usd"],
                    "timeline_months": j["timeline_months"],
                    "rationale": "Market expansion — complementary coverage",
                })
                if len(selected) >= 3:
                    break

        return {
            "budget_usd": budget_usd,
            "phases": phases,
            "total_estimated_cost": sum(p["cost_usd"] for p in phases),
            "remaining_budget": remaining_budget,
            "jurisdictions_selected": len(selected),
        }

    def generate_report(self, budget_usd: Optional[float] = None) -> dict:
        """Full jurisdiction analysis report."""
        return {
            "generated": datetime.now(timezone.utc).isoformat(),
            "jurisdictions_analyzed": len(self.jurisdictions),
            "weights": self.weights,
            "ranking": self.rank_jurisdictions(budget_usd=budget_usd),
            "multi_jurisdiction_strategy": self.multi_jurisdiction_strategy(
                budget_usd or 1000000),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming Jurisdiction Selection Tool")
    parser.add_argument("--all", action="store_true", help="Show all jurisdictions ranked")
    parser.add_argument("--compare", type=str, help="Compare jurisdictions (comma-separated codes)")
    parser.add_argument("--budget", type=float, help="Maximum first-year budget in USD")
    parser.add_argument("--time-limit", type=int, help="Maximum months to obtain license")
    parser.add_argument("--strategy", action="store_true", help="Multi-jurisdiction strategy")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    selector = JurisdictionSelector()

    if args.compare:
        codes = [c.strip() for c in args.compare.split(",")]
        result = selector.compare_jurisdictions(codes)
        print(json.dumps(result, indent=2, default=str))
        return

    if args.strategy:
        budget = args.budget or 1000000
        result = selector.multi_jurisdiction_strategy(budget)
        print(json.dumps(result, indent=2, default=str))
        return

    ranked = selector.rank_jurisdictions(budget_usd=args.budget, time_limit_months=args.time_limit)

    if args.format == "json" or args.all:
        print(json.dumps(ranked if args.all else ranked[:5], indent=2, default=str))
    else:
        print("=== iGaming Jurisdiction Selector ===\n")
        if args.budget:
            print(f"Budget: ${args.budget:,.0f}")
        if args.time_limit:
            print(f"Time limit: {args.time_limit} months\n")

        print(f"{'Rank':<5} {'Jurisdiction':<30} {'Score':<8} {'Cost (Y1)':<15} "
              f"{'Timeline':<10} {'GGR Tax':<10} {'Tier':<5}")
        print("-" * 95)
        for i, r in enumerate(ranked, 1):
            budget_flag = " *" if not r["within_budget"] else ""
            time_flag = " *" if not r["within_timeline"] else ""
            print(f"{i:<5} {r['jurisdiction']:<30} {r['overall_score']:<8.1f} "
                  f"${r['first_year_cost_usd']:<14,.0f} {r['timeline_months']:<2}mo{time_flag:<7s} "
                  f"{r['ggr_tax_pct']:<9.1f}% T{r['reputation_tier']}{budget_flag}")

        print(f"\n* = exceeds constraint")
        print(f"\nTop recommendation: {ranked[0]['jurisdiction']} (Score: {ranked[0]['overall_score']})")


if __name__ == "__main__":
    main()
