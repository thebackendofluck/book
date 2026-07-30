# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Data residency classification matrix for iGaming platforms.

Maps each data class to its residency requirement, approved AWS regions per
jurisdiction, retention period, and encryption requirement.

Used by residency_validator.py at runtime and by backup tooling to select
the correct destination region before any data movement.

Jurisdictions covered:
  nj  — New Jersey (US)  — N.J.A.C. 13:69O
  pa  — Pennsylvania (US) — 58 Pa. Code §441a.7
  mi  — Michigan (US)    — Mich. Admin. Code R 432.632
  on  — Ontario (CA)     — AGCO iGO §8.1.3
  uk  — United Kingdom   — UK GDPR / UKGC LCCP
  mt  — Malta            — GDPR + MGA Technical Standards
  ph  — Philippines      — PAGCOR Charter §15.2

Chapter 27 — Data Sovereignty, Residency, and Backup/Recovery
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ResidencyRequirement(Enum):
    """Granularity of the geographic constraint imposed on this data class."""

    JURISDICTION_LOCKED = "jurisdiction_locked"
    """Must remain within the specific US state or provincial boundary.
    Examples: NJ DGE player data, PA game round records."""

    COUNTRY_LOCKED = "country_locked"
    """Must remain within the country — any region in that country is
    acceptable. Examples: AGCO Ontario (Canada), UKGC (UK)."""

    REGION_LOCKED = "region_locked"
    """Must remain within a geographic region, typically the EU/EEA.
    Examples: MGA Malta player PII under GDPR Chapter V."""

    UNRESTRICTED = "unrestricted"
    """No mandatory residency constraint. Data can be stored in any region.
    Applies only to game catalogues and fully anonymised aggregates."""


class DataClass(Enum):
    """Canonical data classification labels used throughout the platform."""

    PLAYER_PII           = "player_pii"
    FINANCIAL_RECORDS    = "financial_records"
    GAME_ROUNDS          = "game_rounds"
    SESSION_DATA         = "session_data"
    KYC_DOCUMENTS        = "kyc_documents"
    SELF_EXCLUSION       = "self_exclusion"
    GAME_CATALOGUE       = "game_catalogue"
    AGGREGATED_ANALYTICS = "aggregated_analytics"


@dataclass
class ResidencyPolicy:
    """Residency and retention policy for a single data class.

    Attributes:
        data_class:          Which data type this policy covers.
        requirement:         How geographically constrained the data is.
        jurisdictions:       Mapping of jurisdiction code → approved AWS region.
                             Empty dict means no per-jurisdiction constraint.
        retention_days:      Minimum retention period (most restrictive across
                             supported jurisdictions).
        encryption_required: Whether at-rest encryption is mandatory for this class.
        notes:               Free-text regulatory reference for audit purposes.
    """

    data_class:           DataClass
    requirement:          ResidencyRequirement
    jurisdictions:        dict[str, str]
    retention_days:       int
    encryption_required:  bool = True
    notes:                str = ""


# ---------------------------------------------------------------------------
# Master policy table
# ---------------------------------------------------------------------------
# Retention figures use the most restrictive value across all supported
# jurisdictions. Update this table when adding a new jurisdiction.
# ---------------------------------------------------------------------------
POLICIES: list[ResidencyPolicy] = [

    ResidencyPolicy(
        data_class=DataClass.PLAYER_PII,
        requirement=ResidencyRequirement.JURISDICTION_LOCKED,
        jurisdictions={
            "nj": "us-east-1",      # Ashburn VA — NJ DGE written approval required
            "pa": "us-east-2",      # Ohio
            "mi": "us-east-2",      # Ohio
            "on": "ca-central-1",   # Toronto
            "uk": "eu-west-2",      # London
            "mt": "eu-central-1",   # Frankfurt (EEA)
        },
        retention_days=2555,        # 7 years — NJ DGE / UKGC / AGCO maximum
        notes="NJ: N.J.A.C. 13:69O-1.2; UKGC: LCCP SR Code 8.1.1; AGCO: iGO §8.1.3",
    ),

    ResidencyPolicy(
        data_class=DataClass.FINANCIAL_RECORDS,
        requirement=ResidencyRequirement.JURISDICTION_LOCKED,
        jurisdictions={
            "nj": "us-east-1",
            "pa": "us-east-2",
            "mi": "us-east-2",
            "on": "ca-central-1",
            "uk": "eu-west-2",
            "mt": "eu-central-1",
        },
        retention_days=2555,        # 7 years — PCI DSS + NJ DGE
        notes="PCI DSS 4.0 Req 3.2.1; NJ A.C. 13:69O financial record requirement",
    ),

    ResidencyPolicy(
        data_class=DataClass.GAME_ROUNDS,
        requirement=ResidencyRequirement.JURISDICTION_LOCKED,
        jurisdictions={
            "nj": "us-east-1",
            "pa": "us-east-2",
            "mi": "us-east-2",
            "on": "ca-central-1",
            "uk": "eu-west-2",
            "mt": "eu-central-1",
        },
        retention_days=1825,        # 5 years — NJ DGE minimum
        notes="NJ: N.J.A.C. 13:69O-2.1 (game outcome records); MGA TSD §4.3",
    ),

    ResidencyPolicy(
        data_class=DataClass.SESSION_DATA,
        requirement=ResidencyRequirement.JURISDICTION_LOCKED,
        jurisdictions={
            "nj": "us-east-1",
            "pa": "us-east-2",
            "mi": "us-east-2",
            "on": "ca-central-1",
            "uk": "eu-west-2",
            "mt": "eu-central-1",
        },
        retention_days=730,         # 2 years — GDPR Article 5(1)(e) storage limitation
        notes="Session data contains IP, device ID, geolocation — treated as PII",
    ),

    ResidencyPolicy(
        data_class=DataClass.KYC_DOCUMENTS,
        requirement=ResidencyRequirement.COUNTRY_LOCKED,
        jurisdictions={
            "on": "ca-central-1",   # Canadian law: data must stay in Canada
            "uk": "eu-west-2",      # UK: data must stay in UK
            "mt": "eu-central-1",   # GDPR: must stay in EEA
            "nj": "us-east-1",      # NJ DGE: must stay in US
            "pa": "us-east-2",
            "mi": "us-east-2",
        },
        retention_days=1825,        # 5 years — FATF / AML Directive
        notes="4AMLD Article 40; AGCO AML obligations; UKGC LCCP 17.1.1",
    ),

    ResidencyPolicy(
        data_class=DataClass.SELF_EXCLUSION,
        requirement=ResidencyRequirement.JURISDICTION_LOCKED,
        jurisdictions={
            "nj": "us-east-1",
            "pa": "us-east-2",
            "mi": "us-east-2",
            "on": "ca-central-1",
            "uk": "eu-west-2",
            "mt": "eu-central-1",
        },
        retention_days=3650,        # 10 years — most restrictive across jurisdictions
        notes=(
            "UKGC: GAMSTOP records retained for lifetime of exclusion + 7 years; "
            "NJ: ISE records under N.J.A.C. 13:69C-11.8"
        ),
    ),

    ResidencyPolicy(
        data_class=DataClass.GAME_CATALOGUE,
        requirement=ResidencyRequirement.UNRESTRICTED,
        jurisdictions={},           # No per-jurisdiction placement constraint
        retention_days=365,
        encryption_required=False,
        notes="Read-only static content; no PII; regulators permit global CDN distribution",
    ),

    ResidencyPolicy(
        data_class=DataClass.AGGREGATED_ANALYTICS,
        requirement=ResidencyRequirement.UNRESTRICTED,
        jurisdictions={},           # No per-jurisdiction constraint
        retention_days=1095,        # 3 years for trend analysis
        encryption_required=False,
        notes=(
            "Must be irreversibly anonymised per GDPR Recital 26; "
            "k-anonymity ≥ 5 and l-diversity ≥ 2 required before classification as UNRESTRICTED"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_policy_index: dict[DataClass, ResidencyPolicy] = {
    p.data_class: p for p in POLICIES
}


def get_policy(data_class: DataClass) -> ResidencyPolicy:
    """Return the policy for a given data class.

    Raises KeyError if the class is not defined in POLICIES.
    """
    return _policy_index[data_class]


def approved_region(data_class: DataClass, jurisdiction: str) -> str | None:
    """Return the approved AWS region for a data class + jurisdiction combination.

    Returns None if the data class is UNRESTRICTED or the jurisdiction has no
    explicit mapping (indicating the data class is not licensed for that market).
    """
    policy = get_policy(data_class)
    if policy.requirement == ResidencyRequirement.UNRESTRICTED:
        return None
    return policy.jurisdictions.get(jurisdiction)
