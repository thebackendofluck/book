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
TAM/SAM/SOM Calculator for iGaming Jurisdictions
==================================================

Calculates Total Addressable Market, Serviceable Addressable Market,
and Serviceable Obtainable Market for online gambling in any jurisdiction.

Methodology:
  TAM = Population x Internet Penetration x Gambling Participation Rate x Avg Spend
  SAM = TAM x Product Coverage x Regulatory Addressability x Payment Reach
  SOM = SAM x Realistic Market Share x Year-over-Year Ramp

Usage:
    python tam_sam_som_calculator.py --jurisdiction UK
    python tam_sam_som_calculator.py --custom --population 67 --internet-pct 97 \
        --participation-pct 44 --avg-spend 660
    python tam_sam_som_calculator.py --all --format json
    python tam_sam_som_calculator.py --scenario optimistic --jurisdiction BR
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Jurisdiction inputs
# ---------------------------------------------------------------------------

@dataclass
class JurisdictionInputs:
    """Input parameters for TAM/SAM/SOM calculation."""
    code: str
    name: str
    population_millions: float
    internet_penetration_pct: float
    smartphone_penetration_pct: float
    gambling_participation_pct: float
    avg_annual_spend_usd: float

    # Regulatory and market factors
    regulatory_addressability_pct: float  # % of population legally reachable
    product_coverage_pct: float  # % of products you can offer vs full suite
    payment_reach_pct: float  # % of target pop that can transact with you
    ggr_tax_pct: float
    corporate_tax_pct: float

    # Competitive factors
    num_licensed_operators: int
    top_3_share_pct: float
    market_maturity: str  # "emerging", "growing", "mature", "saturated"
    brand_recognition_factor: float = 0.0  # 0-1 if existing brand in market

    # Growth assumptions
    market_growth_pct: float = 10.0  # annual market growth
    channelization_trend_pct: float = 0.0  # annual shift from grey to regulated

    # Product mix (% of your revenue by product)
    sports_pct: float = 40
    casino_pct: float = 35
    live_casino_pct: float = 15
    poker_pct: float = 5
    other_pct: float = 5


JURISDICTIONS = {
    "UK": JurisdictionInputs(
        code="UK", name="United Kingdom",
        population_millions=67.7, internet_penetration_pct=97,
        smartphone_penetration_pct=92, gambling_participation_pct=44,
        avg_annual_spend_usd=660,
        regulatory_addressability_pct=100, product_coverage_pct=95,
        payment_reach_pct=98, ggr_tax_pct=21, corporate_tax_pct=25,
        num_licensed_operators=280, top_3_share_pct=42,
        market_maturity="mature",
        market_growth_pct=3.2, channelization_trend_pct=0,
    ),
    "DE": JurisdictionInputs(
        code="DE", name="Germany",
        population_millions=83.2, internet_penetration_pct=93,
        smartphone_penetration_pct=88, gambling_participation_pct=38,
        avg_annual_spend_usd=494,
        regulatory_addressability_pct=85, product_coverage_pct=70,
        payment_reach_pct=92, ggr_tax_pct=5.3, corporate_tax_pct=30,
        num_licensed_operators=52, top_3_share_pct=35,
        market_maturity="growing",
        market_growth_pct=12, channelization_trend_pct=5,
    ),
    "BR": JurisdictionInputs(
        code="BR", name="Brazil",
        population_millions=215, internet_penetration_pct=84,
        smartphone_penetration_pct=78, gambling_participation_pct=22,
        avg_annual_spend_usd=250,
        regulatory_addressability_pct=95, product_coverage_pct=80,
        payment_reach_pct=90, ggr_tax_pct=12, corporate_tax_pct=34,
        num_licensed_operators=65, top_3_share_pct=55,
        market_maturity="emerging",
        market_growth_pct=45, channelization_trend_pct=15,
    ),
    "US": JurisdictionInputs(
        code="US", name="United States",
        population_millions=335, internet_penetration_pct=95,
        smartphone_penetration_pct=90, gambling_participation_pct=32,
        avg_annual_spend_usd=509,
        regulatory_addressability_pct=45, product_coverage_pct=60,
        payment_reach_pct=95, ggr_tax_pct=20, corporate_tax_pct=21,
        num_licensed_operators=120, top_3_share_pct=72,
        market_maturity="growing",
        market_growth_pct=22, channelization_trend_pct=8,
    ),
    "IT": JurisdictionInputs(
        code="IT", name="Italy",
        population_millions=59, internet_penetration_pct=88,
        smartphone_penetration_pct=83, gambling_participation_pct=30,
        avg_annual_spend_usd=1115,
        regulatory_addressability_pct=100, product_coverage_pct=90,
        payment_reach_pct=88, ggr_tax_pct=25, corporate_tax_pct=24,
        num_licensed_operators=95, top_3_share_pct=38,
        market_maturity="mature",
        market_growth_pct=12, channelization_trend_pct=3,
    ),
    "SE": JurisdictionInputs(
        code="SE", name="Sweden",
        population_millions=10.5, internet_penetration_pct=98,
        smartphone_penetration_pct=95, gambling_participation_pct=58,
        avg_annual_spend_usd=656,
        regulatory_addressability_pct=100, product_coverage_pct=85,
        payment_reach_pct=95, ggr_tax_pct=18, corporate_tax_pct=20.6,
        num_licensed_operators=95, top_3_share_pct=48,
        market_maturity="mature",
        market_growth_pct=4.5, channelization_trend_pct=1,
    ),
    "NG": JurisdictionInputs(
        code="NG", name="Nigeria",
        population_millions=225, internet_penetration_pct=55,
        smartphone_penetration_pct=42, gambling_participation_pct=18,
        avg_annual_spend_usd=92,
        regulatory_addressability_pct=60, product_coverage_pct=70,
        payment_reach_pct=65, ggr_tax_pct=5, corporate_tax_pct=30,
        num_licensed_operators=80, top_3_share_pct=60,
        market_maturity="emerging",
        market_growth_pct=35, channelization_trend_pct=10,
    ),
    "PH": JurisdictionInputs(
        code="PH", name="Philippines",
        population_millions=115, internet_penetration_pct=68,
        smartphone_penetration_pct=62, gambling_participation_pct=28,
        avg_annual_spend_usd=295,
        regulatory_addressability_pct=90, product_coverage_pct=85,
        payment_reach_pct=70, ggr_tax_pct=5, corporate_tax_pct=25,
        num_licensed_operators=55, top_3_share_pct=45,
        market_maturity="growing",
        market_growth_pct=18, channelization_trend_pct=5,
    ),
    "AU": JurisdictionInputs(
        code="AU", name="Australia",
        population_millions=26.5, internet_penetration_pct=96,
        smartphone_penetration_pct=91, gambling_participation_pct=64,
        avg_annual_spend_usd=1238,
        regulatory_addressability_pct=100, product_coverage_pct=35,
        payment_reach_pct=95, ggr_tax_pct=15, corporate_tax_pct=30,
        num_licensed_operators=35, top_3_share_pct=65,
        market_maturity="mature",
        market_growth_pct=6.5, channelization_trend_pct=0,
        sports_pct=92, casino_pct=0, live_casino_pct=0, poker_pct=0, other_pct=8,
    ),
    "JP": JurisdictionInputs(
        code="JP", name="Japan",
        population_millions=124, internet_penetration_pct=93,
        smartphone_penetration_pct=85, gambling_participation_pct=12,
        avg_annual_spend_usd=250,
        regulatory_addressability_pct=15, product_coverage_pct=25,
        payment_reach_pct=80, ggr_tax_pct=30, corporate_tax_pct=23.2,
        num_licensed_operators=8, top_3_share_pct=85,
        market_maturity="emerging",
        market_growth_pct=20, channelization_trend_pct=3,
    ),
}


# ---------------------------------------------------------------------------
# Scenario modifiers
# ---------------------------------------------------------------------------

SCENARIOS = {
    "conservative": {
        "participation_modifier": 0.85,
        "spend_modifier": 0.80,
        "market_share_base": 0.005,
        "ramp_year1": 0.3,
        "ramp_year2": 0.6,
        "ramp_year3": 0.8,
        "ramp_year4": 0.9,
        "ramp_year5": 1.0,
    },
    "base": {
        "participation_modifier": 1.0,
        "spend_modifier": 1.0,
        "market_share_base": 0.01,
        "ramp_year1": 0.4,
        "ramp_year2": 0.7,
        "ramp_year3": 0.85,
        "ramp_year4": 0.95,
        "ramp_year5": 1.0,
    },
    "optimistic": {
        "participation_modifier": 1.15,
        "spend_modifier": 1.20,
        "market_share_base": 0.02,
        "ramp_year1": 0.5,
        "ramp_year2": 0.8,
        "ramp_year3": 0.95,
        "ramp_year4": 1.0,
        "ramp_year5": 1.0,
    },
}


# ---------------------------------------------------------------------------
# Calculator
# ---------------------------------------------------------------------------

class TAMSAMSOMCalculator:
    """Calculate TAM, SAM, SOM for iGaming jurisdictions."""

    def __init__(self, scenario: str = "base"):
        if scenario not in SCENARIOS:
            raise ValueError(f"Unknown scenario: {scenario}. "
                             f"Choose from: {list(SCENARIOS.keys())}")
        self.scenario = SCENARIOS[scenario]
        self.scenario_name = scenario

    def calculate(self, inputs: JurisdictionInputs) -> dict:
        """Full TAM/SAM/SOM calculation."""

        mod = self.scenario

        # --- TAM ---
        # Total potential market if every internet user who gambles spent avg
        addressable_pop = (inputs.population_millions * 1_000_000
                           * inputs.internet_penetration_pct / 100)
        gambling_pop = (addressable_pop
                        * inputs.gambling_participation_pct / 100
                        * mod["participation_modifier"])
        avg_spend = inputs.avg_annual_spend_usd * mod["spend_modifier"]
        tam = gambling_pop * avg_spend

        # --- SAM ---
        # Market you can actually serve given product, regulation, payments
        sam_factor = (inputs.regulatory_addressability_pct / 100
                      * inputs.product_coverage_pct / 100
                      * inputs.payment_reach_pct / 100)
        sam = tam * sam_factor

        # --- SOM ---
        # Realistic obtainable share considering competition and ramp
        market_share = self._estimate_market_share(inputs, mod)
        som = sam * market_share

        # --- 5-year projection ---
        projections = self._five_year_projection(inputs, tam, sam, som, mod)

        # --- Revenue after tax ---
        ggr = som
        net_ggr = ggr * (1 - inputs.ggr_tax_pct / 100)
        net_after_corp = net_ggr * (1 - inputs.corporate_tax_pct / 100)

        # --- Product mix revenue ---
        product_mix = {
            "sports_betting": som * inputs.sports_pct / 100,
            "casino": som * inputs.casino_pct / 100,
            "live_casino": som * inputs.live_casino_pct / 100,
            "poker": som * inputs.poker_pct / 100,
            "other": som * inputs.other_pct / 100,
        }

        return {
            "jurisdiction": inputs.name,
            "code": inputs.code,
            "scenario": self.scenario_name,
            "inputs_summary": {
                "population_millions": inputs.population_millions,
                "internet_penetration_pct": inputs.internet_penetration_pct,
                "gambling_participation_pct": inputs.gambling_participation_pct,
                "avg_annual_spend_usd": inputs.avg_annual_spend_usd,
                "regulatory_addressability_pct": inputs.regulatory_addressability_pct,
                "product_coverage_pct": inputs.product_coverage_pct,
                "payment_reach_pct": inputs.payment_reach_pct,
                "num_competitors": inputs.num_licensed_operators,
                "market_maturity": inputs.market_maturity,
            },
            "tam": {
                "value_usd": round(tam),
                "addressable_population": round(addressable_pop),
                "gambling_population": round(gambling_pop),
                "avg_spend_usd": round(avg_spend, 2),
                "description": "Total potential online gambling revenue if "
                               "full participation at average spend",
            },
            "sam": {
                "value_usd": round(sam),
                "sam_factor": round(sam_factor, 4),
                "pct_of_tam": round(sam / tam * 100, 1) if tam else 0,
                "description": "Market reachable given your regulatory status, "
                               "product suite, and payment coverage",
            },
            "som": {
                "value_usd": round(som),
                "market_share_pct": round(market_share * 100, 3),
                "pct_of_sam": round(som / sam * 100, 1) if sam else 0,
                "description": "Realistic obtainable revenue at steady state "
                               f"({market_share*100:.2f}% market share)",
            },
            "revenue_analysis": {
                "gross_gaming_revenue": round(som),
                "ggr_tax": round(som * inputs.ggr_tax_pct / 100),
                "net_after_ggr_tax": round(net_ggr),
                "corporate_tax": round(net_ggr * inputs.corporate_tax_pct / 100),
                "net_after_all_tax": round(net_after_corp),
                "effective_tax_rate_pct": round(
                    (1 - net_after_corp / som) * 100, 1) if som else 0,
            },
            "product_mix_revenue": {
                k: round(v) for k, v in product_mix.items()
            },
            "five_year_projection": projections,
        }

    def _estimate_market_share(self, inputs: JurisdictionInputs,
                                mod: dict) -> float:
        """Estimate achievable market share based on competition."""
        base_share = mod["market_share_base"]

        # Adjust for market maturity
        maturity_factor = {
            "emerging": 2.5,  # easier to grab share
            "growing": 1.5,
            "mature": 0.8,
            "saturated": 0.5,
        }
        base_share *= maturity_factor.get(inputs.market_maturity, 1.0)

        # Adjust for concentration (harder if top 3 dominate)
        if inputs.top_3_share_pct > 70:
            base_share *= 0.6
        elif inputs.top_3_share_pct > 50:
            base_share *= 0.8

        # Adjust for number of operators
        if inputs.num_licensed_operators > 200:
            base_share *= 0.7
        elif inputs.num_licensed_operators < 30:
            base_share *= 1.3

        # Brand recognition bonus
        base_share *= (1 + inputs.brand_recognition_factor)

        return min(base_share, 0.15)  # cap at 15%

    def _five_year_projection(self, inputs: JurisdictionInputs,
                               tam: float, sam: float, som: float,
                               mod: dict) -> list[dict]:
        """5-year revenue projection with ramp and market growth."""
        projections = []
        for year in range(1, 6):
            ramp = mod[f"ramp_year{year}"]
            growth_factor = (1 + inputs.market_growth_pct / 100) ** (year - 1)
            channel_factor = (1 + inputs.channelization_trend_pct / 100) ** (year - 1)

            year_tam = tam * growth_factor * channel_factor
            year_sam = sam * growth_factor * channel_factor
            year_som = som * ramp * growth_factor * channel_factor
            year_net = year_som * (1 - inputs.ggr_tax_pct / 100)

            projections.append({
                "year": year,
                "ramp_pct": round(ramp * 100),
                "tam_usd": round(year_tam),
                "sam_usd": round(year_sam),
                "som_usd": round(year_som),
                "ggr_after_tax_usd": round(year_net),
                "market_growth_cumulative_pct": round(
                    (growth_factor - 1) * 100, 1),
            })
        return projections

    def compare_jurisdictions(self, codes: list[str]) -> dict:
        """Compare TAM/SAM/SOM across jurisdictions."""
        results = []
        for code in codes:
            inputs = JURISDICTIONS.get(code.upper())
            if inputs:
                calc = self.calculate(inputs)
                results.append({
                    "jurisdiction": calc["jurisdiction"],
                    "code": calc["code"],
                    "tam_usd": calc["tam"]["value_usd"],
                    "sam_usd": calc["sam"]["value_usd"],
                    "som_usd": calc["som"]["value_usd"],
                    "market_share_pct": calc["som"]["market_share_pct"],
                    "net_revenue_yr5": calc["five_year_projection"][4]["ggr_after_tax_usd"],
                    "effective_tax_pct": calc["revenue_analysis"]["effective_tax_rate_pct"],
                })

        results.sort(key=lambda x: x["som_usd"], reverse=True)
        return {
            "scenario": self.scenario_name,
            "comparison": results,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TAM/SAM/SOM Calculator for iGaming Markets")
    parser.add_argument("--jurisdiction", "-j", type=str,
                        help="Jurisdiction code (e.g. UK, BR, US)")
    parser.add_argument("--all", action="store_true",
                        help="Calculate for all jurisdictions")
    parser.add_argument("--compare", type=str,
                        help="Compare jurisdictions (comma-separated)")
    parser.add_argument("--scenario", choices=["conservative", "base", "optimistic"],
                        default="base")
    parser.add_argument("--format", choices=["json", "text"], default="text")

    # Custom inputs
    parser.add_argument("--custom", action="store_true",
                        help="Use custom inputs")
    parser.add_argument("--population", type=float, help="Population in millions")
    parser.add_argument("--internet-pct", type=float,
                        help="Internet penetration %%")
    parser.add_argument("--participation-pct", type=float,
                        help="Gambling participation %%")
    parser.add_argument("--avg-spend", type=float,
                        help="Average annual spend per player (USD)")
    parser.add_argument("--reg-addressability", type=float, default=100,
                        help="Regulatory addressability %%")
    parser.add_argument("--product-coverage", type=float, default=85,
                        help="Product coverage %%")
    parser.add_argument("--payment-reach", type=float, default=90,
                        help="Payment reach %%")
    parser.add_argument("--ggr-tax", type=float, default=15,
                        help="GGR tax rate %%")
    parser.add_argument("--competitors", type=int, default=50,
                        help="Number of licensed operators")
    args = parser.parse_args()

    calc = TAMSAMSOMCalculator(scenario=args.scenario)

    if args.custom:
        if not all([args.population, args.internet_pct,
                     args.participation_pct, args.avg_spend]):
            parser.error("--custom requires --population, --internet-pct, "
                         "--participation-pct, --avg-spend")
        inputs = JurisdictionInputs(
            code="CUSTOM", name="Custom Market",
            population_millions=args.population,
            internet_penetration_pct=args.internet_pct,
            smartphone_penetration_pct=args.internet_pct * 0.9,
            gambling_participation_pct=args.participation_pct,
            avg_annual_spend_usd=args.avg_spend,
            regulatory_addressability_pct=args.reg_addressability,
            product_coverage_pct=args.product_coverage,
            payment_reach_pct=args.payment_reach,
            ggr_tax_pct=args.ggr_tax,
            corporate_tax_pct=25,
            num_licensed_operators=args.competitors,
            top_3_share_pct=50,
            market_maturity="growing",
        )
        result = calc.calculate(inputs)
        _output(result, args.format)
        return

    if args.compare:
        codes = [c.strip() for c in args.compare.split(",")]
        result = calc.compare_jurisdictions(codes)
        print(json.dumps(result, indent=2))
        return

    if args.jurisdiction:
        inputs = JURISDICTIONS.get(args.jurisdiction.upper())
        if not inputs:
            print(f"Unknown jurisdiction: {args.jurisdiction}. "
                  f"Available: {', '.join(JURISDICTIONS.keys())}")
            return
        result = calc.calculate(inputs)
        _output(result, args.format)
        return

    # Default: all jurisdictions
    results = []
    for code, inputs in JURISDICTIONS.items():
        r = calc.calculate(inputs)
        results.append(r)

    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        print(f"=== TAM/SAM/SOM Analysis ({args.scenario} scenario) ===\n")
        print(f"{'Jurisdiction':<25} {'TAM':>18} {'SAM':>18} {'SOM':>18} "
              f"{'Share%':>8} {'Net Rev Y5':>18}")
        print("-" * 115)
        for r in sorted(results, key=lambda x: x["som"]["value_usd"],
                        reverse=True):
            print(f"{r['jurisdiction']:<25} "
                  f"${r['tam']['value_usd']:>16,.0f} "
                  f"${r['sam']['value_usd']:>16,.0f} "
                  f"${r['som']['value_usd']:>16,.0f} "
                  f"{r['som']['market_share_pct']:>7.2f}% "
                  f"${r['five_year_projection'][4]['ggr_after_tax_usd']:>16,.0f}")


def _output(result: dict, fmt: str):
    if fmt == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"=== TAM/SAM/SOM: {result['jurisdiction']} ({result['scenario']}) ===\n")

        for level in ["tam", "sam", "som"]:
            d = result[level]
            print(f"{level.upper()}: ${d['value_usd']:,.0f}")
            print(f"  {d['description']}")
            if "addressable_population" in d:
                print(f"  Addressable pop: {d['addressable_population']:,.0f}")
                print(f"  Gambling pop: {d['gambling_population']:,.0f}")
            if "sam_factor" in d:
                print(f"  SAM factor: {d['sam_factor']:.4f} "
                      f"({d['pct_of_tam']:.1f}% of TAM)")
            if "market_share_pct" in d:
                print(f"  Market share: {d['market_share_pct']:.3f}% "
                      f"({d['pct_of_sam']:.1f}% of SAM)")
            print()

        rev = result["revenue_analysis"]
        print("--- Revenue Analysis ---")
        print(f"  GGR: ${rev['gross_gaming_revenue']:,.0f}")
        print(f"  GGR tax: ${rev['ggr_tax']:,.0f}")
        print(f"  Net after GGR tax: ${rev['net_after_ggr_tax']:,.0f}")
        print(f"  Corporate tax: ${rev['corporate_tax']:,.0f}")
        print(f"  Net after all tax: ${rev['net_after_all_tax']:,.0f}")
        print(f"  Effective tax rate: {rev['effective_tax_rate_pct']:.1f}%\n")

        print("--- 5-Year Projection ---")
        print(f"{'Year':<6} {'Ramp':<8} {'SOM Revenue':>18} {'After Tax':>18}")
        print("-" * 55)
        for p in result["five_year_projection"]:
            print(f"Y{p['year']:<5} {p['ramp_pct']:<7}% "
                  f"${p['som_usd']:>16,.0f} ${p['ggr_after_tax_usd']:>16,.0f}")


if __name__ == "__main__":
    main()
