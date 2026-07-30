#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
eu-license-checker.py

Verify operator licensing status per EU/EEA jurisdiction.

Given an operator's list of held licenses, this tool:
  - Determines in which jurisdictions the operator is licensed
  - Flags jurisdictions where the operator requires a local license but does not hold one
  - Reports channelization-rate thresholds and key compliance obligations per market
  - Produces a JSON compliance report

Usage:
    python eu-license-checker.py --licenses MGA UKGC --player-country SE
    python eu-license-checker.py --licenses MGA UKGC SE DK --report-all
    python eu-license-checker.py --list-markets

Dependencies: none (stdlib only)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class LicenseStatus(str, Enum):
    OPEN = "open"                  # Open licensing — any qualifying operator may apply
    MONOPOLY = "monopoly"          # State monopoly; private online not permitted
    RESTRICTED = "restricted"      # Licensing limited by product type or quota
    PROHIBITED = "prohibited"      # Online gambling prohibited
    TRANSITIONAL = "transitional"  # Regulatory framework in transition


@dataclass
class JurisdictionProfile:
    code: str                          # ISO 3166-1 alpha-2 or custom
    name: str
    regulator: str
    regulator_url: str
    license_code: str                  # Short license identifier used in --licenses
    status: LicenseStatus
    products_covered: list[str]        # e.g. ["casino", "sports", "poker", "bingo"]
    local_license_required: bool       # True = must hold local license to serve players
    eu_passport_accepted: bool         # False = no EU mutual recognition; local license mandatory
    tax_model: str                     # "GGR", "POC", "turnover", "none"
    tax_rate: str                      # e.g. "21% GGR"
    channelization_target: Optional[float]  # Regulator's target share for licensed operators (0–1)
    key_obligations: list[str]
    self_exclusion_registry: Optional[str]
    verification_method: Optional[str]
    notes: str = ""


# ---------------------------------------------------------------------------
# Market definitions
# ---------------------------------------------------------------------------

JURISDICTIONS: dict[str, JurisdictionProfile] = {
    "GB": JurisdictionProfile(
        code="GB",
        name="United Kingdom",
        regulator="UK Gambling Commission (UKGC)",
        regulator_url="https://www.gamblingcommission.gov.uk",
        license_code="UKGC",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo", "lottery"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="POC",
        tax_rate="15% GPT (General Pool Betting Tax); 21% Remote Gaming Duty",
        channelization_target=0.97,
        key_obligations=[
            "Point-of-consumption license mandatory for all GB-facing operators",
            "Affordability checks (enhanced) from 2024 LCCP update",
            "GAMSTOP self-exclusion integration mandatory",
            "Direct debit and credit card deposit restrictions",
            "RTS 13 geo-blocking for unlicensed jurisdictions",
            "Annual compliance report submission",
            "Player identity verification at account opening (KYCS)",
        ],
        self_exclusion_registry="GAMSTOP",
        verification_method="KYC (passport/driving licence + bank statement)",
        notes="Most mature regulated market. Post-Brexit: MGA/Gibraltar licenses no longer passportable. Operators must hold a UKGC Operating Licence.",
    ),
    "MT": JurisdictionProfile(
        code="MT",
        name="Malta",
        regulator="Malta Gaming Authority (MGA)",
        regulator_url="https://www.mga.org.mt",
        license_code="MGA",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo", "lottery", "B2B"],
        local_license_required=True,
        eu_passport_accepted=False,  # MGA is EU but does not passport into other EU markets
        tax_model="GGR",
        tax_rate="5% GGR (B2C); fixed annual fee (B2B)",
        channelization_target=None,
        key_obligations=[
            "B2B and B2C licensing available",
            "Beneficial owner disclosure (UBO register)",
            "AML compliance officer mandatory",
            "Segregated player funds (Level 2 minimum)",
            "RNG certification from approved testing lab",
            "Quarterly financial reporting",
        ],
        self_exclusion_registry="ReSPONSe (Malta national registry)",
        verification_method="KYC (government-issued ID + proof of address)",
        notes="Primary B2B licensing hub. MGA license does not permit serving players in markets with local licensing requirements (SE, DE, NL, IT, etc.) without separate local licenses.",
    ),
    "GI": JurisdictionProfile(
        code="GI",
        name="Gibraltar",
        regulator="Gibraltar Gambling Commissioner (GGC)",
        regulator_url="https://www.gibraltar.gov.gi/gambling",
        license_code="GI",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="1% GGR (capped at £425,000/year)",
        channelization_target=None,
        key_obligations=[
            "Fit and proper test for directors and shareholders",
            "Substance requirements: meaningful operations in Gibraltar",
            "Annual audit by approved auditor",
            "Segregated player funds",
        ],
        self_exclusion_registry=None,
        verification_method="KYC (government-issued ID + source of funds for high-value players)",
        notes="Compact jurisdiction. Low tax rate attractive for large operators. Post-Brexit: lost EU/EEA passporting. Must hold local licenses for EU markets.",
    ),
    "IM": JurisdictionProfile(
        code="IM",
        name="Isle of Man",
        regulator="Gambling Supervision Commission (GSC)",
        regulator_url="https://www.gov.im/categories/business-and-industries/gambling-and-e-gaming/",
        license_code="GSC",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo", "lottery"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="1.5% GGR",
        channelization_target=None,
        key_obligations=[
            "Isle of Man substance requirements",
            "Segregated player funds mandatory",
            "Technical audit every two years",
            "Responsible gambling tools: deposit limits, self-exclusion",
        ],
        self_exclusion_registry=None,
        verification_method="KYC",
        notes="Reputable offshore jurisdiction. Popular with B2C operators serving non-locally-regulated markets.",
    ),
    "SE": JurisdictionProfile(
        code="SE",
        name="Sweden",
        regulator="Spelinspektionen",
        regulator_url="https://www.spelinspektionen.se",
        license_code="SE",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="18% GGR",
        channelization_target=0.90,
        key_obligations=[
            "Re-regulated January 2019 (Spellagen 2018:1138)",
            "Swedish Gambling Authority (Spelinspektionen) license mandatory",
            "Spelpaus self-exclusion integration mandatory",
            "Deposit limits mandatory at account opening",
            "Bonus restrictions: one welcome bonus per player per operator",
            "Marketing restrictions: 'moderate' advertising standard",
            ".se domain or localised Swedish-language service may be required",
            "Swedish BankID for player identity verification",
        ],
        self_exclusion_registry="Spelpaus",
        verification_method="Swedish BankID (electronic national ID)",
        notes="Re-regulated market with strict responsible gambling requirements. Unlicensed operators may be blacklisted by Swedish payment processors.",
    ),
    "DK": JurisdictionProfile(
        code="DK",
        name="Denmark",
        regulator="Spillemyndigheden",
        regulator_url="https://www.spillemyndigheden.dk",
        license_code="DK",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="20% GGR",
        channelization_target=0.85,
        key_obligations=[
            "Danish Gambling Authority license required",
            "ROFUS self-exclusion integration mandatory",
            "Danish language interface required",
            "Responsible gambling tools (deposit limits, cool-off, self-exclusion)",
            ".dk domain required or localised service",
            "Marketing restrictions apply",
        ],
        self_exclusion_registry="ROFUS",
        verification_method="NemID/MitID (Danish national eID)",
        notes="Open licensing since 2012. One of the most established regulated EU markets.",
    ),
    "IT": JurisdictionProfile(
        code="IT",
        name="Italy",
        regulator="Agenzia delle Dogane e dei Monopoli (ADM)",
        regulator_url="https://www.adm.gov.it",
        license_code="IT",
        status=LicenseStatus.RESTRICTED,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="25% GGR (casino/poker); 22% GGR (sports)",
        channelization_target=0.75,
        key_obligations=[
            "ADM (formerly AAMS) license mandatory",
            ".it domain required for Italian-facing services",
            "Technical standards compliance (mandatory certifications)",
            "Player fund segregation",
            "Responsible gambling: self-exclusion, spending limits",
            "Italian language interface required",
            "Heavy compliance documentation requirements",
            "No advertising restrictions eased under 2018 Dignity Decree (\"Decreto Dignità\") ban on gambling advertising — comprehensively prohibited",
        ],
        self_exclusion_registry="AAMS self-exclusion register",
        verification_method="Codice Fiscale (Italian tax code) + government ID",
        notes="High-compliance, high-tax market. Advertising ban (Decreto Dignità 2018) severely restricts marketing. Licences granted by tender; current holders until 2024 renewal.",
    ),
    "ES": JurisdictionProfile(
        code="ES",
        name="Spain",
        regulator="Dirección General de Ordenación del Juego (DGOJ)",
        regulator_url="https://www.ordenacionjuego.es",
        license_code="ES",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="20% GGR",
        channelization_target=0.70,
        key_obligations=[
            "DGOJ national license required",
            "Autonomous community restrictions: Catalonia (DGOJ+Catalan permit) and Basque Country operate additional layers",
            "Responsible gambling: RGIAJ self-exclusion integration",
            "Advertising restrictions (Royal Decree 958/2020): watershed hours, no celebrity endorsements",
            "Spanish language interface",
            "Player identity verification at registration",
        ],
        self_exclusion_registry="RGIAJ (Registro General de Interdicciones de Acceso al Juego)",
        verification_method="DNI/NIE (Spanish national ID/foreigner ID number)",
        notes="National license covers most of Spain; Catalonia and Basque Country have additional regional requirements. Advertising heavily restricted since 2020.",
    ),
    "FR": JurisdictionProfile(
        code="FR",
        name="France",
        regulator="Autorité Nationale des Jeux (ANJ)",
        regulator_url="https://www.anj.fr",
        license_code="FR",
        status=LicenseStatus.RESTRICTED,
        products_covered=["sports", "horse racing", "poker"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="7.5% GGR sports/poker; varies by product",
        channelization_target=0.55,
        key_obligations=[
            "ANJ license required (formerly ARJEL)",
            "Online casino PROHIBITED — sports betting, horse racing, and poker only",
            "Ring-fenced liquidity: French poker players only in French pool",
            "French .fr domain required",
            "Responsible gambling: self-exclusion via AUMS register",
            "Player fund segregation",
            "Advertising restrictions",
        ],
        self_exclusion_registry="AUMS (Autorisations ou interdictions aux jeux)",
        verification_method="Government-issued ID + proof of address",
        notes="No online casino licensing available — poker, sports betting, and horse racing only. French market remains ring-fenced for poker liquidity.",
    ),
    "DE": JurisdictionProfile(
        code="DE",
        name="Germany",
        regulator="Glücksspielbehörde (GGL)",
        regulator_url="https://www.ggl.de",
        license_code="DE",
        status=LicenseStatus.OPEN,
        products_covered=["sports", "poker", "slots (restricted)"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="turnover",
        tax_rate="5.3% of stakes (sports/poker); virtual slots: different rate",
        channelization_target=0.80,
        key_obligations=[
            "GGL (Gemeinsame Glücksspielbehörde der Länder) license mandatory from 2021 Interstate Treaty",
            "Slot limits: €1/spin maximum bet, €1,000/month deposit limit",
            "Panic button / deposit limits mandatory at account opening",
            "No autoplay on slots",
            "OASIS self-exclusion integration mandatory",
            "Player session time limits (€1 per spin, 5 sec minimum spin interval)",
            "No live casino permitted under current framework (2023)",
            "No simultaneous multi-table gambling",
            "Monthly loss limit €1,000 across all licensed operators (cross-operator tracking via LUGAS)",
        ],
        self_exclusion_registry="OASIS (national cross-operator exclusion system)",
        verification_method="Government-issued ID; SCHUFA credit check for deposit limit verification",
        notes="New Interstate Treaty (Glücksspielstaatsvertrag 2021) created GGL as central regulator. Highly restrictive slot rules. LUGAS system tracks player activity across all licensed operators.",
    ),
    "NL": JurisdictionProfile(
        code="NL",
        name="Netherlands",
        regulator="Kansspelautoriteit (KSA)",
        regulator_url="https://www.kansspelautoriteit.nl",
        license_code="NL",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="29.5% GGR",
        channelization_target=0.80,
        key_obligations=[
            "KSA license required (market opened October 2021)",
            "Cruks self-exclusion integration mandatory (national exclusion register)",
            "Strict advertising restrictions: no untargeted advertising, no celebrity endorsements",
            "Maximum €700/month deposit limit during first 30 days of account",
            "Player identification before deposit",
            "iDIN (Dutch bank-based identity verification) strongly preferred",
            "Responsible gambling risk analysis per player (FRISS or equivalent)",
            "Cooling-off period 24h for self-exclusion requests",
        ],
        self_exclusion_registry="Cruks",
        verification_method="iDIN (bank-based eID) or government-issued ID",
        notes="Market opened October 2021. Strict advertising rules led to high-profile fines. KSA actively monitors and enforces; channelization target of 80% under review.",
    ),
    "PT": JurisdictionProfile(
        code="PT",
        name="Portugal",
        regulator="Serviço de Regulação e Inspeção de Jogos (SRIJ)",
        regulator_url="https://www.srij.turismodeportugal.pt",
        license_code="PT",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="15–30% GGR (varies by product)",
        channelization_target=None,
        key_obligations=[
            "SRIJ license required",
            "Ring-fenced poker liquidity (Portuguese players only)",
            "Responsible gambling tools mandatory",
            "Player fund segregation",
            "Portuguese language interface",
        ],
        self_exclusion_registry="SRIJ self-exclusion register",
        verification_method="Portuguese NIF (tax identification number) + government ID",
        notes="Regulated since 2015. Poker liquidity ring-fenced; Portugal did not join EU liquidity sharing initially.",
    ),
    "GR": JurisdictionProfile(
        code="GR",
        name="Greece",
        regulator="Hellenic Gaming Commission (HGC / EEEP)",
        regulator_url="https://www.gamingcommission.gov.gr",
        license_code="GR",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="35% GGR",
        channelization_target=None,
        key_obligations=[
            "HGC/EEEP license required (licensing system updated 2020)",
            "High GGR tax rate",
            "Responsible gambling tools mandatory",
            "Technical standards compliance",
            "Player fund segregation",
        ],
        self_exclusion_registry="EEEP self-exclusion register",
        verification_method="Greek AMKA (social security number) + government ID",
        notes="Re-opened licensing in 2020. High tax rate. Enforcement against unlicensed operators improving.",
    ),
    "RO": JurisdictionProfile(
        code="RO",
        name="Romania",
        regulator="Oficiul Național pentru Jocuri de Noroc (ONJN)",
        regulator_url="https://onjn.gov.ro",
        license_code="RO",
        status=LicenseStatus.OPEN,
        products_covered=["casino", "sports", "poker", "bingo", "lottery"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="16% GGR (online)",
        channelization_target=None,
        key_obligations=[
            "ONJN license required",
            "Technical standards compliance with ONJN specifications",
            "Romanian language interface",
            "Player fund segregation",
            "Responsible gambling tools",
            "Local fiscal representative required",
        ],
        self_exclusion_registry="ONJN self-exclusion register",
        verification_method="Romanian CNP (personal numeric code) + government ID",
        notes="Romania has detailed technical standards that can slow licensing. ONJN enforces actively.",
    ),
    "BE": JurisdictionProfile(
        code="BE",
        name="Belgium",
        regulator="Belgian Gaming Commission (BGC)",
        regulator_url="https://www.gamingcommission.be",
        license_code="BE",
        status=LicenseStatus.RESTRICTED,
        products_covered=["casino", "sports", "poker", "bingo"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="GGR",
        tax_rate="11% GGR",
        channelization_target=0.60,
        key_obligations=[
            "BGC Class IV license required for online operations",
            "Must be linked to a land-based Belgian casino (Class I) or betting office (Class III)",
            "Strict advertising restrictions: prohibition on bonus advertising since 2023",
            "Player verification via eID (Belgian electronic identity card)",
            "EPIS self-exclusion integration mandatory",
            "Deposit limits and spending limits mandatory",
            "No unlicensed B2B software providers permitted",
        ],
        self_exclusion_registry="EPIS (Excluded Persons Information System)",
        verification_method="Belgian eID (electronic identity card)",
        notes="Market is restricted: online license requires link to land-based operation. Very strict advertising rules. Channelization rate among lowest in EU.",
    ),
    "FI": JurisdictionProfile(
        code="FI",
        name="Finland",
        regulator="Veikkaus Oy (state monopoly) / pending: Lotteriinspektionen transition",
        regulator_url="https://www.veikkaus.fi",
        license_code="FI",
        status=LicenseStatus.MONOPOLY,
        products_covered=["casino", "sports", "poker", "bingo", "lottery"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="none",  # monopoly operator pays profit to state
        tax_rate="Veikkaus monopoly — 100% profits to state",
        channelization_target=None,
        key_obligations=[
            "Currently state monopoly: Veikkaus Oy holds exclusive rights",
            "Proposed reform: open licensing framework planned for 2027",
            "Private operators currently prohibited from marketing to Finnish players",
            "Payment blocking enforced against unlicensed operators",
        ],
        self_exclusion_registry="Veikkaus self-exclusion",
        verification_method="Finnish bank ID (Tupas/Suomi.fi)",
        notes="Monopoly framework under EU pressure. Government has committed to open licensing by 2027. Private operators serving Finnish players without a license risk payment blocking.",
    ),
    "NO": JurisdictionProfile(
        code="NO",
        name="Norway",
        regulator="Lotteritilsynet (Lottstift)",
        regulator_url="https://lottstift.no",
        license_code="NO",
        status=LicenseStatus.MONOPOLY,
        products_covered=["casino", "sports", "lottery"],
        local_license_required=True,
        eu_passport_accepted=False,
        tax_model="none",
        tax_rate="Norsk Tipping monopoly",
        channelization_target=None,
        key_obligations=[
            "State monopoly: Norsk Tipping (sports/casino) and Norsk Rikstoto (horse racing)",
            "Strict payment blocking via Lotteritilsynet orders to Norwegian banks",
            "Not an EEA issue (Norway is EEA but gambling exempt from EEA services rules)",
            "Advertising ban on unlicensed gambling strictly enforced",
        ],
        self_exclusion_registry="Norsk Tipping self-exclusion",
        verification_method="Norwegian BankID",
        notes="Norway is EEA but maintains strict state monopoly. Lotteritilsynet issues payment blocking orders. Not expected to open market in near term.",
    ),
}

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def check_licensing_status(
    held_licenses: list[str],
    player_country: str,
) -> dict:
    """
    For a given player's country, determine whether the operator is licensed
    to serve them based on the held_licenses list.
    """
    country = player_country.upper()
    profile = JURISDICTIONS.get(country)

    if not profile:
        return {
            "country": country,
            "status": "unknown",
            "message": f"No compliance data available for country code '{country}'. Manual review required.",
        }

    held = [lic.upper() for lic in held_licenses]
    license_code = profile.license_code.upper()

    if profile.status == LicenseStatus.MONOPOLY:
        return {
            "country": country,
            "name": profile.name,
            "regulator": profile.regulator,
            "status": "prohibited",
            "licensed": False,
            "message": (
                f"{profile.name} operates a state monopoly ({profile.regulator}). "
                "Private online operators are not licensed. Serving players in this "
                "jurisdiction exposes the operator to regulatory and payment-processing risk."
            ),
            "self_exclusion_registry": profile.self_exclusion_registry,
            "notes": profile.notes,
        }

    if profile.status == LicenseStatus.PROHIBITED:
        return {
            "country": country,
            "name": profile.name,
            "regulator": profile.regulator,
            "status": "prohibited",
            "licensed": False,
            "message": f"Online gambling is prohibited in {profile.name}.",
            "notes": profile.notes,
        }

    if license_code in held:
        return {
            "country": country,
            "name": profile.name,
            "regulator": profile.regulator,
            "license_required": license_code,
            "status": "compliant",
            "licensed": True,
            "tax_model": profile.tax_model,
            "tax_rate": profile.tax_rate,
            "channelization_target": profile.channelization_target,
            "key_obligations": profile.key_obligations,
            "self_exclusion_registry": profile.self_exclusion_registry,
            "verification_method": profile.verification_method,
            "products_covered": profile.products_covered,
            "message": (
                f"Operator holds required {license_code} license for {profile.name}. "
                "Ensure all jurisdiction-specific obligations listed are satisfied."
            ),
            "notes": profile.notes,
        }
    else:
        return {
            "country": country,
            "name": profile.name,
            "regulator": profile.regulator,
            "license_required": license_code,
            "status": "non_compliant",
            "licensed": False,
            "held_licenses": held,
            "eu_passport_accepted": profile.eu_passport_accepted,
            "message": (
                f"Operator does NOT hold a {license_code} license required to serve players in {profile.name}. "
                f"EU passporting is {'accepted' if profile.eu_passport_accepted else 'NOT accepted'} — "
                f"a local {license_code} license is mandatory. "
                f"Serving {profile.name} players without this license violates local law and risks "
                "payment blocking, domain blocking, and regulatory sanctions."
            ),
            "regulator_url": profile.regulator_url,
            "notes": profile.notes,
        }


def report_all_markets(held_licenses: list[str]) -> list[dict]:
    """Run licensing check for all known jurisdictions."""
    results = []
    for country_code in sorted(JURISDICTIONS):
        result = check_licensing_status(held_licenses, country_code)
        results.append(result)
    return results


def print_summary_table(results: list[dict]) -> None:
    """Print a human-readable compliance summary table."""
    col_w = [6, 28, 15, 10]
    header = (
        f"{'Code':<{col_w[0]}} {'Market':<{col_w[1]}} {'License Needed':<{col_w[2]}} {'Status':<{col_w[3]}}"
    )
    print()
    print(header)
    print("-" * sum(col_w))
    for r in results:
        status_display = {
            "compliant": "COMPLIANT",
            "non_compliant": "MISSING",
            "prohibited": "MONOPOLY/PROHIBITED",
            "unknown": "UNKNOWN",
        }.get(r.get("status", "unknown"), r.get("status", "?"))

        lic = r.get("license_required", r.get("status", ""))
        print(
            f"{r.get('country', '??'):<{col_w[0]}} "
            f"{r.get('name', 'Unknown')[:col_w[1]-1]:<{col_w[1]}} "
            f"{lic[:col_w[2]-1]:<{col_w[2]}} "
            f"{status_display}"
        )
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="EU/EEA gambling license compliance checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "--licenses",
        nargs="+",
        default=[],
        help="Space-separated list of license codes the operator holds (e.g. MGA UKGC SE DK)",
    )
    p.add_argument(
        "--player-country",
        metavar="CC",
        help="ISO 3166-1 alpha-2 country code of the player (e.g. SE, DE, NL)",
    )
    p.add_argument(
        "--report-all",
        action="store_true",
        help="Report compliance status across all known jurisdictions",
    )
    p.add_argument(
        "--list-markets",
        action="store_true",
        help="List all markets in the database and exit",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )
    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_markets:
        print(f"\nKnown markets ({len(JURISDICTIONS)} total):\n")
        for code, profile in sorted(JURISDICTIONS.items()):
            print(
                f"  {code:4s}  {profile.license_code:8s}  [{profile.status.value:14s}]  {profile.name}"
            )
        print()
        return 0

    if not args.licenses and not args.player_country and not args.report_all:
        parser.print_help()
        return 1

    if args.report_all:
        results = report_all_markets(args.licenses)
        if args.json:
            print(json.dumps(results, indent=2, default=str))
        else:
            print_summary_table(results)
            non_compliant = [r for r in results if r.get("status") == "non_compliant"]
            if non_compliant:
                print(f"WARNING: {len(non_compliant)} market(s) require local licenses not held:")
                for r in non_compliant:
                    print(f"  - {r['country']} ({r['name']}): needs {r['license_required']}")
                print()
        return 0

    if args.player_country:
        result = check_licensing_status(args.licenses, args.player_country)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"\nCompliance check for player country: {result.get('name', args.player_country.upper())}")
            print(f"Status: {result.get('status', 'unknown').upper()}")
            print(f"Message: {result.get('message', '')}")
            if result.get("key_obligations"):
                print("\nKey Obligations:")
                for obligation in result["key_obligations"]:
                    print(f"  - {obligation}")
            if result.get("self_exclusion_registry"):
                print(f"\nSelf-exclusion registry: {result['self_exclusion_registry']}")
            if result.get("verification_method"):
                print(f"Verification method: {result['verification_method']}")
            if result.get("notes"):
                print(f"\nNotes: {result['notes']}")
            print()

        return 0 if result.get("status") == "compliant" else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
