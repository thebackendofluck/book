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
Competitive Landscape Mapping Tool for iGaming Markets
========================================================

Maps the competitive landscape in each jurisdiction including:
  - Market share estimation per operator
  - Estimated player counts and GGR per operator
  - Competitive positioning (product strength, brand, pricing)
  - Market entry difficulty assessment
  - White space analysis (underserved segments)

Usage:
    python competitive_mapper.py --jurisdiction UK
    python competitive_mapper.py --jurisdiction BR --format json
    python competitive_mapper.py --all --top 5
    python competitive_mapper.py --whitespace UK
    python competitive_mapper.py --operator "bet365" --global
"""

import argparse
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class Operator:
    """Profile of a licensed gambling operator."""
    name: str
    parent_company: str
    headquarters: str
    publicly_listed: bool
    ticker: str = ""
    global_revenue_usd: float = 0
    founded_year: int = 0
    employee_count: int = 0
    technology: str = ""  # "proprietary", "third_party", "hybrid"

    # Product strengths (1-10)
    sports_betting_strength: int = 5
    casino_strength: int = 5
    live_casino_strength: int = 5
    poker_strength: int = 5

    # Brand and marketing
    brand_recognition: int = 5  # 1-10
    marketing_spend_tier: str = "medium"  # low, medium, high, very_high
    sponsorship_deals: list = field(default_factory=list)

    # Unique advantages
    unique_features: list = field(default_factory=list)
    weaknesses: list = field(default_factory=list)


@dataclass
class MarketPresence:
    """An operator's presence in a specific jurisdiction."""
    operator_name: str
    jurisdiction_code: str
    market_share_pct: float
    estimated_ggr_usd: float
    estimated_players: int
    ggr_per_player_usd: float
    years_in_market: int
    license_type: str
    product_focus: list = field(default_factory=list)  # ["sports", "casino", ...]
    competitive_position: str = ""  # "leader", "challenger", "niche", "newcomer"
    growth_trend: str = ""  # "growing", "stable", "declining"
    notes: str = ""


# ---------------------------------------------------------------------------
# Global operator database
# ---------------------------------------------------------------------------

OPERATORS = {
    "flutter": Operator(
        name="Flutter Entertainment",
        parent_company="Flutter Entertainment plc",
        headquarters="Dublin, Ireland",
        publicly_listed=True, ticker="FLUT (NYSE/LSE)",
        global_revenue_usd=13500000000, founded_year=2019,
        employee_count=20000, technology="proprietary",
        sports_betting_strength=10, casino_strength=8,
        live_casino_strength=7, poker_strength=10,
        brand_recognition=9, marketing_spend_tier="very_high",
        sponsorship_deals=["FanDuel (US)", "Paddy Power (UK/IE)",
                           "Betfair Exchange", "PokerStars", "Sportsbet (AU)"],
        unique_features=["Betfair Exchange (unique P2P model)",
                         "FanDuel #1 US sportsbook", "PokerStars #1 poker"],
        weaknesses=["Complex multi-brand structure", "Regulatory scrutiny"],
    ),
    "entain": Operator(
        name="Entain",
        parent_company="Entain plc",
        headquarters="London, UK",
        publicly_listed=True, ticker="ENT (LSE)",
        global_revenue_usd=5200000000, founded_year=2004,
        employee_count=29000, technology="proprietary",
        sports_betting_strength=9, casino_strength=9,
        live_casino_strength=8, poker_strength=6,
        brand_recognition=8, marketing_spend_tier="very_high",
        sponsorship_deals=["BetMGM (US JV with MGM)", "Ladbrokes (UK)",
                           "Coral (UK)", "bwin (EU)", "Sportingbet (BR)"],
        unique_features=["BetMGM US JV", "Strong retail + online hybrid",
                         "Large Brazilian presence via Sportingbet"],
        weaknesses=["BetMGM profitability challenges", "HMRC bribery investigation"],
    ),
    "bet365": Operator(
        name="bet365",
        parent_company="bet365 Group Ltd",
        headquarters="Stoke-on-Trent, UK",
        publicly_listed=False,
        global_revenue_usd=4500000000, founded_year=2000,
        employee_count=7000, technology="proprietary",
        sports_betting_strength=10, casino_strength=8,
        live_casino_strength=9, poker_strength=5,
        brand_recognition=10, marketing_spend_tier="high",
        sponsorship_deals=["Stoke City FC", "Various football clubs globally"],
        unique_features=["Best-in-class live streaming", "Cash Out pioneer",
                         "Superior in-play product", "Global reach (200+ countries)"],
        weaknesses=["Private company (limited transparency)",
                    "Concentrated ownership"],
    ),
    "draftkings": Operator(
        name="DraftKings",
        parent_company="DraftKings Inc.",
        headquarters="Boston, USA",
        publicly_listed=True, ticker="DKNG (NASDAQ)",
        global_revenue_usd=3600000000, founded_year=2012,
        employee_count=5000, technology="proprietary",
        sports_betting_strength=9, casino_strength=7,
        live_casino_strength=5, poker_strength=3,
        brand_recognition=9, marketing_spend_tier="very_high",
        sponsorship_deals=["NFL", "NBA", "MLB", "PGA Tour", "UFC"],
        unique_features=["DFS heritage", "Strong US brand",
                         "Proprietary tech stack (post-SBTech acquisition)"],
        weaknesses=["US-only focus", "Profitability pressures",
                    "No international diversification"],
    ),
    "888": Operator(
        name="888 / Evoke",
        parent_company="Evoke plc (formerly 888 Holdings)",
        headquarters="London, UK",
        publicly_listed=True, ticker="EVOK (LSE)",
        global_revenue_usd=1900000000, founded_year=1997,
        employee_count=12000, technology="proprietary",
        sports_betting_strength=7, casino_strength=8,
        live_casino_strength=7, poker_strength=7,
        brand_recognition=7, marketing_spend_tier="medium",
        sponsorship_deals=["William Hill (acquired)", "SI Sportsbook (US)"],
        unique_features=["William Hill retail estate", "Long operating history",
                         "888poker established brand"],
        weaknesses=["Integration challenges post-William Hill acquisition",
                    "US market underperforming"],
    ),
    "betano": Operator(
        name="Betano / Kaizen Gaming",
        parent_company="Kaizen Gaming",
        headquarters="Athens, Greece",
        publicly_listed=False,
        global_revenue_usd=2500000000, founded_year=2012,
        employee_count=3000, technology="proprietary",
        sports_betting_strength=8, casino_strength=8,
        live_casino_strength=7, poker_strength=3,
        brand_recognition=7, marketing_spend_tier="high",
        sponsorship_deals=["UEFA Euro 2024", "Copa America 2024",
                           "Various football clubs (BR, PT, DE)"],
        unique_features=["Fast-growing in Latin America and Europe",
                         "Strong localization per market",
                         "Cash Out and Bet Builder features"],
        weaknesses=["Private (limited financial transparency)",
                    "Concentrated in fewer markets"],
    ),
    "bet9ja": Operator(
        name="Bet9ja",
        parent_company="KC Gaming Networks Ltd",
        headquarters="Lagos, Nigeria",
        publicly_listed=False,
        global_revenue_usd=400000000, founded_year=2013,
        employee_count=2000, technology="hybrid",
        sports_betting_strength=8, casino_strength=5,
        live_casino_strength=3, poker_strength=1,
        brand_recognition=9, marketing_spend_tier="high",
        sponsorship_deals=["Nigerian Premier League", "Various Nigerian events"],
        unique_features=["Dominant Nigerian brand", "Extensive agent network",
                         "USSD betting for feature phones"],
        weaknesses=["Single market exposure", "Technology lags global leaders"],
    ),
    "sportybet": Operator(
        name="SportyBet",
        parent_company="Sporty Group",
        headquarters="London, UK (operations in Africa)",
        publicly_listed=False,
        global_revenue_usd=600000000, founded_year=2018,
        employee_count=1500, technology="proprietary",
        sports_betting_strength=8, casino_strength=4,
        live_casino_strength=2, poker_strength=1,
        brand_recognition=7, marketing_spend_tier="high",
        sponsorship_deals=["Various African football sponsorships"],
        unique_features=["Mobile-first design", "Africa-focused",
                         "Light app (works on low-bandwidth)"],
        weaknesses=["Limited product range", "Regulatory uncertainty in key markets"],
    ),
}


# ---------------------------------------------------------------------------
# Market presence data (estimated, public sources)
# ---------------------------------------------------------------------------

MARKET_PRESENCES = [
    # United Kingdom
    MarketPresence("bet365", "UK", 18.5, 2072000000, 3800000, 545, 24,
                   "UKGC Remote", ["sports", "casino", "live_casino"],
                   "leader", "stable",
                   "Consistently #1 or #2. Superior in-play product."),
    MarketPresence("Flutter Entertainment", "UK", 14.0, 1568000000, 3200000, 490, 20,
                   "UKGC Remote", ["sports", "casino", "poker", "exchange"],
                   "leader", "stable",
                   "Paddy Power + Betfair + Sky Bet combined."),
    MarketPresence("Entain", "UK", 11.0, 1232000000, 2800000, 440, 18,
                   "UKGC Remote + Retail", ["sports", "casino", "retail"],
                   "leader", "stable",
                   "Ladbrokes + Coral retail estate + online."),
    MarketPresence("888 / Evoke", "UK", 7.5, 840000000, 1800000, 467, 25,
                   "UKGC Remote + Retail", ["sports", "casino", "poker"],
                   "challenger", "declining",
                   "William Hill acquisition integration ongoing."),
    MarketPresence("DraftKings", "UK", 0.5, 56000000, 120000, 467, 1,
                   "UKGC Remote", ["casino"],
                   "newcomer", "growing",
                   "Entered UK via Jackpocket acquisition. Small presence."),

    # Brazil
    MarketPresence("bet365", "BR", 22.0, 1760000000, 9000000, 196, 5,
                   "SPA/MF", ["sports", "casino", "live_casino"],
                   "leader", "growing",
                   "Largest brand in Brazil. Strong TV advertising."),
    MarketPresence("Betano / Kaizen Gaming", "BR", 18.0, 1440000000, 7500000, 192, 4,
                   "SPA/MF", ["sports", "casino"],
                   "leader", "growing",
                   "Massive growth. Copa America + football sponsorships."),
    MarketPresence("Entain", "BR", 12.0, 960000000, 5000000, 192, 8,
                   "SPA/MF", ["sports", "casino"],
                   "challenger", "growing",
                   "Via Sportingbet brand. Well-established."),
    MarketPresence("Flutter Entertainment", "BR", 5.0, 400000000, 2000000, 200, 3,
                   "SPA/MF", ["sports"],
                   "challenger", "growing",
                   "Smaller presence than key markets. Growing."),

    # Nigeria
    MarketPresence("Bet9ja", "NG", 35.0, 385000000, 5000000, 77, 11,
                   "Lagos State", ["sports"],
                   "leader", "stable",
                   "Dominant brand. Extensive agent network across Nigeria."),
    MarketPresence("SportyBet", "NG", 20.0, 220000000, 3200000, 69, 6,
                   "Lagos State", ["sports"],
                   "challenger", "growing",
                   "Fastest growing. Superior mobile app."),
    MarketPresence("bet365", "NG", 5.0, 55000000, 800000, 69, 3,
                   "Lagos State", ["sports"],
                   "niche", "growing",
                   "Global brand but less localized than Bet9ja/Sportybet."),

    # United States
    MarketPresence("Flutter Entertainment", "US", 38.0, 10640000000, 18000000, 591, 6,
                   "Multi-state", ["sports", "casino", "DFS"],
                   "leader", "growing",
                   "FanDuel #1 sportsbook (~47% sports market share)."),
    MarketPresence("DraftKings", "US", 28.0, 7840000000, 14000000, 560, 6,
                   "Multi-state", ["sports", "casino", "DFS"],
                   "leader", "growing",
                   "#2 sportsbook (~28% share). Growing iGaming."),
    MarketPresence("Entain", "US", 11.0, 3080000000, 5500000, 560, 5,
                   "Multi-state", ["sports", "casino"],
                   "challenger", "stable",
                   "BetMGM JV with MGM. #3 position."),
    MarketPresence("bet365", "US", 3.0, 840000000, 1500000, 560, 4,
                   "Multi-state", ["sports"],
                   "niche", "growing",
                   "Limited state presence. Growing slowly."),

    # Sweden
    MarketPresence("Flutter Entertainment", "SE", 15.0, 315000000, 420000, 750, 7,
                   "Spelinspektionen", ["sports", "casino", "poker"],
                   "leader", "stable",
                   "Via Betfair and PokerStars brands."),
    MarketPresence("Entain", "SE", 10.0, 210000000, 300000, 700, 7,
                   "Spelinspektionen", ["sports", "casino"],
                   "challenger", "stable",
                   "Via bwin brand."),
    MarketPresence("bet365", "SE", 8.0, 168000000, 240000, 700, 7,
                   "Spelinspektionen", ["sports", "casino"],
                   "challenger", "stable"),

    # Italy
    MarketPresence("Flutter Entertainment", "IT", 12.0, 696000000, 520000, 1338, 12,
                   "ADM", ["sports", "casino", "poker"],
                   "leader", "stable",
                   "Via Sisal (acquired 2022) + PokerStars."),
    MarketPresence("Entain", "IT", 8.0, 464000000, 380000, 1221, 10,
                   "ADM", ["sports", "casino"],
                   "challenger", "growing",
                   "Via bwin + Eurobet brands."),
    MarketPresence("bet365", "IT", 7.0, 406000000, 350000, 1160, 8,
                   "ADM", ["sports", "casino"],
                   "challenger", "stable"),

    # Australia
    MarketPresence("Flutter Entertainment", "AU", 30.0, 1560000000, 1100000, 1418, 15,
                   "NT Racing Commission", ["sports"],
                   "leader", "stable",
                   "Via Sportsbet. #1 in Australian market."),
    MarketPresence("Entain", "AU", 18.0, 936000000, 700000, 1337, 12,
                   "NT Racing Commission", ["sports"],
                   "challenger", "stable",
                   "Via Ladbrokes + Neds brands."),
    MarketPresence("bet365", "AU", 12.0, 624000000, 500000, 1248, 10,
                   "NT Racing Commission", ["sports"],
                   "challenger", "stable"),
]


# ---------------------------------------------------------------------------
# Competitive analysis engine
# ---------------------------------------------------------------------------

class CompetitiveMapper:
    """Analyze competitive landscape in iGaming markets."""

    def __init__(self):
        self.operators = OPERATORS
        self.presences = {}
        for mp in MARKET_PRESENCES:
            key = mp.jurisdiction_code
            if key not in self.presences:
                self.presences[key] = []
            self.presences[key].append(mp)

    def jurisdiction_landscape(self, code: str) -> dict:
        """Full competitive landscape for a jurisdiction."""
        code = code.upper()
        entries = self.presences.get(code, [])
        if not entries:
            return {"error": f"No competitive data for '{code}'"}

        entries.sort(key=lambda x: x.market_share_pct, reverse=True)
        total_mapped_share = sum(e.market_share_pct for e in entries)
        total_mapped_ggr = sum(e.estimated_ggr_usd for e in entries)

        # Market concentration (HHI approximation)
        hhi = sum(e.market_share_pct ** 2 for e in entries)
        if hhi > 2500:
            concentration = "highly_concentrated"
        elif hhi > 1500:
            concentration = "moderately_concentrated"
        else:
            concentration = "fragmented"

        return {
            "jurisdiction": code,
            "operators_mapped": len(entries),
            "total_mapped_share_pct": round(total_mapped_share, 1),
            "remaining_market_pct": round(100 - total_mapped_share, 1),
            "total_mapped_ggr_usd": round(total_mapped_ggr),
            "hhi_index": round(hhi),
            "concentration": concentration,
            "operators": [
                {
                    "rank": i,
                    "name": e.operator_name,
                    "market_share_pct": e.market_share_pct,
                    "estimated_ggr_usd": e.estimated_ggr_usd,
                    "estimated_players": e.estimated_players,
                    "ggr_per_player_usd": e.ggr_per_player_usd,
                    "years_in_market": e.years_in_market,
                    "position": e.competitive_position,
                    "trend": e.growth_trend,
                    "products": e.product_focus,
                    "notes": e.notes,
                }
                for i, e in enumerate(entries, 1)
            ],
            "white_space": self._identify_whitespace(entries),
            "entry_assessment": self._entry_assessment(entries, hhi),
        }

    def _identify_whitespace(self, entries: list[MarketPresence]) -> list[str]:
        """Identify underserved market segments."""
        gaps = []
        all_products = set()
        for e in entries:
            all_products.update(e.product_focus)

        full_suite = {"sports", "casino", "live_casino", "poker",
                      "bingo", "esports", "virtual_sports"}
        missing = full_suite - all_products
        if missing:
            gaps.append(f"Product gaps: {', '.join(missing)} not well served")

        # Check if any operator focuses on niche
        niche_count = sum(1 for e in entries if e.competitive_position == "niche")
        if niche_count < 2:
            gaps.append("Few niche operators - opportunity for specialized positioning")

        # Check growth trends
        growing = sum(1 for e in entries if e.growth_trend == "growing")
        if growing > len(entries) * 0.6:
            gaps.append("Market expanding - new entrants can capture growth share")

        # Check average years in market
        avg_years = sum(e.years_in_market for e in entries) / len(entries)
        if avg_years > 10:
            gaps.append("Established incumbents - differentiation critical for entry")
        elif avg_years < 5:
            gaps.append("Young market - early mover advantage still available")

        remaining = 100 - sum(e.market_share_pct for e in entries)
        if remaining > 30:
            gaps.append(f"{remaining:.0f}% market share not mapped to top operators - "
                        f"long tail of smaller operators exists")

        return gaps

    def _entry_assessment(self, entries: list[MarketPresence],
                          hhi: float) -> dict:
        """Assess difficulty of market entry."""
        top_share = entries[0].market_share_pct if entries else 0
        num_ops = len(entries)

        if hhi > 2500 and top_share > 30:
            difficulty = "VERY HARD"
            strategy = ("Dominant incumbents control the market. Consider niche "
                        "positioning, unique product differentiation, or strategic "
                        "partnership/acquisition approach.")
        elif hhi > 1500:
            difficulty = "HARD"
            strategy = ("Moderately concentrated market. Requires substantial marketing "
                        "budget and clear differentiator. Consider targeting underserved "
                        "segments or demographics.")
        elif num_ops > 5:
            difficulty = "MODERATE"
            strategy = ("Competitive but accessible market. Focus on product quality, "
                        "localization, and customer experience. Marketing efficiency critical.")
        else:
            difficulty = "MODERATE-EASY"
            strategy = ("Few established operators. Opportunity to capture share with "
                        "competitive product and smart marketing. Speed to market matters.")

        return {
            "difficulty": difficulty,
            "recommended_strategy": strategy,
            "estimated_year1_share_pct": max(0.5, min(5, 100 / (num_ops * 5))),
            "estimated_cac_tier": "high" if hhi > 2000 else "medium" if hhi > 1000 else "low",
        }

    def operator_global_view(self, operator_key: str) -> dict:
        """View an operator's global presence."""
        op = self.operators.get(operator_key)
        if not op:
            # Try matching by name
            for k, v in self.operators.items():
                if operator_key.lower() in v.name.lower():
                    op = v
                    operator_key = k
                    break
        if not op:
            return {"error": f"Operator '{operator_key}' not found"}

        markets = []
        for code, entries in self.presences.items():
            for e in entries:
                if e.operator_name == op.name:
                    markets.append({
                        "jurisdiction": code,
                        "market_share_pct": e.market_share_pct,
                        "estimated_ggr_usd": e.estimated_ggr_usd,
                        "position": e.competitive_position,
                        "trend": e.growth_trend,
                    })

        markets.sort(key=lambda x: x["estimated_ggr_usd"], reverse=True)
        total_mapped_ggr = sum(m["estimated_ggr_usd"] for m in markets)

        return {
            "operator": op.name,
            "parent": op.parent_company,
            "headquarters": op.headquarters,
            "global_revenue_usd": op.global_revenue_usd,
            "publicly_listed": op.publicly_listed,
            "ticker": op.ticker,
            "product_strengths": {
                "sports_betting": op.sports_betting_strength,
                "casino": op.casino_strength,
                "live_casino": op.live_casino_strength,
                "poker": op.poker_strength,
            },
            "brand_recognition": op.brand_recognition,
            "sponsorships": op.sponsorship_deals,
            "unique_features": op.unique_features,
            "weaknesses": op.weaknesses,
            "markets_present": len(markets),
            "total_mapped_ggr_usd": total_mapped_ggr,
            "market_presence": markets,
        }

    def all_markets_summary(self, top_n: int = 5) -> list[dict]:
        """Summary of competitive landscape across all markets."""
        summaries = []
        for code in sorted(self.presences.keys()):
            landscape = self.jurisdiction_landscape(code)
            if "error" in landscape:
                continue
            top_ops = landscape["operators"][:top_n]
            summaries.append({
                "jurisdiction": code,
                "operators_mapped": landscape["operators_mapped"],
                "concentration": landscape["concentration"],
                "hhi": landscape["hhi_index"],
                "entry_difficulty": landscape["entry_assessment"]["difficulty"],
                "top_operators": [
                    {"name": o["name"], "share_pct": o["market_share_pct"]}
                    for o in top_ops
                ],
            })
        return summaries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="iGaming Competitive Landscape Mapper")
    parser.add_argument("--jurisdiction", "-j", type=str)
    parser.add_argument("--all", action="store_true",
                        help="Summary of all mapped markets")
    parser.add_argument("--top", type=int, default=5,
                        help="Top N operators to show per market")
    parser.add_argument("--operator", "-o", type=str,
                        help="View global presence of an operator")
    parser.add_argument("--whitespace", type=str,
                        help="White space analysis for a jurisdiction")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    mapper = CompetitiveMapper()

    if args.operator:
        # Try to find the operator
        key = args.operator.lower().replace(" ", "")
        result = mapper.operator_global_view(key)
        if "error" in result:
            # Try partial match
            result = mapper.operator_global_view(args.operator)
        print(json.dumps(result, indent=2))
        return

    if args.jurisdiction:
        result = mapper.jurisdiction_landscape(args.jurisdiction)
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            _print_landscape(result)
        return

    if args.whitespace:
        result = mapper.jurisdiction_landscape(args.whitespace)
        if "error" not in result:
            print(f"=== White Space Analysis: {args.whitespace.upper()} ===\n")
            for gap in result["white_space"]:
                print(f"  - {gap}")
            print(f"\n--- Entry Assessment ---")
            ea = result["entry_assessment"]
            print(f"  Difficulty: {ea['difficulty']}")
            print(f"  Strategy: {ea['recommended_strategy']}")
            print(f"  Est. Year 1 share: {ea['estimated_year1_share_pct']:.1f}%")
        return

    # Default: all markets
    summaries = mapper.all_markets_summary(top_n=args.top)
    if args.format == "json":
        print(json.dumps(summaries, indent=2))
    else:
        print("=== Competitive Landscape Summary ===\n")
        for s in summaries:
            print(f"--- {s['jurisdiction']} ({s['concentration']}, "
                  f"HHI: {s['hhi']}, Entry: {s['entry_difficulty']}) ---")
            for op in s["top_operators"]:
                bar = "#" * int(op["share_pct"] / 2)
                print(f"  {op['name']:<35} {op['share_pct']:>5.1f}% {bar}")
            print()


def _print_landscape(result: dict):
    """Pretty-print a competitive landscape."""
    if "error" in result:
        print(result["error"])
        return

    print(f"=== Competitive Landscape: {result['jurisdiction']} ===\n")
    print(f"Operators mapped: {result['operators_mapped']}")
    print(f"Mapped market share: {result['total_mapped_share_pct']}%")
    print(f"HHI Index: {result['hhi_index']} ({result['concentration']})\n")

    print(f"{'#':<3} {'Operator':<35} {'Share%':>7} {'GGR (USD)':>18} "
          f"{'Players':>12} {'$/Player':>10} {'Position':<12} {'Trend':<10}")
    print("-" * 115)
    for op in result["operators"]:
        print(f"{op['rank']:<3} {op['name']:<35} {op['market_share_pct']:>6.1f}% "
              f"${op['estimated_ggr_usd']:>16,.0f} {op['estimated_players']:>11,} "
              f"${op['ggr_per_player_usd']:>8,.0f} {op['position']:<12} "
              f"{op['trend']:<10}")
        if op.get("notes"):
            print(f"    {op['notes']}")

    print(f"\n--- White Space Opportunities ---")
    for gap in result["white_space"]:
        print(f"  - {gap}")

    ea = result["entry_assessment"]
    print(f"\n--- Entry Assessment ---")
    print(f"  Difficulty: {ea['difficulty']}")
    print(f"  Strategy: {ea['recommended_strategy']}")
    print(f"  Est. Year 1 share: {ea['estimated_year1_share_pct']:.1f}%")
    print(f"  Est. CAC tier: {ea['estimated_cac_tier']}")


if __name__ == "__main__":
    main()
