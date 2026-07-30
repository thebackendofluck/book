#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 04, Market Analysis and Industry Players.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Competitive Intelligence Data Collector for iGaming Industry
=============================================================

Collects and analyzes competitive data across the online gambling industry
including market share estimates, revenue tracking, product offerings,
geographic presence, and technology stack analysis.

Usage:
    python competitive_intelligence.py --config competitors.yaml
    python competitive_intelligence.py --competitor "Entain" --report
    python competitive_intelligence.py --market-overview --format html
"""

import json
import csv
import logging
import argparse
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class MarketSegment(Enum):
    SPORTS_BETTING = "sports_betting"
    ONLINE_CASINO = "online_casino"
    POKER = "poker"
    BINGO = "bingo"
    LOTTERY = "lottery"
    FANTASY_SPORTS = "fantasy_sports"
    ESPORTS = "esports"
    LIVE_CASINO = "live_casino"


class CompetitorTier(Enum):
    TIER_1 = "tier_1"   # >$5B revenue (Flutter, DraftKings, Entain)
    TIER_2 = "tier_2"   # $1-5B revenue (888, Betsson, Kindred)
    TIER_3 = "tier_3"   # $100M-1B revenue (regional leaders)
    TIER_4 = "tier_4"   # <$100M revenue (niche/startup)


@dataclass
class MarketPresence:
    jurisdiction: str
    license_type: str
    market_share_pct: float
    entry_year: int
    revenue_estimate_usd: float
    player_base_estimate: int
    status: str = "active"  # active, pending, exiting


@dataclass
class ProductOffering:
    segment: MarketSegment
    platform_provider: str        # in-house, Kambi, SBTech, etc.
    game_providers: list = field(default_factory=list)
    unique_features: list = field(default_factory=list)
    mobile_app: bool = True
    live_streaming: bool = False
    cash_out: bool = True


@dataclass
class TechnologyProfile:
    cloud_provider: str           # AWS, GCP, Azure, hybrid
    cdn_provider: str
    primary_languages: list = field(default_factory=list)
    microservices: bool = True
    containerized: bool = True
    ai_ml_capabilities: list = field(default_factory=list)
    known_tech_stack: dict = field(default_factory=dict)


@dataclass
class FinancialSnapshot:
    period: str                   # "2025-Q3"
    revenue_usd: float
    gross_gaming_revenue_usd: float
    ebitda_usd: float
    active_players: int
    revenue_per_player_usd: float
    yoy_growth_pct: float
    marketing_spend_pct: float    # as % of revenue


@dataclass
class Competitor:
    name: str
    legal_name: str
    headquarters: str
    tier: CompetitorTier
    founded_year: int
    public_ticker: Optional[str] = None
    website: str = ""
    employee_count: int = 0
    market_presences: list = field(default_factory=list)
    products: list = field(default_factory=list)
    technology: Optional[TechnologyProfile] = None
    financials: list = field(default_factory=list)
    recent_acquisitions: list = field(default_factory=list)
    strengths: list = field(default_factory=list)
    weaknesses: list = field(default_factory=list)
    strategic_moves: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sample industry data (realistic estimates based on public information)
# ---------------------------------------------------------------------------

SAMPLE_COMPETITORS = [
    Competitor(
        name="Flutter Entertainment",
        legal_name="Flutter Entertainment plc",
        headquarters="Dublin, Ireland",
        tier=CompetitorTier.TIER_1,
        founded_year=2019,
        public_ticker="FLUT",
        website="https://www.flutter.com",
        employee_count=21000,
        market_presences=[
            MarketPresence("United Kingdom", "UKGC", 22.5, 2000, 4200000000, 8500000),
            MarketPresence("United States", "Multi-state", 38.0, 2018, 5100000000, 4200000),
            MarketPresence("Australia", "Multi-state", 45.0, 2005, 2800000000, 2100000),
            MarketPresence("Italy", "ADM", 8.5, 2010, 680000000, 1200000),
            MarketPresence("Brazil", "SPA/MF", 15.0, 2024, 320000000, 3500000),
        ],
        products=[
            ProductOffering(MarketSegment.SPORTS_BETTING, "In-house (FanDuel/Betfair)",
                            unique_features=["Betfair Exchange", "Same Game Parlay+"],
                            live_streaming=True),
            ProductOffering(MarketSegment.ONLINE_CASINO, "In-house",
                            game_providers=["Evolution", "Pragmatic Play", "NetEnt"],
                            unique_features=["Exclusive titles", "Live casino integration"]),
            ProductOffering(MarketSegment.FANTASY_SPORTS, "In-house (FanDuel)",
                            unique_features=["DFS integration with sportsbook"]),
        ],
        technology=TechnologyProfile(
            cloud_provider="AWS (primary), GCP (secondary)",
            cdn_provider="Cloudflare",
            primary_languages=["Java", "Kotlin", "Python", "React"],
            ai_ml_capabilities=["Personalization", "Risk management", "Fraud detection"],
            known_tech_stack={"data_platform": "Databricks", "messaging": "Kafka"}
        ),
        financials=[
            FinancialSnapshot("2025-Q3", 3800000000, 3200000000, 680000000,
                              12400000, 306.45, 18.2, 28.5),
            FinancialSnapshot("2025-Q2", 3500000000, 2950000000, 620000000,
                              11800000, 296.61, 15.8, 30.1),
        ],
        strengths=["Largest global scale", "Exchange model moat", "US market leader via FanDuel",
                    "Strong brand portfolio", "Data & personalization capabilities"],
        weaknesses=["Regulatory concentration risk", "Complex multi-brand architecture",
                     "High marketing spend in US", "Integration challenges from acquisitions"],
        recent_acquisitions=[
            {"target": "Sisal", "year": 2022, "value_usd": 2200000000, "rationale": "Italian market entry"},
            {"target": "MaxBet", "year": 2024, "value_usd": 140000000, "rationale": "Serbian market expansion"},
        ],
        strategic_moves=["US market consolidation", "Exchange model expansion",
                         "AI-driven personalization", "Emerging markets entry (Brazil, India)"],
    ),
    Competitor(
        name="Entain",
        legal_name="Entain plc",
        headquarters="London, United Kingdom",
        tier=CompetitorTier.TIER_1,
        founded_year=2004,
        public_ticker="ENT.L",
        website="https://www.entaingroup.com",
        employee_count=29000,
        market_presences=[
            MarketPresence("United Kingdom", "UKGC", 15.8, 2004, 2100000000, 5200000),
            MarketPresence("United States", "Multi-state", 18.5, 2020, 2400000000, 3100000,
                           status="active"),
            MarketPresence("Germany", "GGL", 12.0, 2012, 420000000, 900000),
            MarketPresence("Brazil", "SPA/MF", 8.0, 2024, 180000000, 2200000),
        ],
        products=[
            ProductOffering(MarketSegment.SPORTS_BETTING, "In-house",
                            unique_features=["BetBuilder", "Acca Insurance"],
                            live_streaming=True),
            ProductOffering(MarketSegment.ONLINE_CASINO, "In-house",
                            game_providers=["Evolution", "Playtech", "IGT"],
                            unique_features=["bwin Casino", "Party Casino"]),
        ],
        technology=TechnologyProfile(
            cloud_provider="GCP (primary)",
            cdn_provider="Akamai",
            primary_languages=["Java", "Go", "TypeScript", "Python"],
            ai_ml_capabilities=["ARC (Advanced Responsibility & Care)", "Player protection AI"],
            known_tech_stack={"platform": "Custom", "data": "BigQuery"}
        ),
        financials=[
            FinancialSnapshot("2025-Q3", 2800000000, 2350000000, 490000000,
                              9800000, 285.71, 12.5, 26.0),
        ],
        strengths=["Strong B2B platform", "Player protection technology (ARC)",
                    "Multi-brand strategy", "BetMGM JV in US"],
        weaknesses=["Governance concerns", "BetMGM JV complexity",
                     "Slower US growth vs competitors", "Regulatory fines history"],
        recent_acquisitions=[
            {"target": "SuperSport", "year": 2023, "value_usd": 800000000,
             "rationale": "Central/Eastern European expansion"},
        ],
        strategic_moves=["B2B platform licensing", "Player safety leadership",
                         "Latin America expansion", "BetMGM optimization"],
    ),
    Competitor(
        name="bet365",
        legal_name="bet365 Group Ltd",
        headquarters="Stoke-on-Trent, United Kingdom",
        tier=CompetitorTier.TIER_1,
        founded_year=2000,
        website="https://www.bet365.com",
        employee_count=7000,
        market_presences=[
            MarketPresence("United Kingdom", "UKGC", 18.0, 2000, 2800000000, 6000000),
            MarketPresence("Australia", "Multi-state", 25.0, 2012, 1500000000, 1800000),
            MarketPresence("United States", "Multi-state", 3.5, 2023, 280000000, 450000),
        ],
        products=[
            ProductOffering(MarketSegment.SPORTS_BETTING, "In-house",
                            unique_features=["Best-in-class live betting", "Bet Builder",
                                             "Extensive live streaming"],
                            live_streaming=True, cash_out=True),
            ProductOffering(MarketSegment.ONLINE_CASINO, "In-house",
                            game_providers=["Playtech", "Microgaming", "NetEnt"]),
        ],
        technology=TechnologyProfile(
            cloud_provider="Hybrid (private + AWS)",
            cdn_provider="Custom / Akamai",
            primary_languages=["Java", "C++", "JavaScript"],
            ai_ml_capabilities=["Live odds engine", "Risk management"],
            known_tech_stack={"streaming": "Custom low-latency", "odds": "Proprietary"}
        ),
        financials=[
            FinancialSnapshot("2024-FY", 4700000000, 3900000000, 980000000,
                              9500000, 494.74, 10.2, 18.0),
        ],
        strengths=["Best live betting product globally", "Private company (long-term focus)",
                    "Low marketing spend efficiency", "Proprietary technology"],
        weaknesses=["Limited US presence", "Single brand strategy",
                     "Opaque financials (private)", "Slow to diversify beyond sports"],
        strategic_moves=["US state-by-state rollout", "Live streaming investment",
                         "Technology moat deepening"],
    ),
    Competitor(
        name="DraftKings",
        legal_name="DraftKings Inc.",
        headquarters="Boston, Massachusetts, USA",
        tier=CompetitorTier.TIER_1,
        founded_year=2012,
        public_ticker="DKNG",
        website="https://www.draftkings.com",
        employee_count=5500,
        market_presences=[
            MarketPresence("United States", "Multi-state", 30.0, 2018, 4200000000, 3800000),
            MarketPresence("Canada", "Provincial", 5.0, 2022, 120000000, 250000),
        ],
        products=[
            ProductOffering(MarketSegment.SPORTS_BETTING, "In-house (SBTech acquired)",
                            unique_features=["Flash Bet", "SGP", "Live in-game micro betting"],
                            live_streaming=True),
            ProductOffering(MarketSegment.ONLINE_CASINO, "In-house",
                            game_providers=["Evolution", "IGT", "Red Tiger"],
                            unique_features=["DK Casino exclusive titles"]),
            ProductOffering(MarketSegment.FANTASY_SPORTS, "In-house",
                            unique_features=["Original DFS platform", "Pick6"]),
        ],
        technology=TechnologyProfile(
            cloud_provider="AWS",
            cdn_provider="CloudFront / Cloudflare",
            primary_languages=["Java", "Kotlin", "Python", "React Native"],
            ai_ml_capabilities=["Player lifetime value", "Dynamic pricing",
                                "Responsible gaming detection"],
            known_tech_stack={"data": "Snowflake", "ml": "SageMaker"}
        ),
        financials=[
            FinancialSnapshot("2025-Q3", 1250000000, 1050000000, 180000000,
                              3800000, 328.95, 22.5, 32.0),
        ],
        strengths=["US-first strategy", "Strong brand recognition", "Technology-driven culture",
                    "Vertical integration (SBTech)", "NFT/Web3 experiments"],
        weaknesses=["US-only concentration", "Path to sustained profitability",
                     "High customer acquisition costs", "No international diversification"],
        strategic_moves=["iGaming expansion in US states", "Profitability focus",
                         "Micro-betting innovation", "Potential international expansion"],
    ),
    Competitor(
        name="Betsson Group",
        legal_name="Betsson AB",
        headquarters="Stockholm, Sweden",
        tier=CompetitorTier.TIER_2,
        founded_year=1963,
        public_ticker="BETS.ST",
        website="https://www.betssongroup.com",
        employee_count=2200,
        market_presences=[
            MarketPresence("Sweden", "Spelinspektionen", 12.0, 1963, 180000000, 450000),
            MarketPresence("United Kingdom", "UKGC", 2.5, 2005, 120000000, 280000),
            MarketPresence("Latin America", "Multi-jurisdiction", 8.0, 2020, 250000000, 800000),
            MarketPresence("Africa", "Multi-jurisdiction", 5.0, 2021, 80000000, 600000),
        ],
        products=[
            ProductOffering(MarketSegment.SPORTS_BETTING, "In-house + Kambi",
                            unique_features=["Multi-brand portfolio"],
                            live_streaming=True),
            ProductOffering(MarketSegment.ONLINE_CASINO, "In-house",
                            game_providers=["NetEnt", "Red Tiger", "Evolution"]),
        ],
        technology=TechnologyProfile(
            cloud_provider="AWS",
            cdn_provider="Cloudflare",
            primary_languages=["Java", "Kotlin", "TypeScript"],
            ai_ml_capabilities=["CRM optimization", "Bonus engine"],
        ),
        financials=[
            FinancialSnapshot("2025-Q3", 285000000, 240000000, 62000000,
                              1600000, 178.13, 14.0, 22.0),
        ],
        strengths=["Profitable and disciplined", "Multi-brand strategy",
                    "Strong in emerging markets", "B2B capabilities"],
        weaknesses=["Smaller scale vs Tier 1", "Limited US exposure",
                     "Brand fragmentation risk"],
        strategic_moves=["Latin America expansion", "Africa growth",
                         "B2B platform licensing"],
    ),
]


# ---------------------------------------------------------------------------
# Competitive Intelligence Engine
# ---------------------------------------------------------------------------

class CompetitiveIntelligenceEngine:
    """Core engine for competitive analysis in the iGaming industry."""

    def __init__(self):
        self.competitors: dict[str, Competitor] = {}
        self.market_data: dict = {}
        self._load_sample_data()

    def _load_sample_data(self):
        for c in SAMPLE_COMPETITORS:
            self.competitors[c.name.lower()] = c

    def add_competitor(self, competitor: Competitor):
        self.competitors[competitor.name.lower()] = competitor
        logger.info("Added competitor: %s (Tier %s)", competitor.name, competitor.tier.value)

    # ------ Market share analysis ------

    def calculate_market_shares(self, jurisdiction: str) -> list[dict]:
        """Calculate market share breakdown for a jurisdiction."""
        results = []
        for c in self.competitors.values():
            for mp in c.market_presences:
                if mp.jurisdiction.lower() == jurisdiction.lower() and mp.status == "active":
                    results.append({
                        "competitor": c.name,
                        "tier": c.tier.value,
                        "market_share_pct": mp.market_share_pct,
                        "revenue_estimate_usd": mp.revenue_estimate_usd,
                        "player_base": mp.player_base_estimate,
                        "entry_year": mp.entry_year,
                    })
        results.sort(key=lambda x: x["market_share_pct"], reverse=True)
        return results

    def get_total_addressable_market(self, jurisdiction: str) -> dict:
        """Estimate TAM for a jurisdiction based on competitor revenues."""
        shares = self.calculate_market_shares(jurisdiction)
        if not shares:
            return {"jurisdiction": jurisdiction, "tam_usd": 0, "competitors_tracked": 0}

        total_tracked_share = sum(s["market_share_pct"] for s in shares)
        total_tracked_revenue = sum(s["revenue_estimate_usd"] for s in shares)

        tam_estimate = (total_tracked_revenue / total_tracked_share * 100) if total_tracked_share > 0 else 0

        return {
            "jurisdiction": jurisdiction,
            "tam_estimate_usd": round(tam_estimate),
            "tracked_revenue_usd": total_tracked_revenue,
            "tracked_share_pct": total_tracked_share,
            "competitors_tracked": len(shares),
            "hhi_index": self._calculate_hhi(shares),
            "concentration": self._market_concentration(shares),
        }

    def _calculate_hhi(self, shares: list[dict]) -> int:
        """Herfindahl-Hirschman Index (market concentration)."""
        return int(sum(s["market_share_pct"] ** 2 for s in shares))

    def _market_concentration(self, shares: list[dict]) -> str:
        hhi = self._calculate_hhi(shares)
        if hhi < 1500:
            return "competitive"
        elif hhi < 2500:
            return "moderately_concentrated"
        else:
            return "highly_concentrated"

    # ------ Revenue analysis ------

    def revenue_comparison(self) -> list[dict]:
        """Compare latest revenue across competitors."""
        results = []
        for c in self.competitors.values():
            if c.financials:
                latest = c.financials[0]
                results.append({
                    "competitor": c.name,
                    "tier": c.tier.value,
                    "period": latest.period,
                    "revenue_usd": latest.revenue_usd,
                    "ggr_usd": latest.gross_gaming_revenue_usd,
                    "ebitda_usd": latest.ebitda_usd,
                    "active_players": latest.active_players,
                    "rev_per_player_usd": latest.revenue_per_player_usd,
                    "yoy_growth_pct": latest.yoy_growth_pct,
                    "marketing_spend_pct": latest.marketing_spend_pct,
                })
        results.sort(key=lambda x: x["revenue_usd"], reverse=True)
        return results

    # ------ Technology analysis ------

    def technology_comparison(self) -> list[dict]:
        """Compare technology stacks across competitors."""
        results = []
        for c in self.competitors.values():
            if c.technology:
                t = c.technology
                results.append({
                    "competitor": c.name,
                    "cloud": t.cloud_provider,
                    "cdn": t.cdn_provider,
                    "languages": t.primary_languages,
                    "microservices": t.microservices,
                    "containerized": t.containerized,
                    "ai_capabilities": t.ai_ml_capabilities,
                    "ai_maturity_score": len(t.ai_ml_capabilities),  # simple proxy
                })
        return results

    # ------ SWOT analysis ------

    def generate_swot(self, competitor_name: str) -> dict:
        """Generate a SWOT analysis for a competitor."""
        c = self.competitors.get(competitor_name.lower())
        if not c:
            return {"error": f"Competitor '{competitor_name}' not found"}

        total_revenue = sum(mp.revenue_estimate_usd for mp in c.market_presences)
        jurisdiction_count = len([mp for mp in c.market_presences if mp.status == "active"])

        opportunities = []
        threats = []

        # Auto-detect opportunities based on gaps
        all_jurisdictions = set()
        for comp in self.competitors.values():
            for mp in comp.market_presences:
                all_jurisdictions.add(mp.jurisdiction)

        competitor_jurisdictions = {mp.jurisdiction for mp in c.market_presences}
        missing = all_jurisdictions - competitor_jurisdictions
        if missing:
            opportunities.append(f"Expand into {len(missing)} jurisdictions: {', '.join(list(missing)[:3])}")

        has_segments = {p.segment for p in c.products}
        all_segments = set(MarketSegment)
        missing_segments = all_segments - has_segments
        if missing_segments:
            opportunities.append(
                f"Enter {len(missing_segments)} new segments: "
                f"{', '.join(s.value for s in list(missing_segments)[:3])}"
            )

        # Threats
        for comp in self.competitors.values():
            if comp.name == c.name:
                continue
            if comp.financials and c.financials:
                if comp.financials[0].yoy_growth_pct > c.financials[0].yoy_growth_pct + 5:
                    threats.append(f"{comp.name} growing {comp.financials[0].yoy_growth_pct}% YoY (faster)")

        threats.append("Regulatory tightening in key markets")
        threats.append("Increased taxation pressure")

        return {
            "competitor": c.name,
            "strengths": c.strengths,
            "weaknesses": c.weaknesses,
            "opportunities": opportunities,
            "threats": threats,
            "summary": {
                "total_estimated_revenue_usd": total_revenue,
                "active_jurisdictions": jurisdiction_count,
                "product_segments": len(c.products),
                "tier": c.tier.value,
            },
        }

    # ------ Competitive positioning map ------

    def positioning_matrix(self) -> list[dict]:
        """Create a positioning matrix (scale vs. growth) for visualization."""
        results = []
        for c in self.competitors.values():
            if c.financials:
                latest = c.financials[0]
                total_rev = sum(mp.revenue_estimate_usd for mp in c.market_presences)
                results.append({
                    "competitor": c.name,
                    "x_scale_revenue_usd": total_rev,
                    "y_growth_pct": latest.yoy_growth_pct,
                    "bubble_size_players": latest.active_players,
                    "tier": c.tier.value,
                    "jurisdictions": len(c.market_presences),
                })
        return results

    # ------ Acquisition landscape ------

    def acquisition_landscape(self) -> list[dict]:
        """Map recent M&A activity across the industry."""
        all_acquisitions = []
        for c in self.competitors.values():
            for acq in c.recent_acquisitions:
                all_acquisitions.append({
                    "acquirer": c.name,
                    "target": acq["target"],
                    "year": acq["year"],
                    "value_usd": acq.get("value_usd", 0),
                    "rationale": acq.get("rationale", ""),
                })
        all_acquisitions.sort(key=lambda x: x["year"], reverse=True)
        return all_acquisitions

    # ------ Full report generation ------

    def generate_full_report(self, competitor_name: Optional[str] = None) -> dict:
        """Generate a comprehensive competitive intelligence report."""
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report_type": "competitive_intelligence",
        }

        if competitor_name:
            c = self.competitors.get(competitor_name.lower())
            if not c:
                return {"error": f"Competitor '{competitor_name}' not found"}
            report["focus_competitor"] = competitor_name
            report["swot"] = self.generate_swot(competitor_name)  # ty:ignore[invalid-assignment]
            report["financials"] = [asdict(f) for f in c.financials]  # ty:ignore[invalid-assignment]
            report["technology"] = asdict(c.technology) if c.technology else None  # ty:ignore[invalid-assignment]
            report["market_presence"] = [asdict(mp) for mp in c.market_presences]  # ty:ignore[invalid-assignment]
        else:
            report["market_overview"] = {
                "competitors_tracked": len(self.competitors),
                "revenue_comparison": self.revenue_comparison(),
                "positioning_matrix": self.positioning_matrix(),
                "technology_comparison": self.technology_comparison(),
                "acquisition_landscape": self.acquisition_landscape(),
            }  # ty:ignore[invalid-assignment]
            # Key jurisdiction analysis
            key_jurisdictions = ["United Kingdom", "United States", "Australia"]
            report["jurisdiction_analysis"] = {}  # ty:ignore[invalid-assignment]
            for j in key_jurisdictions:
                tam = self.get_total_addressable_market(j)
                if tam["competitors_tracked"] > 0:
                    report["jurisdiction_analysis"][j] = {  # ty:ignore[invalid-assignment]
                        "tam": tam,
                        "shares": self.calculate_market_shares(j),
                    }

        return report

    def export_csv(self, output_path: str):
        """Export revenue comparison to CSV."""
        data = self.revenue_comparison()
        if not data:
            logger.warning("No data to export")
            return

        path = Path(output_path)
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        logger.info("Exported %d records to %s", len(data), path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="iGaming Competitive Intelligence Collector")
    parser.add_argument("--competitor", type=str, help="Focus on specific competitor")
    parser.add_argument("--market-overview", action="store_true", help="Full market overview")
    parser.add_argument("--jurisdiction", type=str, help="Analyze specific jurisdiction")
    parser.add_argument("--swot", type=str, help="Generate SWOT for competitor")
    parser.add_argument("--revenue", action="store_true", help="Revenue comparison")
    parser.add_argument("--tech", action="store_true", help="Technology comparison")
    parser.add_argument("--acquisitions", action="store_true", help="M&A landscape")
    parser.add_argument("--export-csv", type=str, help="Export revenue data to CSV")
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format")
    args = parser.parse_args()

    engine = CompetitiveIntelligenceEngine()

    if args.export_csv:
        engine.export_csv(args.export_csv)
        return

    result = None

    if args.competitor:
        result = engine.generate_full_report(args.competitor)
    elif args.jurisdiction:
        result = {
            "tam": engine.get_total_addressable_market(args.jurisdiction),
            "market_shares": engine.calculate_market_shares(args.jurisdiction),
        }
    elif args.swot:
        result = engine.generate_swot(args.swot)
    elif args.revenue:
        result = engine.revenue_comparison()
    elif args.tech:
        result = engine.technology_comparison()
    elif args.acquisitions:
        result = engine.acquisition_landscape()
    elif args.market_overview:
        result = engine.generate_full_report()
    else:
        # Default: summary
        print("=== iGaming Competitive Intelligence ===\n")
        print(f"Competitors tracked: {len(engine.competitors)}")
        print(f"Tier 1: {sum(1 for c in engine.competitors.values() if c.tier == CompetitorTier.TIER_1)}")
        print(f"Tier 2: {sum(1 for c in engine.competitors.values() if c.tier == CompetitorTier.TIER_2)}")
        print()
        rev = engine.revenue_comparison()
        print("Revenue Ranking (Latest Quarter):")
        for i, r in enumerate(rev, 1):
            print(f"  {i}. {r['competitor']:25s} ${r['revenue_usd']:>15,.0f}  "
                  f"(YoY: {r['yoy_growth_pct']:+.1f}%)")
        return

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
