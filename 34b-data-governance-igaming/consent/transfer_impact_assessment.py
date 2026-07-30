# Companion code for "The Backend of Luck" - Chapter 34b, Data Governance for iGaming Platforms.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Transfer Impact Assessment (TIA) — Schrems II Compliance Framework.

Regulatory References:
  - CJEU Case C-311/18 (Schrems II), 16 July 2020 — invalidation of Privacy Shield
  - GDPR (EU) 2016/679, Chapter V (Arts. 44–49) — international data transfers
  - EDPB Recommendations 01/2020 on measures supplementing transfer tools
  - EDPB Recommendations 02/2020 on European Essential Guarantees
  - Standard Contractual Clauses (SCCs) — Commission Decision (EU) 2021/914
  - UK International Data Transfer Agreement (IDTA) / UK Addendum to SCCs
  - LGPD (Lei 13.709/2018), Art. 33 — international transfer conditions

Schrems II Risk Model (per EDPB Rec. 01/2020 Step 4):
  A transfer is HIGH RISK if the destination country:
    (a) lacks an adequacy decision from the EC, AND
    (b) has broad surveillance legislation (e.g., FISA §702, EO 12333, UK IPA 2016), OR
    (c) lacks an independent judicial authority able to challenge state access.

Sub-processor registry covers common iGaming vendors:
  AWS (us-east-1), Cloudflare (USA), Pragmatic Play (Gibraltar),
  Jumio (Germany/USA), Sentry (USA), SendGrid/Twilio (USA).
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"


class TransferMechanism(str, Enum):
    ADEQUACY = "adequacy_decision"             # Art. 45
    SCC = "standard_contractual_clauses"       # Art. 46(2)(c)
    BCR = "binding_corporate_rules"            # Art. 46(2)(b)
    DEROGATION = "derogation"                  # Art. 49
    IDTA = "uk_idta"                           # UK post-Brexit IDTA
    NONE = "none"


# ---------------------------------------------------------------------------
# Country risk database (EDPB / EC adequacy list as at 2025)
# ---------------------------------------------------------------------------

# Countries with EC adequacy decisions (Art. 45) — transfer freely allowed
_ADEQUACY_COUNTRIES: set[str] = {
    "AD", "AR", "CA", "FO", "GG", "IL", "IM", "JP", "JE", "NZ", "CH", "UY",
    "KR", "GB",   # GB has UK adequacy decision for EU transfers
}

# Countries with known broad surveillance legislation materially affecting
# transfer risk (non-exhaustive reference list for illustrative purposes).
_HIGH_SURVEILLANCE_COUNTRIES: set[str] = {
    "US",   # FISA §702, EO 12333, USA FREEDOM Act
    "CN",   # Cybersecurity Law, National Security Law
    "RU",   # SORM surveillance, FSB obligations
    "IN",   # Information Technology (Amendment) Act 2008 §69
}

_MEDIUM_RISK_COUNTRIES: set[str] = {
    "SG",   # Personal Data Protection Act; some state access powers
    "AU",   # Assistance and Access Act 2018 (TOLA) — compelled assistance
    "CA",   # PIPEDA adequate for EU; CSEC surveillance powers
    "BR",   # LGPD Art. 33 — transfer mechanism required; no adequacy decision yet
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class SupplementaryMeasures:
    """EDPB Rec. 01/2020 — three categories of supplementary measures."""

    # Technical measures (e.g., end-to-end encryption, pseudonymisation)
    technical: list[str] = field(default_factory=list)
    # Contractual measures (e.g., enhanced SCC obligations, audit rights)
    contractual: list[str] = field(default_factory=list)
    # Organisational measures (e.g., data minimisation policies, access controls)
    organisational: list[str] = field(default_factory=list)


@dataclass
class SubProcessor:
    """A third-party processor that receives personal data from the platform."""

    name: str
    service_category: str
    hq_country: str                        # ISO 3166-1 alpha-2
    processing_countries: list[str]        # All countries where data is processed
    transfer_mechanism: TransferMechanism
    personal_data_categories: list[str]
    dpa_reference: Optional[str] = None    # Data Processing Agreement reference
    tia_notes: Optional[str] = None


@dataclass
class TIAResult:
    """Output of a Transfer Impact Assessment for a sub-processor."""

    sub_processor_name: str
    destination_country: str
    risk_level: RiskLevel
    transfer_mechanism: TransferMechanism
    adequacy_decision: bool
    surveillance_risk: bool
    supplementary_measures: SupplementaryMeasures
    assessment_notes: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sub-processor registry
# ---------------------------------------------------------------------------

_SUB_PROCESSOR_REGISTRY: dict[str, SubProcessor] = {
    "aws": SubProcessor(
        name="Amazon Web Services",
        service_category="Cloud Infrastructure / IaaS",
        hq_country="US",
        processing_countries=["US", "IE", "DE", "SG"],
        transfer_mechanism=TransferMechanism.SCC,
        personal_data_categories=["All categories — infrastructure layer"],
        dpa_reference="AWS DPA 2024.1",
        tia_notes="EU-US Data Privacy Framework participant as of 2023; SCC Annex II required.",
    ),
    "cloudflare": SubProcessor(
        name="Cloudflare Inc.",
        service_category="CDN / DDoS Protection / Bot Management",
        hq_country="US",
        processing_countries=["US", "DE", "GB", "SG", "BR"],
        transfer_mechanism=TransferMechanism.SCC,
        personal_data_categories=["IP addresses", "HTTP headers", "TLS metadata"],
        dpa_reference="Cloudflare DPA v3.0",
        tia_notes="EU-US DPF participant; edge node processing minimises data at rest in US.",
    ),
    "pragmatic_play": SubProcessor(
        name="Pragmatic Play Ltd.",
        service_category="Game Content Provider (RNG / Live Casino)",
        hq_country="GI",
        processing_countries=["GI", "MT"],
        transfer_mechanism=TransferMechanism.SCC,
        personal_data_categories=["Player ID", "bet history", "game session data"],
        dpa_reference="PP DPA 2023",
        tia_notes="Gibraltar has UK adequacy; MGA-licensed. Low surveillance risk.",
    ),
    "jumio": SubProcessor(
        name="Jumio Corporation",
        service_category="KYC / Identity Verification",
        hq_country="US",
        processing_countries=["US", "DE"],
        transfer_mechanism=TransferMechanism.SCC,
        personal_data_categories=["Government ID scans", "biometric data", "facial images"],
        dpa_reference="Jumio DPA 2024",
        tia_notes=(
            "Biometric data is special-category (GDPR Art. 9). "
            "Enhanced SCC obligations and encryption-in-transit mandatory."
        ),
    ),
    "sentry": SubProcessor(
        name="Sentry (Functional Software Inc.)",
        service_category="Error Monitoring & APM",
        hq_country="US",
        processing_countries=["US"],
        transfer_mechanism=TransferMechanism.SCC,
        personal_data_categories=["Error stack traces", "session IDs", "limited PII in payloads"],
        dpa_reference="Sentry DPA 2024",
        tia_notes="Self-hosted Sentry on EU infrastructure recommended for GDPR compliance.",
    ),
    "sendgrid": SubProcessor(
        name="SendGrid / Twilio Inc.",
        service_category="Transactional & Marketing Email",
        hq_country="US",
        processing_countries=["US", "IE"],
        transfer_mechanism=TransferMechanism.SCC,
        personal_data_categories=["Email addresses", "open/click events", "device info"],
        dpa_reference="Twilio DPA 2024",
        tia_notes="EU-US DPF participant. EU regional sending available to minimise US exposure.",
    ),
}


# ---------------------------------------------------------------------------
# Assessment logic
# ---------------------------------------------------------------------------


def _derive_supplementary_measures(
    destination_country: str,
    sub_processor: SubProcessor,
) -> SupplementaryMeasures:
    """Derive EDPB Rec. 01/2020 supplementary measures based on risk profile."""
    measures = SupplementaryMeasures()

    if destination_country in _HIGH_SURVEILLANCE_COUNTRIES:
        measures.technical = [
            "End-to-end encryption with keys held outside destination country",
            "Pseudonymisation before transfer where processing purpose permits",
            "TLS 1.3 in transit; AES-256 at rest",
            "Tokenisation of identifiers crossing the border",
        ]
        measures.contractual = [
            "Enhanced SCC Module 1/2/3 as applicable (Decision 2021/914)",
            "Contractual obligation to notify of government access requests",
            "Audit rights and third-party audit reports (SOC 2 Type II, ISO 27001)",
            "Data deletion obligations within 30 days of contract termination",
        ]
        measures.organisational = [
            "Data minimisation — transfer only data strictly necessary for service",
            "Access control matrix limiting processor staff with data access",
            "Incident response SLA < 72 hours for breach notification",
            "Regular TIA review (minimum annually or on regulatory change)",
        ]
    elif destination_country in _MEDIUM_RISK_COUNTRIES:
        measures.technical = [
            "TLS 1.3 in transit; encryption at rest",
            "Pseudonymisation where feasible",
        ]
        measures.contractual = [
            "SCCs or equivalent transfer mechanism in place",
            "Government access notification clause",
        ]
        measures.organisational = [
            "Data minimisation policy",
            "Annual TIA review",
        ]
    else:
        measures.organisational = [
            "Standard DPA in place",
            "Processor compliance monitoring",
        ]

    return measures


def assess_transfer(sub_processor_key: str, destination_country: str) -> TIAResult:
    """Perform a Transfer Impact Assessment for a sub-processor and destination.

    Args:
        sub_processor_key: Key in the registry (e.g. ``"aws"``, ``"jumio"``).
        destination_country: ISO 3166-1 alpha-2 destination country code.

    Returns:
        :class:`TIAResult` with risk level and recommended supplementary measures.

    Raises:
        KeyError: If the sub-processor key is not found in the registry.
    """
    sp = _SUB_PROCESSOR_REGISTRY[sub_processor_key]
    country = destination_country.upper()

    adequacy = country in _ADEQUACY_COUNTRIES
    surveillance_risk = country in _HIGH_SURVEILLANCE_COUNTRIES
    medium_risk = country in _MEDIUM_RISK_COUNTRIES

    notes: list[str] = []
    actions: list[str] = []

    if adequacy:
        risk = RiskLevel.LOW
        notes.append(f"{country} has an EC adequacy decision — free transfer permitted (Art. 45).")
    elif surveillance_risk:
        risk = RiskLevel.HIGH
        notes.append(f"{country} has broad surveillance legislation per EDPB Rec. 02/2020 EEG analysis.")
        notes.append("Transfer must be supported by Art. 46 mechanism + supplementary measures.")
        if sp.transfer_mechanism == TransferMechanism.NONE:
            risk = RiskLevel.PROHIBITED
            actions.append("CRITICAL: No transfer mechanism in place. Transfer must be suspended.")
        else:
            actions.append("Review SCC Annex II technical measures for adequacy.")
            actions.append("Conduct government access request probability analysis.")
    elif medium_risk:
        risk = RiskLevel.MEDIUM
        notes.append(f"{country} presents moderate risk — transfer mechanism required.")
    else:
        risk = RiskLevel.LOW
        notes.append(f"No known elevated surveillance risk for {country}.")

    if sp.tia_notes:
        notes.append(f"Processor note: {sp.tia_notes}")

    supplementary = _derive_supplementary_measures(country, sp)

    logger.info(
        "TIA complete: %s -> %s risk=%s mechanism=%s",
        sp.name,
        country,
        risk.value,
        sp.transfer_mechanism.value,
    )

    return TIAResult(
        sub_processor_name=sp.name,
        destination_country=country,
        risk_level=risk,
        transfer_mechanism=sp.transfer_mechanism,
        adequacy_decision=adequacy,
        surveillance_risk=surveillance_risk,
        supplementary_measures=supplementary,
        assessment_notes=notes,
        recommended_actions=actions,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Transfer Impact Assessment for a sub-processor.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "sub_processor",
        choices=list(_SUB_PROCESSOR_REGISTRY.keys()),
        help="Sub-processor key from registry",
    )
    parser.add_argument(
        "destination_country",
        help="ISO 3166-1 alpha-2 destination country (e.g. US, DE, SG)",
    )
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    logging.basicConfig(level=args.log_level)

    result = assess_transfer(args.sub_processor, args.destination_country)
    output = {
        "sub_processor": result.sub_processor_name,
        "destination_country": result.destination_country,
        "risk_level": result.risk_level.value,
        "transfer_mechanism": result.transfer_mechanism.value,
        "adequacy_decision": result.adequacy_decision,
        "surveillance_risk": result.surveillance_risk,
        "supplementary_measures": {
            "technical": result.supplementary_measures.technical,
            "contractual": result.supplementary_measures.contractual,
            "organisational": result.supplementary_measures.organisational,
        },
        "assessment_notes": result.assessment_notes,
        "recommended_actions": result.recommended_actions,
    }
    print(json.dumps(output, indent=2))
