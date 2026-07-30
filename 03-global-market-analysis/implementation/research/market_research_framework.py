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
Market Research Data Collection Framework for iGaming
======================================================

Aggregates data from public sources (H2 Gambling Capital estimates, Statista,
regulatory filings, operator annual reports) to build jurisdiction-level market
intelligence profiles.

Usage:
    python market_research_framework.py --jurisdiction UK
    python market_research_framework.py --region europe --format json
    python market_research_framework.py --all --export csv
    python market_research_framework.py --update-scores
"""

import argparse
import csv
import io
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class DataSource:
    """A market data source with reliability rating."""
    name: str
    type: str  # "commercial", "regulatory", "operator", "academic", "news"
    url: str
    reliability_score: float  # 0-1
    update_frequency: str  # "quarterly", "annual", "real-time", "ad-hoc"
    cost_tier: str  # "free", "moderate", "expensive"
    coverage: list = field(default_factory=list)  # list of region codes
    notes: str = ""


@dataclass
class MarketDataPoint:
    """A single data point for a jurisdiction."""
    metric: str
    value: float
    unit: str
    year: int
    source: str
    confidence: str  # "high", "medium", "low", "estimated"
    notes: str = ""


@dataclass
class JurisdictionMarketProfile:
    """Full market profile for one jurisdiction."""
    code: str
    name: str
    region: str
    population_millions: float
    gdp_per_capita_usd: float
    internet_penetration_pct: float
    smartphone_penetration_pct: float

    # Gambling market data
    total_gambling_revenue_usd: float
    online_gambling_revenue_usd: float
    online_share_pct: float
    yoy_growth_pct: float
    cagr_5yr_pct: float

    # Market segments
    sports_betting_revenue_usd: float
    casino_revenue_usd: float
    poker_revenue_usd: float
    bingo_lottery_revenue_usd: float

    # Player demographics
    total_online_players: int
    gambling_participation_pct: float
    avg_spend_per_player_usd: float
    avg_age_player: float
    male_pct: float

    # Regulatory
    regulatory_status: str  # "regulated", "partially_regulated", "unregulated", "prohibited"
    licensing_regime: str
    ggr_tax_pct: float
    advertising_allowed: bool
    affiliate_marketing_allowed: bool

    # Competitive landscape
    licensed_operators: int
    top_3_market_share_pct: float
    market_concentration: str  # "fragmented", "moderate", "concentrated", "monopoly"

    # Digital infrastructure
    avg_internet_speed_mbps: float
    mobile_payment_adoption_pct: float
    dominant_payment_methods: list = field(default_factory=list)

    # Scores (computed)
    attractiveness_score: float = 0
    risk_score: float = 0
    opportunity_score: float = 0

    data_points: list = field(default_factory=list)
    last_updated: str = ""


# ---------------------------------------------------------------------------
# Data source registry (real sources used in industry)
# ---------------------------------------------------------------------------

DATA_SOURCES = [
    DataSource(
        name="H2 Gambling Capital",
        type="commercial",
        url="https://h2gc.com",
        reliability_score=0.95,
        update_frequency="quarterly",
        cost_tier="expensive",
        coverage=["global"],
        notes="Industry gold standard for market sizing. Covers 200+ jurisdictions. "
              "Annual subscription ~$25,000-$50,000.",
    ),
    DataSource(
        name="Statista - Gambling & Betting",
        type="commercial",
        url="https://www.statista.com/outlook/dmo/eservices/online-gambling/",
        reliability_score=0.80,
        update_frequency="quarterly",
        cost_tier="moderate",
        coverage=["global"],
        notes="Good for market overviews and forecasts. Aggregates multiple sources. "
              "Professional plan ~$5,000/year.",
    ),
    DataSource(
        name="UKGC Industry Statistics",
        type="regulatory",
        url="https://www.gamblingcommission.gov.uk/statistics-and-research/publication/"
             "industry-statistics",
        reliability_score=0.98,
        update_frequency="biannual",
        cost_tier="free",
        coverage=["UK"],
        notes="Official UKGC data. GGR by sector, participation rates, "
              "operator counts. Published May and November.",
    ),
    DataSource(
        name="MGA Annual Report",
        type="regulatory",
        url="https://www.mga.org.mt/publications/annual-reports/",
        reliability_score=0.95,
        update_frequency="annual",
        cost_tier="free",
        coverage=["MT", "EU"],
        notes="Malta Gaming Authority annual reports with GGR, player data, "
              "licensee counts, compliance metrics.",
    ),
    DataSource(
        name="Spelinspektionen Market Reports",
        type="regulatory",
        url="https://www.spelinspektionen.se/en/statistics/",
        reliability_score=0.97,
        update_frequency="quarterly",
        cost_tier="free",
        coverage=["SE"],
        notes="Swedish gambling authority. Detailed channelization rates, "
              "GGR by product, self-exclusion data.",
    ),
    DataSource(
        name="IBIA Betting Integrity Reports",
        type="commercial",
        url="https://ibia.bet/integrity-reports/",
        reliability_score=0.85,
        update_frequency="quarterly",
        cost_tier="free",
        coverage=["global"],
        notes="International Betting Integrity Association. Suspicious "
              "betting alerts by sport and region.",
    ),
    DataSource(
        name="European Gaming & Betting Association (EGBA)",
        type="commercial",
        url="https://www.egba.eu/resources/",
        reliability_score=0.85,
        update_frequency="annual",
        cost_tier="free",
        coverage=["EU"],
        notes="European market data, channelization rates, regulatory tracker.",
    ),
    DataSource(
        name="Eilers & Krejcik Gaming",
        type="commercial",
        url="https://www.eilerandkrejcik.com",
        reliability_score=0.90,
        update_frequency="monthly",
        cost_tier="expensive",
        coverage=["US", "CA"],
        notes="Leading North American iGaming/sports betting analyst. "
              "State-level tracking, handle and revenue data.",
    ),
    DataSource(
        name="SBC News / iGaming NEXT",
        type="news",
        url="https://www.sbcnews.co.uk",
        reliability_score=0.65,
        update_frequency="real-time",
        cost_tier="free",
        coverage=["global"],
        notes="Industry news. Good for M&A, new market entries, "
              "regulatory developments. Cross-reference with official sources.",
    ),
    DataSource(
        name="Operator Annual Reports (Flutter, Entain, bet365, DraftKings)",
        type="operator",
        url="",
        reliability_score=0.90,
        update_frequency="quarterly",
        cost_tier="free",
        coverage=["global"],
        notes="Publicly listed operators file quarterly/annual reports with "
              "revenue breakdowns by region and product.",
    ),
    DataSource(
        name="World Bank Data",
        type="academic",
        url="https://data.worldbank.org",
        reliability_score=0.95,
        update_frequency="annual",
        cost_tier="free",
        coverage=["global"],
        notes="GDP, population, internet penetration, financial inclusion. "
              "Essential for TAM calculations.",
    ),
    DataSource(
        name="ITU ICT Statistics",
        type="academic",
        url="https://datahub.itu.int",
        reliability_score=0.93,
        update_frequency="annual",
        cost_tier="free",
        coverage=["global"],
        notes="Internet and mobile penetration by country. "
              "Broadband speeds, smartphone adoption.",
    ),
]


# ---------------------------------------------------------------------------
# Jurisdiction market database (realistic estimates 2024-2025)
# ---------------------------------------------------------------------------

JURISDICTION_PROFILES = [
    JurisdictionMarketProfile(
        code="UK", name="United Kingdom", region="europe",
        population_millions=67.7, gdp_per_capita_usd=46510,
        internet_penetration_pct=97.0, smartphone_penetration_pct=92.0,
        total_gambling_revenue_usd=22400000000,
        online_gambling_revenue_usd=11200000000,
        online_share_pct=50.0, yoy_growth_pct=3.2, cagr_5yr_pct=5.1,
        sports_betting_revenue_usd=4500000000,
        casino_revenue_usd=4200000000,
        poker_revenue_usd=350000000,
        bingo_lottery_revenue_usd=2150000000,
        total_online_players=17000000, gambling_participation_pct=44.0,
        avg_spend_per_player_usd=660, avg_age_player=37, male_pct=62,
        regulatory_status="regulated", licensing_regime="UKGC",
        ggr_tax_pct=21.0, advertising_allowed=True,
        affiliate_marketing_allowed=True,
        licensed_operators=280, top_3_market_share_pct=42,
        market_concentration="moderate",
        avg_internet_speed_mbps=75.5, mobile_payment_adoption_pct=68,
        dominant_payment_methods=["Visa/Mastercard", "PayPal", "Apple Pay",
                                   "Paysafecard", "Bank Transfer"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="DE", name="Germany", region="europe",
        population_millions=83.2, gdp_per_capita_usd=51380,
        internet_penetration_pct=93.0, smartphone_penetration_pct=88.0,
        total_gambling_revenue_usd=16800000000,
        online_gambling_revenue_usd=4200000000,
        online_share_pct=25.0, yoy_growth_pct=8.5, cagr_5yr_pct=12.0,
        sports_betting_revenue_usd=2100000000,
        casino_revenue_usd=1400000000,
        poker_revenue_usd=280000000,
        bingo_lottery_revenue_usd=420000000,
        total_online_players=8500000, gambling_participation_pct=38.0,
        avg_spend_per_player_usd=494, avg_age_player=35, male_pct=68,
        regulatory_status="regulated", licensing_regime="GGL (Glucksspielbehorde)",
        ggr_tax_pct=5.3, advertising_allowed=True,
        affiliate_marketing_allowed=True,
        licensed_operators=52, top_3_market_share_pct=35,
        market_concentration="moderate",
        avg_internet_speed_mbps=65.2, mobile_payment_adoption_pct=55,
        dominant_payment_methods=["PayPal", "Sofort/Klarna", "Visa/Mastercard",
                                   "Bank Transfer", "Paysafecard"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="IT", name="Italy", region="europe",
        population_millions=59.0, gdp_per_capita_usd=34770,
        internet_penetration_pct=88.0, smartphone_penetration_pct=83.0,
        total_gambling_revenue_usd=24000000000,
        online_gambling_revenue_usd=5800000000,
        online_share_pct=24.0, yoy_growth_pct=12.0, cagr_5yr_pct=15.0,
        sports_betting_revenue_usd=2800000000,
        casino_revenue_usd=2200000000,
        poker_revenue_usd=380000000,
        bingo_lottery_revenue_usd=420000000,
        total_online_players=5200000, gambling_participation_pct=30.0,
        avg_spend_per_player_usd=1115, avg_age_player=36, male_pct=65,
        regulatory_status="regulated", licensing_regime="ADM",
        ggr_tax_pct=25.0, advertising_allowed=False,
        affiliate_marketing_allowed=False,
        licensed_operators=95, top_3_market_share_pct=38,
        market_concentration="moderate",
        avg_internet_speed_mbps=55.8, mobile_payment_adoption_pct=48,
        dominant_payment_methods=["PostePay", "Visa/Mastercard", "PayPal",
                                   "Bank Transfer", "Skrill"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="US", name="United States (regulated states)", region="north_america",
        population_millions=335.0, gdp_per_capita_usd=76330,
        internet_penetration_pct=95.0, smartphone_penetration_pct=90.0,
        total_gambling_revenue_usd=66000000000,
        online_gambling_revenue_usd=28000000000,
        online_share_pct=42.0, yoy_growth_pct=22.0, cagr_5yr_pct=35.0,
        sports_betting_revenue_usd=15000000000,
        casino_revenue_usd=8000000000,
        poker_revenue_usd=800000000,
        bingo_lottery_revenue_usd=4200000000,
        total_online_players=55000000, gambling_participation_pct=32.0,
        avg_spend_per_player_usd=509, avg_age_player=38, male_pct=64,
        regulatory_status="partially_regulated",
        licensing_regime="State-by-state (38 states sports, 7 iGaming)",
        ggr_tax_pct=20.0, advertising_allowed=True,
        affiliate_marketing_allowed=True,
        licensed_operators=120, top_3_market_share_pct=72,
        market_concentration="concentrated",
        avg_internet_speed_mbps=95.3, mobile_payment_adoption_pct=72,
        dominant_payment_methods=["Visa/Mastercard", "PayPal", "Play+",
                                   "VIP Preferred (ACH)", "Apple Pay"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="BR", name="Brazil", region="latin_america",
        population_millions=215.0, gdp_per_capita_usd=9670,
        internet_penetration_pct=84.0, smartphone_penetration_pct=78.0,
        total_gambling_revenue_usd=12000000000,
        online_gambling_revenue_usd=8000000000,
        online_share_pct=67.0, yoy_growth_pct=45.0, cagr_5yr_pct=55.0,
        sports_betting_revenue_usd=6000000000,
        casino_revenue_usd=1500000000,
        poker_revenue_usd=200000000,
        bingo_lottery_revenue_usd=300000000,
        total_online_players=32000000, gambling_participation_pct=22.0,
        avg_spend_per_player_usd=250, avg_age_player=30, male_pct=62,
        regulatory_status="regulated",
        licensing_regime="SPA (Secretaria de Premios e Apostas)",
        ggr_tax_pct=12.0, advertising_allowed=True,
        affiliate_marketing_allowed=True,
        licensed_operators=65, top_3_market_share_pct=55,
        market_concentration="moderate",
        avg_internet_speed_mbps=42.5, mobile_payment_adoption_pct=85,
        dominant_payment_methods=["PIX", "Boleto Bancario", "Credit Card",
                                   "PicPay", "Mercado Pago"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="AU", name="Australia", region="oceania",
        population_millions=26.5, gdp_per_capita_usd=63530,
        internet_penetration_pct=96.0, smartphone_penetration_pct=91.0,
        total_gambling_revenue_usd=18500000000,
        online_gambling_revenue_usd=5200000000,
        online_share_pct=28.0, yoy_growth_pct=6.5, cagr_5yr_pct=8.0,
        sports_betting_revenue_usd=4800000000,
        casino_revenue_usd=0,  # online casino prohibited
        poker_revenue_usd=0,
        bingo_lottery_revenue_usd=400000000,
        total_online_players=4200000, gambling_participation_pct=64.0,
        avg_spend_per_player_usd=1238, avg_age_player=38, male_pct=68,
        regulatory_status="partially_regulated",
        licensing_regime="State + Federal (ACMA, Northern Territory)",
        ggr_tax_pct=15.0, advertising_allowed=True,
        affiliate_marketing_allowed=False,
        licensed_operators=35, top_3_market_share_pct=65,
        market_concentration="concentrated",
        avg_internet_speed_mbps=62.8, mobile_payment_adoption_pct=65,
        dominant_payment_methods=["Visa/Mastercard", "POLi", "PayPal",
                                   "Apple Pay", "Bank Transfer"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="NG", name="Nigeria", region="africa",
        population_millions=225.0, gdp_per_capita_usd=2180,
        internet_penetration_pct=55.0, smartphone_penetration_pct=42.0,
        total_gambling_revenue_usd=2800000000,
        online_gambling_revenue_usd=1100000000,
        online_share_pct=39.0, yoy_growth_pct=35.0, cagr_5yr_pct=40.0,
        sports_betting_revenue_usd=950000000,
        casino_revenue_usd=100000000,
        poker_revenue_usd=10000000,
        bingo_lottery_revenue_usd=40000000,
        total_online_players=12000000, gambling_participation_pct=18.0,
        avg_spend_per_player_usd=92, avg_age_player=27, male_pct=72,
        regulatory_status="partially_regulated",
        licensing_regime="State-level (Lagos, Oyo, etc.) + NLRC",
        ggr_tax_pct=5.0, advertising_allowed=True,
        affiliate_marketing_allowed=True,
        licensed_operators=80, top_3_market_share_pct=60,
        market_concentration="moderate",
        avg_internet_speed_mbps=18.5, mobile_payment_adoption_pct=75,
        dominant_payment_methods=["Mobile Money", "Bank Transfer",
                                   "USSD", "OPay", "Flutterwave"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="PH", name="Philippines", region="asia_pacific",
        population_millions=115.0, gdp_per_capita_usd=3950,
        internet_penetration_pct=68.0, smartphone_penetration_pct=62.0,
        total_gambling_revenue_usd=7500000000,
        online_gambling_revenue_usd=2800000000,
        online_share_pct=37.0, yoy_growth_pct=18.0, cagr_5yr_pct=22.0,
        sports_betting_revenue_usd=1200000000,
        casino_revenue_usd=1100000000,
        poker_revenue_usd=150000000,
        bingo_lottery_revenue_usd=350000000,
        total_online_players=9500000, gambling_participation_pct=28.0,
        avg_spend_per_player_usd=295, avg_age_player=29, male_pct=66,
        regulatory_status="regulated",
        licensing_regime="PAGCOR",
        ggr_tax_pct=5.0, advertising_allowed=True,
        affiliate_marketing_allowed=True,
        licensed_operators=55, top_3_market_share_pct=45,
        market_concentration="moderate",
        avg_internet_speed_mbps=32.4, mobile_payment_adoption_pct=55,
        dominant_payment_methods=["GCash", "Maya", "Bank Transfer",
                                   "OTC (7-Eleven)", "Credit Card"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="SE", name="Sweden", region="europe",
        population_millions=10.5, gdp_per_capita_usd=55560,
        internet_penetration_pct=98.0, smartphone_penetration_pct=95.0,
        total_gambling_revenue_usd=3200000000,
        online_gambling_revenue_usd=2100000000,
        online_share_pct=66.0, yoy_growth_pct=4.5, cagr_5yr_pct=6.0,
        sports_betting_revenue_usd=850000000,
        casino_revenue_usd=800000000,
        poker_revenue_usd=120000000,
        bingo_lottery_revenue_usd=330000000,
        total_online_players=3200000, gambling_participation_pct=58.0,
        avg_spend_per_player_usd=656, avg_age_player=38, male_pct=60,
        regulatory_status="regulated",
        licensing_regime="Spelinspektionen",
        ggr_tax_pct=18.0, advertising_allowed=True,
        affiliate_marketing_allowed=True,
        licensed_operators=95, top_3_market_share_pct=48,
        market_concentration="moderate",
        avg_internet_speed_mbps=115.0, mobile_payment_adoption_pct=82,
        dominant_payment_methods=["Swish", "Trustly", "Visa/Mastercard",
                                   "Zimpler", "Bank Transfer"],
        last_updated="2025-06",
    ),
    JurisdictionMarketProfile(
        code="JP", name="Japan", region="asia_pacific",
        population_millions=124.0, gdp_per_capita_usd=33950,
        internet_penetration_pct=93.0, smartphone_penetration_pct=85.0,
        total_gambling_revenue_usd=30000000000,
        online_gambling_revenue_usd=1500000000,
        online_share_pct=5.0, yoy_growth_pct=15.0, cagr_5yr_pct=20.0,
        sports_betting_revenue_usd=800000000,
        casino_revenue_usd=200000000,
        poker_revenue_usd=50000000,
        bingo_lottery_revenue_usd=450000000,
        total_online_players=6000000, gambling_participation_pct=12.0,
        avg_spend_per_player_usd=250, avg_age_player=40, male_pct=72,
        regulatory_status="partially_regulated",
        licensing_regime="Limited (horse, boat, cycle racing; casino IR pending)",
        ggr_tax_pct=30.0, advertising_allowed=False,
        affiliate_marketing_allowed=False,
        licensed_operators=8, top_3_market_share_pct=85,
        market_concentration="concentrated",
        avg_internet_speed_mbps=120.0, mobile_payment_adoption_pct=70,
        dominant_payment_methods=["Credit Card", "PayPay", "LINE Pay",
                                   "Convenience Store", "Bank Transfer"],
        last_updated="2025-06",
    ),
]


# ---------------------------------------------------------------------------
# Scoring methodology
# ---------------------------------------------------------------------------

class MarketScorer:
    """Score jurisdictions on attractiveness, risk, and opportunity."""

    ATTRACTIVENESS_WEIGHTS = {
        "market_size": 0.25,
        "growth_rate": 0.20,
        "digital_readiness": 0.15,
        "spend_per_player": 0.15,
        "regulatory_clarity": 0.15,
        "payment_ecosystem": 0.10,
    }

    RISK_WEIGHTS = {
        "regulatory_uncertainty": 0.25,
        "tax_burden": 0.20,
        "competition_intensity": 0.20,
        "advertising_restrictions": 0.15,
        "infrastructure_gaps": 0.10,
        "currency_gdp_risk": 0.10,
    }

    def score_attractiveness(self, profile: JurisdictionMarketProfile) -> float:
        """Score market attractiveness (0-100)."""
        scores = {}

        # Market size
        rev = profile.online_gambling_revenue_usd
        if rev > 10_000_000_000:
            scores["market_size"] = 95
        elif rev > 5_000_000_000:
            scores["market_size"] = 85
        elif rev > 2_000_000_000:
            scores["market_size"] = 70
        elif rev > 500_000_000:
            scores["market_size"] = 55
        elif rev > 100_000_000:
            scores["market_size"] = 35
        else:
            scores["market_size"] = 15

        # Growth rate
        cagr = profile.cagr_5yr_pct
        if cagr > 30:
            scores["growth_rate"] = 95
        elif cagr > 20:
            scores["growth_rate"] = 85
        elif cagr > 10:
            scores["growth_rate"] = 70
        elif cagr > 5:
            scores["growth_rate"] = 55
        else:
            scores["growth_rate"] = 35

        # Digital readiness
        digital = (profile.internet_penetration_pct * 0.4 +
                   profile.smartphone_penetration_pct * 0.3 +
                   min(profile.avg_internet_speed_mbps / 1.2, 100) * 0.3)
        scores["digital_readiness"] = min(100, digital)

        # Spend per player
        spend = profile.avg_spend_per_player_usd
        if spend > 1000:
            scores["spend_per_player"] = 90
        elif spend > 500:
            scores["spend_per_player"] = 75
        elif spend > 250:
            scores["spend_per_player"] = 55
        elif spend > 100:
            scores["spend_per_player"] = 35
        else:
            scores["spend_per_player"] = 20

        # Regulatory clarity
        reg_scores = {
            "regulated": 85,
            "partially_regulated": 55,
            "unregulated": 30,
            "prohibited": 5,
        }
        scores["regulatory_clarity"] = reg_scores.get(
            profile.regulatory_status, 40)

        # Payment ecosystem
        scores["payment_ecosystem"] = min(100,
                                           profile.mobile_payment_adoption_pct * 1.1)

        return round(sum(
            scores[k] * self.ATTRACTIVENESS_WEIGHTS[k] for k in scores
        ), 1)

    def score_risk(self, profile: JurisdictionMarketProfile) -> float:
        """Score market risk (0-100, higher = more risky)."""
        scores = {}

        # Regulatory uncertainty (partially regulated = higher risk)
        reg_risk = {
            "regulated": 20,
            "partially_regulated": 60,
            "unregulated": 80,
            "prohibited": 95,
        }
        scores["regulatory_uncertainty"] = reg_risk.get(
            profile.regulatory_status, 50)

        # Tax burden
        tax = profile.ggr_tax_pct
        if tax > 25:
            scores["tax_burden"] = 85
        elif tax > 20:
            scores["tax_burden"] = 70
        elif tax > 15:
            scores["tax_burden"] = 55
        elif tax > 5:
            scores["tax_burden"] = 35
        else:
            scores["tax_burden"] = 15

        # Competition intensity
        conc = profile.top_3_market_share_pct
        if conc > 70:
            scores["competition_intensity"] = 85
        elif conc > 50:
            scores["competition_intensity"] = 65
        elif conc > 30:
            scores["competition_intensity"] = 45
        else:
            scores["competition_intensity"] = 25

        # Advertising restrictions
        if not profile.advertising_allowed:
            scores["advertising_restrictions"] = 85
        elif not profile.affiliate_marketing_allowed:
            scores["advertising_restrictions"] = 60
        else:
            scores["advertising_restrictions"] = 20

        # Infrastructure gaps
        infra = 100 - profile.internet_penetration_pct
        scores["infrastructure_gaps"] = min(100, infra * 2.5)

        # Currency / GDP risk
        gdp = profile.gdp_per_capita_usd
        if gdp < 5000:
            scores["currency_gdp_risk"] = 80
        elif gdp < 15000:
            scores["currency_gdp_risk"] = 55
        elif gdp < 30000:
            scores["currency_gdp_risk"] = 30
        else:
            scores["currency_gdp_risk"] = 10

        return round(sum(
            scores[k] * self.RISK_WEIGHTS[k] for k in scores
        ), 1)

    def compute_opportunity(self, attractiveness: float,
                             risk: float) -> float:
        """Opportunity = attractiveness adjusted for risk."""
        return round(attractiveness * (1 - risk / 200), 1)

    def score_all(self, profiles: list[JurisdictionMarketProfile]):
        """Score every profile in place."""
        for p in profiles:
            p.attractiveness_score = self.score_attractiveness(p)
            p.risk_score = self.score_risk(p)
            p.opportunity_score = self.compute_opportunity(
                p.attractiveness_score, p.risk_score)


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class MarketResearchReport:
    """Generate market research reports."""

    def __init__(self, profiles: list[JurisdictionMarketProfile]):
        self.profiles = {p.code: p for p in profiles}
        self.scorer = MarketScorer()
        self.scorer.score_all(list(self.profiles.values()))

    def jurisdiction_report(self, code: str) -> dict:
        """Detailed report for a single jurisdiction."""
        p = self.profiles.get(code.upper())
        if not p:
            return {"error": f"Jurisdiction '{code}' not found"}
        return {
            "jurisdiction": p.name,
            "code": p.code,
            "region": p.region,
            "scores": {
                "attractiveness": p.attractiveness_score,
                "risk": p.risk_score,
                "opportunity": p.opportunity_score,
            },
            "market_summary": {
                "population_millions": p.population_millions,
                "gdp_per_capita_usd": p.gdp_per_capita_usd,
                "total_gambling_revenue_usd": p.total_gambling_revenue_usd,
                "online_gambling_revenue_usd": p.online_gambling_revenue_usd,
                "online_share_pct": p.online_share_pct,
                "yoy_growth_pct": p.yoy_growth_pct,
                "cagr_5yr_pct": p.cagr_5yr_pct,
            },
            "player_data": {
                "total_online_players": p.total_online_players,
                "gambling_participation_pct": p.gambling_participation_pct,
                "avg_spend_per_player_usd": p.avg_spend_per_player_usd,
                "avg_age": p.avg_age_player,
                "male_pct": p.male_pct,
            },
            "segments": {
                "sports_betting": p.sports_betting_revenue_usd,
                "casino": p.casino_revenue_usd,
                "poker": p.poker_revenue_usd,
                "bingo_lottery": p.bingo_lottery_revenue_usd,
            },
            "regulatory": {
                "status": p.regulatory_status,
                "regime": p.licensing_regime,
                "ggr_tax_pct": p.ggr_tax_pct,
                "advertising_allowed": p.advertising_allowed,
            },
            "competitive_landscape": {
                "licensed_operators": p.licensed_operators,
                "top_3_share_pct": p.top_3_market_share_pct,
                "concentration": p.market_concentration,
            },
            "digital_infra": {
                "internet_penetration_pct": p.internet_penetration_pct,
                "mobile_payment_adoption_pct": p.mobile_payment_adoption_pct,
                "dominant_payment_methods": p.dominant_payment_methods,
            },
            "data_sources_recommended": [
                s.name for s in DATA_SOURCES
                if code.upper() in s.coverage or "global" in s.coverage
            ],
        }

    def region_report(self, region: str) -> dict:
        """Report for a region."""
        profiles = [p for p in self.profiles.values()
                     if p.region == region.lower()]
        if not profiles:
            return {"error": f"No jurisdictions found for region '{region}'"}

        profiles.sort(key=lambda p: p.opportunity_score, reverse=True)
        total_rev = sum(p.online_gambling_revenue_usd for p in profiles)
        total_players = sum(p.total_online_players for p in profiles)

        return {
            "region": region,
            "jurisdictions_analyzed": len(profiles),
            "total_online_revenue_usd": total_rev,
            "total_online_players": total_players,
            "rankings": [
                {
                    "jurisdiction": p.name,
                    "code": p.code,
                    "opportunity_score": p.opportunity_score,
                    "attractiveness": p.attractiveness_score,
                    "risk": p.risk_score,
                    "online_revenue_usd": p.online_gambling_revenue_usd,
                    "growth_cagr_pct": p.cagr_5yr_pct,
                }
                for p in profiles
            ],
        }

    def global_ranking(self) -> list[dict]:
        """Rank all jurisdictions by opportunity score."""
        profiles = sorted(self.profiles.values(),
                           key=lambda p: p.opportunity_score, reverse=True)
        return [
            {
                "rank": i,
                "jurisdiction": p.name,
                "code": p.code,
                "region": p.region,
                "opportunity_score": p.opportunity_score,
                "attractiveness": p.attractiveness_score,
                "risk": p.risk_score,
                "online_revenue_usd": p.online_gambling_revenue_usd,
                "cagr_5yr_pct": p.cagr_5yr_pct,
                "ggr_tax_pct": p.ggr_tax_pct,
            }
            for i, p in enumerate(profiles, 1)
        ]

    def data_source_guide(self) -> list[dict]:
        """Guide to available data sources."""
        return [
            {
                "name": s.name,
                "type": s.type,
                "url": s.url,
                "reliability": s.reliability_score,
                "frequency": s.update_frequency,
                "cost": s.cost_tier,
                "coverage": s.coverage,
                "notes": s.notes,
            }
            for s in sorted(DATA_SOURCES,
                            key=lambda s: s.reliability_score, reverse=True)
        ]

    def export_csv(self) -> str:
        """Export all jurisdiction data as CSV."""
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "Code", "Jurisdiction", "Region",
            "Opportunity", "Attractiveness", "Risk",
            "Online Revenue (USD)", "CAGR 5yr %", "GGR Tax %",
            "Players", "Avg Spend (USD)", "Regulatory Status",
        ])
        for p in sorted(self.profiles.values(),
                        key=lambda x: x.opportunity_score, reverse=True):
            writer.writerow([
                p.code, p.name, p.region,
                p.opportunity_score, p.attractiveness_score, p.risk_score,
                p.online_gambling_revenue_usd, p.cagr_5yr_pct, p.ggr_tax_pct,
                p.total_online_players, p.avg_spend_per_player_usd,
                p.regulatory_status,
            ])
        return output.getvalue()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="iGaming Market Research Framework")
    parser.add_argument("--jurisdiction", "-j", type=str,
                        help="Jurisdiction code (e.g. UK, BR, DE)")
    parser.add_argument("--region", "-r", type=str,
                        help="Region (europe, north_america, latin_america, "
                             "asia_pacific, africa, oceania)")
    parser.add_argument("--all", action="store_true",
                        help="Global ranking of all jurisdictions")
    parser.add_argument("--sources", action="store_true",
                        help="List all data sources")
    parser.add_argument("--update-scores", action="store_true",
                        help="Recalculate all scores")
    parser.add_argument("--format", choices=["json", "text", "csv"],
                        default="text")
    parser.add_argument("--export", choices=["csv", "json"],
                        help="Export full dataset")
    args = parser.parse_args()

    report = MarketResearchReport(JURISDICTION_PROFILES)

    if args.sources:
        result = report.data_source_guide()
        print(json.dumps(result, indent=2))
        return

    if args.export == "csv":
        print(report.export_csv())
        return

    if args.jurisdiction:
        result = report.jurisdiction_report(args.jurisdiction)
        if args.format == "json":
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_jurisdiction_text(result)
        return

    if args.region:
        result = report.region_report(args.region)
        print(json.dumps(result, indent=2, default=str))
        return

    # Default: global ranking
    ranking = report.global_ranking()
    if args.format == "json":
        print(json.dumps(ranking, indent=2))
    else:
        print("=== iGaming Global Market Ranking ===\n")
        print(f"{'Rank':<5} {'Jurisdiction':<30} {'Opportunity':<12} "
              f"{'Attract.':<10} {'Risk':<8} "
              f"{'Online Rev':<18} {'CAGR':<8} {'Tax':<6}")
        print("-" * 105)
        for r in ranking:
            print(f"{r['rank']:<5} {r['jurisdiction']:<30} "
                  f"{r['opportunity_score']:<12.1f} "
                  f"{r['attractiveness']:<10.1f} {r['risk']:<8.1f} "
                  f"${r['online_revenue_usd']:>14,.0f} "
                  f"{r['cagr_5yr_pct']:<7.1f}% {r['ggr_tax_pct']:.0f}%")


def _print_jurisdiction_text(result: dict):
    """Pretty-print a jurisdiction report."""
    if "error" in result:
        print(result["error"])
        return
    print(f"=== Market Report: {result['jurisdiction']} ({result['code']}) ===\n")

    print(f"Region: {result['region']}")
    s = result["scores"]
    print(f"Opportunity Score: {s['opportunity']:.1f} "
          f"(Attractiveness: {s['attractiveness']:.1f}, Risk: {s['risk']:.1f})\n")

    ms = result["market_summary"]
    print("--- Market Summary ---")
    print(f"  Population: {ms['population_millions']:.1f}M")
    print(f"  GDP/capita: ${ms['gdp_per_capita_usd']:,.0f}")
    print(f"  Total gambling revenue: ${ms['total_gambling_revenue_usd']:,.0f}")
    print(f"  Online revenue: ${ms['online_gambling_revenue_usd']:,.0f} "
          f"({ms['online_share_pct']:.0f}% of total)")
    print(f"  YoY growth: {ms['yoy_growth_pct']:.1f}%")
    print(f"  5-year CAGR: {ms['cagr_5yr_pct']:.1f}%\n")

    pd = result["player_data"]
    print("--- Player Demographics ---")
    print(f"  Online players: {pd['total_online_players']:,}")
    print(f"  Participation rate: {pd['gambling_participation_pct']:.0f}%")
    print(f"  Avg spend/player: ${pd['avg_spend_per_player_usd']:,.0f}")
    print(f"  Avg age: {pd['avg_age']}")
    print(f"  Male: {pd['male_pct']}%\n")

    seg = result["segments"]
    print("--- Revenue by Segment ---")
    for name, val in seg.items():
        print(f"  {name.replace('_', ' ').title()}: ${val:,.0f}")

    reg = result["regulatory"]
    print(f"\n--- Regulatory ---")
    print(f"  Status: {reg['status']}")
    print(f"  Regime: {reg['regime']}")
    print(f"  GGR Tax: {reg['ggr_tax_pct']:.1f}%")
    print(f"  Advertising: {'Yes' if reg['advertising_allowed'] else 'No'}")

    cl = result["competitive_landscape"]
    print(f"\n--- Competition ---")
    print(f"  Licensed operators: {cl['licensed_operators']}")
    print(f"  Top 3 market share: {cl['top_3_share_pct']}%")
    print(f"  Concentration: {cl['concentration']}")

    di = result["digital_infra"]
    print(f"\n--- Digital Infrastructure ---")
    print(f"  Internet penetration: {di['internet_penetration_pct']}%")
    print(f"  Mobile payment adoption: {di['mobile_payment_adoption_pct']}%")
    print(f"  Payment methods: {', '.join(di['dominant_payment_methods'])}")

    print(f"\n--- Recommended Data Sources ---")
    for s in result["data_sources_recommended"]:
        print(f"  - {s}")


if __name__ == "__main__":
    main()
