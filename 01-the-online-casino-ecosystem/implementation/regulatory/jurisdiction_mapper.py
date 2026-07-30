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
iGaming Jurisdiction Mapper - Licensing Requirements, Timelines, and Costs

Comprehensive reference for gambling license requirements across major jurisdictions.
Covers application process, documentation, technical requirements, timelines, and
total cost of licensing.

Usage:
    python jurisdiction_mapper.py --jurisdiction UK,Malta
    python jurisdiction_mapper.py --compare UK,Malta,Curacao
    python jurisdiction_mapper.py --quickest --budget 500000
    python jurisdiction_mapper.py --all --format json
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class LicenseRequirement:
    jurisdiction: str
    full_name: str
    regulator: str
    regulator_website: str
    license_types: list
    # Application
    application_fee_eur: float
    annual_fee_eur: float
    capital_requirement_eur: float  # minimum share capital / reserves
    bank_guarantee_eur: float
    total_first_year_cost_eur: float  # all-in estimate
    # Timeline
    application_timeline_months: int
    pre_application_months: int  # preparation time
    total_timeline_months: int
    # Requirements
    local_entity_required: bool
    local_director_required: bool
    local_office_required: bool
    local_server_required: bool
    key_person_background_check: bool
    source_of_funds_proof: bool
    business_plan_required: bool
    technical_audit_required: bool
    rng_certification_required: bool
    aml_program_required: bool
    responsible_gambling_required: bool
    # Technical
    technical_standards: list
    approved_testing_labs: list
    data_retention_years: int
    reporting_frequency: str
    # Tax
    tax_rate_pct: float
    tax_base: str
    minimum_rtp_pct: Optional[float]
    # Documentation
    required_documents: list
    # Products
    allowed_products: list
    restricted_products: list
    # Market access
    target_markets: list  # markets you can serve with this license
    restricted_markets: list  # markets that don't accept this license


LICENSES = {
    "UK": LicenseRequirement(
        jurisdiction="UK", full_name="United Kingdom",
        regulator="UK Gambling Commission (UKGC)",
        regulator_website="https://www.gamblingcommission.gov.uk",
        license_types=["Remote Casino", "Remote Bingo", "Remote Betting",
                       "Remote General Betting", "Remote Pool Betting",
                       "Remote Gambling Software"],
        application_fee_eur=150_000,
        annual_fee_eur=85_000,
        capital_requirement_eur=500_000,
        bank_guarantee_eur=0,
        total_first_year_cost_eur=900_000,
        application_timeline_months=6,
        pre_application_months=3,
        total_timeline_months=9,
        local_entity_required=False,
        local_director_required=False,
        local_office_required=False,
        local_server_required=False,
        key_person_background_check=True,
        source_of_funds_proof=True,
        business_plan_required=True,
        technical_audit_required=True,
        rng_certification_required=True,
        aml_program_required=True,
        responsible_gambling_required=True,
        technical_standards=["Remote gambling and software technical standards (RTS)",
                             "LCCP conditions and codes of practice"],
        approved_testing_labs=["eCOGRA", "GLI", "BMM", "NMi", "QUINEL"],
        data_retention_years=3,
        reporting_frequency="monthly + annual",
        tax_rate_pct=21.0,
        tax_base="GGR (point of consumption)",
        minimum_rtp_pct=None,
        required_documents=[
            "Application form (all sections)",
            "Personal declarations for all key persons",
            "DBS checks for UK residents / equivalent for non-UK",
            "Detailed business plan (3-year projections)",
            "Anti-money laundering policies and procedures",
            "Responsible gambling strategy and implementation plan",
            "IT security policies (ISO 27001 recommended)",
            "Terms and conditions (draft)",
            "Privacy policy (GDPR compliant)",
            "Game fairness testing certificates",
            "Platform technical documentation",
            "Disaster recovery plan",
            "Financial projections and funding evidence",
            "Corporate structure chart",
            "Shareholder register (all beneficial owners > 3%)",
        ],
        allowed_products=["slots", "table_games", "live_casino", "sports_betting",
                         "bingo", "poker", "virtual_sports", "lottery"],
        restricted_products=["credit_card_deposits_banned", "reverse_withdrawal_banned",
                            "auto_play_restrictions", "stake_limits_pending"],
        target_markets=["United Kingdom"],
        restricted_markets=["Cannot serve non-UK from UK license alone"],
    ),
    "Malta": LicenseRequirement(
        jurisdiction="Malta", full_name="Malta (MGA)",
        regulator="Malta Gaming Authority (MGA)",
        regulator_website="https://www.mga.org.mt",
        license_types=["B2C Type 1 (Casino/Table Games)",
                       "B2C Type 2 (Fixed-odds Betting)",
                       "B2C Type 3 (P2P - Poker/Bingo/Betting Exchange)",
                       "B2B Critical Supply"],
        application_fee_eur=5_000,
        annual_fee_eur=25_000,
        capital_requirement_eur=240_000,
        bank_guarantee_eur=100_000,
        total_first_year_cost_eur=450_000,
        application_timeline_months=4,
        pre_application_months=2,
        total_timeline_months=6,
        local_entity_required=True,
        local_director_required=True,
        local_office_required=True,
        local_server_required=False,
        key_person_background_check=True,
        source_of_funds_proof=True,
        business_plan_required=True,
        technical_audit_required=True,
        rng_certification_required=True,
        aml_program_required=True,
        responsible_gambling_required=True,
        technical_standards=["MGA Technical Setup Requirements",
                             "Gaming Premises Directive",
                             "Player Protection Directive 2018"],
        approved_testing_labs=["eCOGRA", "GLI", "BMM", "NMi", "iTech Labs",
                               "SIQ", "Quinel"],
        data_retention_years=5,
        reporting_frequency="monthly",
        tax_rate_pct=5.0,
        tax_base="GGR (with EUR 466K annual cap per license type)",
        minimum_rtp_pct=92.0,
        required_documents=[
            "Application form with all annexes",
            "Certificate of incorporation (Malta company)",
            "Memorandum and Articles of Association",
            "Shareholder register and UBO declarations",
            "Key function holder declarations",
            "Police conduct certificates (all directors/UBOs)",
            "System audit report (approved testing lab)",
            "Game audit report with RTP verification",
            "AML/CFT risk assessment and procedures",
            "Responsible gaming policy",
            "Data protection impact assessment",
            "Player terms and conditions",
            "Business plan (3 years)",
            "Audited financial statements",
            "Player funds segregation evidence",
            "Hosting / server architecture documentation",
        ],
        allowed_products=["slots", "table_games", "live_casino", "sports_betting",
                         "bingo", "poker", "betting_exchange", "virtual_sports"],
        restricted_products=[],
        target_markets=["EU/EEA (passporting)", "Non-regulated markets",
                       "Various MGA-accepted jurisdictions"],
        restricted_markets=["UK (need UKGC)", "US states", "France (need ANJ)",
                           "Italy (need ADM)", "Spain (need DGOJ)"],
    ),
    "Gibraltar": LicenseRequirement(
        jurisdiction="Gibraltar", full_name="Gibraltar",
        regulator="Gibraltar Gambling Commissioner",
        regulator_website="https://www.gibraltar.gov.gi/gambling",
        license_types=["Remote Gambling License"],
        application_fee_eur=100_000,
        annual_fee_eur=100_000,
        capital_requirement_eur=500_000,
        bank_guarantee_eur=0,
        total_first_year_cost_eur=850_000,
        application_timeline_months=6,
        pre_application_months=3,
        total_timeline_months=9,
        local_entity_required=True,
        local_director_required=True,
        local_office_required=True,
        local_server_required=False,
        key_person_background_check=True,
        source_of_funds_proof=True,
        business_plan_required=True,
        technical_audit_required=True,
        rng_certification_required=True,
        aml_program_required=True,
        responsible_gambling_required=True,
        technical_standards=["Gibraltar Remote Technical Standards"],
        approved_testing_labs=["eCOGRA", "GLI", "BMM"],
        data_retention_years=5,
        reporting_frequency="quarterly",
        tax_rate_pct=0.15,
        tax_base="Revenue (1% of turnover up to GBP 42.5K/year)",
        minimum_rtp_pct=None,
        required_documents=[
            "Application form", "Company incorporation docs",
            "Director CVs and police checks", "Business plan",
            "Technical architecture", "AML procedures",
            "Responsible gambling policy", "Financial projections",
        ],
        allowed_products=["slots", "table_games", "live_casino", "sports_betting",
                         "bingo", "poker", "virtual_sports"],
        restricted_products=[],
        target_markets=["UK (with UKGC)", "EU/EEA", "Various global markets"],
        restricted_markets=["US states"],
    ),
    "Curacao": LicenseRequirement(
        jurisdiction="Curacao", full_name="Curacao (new framework 2024+)",
        regulator="Curacao Gaming Control Board (GCB)",
        regulator_website="https://www.gamingcontrolcuracao.org",
        license_types=["Online Casino License", "Online Sports Betting License"],
        application_fee_eur=45_000,
        annual_fee_eur=24_000,
        capital_requirement_eur=100_000,
        bank_guarantee_eur=0,
        total_first_year_cost_eur=200_000,
        application_timeline_months=3,
        pre_application_months=1,
        total_timeline_months=4,
        local_entity_required=True,
        local_director_required=False,
        local_office_required=True,
        local_server_required=False,
        key_person_background_check=True,
        source_of_funds_proof=True,
        business_plan_required=True,
        technical_audit_required=True,
        rng_certification_required=True,
        aml_program_required=True,
        responsible_gambling_required=True,
        technical_standards=["New GCB Technical Standards (2024)",
                             "AML/CFT compliance framework"],
        approved_testing_labs=["GLI", "BMM", "iTech Labs", "eCOGRA"],
        data_retention_years=5,
        reporting_frequency="monthly",
        tax_rate_pct=3.0,
        tax_base="GGR",
        minimum_rtp_pct=None,
        required_documents=[
            "Application form (new GCB format)",
            "Company incorporation (Curacao entity)",
            "UBO declarations", "AML procedures",
            "Technical system audit", "RNG certificates",
            "Responsible gambling framework",
            "Business plan", "Financial projections",
        ],
        allowed_products=["slots", "table_games", "live_casino", "sports_betting",
                         "bingo", "poker", "virtual_sports", "esports"],
        restricted_products=[],
        target_markets=["Non-regulated markets", "Asia", "Latin America", "Africa"],
        restricted_markets=["UK", "US", "EU locally-regulated markets",
                           "Netherlands", "Australia"],
    ),
    "Isle_of_Man": LicenseRequirement(
        jurisdiction="Isle_of_Man", full_name="Isle of Man",
        regulator="Isle of Man Gambling Supervision Commission (GSC)",
        regulator_website="https://www.gov.im/gambling",
        license_types=["Online Gambling Regulation Act 2001 License",
                       "Network Services License", "Software Supplier License"],
        application_fee_eur=50_000,
        annual_fee_eur=35_000,
        capital_requirement_eur=250_000,
        bank_guarantee_eur=0,
        total_first_year_cost_eur=500_000,
        application_timeline_months=4,
        pre_application_months=2,
        total_timeline_months=6,
        local_entity_required=True,
        local_director_required=True,
        local_office_required=True,
        local_server_required=True,
        key_person_background_check=True,
        source_of_funds_proof=True,
        business_plan_required=True,
        technical_audit_required=True,
        rng_certification_required=True,
        aml_program_required=True,
        responsible_gambling_required=True,
        technical_standards=["OGRA Technical Standards",
                             "GSC Player Protection Regulations"],
        approved_testing_labs=["eCOGRA", "GLI", "BMM", "NMi"],
        data_retention_years=6,
        reporting_frequency="monthly",
        tax_rate_pct=0.0,
        tax_base="Duty: 0.1-1.5% of net yield (tiered)",
        minimum_rtp_pct=None,
        required_documents=[
            "Application form", "Company documents",
            "Personal declaration forms (all key persons)",
            "Police certificates", "Business plan",
            "Technical documentation", "AML program",
            "Responsible gambling policy", "Financial statements",
        ],
        allowed_products=["slots", "table_games", "live_casino", "sports_betting",
                         "bingo", "poker", "virtual_sports"],
        restricted_products=[],
        target_markets=["UK (white-listed)", "Global markets"],
        restricted_markets=["US states"],
    ),
    "Kahnawake": LicenseRequirement(
        jurisdiction="Kahnawake", full_name="Kahnawake (Canada)",
        regulator="Kahnawake Gaming Commission (KGC)",
        regulator_website="https://www.gamingcommission.ca",
        license_types=["Interactive Gaming License",
                       "Client Provider Authorization"],
        application_fee_eur=40_000,
        annual_fee_eur=20_000,
        capital_requirement_eur=50_000,
        bank_guarantee_eur=0,
        total_first_year_cost_eur=150_000,
        application_timeline_months=3,
        pre_application_months=1,
        total_timeline_months=4,
        local_entity_required=False,
        local_director_required=False,
        local_office_required=False,
        local_server_required=True,
        key_person_background_check=True,
        source_of_funds_proof=True,
        business_plan_required=True,
        technical_audit_required=True,
        rng_certification_required=True,
        aml_program_required=True,
        responsible_gambling_required=True,
        technical_standards=["KGC Regulations"],
        approved_testing_labs=["eCOGRA", "GLI", "iTech Labs", "BMM"],
        data_retention_years=5,
        reporting_frequency="monthly",
        tax_rate_pct=0.0,
        tax_base="Fixed fee model (no GGR tax)",
        minimum_rtp_pct=None,
        required_documents=[
            "Application form", "Corporate documents",
            "Key person background checks", "Technical audit",
            "RNG certification", "AML procedures",
        ],
        allowed_products=["slots", "table_games", "live_casino", "sports_betting",
                         "poker", "bingo"],
        restricted_products=[],
        target_markets=["Non-regulated markets", "Canada (limited)"],
        restricted_markets=["US", "UK", "EU regulated markets", "Ontario"],
    ),
}


class JurisdictionMapper:
    """Maps and compares licensing requirements across jurisdictions."""

    def __init__(self):
        self.licenses = LICENSES

    def get_details(self, jurisdiction: str) -> Optional[dict]:
        """Get full details for a jurisdiction."""
        lic = self.licenses.get(jurisdiction)
        if not lic:
            return None
        return {
            "jurisdiction": lic.jurisdiction,
            "full_name": lic.full_name,
            "regulator": lic.regulator,
            "website": lic.regulator_website,
            "license_types": lic.license_types,
            "costs": {
                "application_fee_eur": lic.application_fee_eur,
                "annual_fee_eur": lic.annual_fee_eur,
                "capital_requirement_eur": lic.capital_requirement_eur,
                "bank_guarantee_eur": lic.bank_guarantee_eur,
                "total_first_year_eur": lic.total_first_year_cost_eur,
            },
            "timeline": {
                "preparation_months": lic.pre_application_months,
                "application_months": lic.application_timeline_months,
                "total_months": lic.total_timeline_months,
            },
            "local_requirements": {
                "entity": lic.local_entity_required,
                "director": lic.local_director_required,
                "office": lic.local_office_required,
                "server": lic.local_server_required,
            },
            "compliance_requirements": {
                "background_check": lic.key_person_background_check,
                "source_of_funds": lic.source_of_funds_proof,
                "business_plan": lic.business_plan_required,
                "technical_audit": lic.technical_audit_required,
                "rng_certification": lic.rng_certification_required,
                "aml_program": lic.aml_program_required,
                "responsible_gambling": lic.responsible_gambling_required,
            },
            "tax": {
                "rate_pct": lic.tax_rate_pct,
                "base": lic.tax_base,
                "minimum_rtp_pct": lic.minimum_rtp_pct,
            },
            "technical": {
                "standards": lic.technical_standards,
                "approved_labs": lic.approved_testing_labs,
                "data_retention_years": lic.data_retention_years,
                "reporting_frequency": lic.reporting_frequency,
            },
            "required_documents": lic.required_documents,
            "products": {
                "allowed": lic.allowed_products,
                "restricted": lic.restricted_products,
            },
            "market_access": {
                "target_markets": lic.target_markets,
                "restricted_markets": lic.restricted_markets,
            },
        }

    def compare(self, jurisdictions: list[str]) -> dict:
        """Side-by-side comparison of multiple jurisdictions."""
        comparison = {}
        for j in jurisdictions:
            details = self.get_details(j)
            if details:
                comparison[j] = details
        return comparison

    def find_by_budget(self, max_budget_eur: float, max_months: int = 24) -> list[dict]:
        """Find jurisdictions within budget and timeline constraints."""
        results = []
        for code, lic in self.licenses.items():
            if lic.total_first_year_cost_eur <= max_budget_eur and lic.total_timeline_months <= max_months:
                results.append({
                    "jurisdiction": code,
                    "name": lic.full_name,
                    "total_cost_eur": lic.total_first_year_cost_eur,
                    "timeline_months": lic.total_timeline_months,
                    "tax_rate_pct": lic.tax_rate_pct,
                    "regulator": lic.regulator,
                })
        results.sort(key=lambda x: x["total_cost_eur"])
        return results

    def find_quickest(self) -> list[dict]:
        """Rank jurisdictions by speed to market."""
        results = []
        for code, lic in self.licenses.items():
            results.append({
                "jurisdiction": code,
                "name": lic.full_name,
                "total_months": lic.total_timeline_months,
                "cost_eur": lic.total_first_year_cost_eur,
                "tax_rate_pct": lic.tax_rate_pct,
            })
        results.sort(key=lambda x: x["total_months"])
        return results

    def licensing_roadmap(self, jurisdiction: str) -> Optional[dict]:
        """Generate step-by-step licensing roadmap."""
        lic = self.licenses.get(jurisdiction)
        if not lic:
            return None

        steps = []
        month = 0

        # Phase 1: Preparation
        steps.append({
            "phase": "1. PREPARATION",
            "month_start": 1,
            "month_end": lic.pre_application_months,
            "tasks": [
                "Engage specialized iGaming legal counsel in jurisdiction",
                "Incorporate local entity (if required)" if lic.local_entity_required else None,
                "Appoint local director (if required)" if lic.local_director_required else None,
                "Establish local office (if required)" if lic.local_office_required else None,
                "Open corporate bank account with gambling-friendly bank",
                "Prepare business plan with 3-year financial projections",
                "Draft AML/CFT policies and procedures",
                "Draft responsible gambling strategy",
                "Initiate key person background checks",
                "Engage approved testing laboratory for platform audit",
                "Obtain RNG certification for all game content",
                "Draft player terms and conditions",
                "Prepare technical documentation",
            ],
        })

        # Phase 2: Application
        app_start = lic.pre_application_months + 1
        steps.append({
            "phase": "2. APPLICATION SUBMISSION",
            "month_start": app_start,
            "month_end": app_start,
            "tasks": [
                f"Submit application form to {lic.regulator}",
                f"Pay application fee: EUR {lic.application_fee_eur:,.0f}",
                "Submit all required documentation package",
                f"Deposit capital requirement: EUR {lic.capital_requirement_eur:,.0f}",
                f"Arrange bank guarantee: EUR {lic.bank_guarantee_eur:,.0f}" if lic.bank_guarantee_eur > 0 else None,
                "Submit technical audit report from approved lab",
                "Submit RNG certification documents",
            ],
        })

        # Phase 3: Review
        review_start = app_start + 1
        review_end = app_start + lic.application_timeline_months - 1
        steps.append({
            "phase": "3. REGULATORY REVIEW",
            "month_start": review_start,
            "month_end": review_end,
            "tasks": [
                "Respond to regulator information requests (typically 2-4 rounds)",
                "Additional documentation as requested",
                "Possible in-person interview with key persons",
                "Technical assessment / penetration testing review",
                "Financial suitability verification",
                "Address any conditional approval requirements",
            ],
        })

        # Phase 4: Launch
        steps.append({
            "phase": "4. LICENSE GRANT & LAUNCH",
            "month_start": lic.total_timeline_months,
            "month_end": lic.total_timeline_months + 1,
            "tasks": [
                "Receive conditional or full license",
                "Complete any remaining conditions",
                f"Pay first annual fee: EUR {lic.annual_fee_eur:,.0f}",
                "Configure production environment",
                "Soft launch with limited player pool",
                "Regulator verification of live environment",
                "Full public launch",
                "Begin regulatory reporting cycle",
            ],
        })

        # Clean None values
        for step in steps:
            step["tasks"] = [t for t in step["tasks"] if t is not None]

        return {
            "jurisdiction": jurisdiction,
            "name": lic.full_name,
            "regulator": lic.regulator,
            "total_timeline_months": lic.total_timeline_months,
            "total_cost_eur": lic.total_first_year_cost_eur,
            "roadmap": steps,
            "required_documents": lic.required_documents,
        }

    def print_comparison_table(self, jurisdictions: list[str]):
        """Print formatted comparison table."""
        print(f"\n{'=' * 110}")
        print(f"  JURISDICTION LICENSING COMPARISON")
        print(f"{'=' * 110}")

        metrics = [
            ("Regulator", lambda l: l.regulator[:40]),
            ("Application Fee", lambda l: f"EUR {l.application_fee_eur:>10,.0f}"),
            ("Annual Fee", lambda l: f"EUR {l.annual_fee_eur:>10,.0f}"),
            ("Capital Required", lambda l: f"EUR {l.capital_requirement_eur:>10,.0f}"),
            ("Total Year 1 Cost", lambda l: f"EUR {l.total_first_year_cost_eur:>10,.0f}"),
            ("Timeline (months)", lambda l: f"{l.total_timeline_months:>10}"),
            ("Tax Rate", lambda l: f"{l.tax_rate_pct:>9.1f}%"),
            ("Tax Base", lambda l: l.tax_base[:35]),
            ("Local Entity?", lambda l: "YES" if l.local_entity_required else "no"),
            ("Local Director?", lambda l: "YES" if l.local_director_required else "no"),
            ("Local Server?", lambda l: "YES" if l.local_server_required else "no"),
            ("Data Retention", lambda l: f"{l.data_retention_years} years"),
        ]

        valid_jurisdictions = [j for j in jurisdictions if j in self.licenses]
        col_width = max(20, 100 // len(valid_jurisdictions))

        header = f"  {'Metric':<25}" + "".join(f"{j:>{col_width}}" for j in valid_jurisdictions)
        print(header)
        print(f"  {'-' * (25 + col_width * len(valid_jurisdictions))}")

        for label, extractor in metrics:
            vals = []
            for j in valid_jurisdictions:
                lic = self.licenses[j]
                vals.append(f"{extractor(lic):>{col_width}}")
            print(f"  {label:<25}" + "".join(vals))


def main():
    parser = argparse.ArgumentParser(description="iGaming Jurisdiction Mapper")
    parser.add_argument("--jurisdiction", "-j", type=str,
                        help="Jurisdiction code(s) for details (comma-separated)")
    parser.add_argument("--compare", type=str,
                        help="Compare jurisdictions side by side (comma-separated)")
    parser.add_argument("--quickest", action="store_true",
                        help="Rank by quickest time to market")
    parser.add_argument("--budget", type=float,
                        help="Find jurisdictions within budget (EUR)")
    parser.add_argument("--roadmap", type=str,
                        help="Generate licensing roadmap for jurisdiction")
    parser.add_argument("--all", action="store_true", help="Show all jurisdictions")
    parser.add_argument("--format", choices=["table", "json"], default="table")
    args = parser.parse_args()

    mapper = JurisdictionMapper()
    available = list(LICENSES.keys())

    if args.roadmap:
        roadmap = mapper.licensing_roadmap(args.roadmap)
        if roadmap is None:
            print(f"Unknown jurisdiction: {args.roadmap}. Available: {', '.join(available)}")
            sys.exit(1)
        assert roadmap is not None
        if args.format == "json":
            print(json.dumps(roadmap, indent=2))
        else:
            print(f"\n{'=' * 80}")
            print(f"  LICENSING ROADMAP: {roadmap['name']}")
            print(f"  Regulator: {roadmap['regulator']}")
            print(f"  Total Timeline: {roadmap['total_timeline_months']} months")
            print(f"  Total Cost: EUR {roadmap['total_cost_eur']:,.0f}")
            print(f"{'=' * 80}")
            for step in roadmap["roadmap"]:
                print(f"\n  {step['phase']} (Months {step['month_start']}-{step['month_end']})")
                for task in step["tasks"]:
                    print(f"    [ ] {task}")
            print(f"\n  Required Documents:")
            for doc in roadmap["required_documents"]:
                print(f"    [ ] {doc}")
        return

    if args.budget:
        results = mapper.find_by_budget(args.budget)
        if args.format == "json":
            print(json.dumps(results, indent=2))
        else:
            print(f"\n  Jurisdictions within EUR {args.budget:,.0f} budget:")
            for r in results:
                print(f"    {r['jurisdiction']:>12}: EUR {r['total_cost_eur']:>10,.0f} | "
                      f"{r['timeline_months']} months | Tax: {r['tax_rate_pct']}%")
        return

    if args.quickest:
        results = mapper.find_quickest()
        if args.format == "json":
            print(json.dumps(results, indent=2))
        else:
            print(f"\n  Jurisdictions ranked by speed to market:")
            for r in results:
                print(f"    {r['jurisdiction']:>12}: {r['total_months']:>2} months | "
                      f"EUR {r['cost_eur']:>10,.0f} | Tax: {r['tax_rate_pct']}%")
        return

    if args.compare:
        codes = [c.strip() for c in args.compare.split(",")]
        if args.format == "json":
            print(json.dumps(mapper.compare(codes), indent=2))
        else:
            mapper.print_comparison_table(codes)
        return

    if args.jurisdiction:
        codes = [c.strip() for c in args.jurisdiction.split(",")]
        for code in codes:
            details = mapper.get_details(code)
            if details:
                if args.format == "json":
                    print(json.dumps(details, indent=2))
                else:
                    print(f"\n{'=' * 80}")
                    print(f"  {details['full_name']} - {details['regulator']}")
                    print(f"{'=' * 80}")
                    print(f"  Website: {details['website']}")
                    print(f"  Total Year 1 Cost: EUR {details['costs']['total_first_year_eur']:,.0f}")
                    print(f"  Timeline: {details['timeline']['total_months']} months")
                    print(f"  Tax: {details['tax']['rate_pct']}% on {details['tax']['base']}")
            else:
                print(f"  Unknown jurisdiction: {code}. Available: {', '.join(available)}")
        return

    if args.all:
        mapper.print_comparison_table(available)
        return

    # Default: show all
    mapper.print_comparison_table(available)


if __name__ == "__main__":
    main()
