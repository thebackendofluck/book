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
iGaming Market Analyzer - Competitive Analysis and TAM Sizing per Jurisdiction

Provides Total Addressable Market (TAM), Serviceable Available Market (SAM),
and Serviceable Obtainable Market (SOM) estimates by jurisdiction. Includes
competitive landscape mapping, market growth projections, and entry feasibility
scoring.

Usage:
    python market_analyzer.py --jurisdiction UK,Malta,Ontario
    python market_analyzer.py --region europe --format json
    python market_analyzer.py --all --export report.csv
"""

import argparse
import json
import csv
import sys
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime, timezone
from enum import Enum


class MarketMaturity(Enum):
    EMERGING = "emerging"
    GROWING = "growing"
    MATURE = "mature"
    SATURATED = "saturated"


class EntryDifficulty(Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    VERY_HIGH = "very_high"


@dataclass
class Competitor:
    name: str
    market_share_pct: float
    verticals: list  # casino, sports, poker, bingo, lottery
    strengths: list
    estimated_revenue_eur_m: float


@dataclass
class JurisdictionMarket:
    code: str
    name: str
    region: str
    population_m: float
    internet_penetration_pct: float
    gambling_participation_pct: float
    online_share_pct: float
    gross_gaming_revenue_eur_b: float
    online_ggr_eur_b: float
    yoy_growth_pct: float
    projected_cagr_5yr_pct: float
    maturity: MarketMaturity
    entry_difficulty: EntryDifficulty
    license_cost_eur: float
    license_timeline_months: int
    tax_rate_pct: float
    tax_model: str  # GGR, turnover, point_of_consumption
    top_competitors: list = field(default_factory=list)
    key_regulations: list = field(default_factory=list)
    payment_preferences: list = field(default_factory=list)
    language_requirements: list = field(default_factory=list)
    restricted_products: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Reference data: Jurisdiction market profiles (2024-2025 estimates)
# ---------------------------------------------------------------------------
JURISDICTIONS = {
    # --- Europe ---
    "UK": JurisdictionMarket(
        code="UK", name="United Kingdom", region="europe",
        population_m=67.7, internet_penetration_pct=96.0,
        gambling_participation_pct=44.0, online_share_pct=62.0,
        gross_gaming_revenue_eur_b=16.2, online_ggr_eur_b=7.8,
        yoy_growth_pct=2.1, projected_cagr_5yr_pct=3.5,
        maturity=MarketMaturity.SATURATED,
        entry_difficulty=EntryDifficulty.VERY_HIGH,
        license_cost_eur=150_000, license_timeline_months=6,
        tax_rate_pct=21.0, tax_model="point_of_consumption",
        top_competitors=[
            Competitor("Flutter/Paddy Power", 22.0, ["casino", "sports", "poker"], ["brand", "scale"], 2100),
            Competitor("Entain/Ladbrokes", 14.0, ["casino", "sports", "bingo"], ["retail presence"], 1500),
            Competitor("bet365", 12.0, ["sports", "casino"], ["tech", "UX"], 1200),
            Competitor("888/William Hill", 9.0, ["casino", "sports", "poker"], ["brand"], 850),
            Competitor("Betfred", 4.5, ["sports", "casino"], ["retail"], 380),
        ],
        key_regulations=["UKGC remote license", "AML 5th Directive", "Gambling Act 2005",
                         "Affordability checks", "Stake limits pending"],
        payment_preferences=["debit_card", "paypal", "apple_pay", "bank_transfer"],
        language_requirements=["en"],
        restricted_products=["credit_cards_banned", "vip_restrictions"],
    ),
    "MT": JurisdictionMarket(
        code="MT", name="Malta (MGA)", region="europe",
        population_m=0.5, internet_penetration_pct=90.0,
        gambling_participation_pct=8.0, online_share_pct=95.0,
        gross_gaming_revenue_eur_b=2.8, online_ggr_eur_b=2.7,
        yoy_growth_pct=8.0, projected_cagr_5yr_pct=6.0,
        maturity=MarketMaturity.MATURE,
        entry_difficulty=EntryDifficulty.MODERATE,
        license_cost_eur=25_000, license_timeline_months=4,
        tax_rate_pct=5.0, tax_model="GGR",
        top_competitors=[
            Competitor("Tipico", 5.0, ["sports", "casino"], ["DACH market"], 350),
            Competitor("LeoVegas/MGM", 4.0, ["casino", "sports"], ["mobile UX"], 280),
            Competitor("Betsson", 3.5, ["casino", "sports"], ["multi-brand"], 260),
        ],
        key_regulations=["MGA B2C/B2B license", "Player Protection Directive",
                         "AML framework", "GDPR compliance"],
        payment_preferences=["visa", "mastercard", "skrill", "neteller", "trustly"],
        language_requirements=["en", "mt"],
        restricted_products=[],
    ),
    "DE": JurisdictionMarket(
        code="DE", name="Germany", region="europe",
        population_m=84.4, internet_penetration_pct=93.0,
        gambling_participation_pct=32.0, online_share_pct=35.0,
        gross_gaming_revenue_eur_b=14.8, online_ggr_eur_b=4.2,
        yoy_growth_pct=12.0, projected_cagr_5yr_pct=9.0,
        maturity=MarketMaturity.GROWING,
        entry_difficulty=EntryDifficulty.HIGH,
        license_cost_eur=200_000, license_timeline_months=12,
        tax_rate_pct=5.3, tax_model="turnover",
        top_competitors=[
            Competitor("Tipico", 18.0, ["sports", "casino"], ["brand awareness"], 600),
            Competitor("bwin", 10.0, ["sports", "casino"], ["legacy player base"], 380),
            Competitor("bet365", 8.0, ["sports", "casino"], ["product quality"], 300),
        ],
        key_regulations=["GlüStV 2021", "EUR 1 slot stake limit", "5-sec spin timer",
                         "Monthly EUR 1000 deposit limit", "No table games online"],
        payment_preferences=["giropay", "sofort", "paypal", "bank_transfer", "visa"],
        language_requirements=["de"],
        restricted_products=["table_games_online_banned", "slot_stake_limit_1eur",
                             "no_autoplay", "no_turbo_spin"],
    ),
    "SE": JurisdictionMarket(
        code="SE", name="Sweden", region="europe",
        population_m=10.5, internet_penetration_pct=97.0,
        gambling_participation_pct=55.0, online_share_pct=55.0,
        gross_gaming_revenue_eur_b=2.6, online_ggr_eur_b=1.8,
        yoy_growth_pct=3.0, projected_cagr_5yr_pct=4.0,
        maturity=MarketMaturity.MATURE,
        entry_difficulty=EntryDifficulty.HIGH,
        license_cost_eur=50_000, license_timeline_months=6,
        tax_rate_pct=18.0, tax_model="GGR",
        top_competitors=[
            Competitor("Svenska Spel", 25.0, ["sports", "casino", "lottery"], ["monopoly legacy"], 450),
            Competitor("Kindred/Unibet", 12.0, ["sports", "casino"], ["local brand"], 220),
            Competitor("Betsson", 10.0, ["casino", "sports"], ["Swedish heritage"], 180),
        ],
        key_regulations=["Swedish Gambling Act 2019", "Bonus restriction (first deposit only)",
                         "Temporary deposit limit SEK 5000/week", "3-hour mandatory breaks"],
        payment_preferences=["swish", "trustly", "bank_transfer", "visa"],
        language_requirements=["sv"],
        restricted_products=["bonus_after_first_deposit_banned"],
    ),
    "ES": JurisdictionMarket(
        code="ES", name="Spain", region="europe",
        population_m=47.8, internet_penetration_pct=93.0,
        gambling_participation_pct=28.0, online_share_pct=40.0,
        gross_gaming_revenue_eur_b=10.5, online_ggr_eur_b=1.2,
        yoy_growth_pct=7.0, projected_cagr_5yr_pct=5.5,
        maturity=MarketMaturity.GROWING,
        entry_difficulty=EntryDifficulty.HIGH,
        license_cost_eur=100_000, license_timeline_months=9,
        tax_rate_pct=20.0, tax_model="GGR",
        top_competitors=[
            Competitor("Codere", 15.0, ["sports", "casino"], ["retail presence"], 180),
            Competitor("bet365", 12.0, ["sports", "casino"], ["product"], 145),
            Competitor("888/William Hill", 8.0, ["casino", "sports"], ["brand"], 95),
        ],
        key_regulations=["Royal Decree 958/2020", "Advertising ban (1am-5am only)",
                         "No welcome bonus after first", "DGOJ oversight"],
        payment_preferences=["visa", "mastercard", "paypal", "bank_transfer", "bizum"],
        language_requirements=["es"],
        restricted_products=["advertising_restrictions", "bonus_restrictions"],
    ),
    "IT": JurisdictionMarket(
        code="IT", name="Italy", region="europe",
        population_m=58.9, internet_penetration_pct=87.0,
        gambling_participation_pct=35.0, online_share_pct=30.0,
        gross_gaming_revenue_eur_b=22.0, online_ggr_eur_b=3.5,
        yoy_growth_pct=9.0, projected_cagr_5yr_pct=7.0,
        maturity=MarketMaturity.GROWING,
        entry_difficulty=EntryDifficulty.HIGH,
        license_cost_eur=350_000, license_timeline_months=12,
        tax_rate_pct=25.0, tax_model="GGR",
        top_competitors=[
            Competitor("Sisal", 14.0, ["sports", "casino", "lottery"], ["retail"], 490),
            Competitor("Snaitech", 10.0, ["sports", "casino"], ["retail", "brand"], 350),
            Competitor("Lottomatica", 9.0, ["lottery", "casino"], ["monopoly heritage"], 315),
        ],
        key_regulations=["ADM license", "Dignity Decree (advertising ban)",
                         "Fiscal code verification", "Server location in Italy"],
        payment_preferences=["postepay", "visa", "bank_transfer", "paypal"],
        language_requirements=["it"],
        restricted_products=["full_advertising_ban", "server_must_be_in_italy"],
    ),
    # --- North America ---
    "ON": JurisdictionMarket(
        code="ON", name="Ontario (Canada)", region="north_america",
        population_m=15.0, internet_penetration_pct=95.0,
        gambling_participation_pct=40.0, online_share_pct=45.0,
        gross_gaming_revenue_eur_b=5.5, online_ggr_eur_b=2.0,
        yoy_growth_pct=35.0, projected_cagr_5yr_pct=15.0,
        maturity=MarketMaturity.GROWING,
        entry_difficulty=EntryDifficulty.MODERATE,
        license_cost_eur=100_000, license_timeline_months=6,
        tax_rate_pct=20.0, tax_model="GGR",
        top_competitors=[
            Competitor("bet365", 15.0, ["sports", "casino"], ["brand", "UX"], 300),
            Competitor("BetMGM", 10.0, ["sports", "casino"], ["US synergy"], 200),
            Competitor("FanDuel", 9.0, ["sports", "casino"], ["DFS heritage"], 180),
            Competitor("PointsBet", 4.0, ["sports"], ["unique betting model"], 80),
        ],
        key_regulations=["iGO/AGCO registration", "Responsible gambling standards",
                         "Data localization", "KYC at registration"],
        payment_preferences=["interac", "visa", "mastercard", "apple_pay"],
        language_requirements=["en", "fr"],
        restricted_products=[],
    ),
    "NJ": JurisdictionMarket(
        code="NJ", name="New Jersey (US)", region="north_america",
        population_m=9.3, internet_penetration_pct=93.0,
        gambling_participation_pct=38.0, online_share_pct=50.0,
        gross_gaming_revenue_eur_b=5.0, online_ggr_eur_b=1.9,
        yoy_growth_pct=18.0, projected_cagr_5yr_pct=10.0,
        maturity=MarketMaturity.MATURE,
        entry_difficulty=EntryDifficulty.VERY_HIGH,
        license_cost_eur=400_000, license_timeline_months=18,
        tax_rate_pct=17.5, tax_model="GGR",
        top_competitors=[
            Competitor("FanDuel", 28.0, ["sports", "casino"], ["DFS conversion", "brand"], 530),
            Competitor("DraftKings", 25.0, ["sports", "casino"], ["DFS conversion", "tech"], 475),
            Competitor("BetMGM", 15.0, ["casino", "sports"], ["Borgata", "MGM brand"], 285),
            Competitor("Caesars", 8.0, ["sports", "casino"], ["retail loyalty"], 152),
        ],
        key_regulations=["DGE license", "Must partner with Atlantic City casino",
                         "Geolocation mandatory", "PASPA post-2018"],
        payment_preferences=["visa", "mastercard", "paypal", "ach", "play+"],
        language_requirements=["en"],
        restricted_products=["must_partner_with_land_casino"],
    ),
    # --- Latin America ---
    "BR": JurisdictionMarket(
        code="BR", name="Brazil", region="latin_america",
        population_m=215.0, internet_penetration_pct=81.0,
        gambling_participation_pct=22.0, online_share_pct=85.0,
        gross_gaming_revenue_eur_b=8.0, online_ggr_eur_b=5.5,
        yoy_growth_pct=45.0, projected_cagr_5yr_pct=20.0,
        maturity=MarketMaturity.EMERGING,
        entry_difficulty=EntryDifficulty.MODERATE,
        license_cost_eur=5_500_000, license_timeline_months=8,
        tax_rate_pct=12.0, tax_model="GGR",
        top_competitors=[
            Competitor("Betano", 18.0, ["sports", "casino"], ["brand", "sponsorships"], 990),
            Competitor("Bet365", 15.0, ["sports", "casino"], ["product quality"], 825),
            Competitor("Sportingbet", 8.0, ["sports", "casino"], ["legacy brand"], 440),
            Competitor("Pixbet", 6.0, ["sports"], ["PIX native", "local"], 330),
        ],
        key_regulations=["Lei 14.790/2023 (regulatory framework)", "SIGAP system",
                         "BRL 30M license fee", "CPF verification", "Local entity required"],
        payment_preferences=["pix", "boleto", "visa", "mastercard"],
        language_requirements=["pt-br"],
        restricted_products=["local_entity_required"],
    ),
    # --- Asia-Pacific ---
    "PH": JurisdictionMarket(
        code="PH", name="Philippines (PAGCOR)", region="asia_pacific",
        population_m=115.0, internet_penetration_pct=68.0,
        gambling_participation_pct=15.0, online_share_pct=70.0,
        gross_gaming_revenue_eur_b=5.0, online_ggr_eur_b=2.2,
        yoy_growth_pct=12.0, projected_cagr_5yr_pct=8.0,
        maturity=MarketMaturity.GROWING,
        entry_difficulty=EntryDifficulty.MODERATE,
        license_cost_eur=80_000, license_timeline_months=4,
        tax_rate_pct=5.0, tax_model="GGR",
        top_competitors=[
            Competitor("PAGCOR e-Games", 30.0, ["casino", "sports"], ["monopoly"], 660),
            Competitor("PhilWeb", 10.0, ["casino"], ["e-games network"], 220),
        ],
        key_regulations=["PAGCOR offshore license", "POGO regulations",
                         "AML Act compliance", "Cannot serve PH residents (offshore)"],
        payment_preferences=["gcash", "paymaya", "bank_transfer", "crypto"],
        language_requirements=["en", "tl"],
        restricted_products=["offshore_cannot_serve_ph_residents"],
    ),
    "AU": JurisdictionMarket(
        code="AU", name="Australia", region="asia_pacific",
        population_m=26.5, internet_penetration_pct=96.0,
        gambling_participation_pct=64.0, online_share_pct=25.0,
        gross_gaming_revenue_eur_b=18.0, online_ggr_eur_b=4.5,
        yoy_growth_pct=5.0, projected_cagr_5yr_pct=4.0,
        maturity=MarketMaturity.MATURE,
        entry_difficulty=EntryDifficulty.VERY_HIGH,
        license_cost_eur=300_000, license_timeline_months=12,
        tax_rate_pct=15.0, tax_model="point_of_consumption",
        top_competitors=[
            Competitor("Sportsbet/Flutter", 30.0, ["sports"], ["brand", "scale"], 1350),
            Competitor("TAB/Entain", 20.0, ["sports"], ["retail", "racing"], 900),
            Competitor("Ladbrokes AU", 12.0, ["sports"], ["brand"], 540),
        ],
        key_regulations=["Interactive Gambling Act 2001", "Online casino BANNED",
                         "Sports betting only", "BetStop self-exclusion register",
                         "National Consumer Protection Framework"],
        payment_preferences=["visa", "mastercard", "poli", "bank_transfer", "apple_pay"],
        language_requirements=["en"],
        restricted_products=["online_casino_banned", "in_play_betting_limited"],
    ),
}

REGIONS = {
    "europe": ["UK", "MT", "DE", "SE", "ES", "IT"],
    "north_america": ["ON", "NJ"],
    "latin_america": ["BR"],
    "asia_pacific": ["PH", "AU"],
}


class MarketAnalyzer:
    """Performs competitive analysis and market sizing for iGaming jurisdictions."""

    def __init__(self, jurisdictions: list[str] | None = None, region: str | None = None):
        if region and region in REGIONS:
            self.targets = [JURISDICTIONS[c] for c in REGIONS[region] if c in JURISDICTIONS]
        elif jurisdictions:
            self.targets = [JURISDICTIONS[c] for c in jurisdictions if c in JURISDICTIONS]
        else:
            self.targets = list(JURISDICTIONS.values())

    # ----- TAM / SAM / SOM Estimation -----

    def estimate_market_sizes(self, target_market_share_pct: float = 2.0) -> list[dict]:
        """Calculate TAM, SAM, SOM for each jurisdiction."""
        results = []
        for j in self.targets:
            tam = j.online_ggr_eur_b * 1_000  # convert to EUR millions
            sam = tam * (1 - self._concentration_discount(j))
            som = sam * (target_market_share_pct / 100)

            # 5-year projection
            tam_5yr = tam * ((1 + j.projected_cagr_5yr_pct / 100) ** 5)
            som_5yr = tam_5yr * (1 - self._concentration_discount(j)) * (target_market_share_pct / 100)

            results.append({
                "jurisdiction": j.code,
                "name": j.name,
                "region": j.region,
                "tam_eur_m": round(tam, 1),
                "sam_eur_m": round(sam, 1),
                "som_eur_m": round(som, 1),
                "tam_5yr_eur_m": round(tam_5yr, 1),
                "som_5yr_eur_m": round(som_5yr, 1),
                "cagr_pct": j.projected_cagr_5yr_pct,
                "maturity": j.maturity.value,
            })
        return results

    def _concentration_discount(self, j: JurisdictionMarket) -> float:
        """Discount factor based on market concentration (0 = open, 0.6 = monopolistic)."""
        if not j.top_competitors:
            return 0.2
        top3_share = sum(c.market_share_pct for c in sorted(
            j.top_competitors, key=lambda c: c.market_share_pct, reverse=True
        )[:3])
        if top3_share > 60:
            return 0.5
        elif top3_share > 40:
            return 0.35
        elif top3_share > 25:
            return 0.2
        return 0.1

    # ----- Competitive Landscape -----

    def competitive_landscape(self) -> list[dict]:
        """Map competitive landscape per jurisdiction."""
        results = []
        for j in self.targets:
            hhi = sum(c.market_share_pct ** 2 for c in j.top_competitors) if j.top_competitors else 0
            concentration = "low" if hhi < 1000 else "moderate" if hhi < 1800 else "high"

            results.append({
                "jurisdiction": j.code,
                "name": j.name,
                "num_major_competitors": len(j.top_competitors),
                "hhi_index": round(hhi, 0),
                "concentration": concentration,
                "top3_combined_share_pct": round(
                    sum(c.market_share_pct for c in sorted(
                        j.top_competitors, key=lambda c: c.market_share_pct, reverse=True
                    )[:3]), 1
                ),
                "competitors": [
                    {
                        "name": c.name,
                        "share_pct": c.market_share_pct,
                        "verticals": c.verticals,
                        "strengths": c.strengths,
                        "est_revenue_eur_m": c.estimated_revenue_eur_m,
                    }
                    for c in j.top_competitors
                ],
            })
        return results

    # ----- Entry Feasibility Score -----

    def entry_feasibility(self) -> list[dict]:
        """Score each market for entry feasibility (0-100)."""
        results = []
        for j in self.targets:
            scores = {
                "market_size": self._score_market_size(j),
                "growth": self._score_growth(j),
                "competition": self._score_competition(j),
                "regulatory_ease": self._score_regulatory(j),
                "tax_efficiency": self._score_tax(j),
            }
            weights = {"market_size": 0.25, "growth": 0.25, "competition": 0.20,
                       "regulatory_ease": 0.15, "tax_efficiency": 0.15}
            total = sum(scores[k] * weights[k] for k in scores)

            results.append({
                "jurisdiction": j.code,
                "name": j.name,
                "overall_score": round(total, 1),
                "component_scores": {k: round(v, 1) for k, v in scores.items()},
                "recommendation": self._recommendation(total),
                "entry_difficulty": j.entry_difficulty.value,
                "license_cost_eur": j.license_cost_eur,
                "license_timeline_months": j.license_timeline_months,
                "restricted_products": j.restricted_products,
            })
        results.sort(key=lambda x: x["overall_score"], reverse=True)
        return results

    def _score_market_size(self, j: JurisdictionMarket) -> float:
        tam = j.online_ggr_eur_b * 1000
        if tam > 5000:
            return 90
        elif tam > 2000:
            return 75
        elif tam > 1000:
            return 60
        elif tam > 500:
            return 45
        return 30

    def _score_growth(self, j: JurisdictionMarket) -> float:
        cagr = j.projected_cagr_5yr_pct
        if cagr > 15:
            return 95
        elif cagr > 10:
            return 80
        elif cagr > 5:
            return 60
        elif cagr > 2:
            return 40
        return 25

    def _score_competition(self, j: JurisdictionMarket) -> float:
        if not j.top_competitors:
            return 70
        top3 = sum(c.market_share_pct for c in sorted(
            j.top_competitors, key=lambda c: c.market_share_pct, reverse=True
        )[:3])
        if top3 < 30:
            return 85
        elif top3 < 45:
            return 65
        elif top3 < 60:
            return 45
        return 25

    def _score_regulatory(self, j: JurisdictionMarket) -> float:
        mapping = {
            EntryDifficulty.LOW: 90,
            EntryDifficulty.MODERATE: 70,
            EntryDifficulty.HIGH: 45,
            EntryDifficulty.VERY_HIGH: 20,
        }
        return mapping.get(j.entry_difficulty, 50)

    def _score_tax(self, j: JurisdictionMarket) -> float:
        if j.tax_model == "turnover":
            effective = j.tax_rate_pct * 3  # turnover tax is much more punitive
        else:
            effective = j.tax_rate_pct
        if effective < 10:
            return 90
        elif effective < 20:
            return 70
        elif effective < 30:
            return 50
        return 30

    def _recommendation(self, score: float) -> str:
        if score >= 75:
            return "STRONG_ENTRY - High priority market"
        elif score >= 60:
            return "CONSIDER - Good potential with manageable barriers"
        elif score >= 45:
            return "CAUTIOUS - Significant challenges, needs strong differentiation"
        return "AVOID - High barriers, limited opportunity for new entrants"

    # ----- Report Generation -----

    def full_report(self) -> dict:
        """Generate comprehensive market analysis report."""
        return {
            "report_date": datetime.now(timezone.utc).isoformat(),
            "jurisdictions_analyzed": len(self.targets),
            "market_sizes": self.estimate_market_sizes(),
            "competitive_landscape": self.competitive_landscape(),
            "entry_feasibility": self.entry_feasibility(),
            "summary": self._executive_summary(),
        }

    def _executive_summary(self) -> dict:
        feasibility = self.entry_feasibility()
        market_sizes = self.estimate_market_sizes()
        total_tam = sum(m["tam_eur_m"] for m in market_sizes)

        return {
            "total_tam_eur_m": round(total_tam, 1),
            "top_opportunities": [
                {"jurisdiction": f["jurisdiction"], "name": f["name"], "score": f["overall_score"]}
                for f in feasibility[:3]
            ],
            "avoid_list": [
                {"jurisdiction": f["jurisdiction"], "name": f["name"], "score": f["overall_score"]}
                for f in feasibility if f["overall_score"] < 45
            ],
        }

    # ----- Output Formatting -----

    def print_table(self, data: list[dict], title: str):
        """Print formatted table to stdout."""
        if not data:
            print(f"\n  No data for {title}")
            return
        print(f"\n{'=' * 80}")
        print(f"  {title}")
        print(f"{'=' * 80}")

        keys = list(data[0].keys())
        # Filter out nested structures for table display
        simple_keys = [k for k in keys if not isinstance(data[0][k], (list, dict))]
        widths = {k: max(len(str(k)), max(len(str(r.get(k, ""))) for r in data)) for k in simple_keys}

        header = " | ".join(f"{k:>{widths[k]}}" for k in simple_keys)
        print(f"  {header}")
        print(f"  {'-' * len(header)}")
        for row in data:
            line = " | ".join(f"{str(row.get(k, '')):>{widths[k]}}" for k in simple_keys)
            print(f"  {line}")

    def export_csv(self, filepath: str):
        """Export full report data to CSV."""
        report = self.full_report()
        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)
            # Market sizes
            sizes = report["market_sizes"]
            if sizes:
                writer.writerow(["=== MARKET SIZES ==="])
                writer.writerow(sizes[0].keys())
                for row in sizes:
                    writer.writerow(row.values())
                writer.writerow([])

            # Feasibility
            feas = report["entry_feasibility"]
            if feas:
                writer.writerow(["=== ENTRY FEASIBILITY ==="])
                simple_keys = [k for k in feas[0] if not isinstance(feas[0][k], (list, dict))]
                writer.writerow(simple_keys)
                for row in feas:
                    writer.writerow([row[k] for k in simple_keys])

        print(f"  Report exported to {filepath}")


def main():
    parser = argparse.ArgumentParser(
        description="iGaming Market Analyzer - TAM Sizing & Competitive Analysis"
    )
    parser.add_argument("--jurisdiction", "-j", type=str,
                        help="Comma-separated jurisdiction codes (UK,MT,DE,SE,ES,IT,ON,NJ,BR,PH,AU)")
    parser.add_argument("--region", "-r", type=str, choices=list(REGIONS.keys()),
                        help="Analyze entire region")
    parser.add_argument("--all", action="store_true", help="Analyze all jurisdictions")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    parser.add_argument("--export", type=str, help="Export to CSV file")
    parser.add_argument("--target-share", type=float, default=2.0,
                        help="Target market share %% for SOM calculation (default: 2.0)")
    args = parser.parse_args()

    jurisdictions = None
    if args.jurisdiction:
        jurisdictions = [c.strip().upper() for c in args.jurisdiction.split(",")]
        invalid = [c for c in jurisdictions if c not in JURISDICTIONS]
        if invalid:
            print(f"Unknown jurisdiction(s): {', '.join(invalid)}")
            print(f"Available: {', '.join(JURISDICTIONS.keys())}")
            sys.exit(1)

    analyzer = MarketAnalyzer(
        jurisdictions=jurisdictions,
        region=args.region if not args.jurisdiction else None,
    )

    if args.export:
        analyzer.export_csv(args.export)
        return

    report = analyzer.full_report()

    if args.format == "json":
        print(json.dumps(report, indent=2, default=str))
    else:
        analyzer.print_table(report["market_sizes"], "MARKET SIZING (EUR Millions)")
        analyzer.print_table(report["entry_feasibility"], "ENTRY FEASIBILITY RANKING")

        summary = report["summary"]
        print(f"\n{'=' * 80}")
        print(f"  EXECUTIVE SUMMARY")
        print(f"{'=' * 80}")
        print(f"  Total TAM across analyzed markets: EUR {summary['total_tam_eur_m']:,.0f}M")
        print(f"\n  Top Opportunities:")
        for opp in summary["top_opportunities"]:
            print(f"    - {opp['name']} ({opp['jurisdiction']}): Score {opp['score']}")
        if summary["avoid_list"]:
            print(f"\n  Markets to Avoid (score < 45):")
            for avoid in summary["avoid_list"]:
                print(f"    - {avoid['name']} ({avoid['jurisdiction']}): Score {avoid['score']}")


if __name__ == "__main__":
    main()
