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
Wasabi backup configuration with data jurisdiction enforcement.

Each jurisdiction maps to a specific Wasabi region where player data
is legally permitted to reside. Wasabi is S3-compatible — the same boto3
API applies; only the endpoint and region change.

WHY WASABI?
  Wasabi offers ~80% cost reduction vs. AWS S3 with no egress fees.
  For long-retention compliance archives (7 years), this is material.
  The trade-off is that Wasabi has no Glacier-equivalent; all objects
  are stored at the same tier. Price per TB/month: ~$6.99 (Wasabi) vs.
  ~$23 (AWS S3 Standard) or ~$4 (S3 Glacier, with retrieval fees).

DATA JURISDICTION MAPPING (Wasabi regions, as of 2024):
  us-east-1   → Ashburn, Virginia (USA)
  us-east-2   → Manassas, Virginia (USA)
  us-west-1   → Hillsboro, Oregon (USA)
  eu-central-1 → Amsterdam, Netherlands (EU)
  eu-central-2 → Frankfurt, Germany (EU)
  eu-west-1   → London, United Kingdom
  eu-west-2   → Paris, France (EU)
  ap-northeast-1 → Tokyo, Japan
  ap-southeast-1 → Singapore
  ca-central-1   → Toronto, Canada

Wasabi endpoint pattern: https://s3.<region>.wasabisys.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Jurisdiction codes (must match spoke identifiers in classification.py)
# ---------------------------------------------------------------------------

class Jurisdiction(str, Enum):
    MGA        = "mga"        # Malta Gaming Authority (EU/EEA)
    UKGC       = "ukgc"       # UK Gambling Commission
    DGE_NJ     = "dge_nj"     # New Jersey Division of Gaming Enforcement
    PGCB_PA    = "pgcb_pa"    # Pennsylvania Gaming Control Board
    MGCB_MI    = "mgcb_mi"    # Michigan Gaming Control Board
    AGCO_ON    = "agco_on"    # Alcohol and Gaming Commission of Ontario
    PAGCOR     = "pagcor"     # Philippine Amusement and Gaming Corporation


# ---------------------------------------------------------------------------
# Per-jurisdiction backup configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WasabiJurisdictionConfig:
    """
    Immutable configuration for a single licensed jurisdiction's Wasabi backup.

    Fields
    ------
    jurisdiction : Jurisdiction
        The regulatory body this configuration applies to.
    wasabi_region : str
        Wasabi region identifier.  Must map to a physical location that
        satisfies the governing framework's data residency rules.
    wasabi_endpoint : str
        S3-compatible endpoint URL for this region.
    physical_location : str
        Human-readable data-centre location.  Used in audit evidence.
    bucket_prefix : str
        Prefix for bucket names.  Append environment and purpose suffix,
        e.g. ``acmetocasino-mga-backup-prod-db``.
    retention_days : int
        Minimum object retention in days.  Set at bucket creation via
        Wasabi Object Lock.  Driven by the most demanding applicable
        regulatory requirement.
    encryption_algorithm : str
        Server-side encryption algorithm.  Wasabi supports AES-256 (SSE-S3).
        All objects must ALSO be encrypted client-side before upload —
        see wasabi-backup.sh for the AES-256-CBC envelope applied with
        openssl before the bytes leave the server.
    legal_basis : str
        Citation of the specific regulatory provision requiring this region.
        Included in every backup manifest entry for audit purposes.
    cross_region_replica_allowed : bool
        Whether a secondary copy to another Wasabi region is permitted.
        False for NJ (strict jurisdiction lock) and Ontario (AGCO prohibition).
        True for EU spokes where cross-EEA replication is GDPR-compliant.
    secondary_region : str | None
        If cross_region_replica_allowed, the DR Wasabi region.  Must be
        in the same regulatory territory (EU → another EU region, etc.).
    """
    jurisdiction:              Jurisdiction
    wasabi_region:             str
    wasabi_endpoint:           str
    physical_location:         str
    bucket_prefix:             str
    retention_days:            int
    encryption_algorithm:      str
    legal_basis:               str
    cross_region_replica_allowed: bool
    secondary_region:          str | None = None
    secondary_endpoint:        str | None = None


JURISDICTION_BACKUP_CONFIG: dict[Jurisdiction, WasabiJurisdictionConfig] = {

    # ------------------------------------------------------------------
    # MGA — Malta Gaming Authority
    # Primary data in Amsterdam (EU).  Secondary replication to Frankfurt
    # is permitted under GDPR because both regions are within the EEA.
    # Retention: 7 years — MGA Technical Standards and GDPR Article 5(1)(e).
    # ------------------------------------------------------------------
    Jurisdiction.MGA: WasabiJurisdictionConfig(
        jurisdiction=Jurisdiction.MGA,
        wasabi_region="eu-central-1",
        wasabi_endpoint="https://s3.eu-central-1.wasabisys.com",
        physical_location="Amsterdam, Netherlands (EU/EEA)",
        bucket_prefix="acmetocasino-mga-backup",
        retention_days=2555,   # 7 years
        encryption_algorithm="AES-256",
        legal_basis=(
            "GDPR Art. 32 (security of processing) + "
            "MGA Technical Standards for Gaming Devices, §8 (audit trail retention) + "
            "EU data residency: data must remain within EEA under GDPR Chapter V"
        ),
        cross_region_replica_allowed=True,
        secondary_region="eu-central-2",            # Frankfurt, Germany
        secondary_endpoint="https://s3.eu-central-2.wasabisys.com",
    ),

    # ------------------------------------------------------------------
    # UKGC — UK Gambling Commission
    # Post-Brexit, UK GDPR (retained EU law) applies independently.
    # Data must reside in the UK or an adequacy-decision country.
    # Wasabi eu-west-1 (London) is the only UK-soil Wasabi region.
    # Cross-replication to Ireland (eu-west-1 in AWS terms) is NOT
    # permitted post-Brexit without a UK IDTA or equivalent safeguard —
    # Wasabi does not offer an Ireland region, so no secondary here.
    # Retention: 5 years — UKGC LCCP SR Code 15.2.1 (AML records 5 yrs);
    # 3 years for general player records; use 5 as the governing floor.
    # ------------------------------------------------------------------
    Jurisdiction.UKGC: WasabiJurisdictionConfig(
        jurisdiction=Jurisdiction.UKGC,
        wasabi_region="eu-west-1",
        wasabi_endpoint="https://s3.eu-west-1.wasabisys.com",
        physical_location="London, United Kingdom",
        bucket_prefix="acmetocasino-ukgc-backup",
        retention_days=1825,   # 5 years (AML requirement; 3-yr general floor + margin)
        encryption_algorithm="AES-256",
        legal_basis=(
            "UK GDPR Art. 32 (integrity and confidentiality) + "
            "UKGC LCCP SR Code 15.2.1 (AML records 5 years) + "
            "UK GDPR Chapter V: international transfers require adequacy or IDTA; "
            "Wasabi eu-west-1 (London) keeps data on UK soil, avoiding transfer rules"
        ),
        cross_region_replica_allowed=False,   # No UK-adequate Wasabi region available
        secondary_region=None,
    ),

    # ------------------------------------------------------------------
    # DGE NJ — New Jersey Division of Gaming Enforcement
    # Strictest US jurisdiction.  All gaming servers and player data must
    # reside within Atlantic City.  N.J.A.C. 13:69O-1.2 mandates physical
    # location within the state; DGE guidance further restricts to
    # Atlantic City data centres.  Wasabi us-east-1 (Ashburn, VA) is the
    # closest compliant Wasabi region — it does NOT satisfy the Atlantic
    # City requirement on its own.  Wasabi MUST be used only for
    # encrypted archive objects where the DGE has granted written
    # authorisation for off-premises archival.  Live transactional data
    # must remain in Atlantic City DC.  NEVER enable cross-region replica.
    # Retention: 7 years — N.J.A.C. 13:69D-1.13(d) financial records,
    # 5 years game logs per N.J.A.C. 13:69E-1.
    # ------------------------------------------------------------------
    Jurisdiction.DGE_NJ: WasabiJurisdictionConfig(
        jurisdiction=Jurisdiction.DGE_NJ,
        wasabi_region="us-east-1",
        wasabi_endpoint="https://s3.us-east-1.wasabisys.com",
        physical_location="Ashburn, Virginia, USA",
        bucket_prefix="acmetocasino-nj-backup",
        retention_days=2555,   # 7 years — most restrictive NJ requirement
        encryption_algorithm="AES-256",
        legal_basis=(
            "N.J.A.C. 13:69O-1.2 (internet gaming systems must reside in NJ) + "
            "N.J.A.C. 13:69D-1.13(d) (financial records 7 years) + "
            "N.J.A.C. 13:69E-1 (game records 5 years) + "
            "WARNING: Wasabi us-east-1 (Ashburn VA) is outside Atlantic City. "
            "Use ONLY for encrypted archives with prior DGE written authorisation. "
            "Live data and primary backups MUST stay in Atlantic City DC."
        ),
        cross_region_replica_allowed=False,   # NJ data cannot leave us-east-1 under any circumstance
        secondary_region=None,
    ),

    # ------------------------------------------------------------------
    # PGCB PA — Pennsylvania Gaming Control Board
    # More flexible than NJ.  In-state primary required; interstate backup
    # permitted with encryption.  Wasabi us-east-1 (Ashburn, VA) is
    # acceptable for archive.  Retention: 7 years per 58 Pa. Code §441a.7.
    # ------------------------------------------------------------------
    Jurisdiction.PGCB_PA: WasabiJurisdictionConfig(
        jurisdiction=Jurisdiction.PGCB_PA,
        wasabi_region="us-east-1",
        wasabi_endpoint="https://s3.us-east-1.wasabisys.com",
        physical_location="Ashburn, Virginia, USA",
        bucket_prefix="acmetocasino-pa-backup",
        retention_days=2555,   # 7 years — 58 Pa. Code §441a.7
        encryption_algorithm="AES-256",
        legal_basis=(
            "58 Pa. Code §441a.7 (records retention 7 years) + "
            "Pennsylvania Gaming Control Board Technical Standards, §1180a.3 + "
            "Interstate DR permitted with AES-256 encryption per PGCB guidance"
        ),
        cross_region_replica_allowed=True,
        secondary_region="us-east-2",          # Manassas, Virginia (same legal territory)
        secondary_endpoint="https://s3.us-east-2.wasabisys.com",
    ),

    # ------------------------------------------------------------------
    # MGCB MI — Michigan Gaming Control Board
    # Most cloud-friendly US jurisdiction.  Primary in-state required;
    # DR out-of-state approved.  Wasabi us-east-1 acceptable.
    # Retention: 5 years per Mich. Admin. Code R 432.632.
    # ------------------------------------------------------------------
    Jurisdiction.MGCB_MI: WasabiJurisdictionConfig(
        jurisdiction=Jurisdiction.MGCB_MI,
        wasabi_region="us-east-1",
        wasabi_endpoint="https://s3.us-east-1.wasabisys.com",
        physical_location="Ashburn, Virginia, USA",
        bucket_prefix="acmetocasino-mi-backup",
        retention_days=1825,   # 5 years — Mich. Admin. Code R 432.632
        encryption_algorithm="AES-256",
        legal_basis=(
            "Mich. Admin. Code R 432.632 (records retention 5 years) + "
            "MGCB Internet Gaming Rules, §432.654 + "
            "Out-of-state DR explicitly approved with encryption and annual security assessment"
        ),
        cross_region_replica_allowed=True,
        secondary_region="us-east-2",
        secondary_endpoint="https://s3.us-east-2.wasabisys.com",
    ),

    # ------------------------------------------------------------------
    # AGCO ON — Alcohol and Gaming Commission of Ontario
    # Canadian data must remain on Canadian soil.  AGCO iGO Technical
    # Standards §8.1.3 prohibits offshore backup without written approval.
    # Wasabi ca-central-1 (Toronto) is the only Canadian Wasabi region.
    # NEVER cross-replicate to a US or EU Wasabi region.
    # Retention: 7 years — AGCO Rules Respecting iGaming §7.4.
    # ------------------------------------------------------------------
    Jurisdiction.AGCO_ON: WasabiJurisdictionConfig(
        jurisdiction=Jurisdiction.AGCO_ON,
        wasabi_region="ca-central-1",
        wasabi_endpoint="https://s3.ca-central-1.wasabisys.com",
        physical_location="Toronto, Ontario, Canada",
        bucket_prefix="acmetocasino-on-backup",
        retention_days=2555,   # 7 years — AGCO Rules Respecting iGaming §7.4
        encryption_algorithm="AES-256",
        legal_basis=(
            "AGCO Rules Respecting iGaming §7.4 (records retention 7 years) + "
            "AGCO iGO Technical Standards §8.1.3 (data must reside on Canadian soil) + "
            "PIPEDA / Ontario PHIPA: personal data cannot leave Canada without consent; "
            "Wasabi ca-central-1 (Toronto) is the sole compliant Wasabi region"
        ),
        cross_region_replica_allowed=False,   # AGCO prohibits offshore backup
        secondary_region=None,
    ),

    # ------------------------------------------------------------------
    # PAGCOR — Philippine Amusement and Gaming Corporation
    # Primary infrastructure required in the Philippines.  Wasabi does
    # not offer a Philippine region.  Singapore (ap-southeast-1) is the
    # nearest PAGCOR-approved alternative for encrypted archive — but
    # requires written PAGCOR approval per §15.2 of the PAGCOR Charter.
    # Retention: 5 years per PAGCOR Offshore Gaming License §4.3.
    # ------------------------------------------------------------------
    Jurisdiction.PAGCOR: WasabiJurisdictionConfig(
        jurisdiction=Jurisdiction.PAGCOR,
        wasabi_region="ap-southeast-1",
        wasabi_endpoint="https://s3.ap-southeast-1.wasabisys.com",
        physical_location="Singapore",
        bucket_prefix="acmetocasino-ph-backup",
        retention_days=1825,   # 5 years — PAGCOR Offshore Gaming License §4.3
        encryption_algorithm="AES-256",
        legal_basis=(
            "PAGCOR Offshore Gaming License §4.3 (records retention 5 years) + "
            "PAGCOR Charter §15.2 (prior written approval for offshore data storage) + "
            "WARNING: No Wasabi PH region exists. Wasabi ap-southeast-1 (Singapore) "
            "may be used for encrypted archives ONLY with PAGCOR written authorisation. "
            "Primary data and local backups must remain in Philippines."
        ),
        cross_region_replica_allowed=False,
        secondary_region=None,
    ),
}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def get_config(jurisdiction: Jurisdiction) -> WasabiJurisdictionConfig:
    """Return the WasabiJurisdictionConfig for *jurisdiction*, raising
    KeyError if the jurisdiction has no registered configuration."""
    if jurisdiction not in JURISDICTION_BACKUP_CONFIG:
        raise KeyError(
            f"No Wasabi backup configuration for jurisdiction '{jurisdiction}'. "
            f"Registered: {[j.value for j in JURISDICTION_BACKUP_CONFIG]}"
        )
    return JURISDICTION_BACKUP_CONFIG[jurisdiction]


def assert_region_compliant(jurisdiction: Jurisdiction, actual_region: str) -> None:
    """Raise ValueError if *actual_region* does not match the approved
    primary or secondary region for *jurisdiction*.

    Call this from any code path that uploads to Wasabi to act as a
    last-line guardrail against misconfigured environment variables.

    >>> assert_region_compliant(Jurisdiction.AGCO_ON, "us-east-1")
    ValueError: ...
    """
    cfg = get_config(jurisdiction)
    approved = {cfg.wasabi_region}
    if cfg.secondary_region:
        approved.add(cfg.secondary_region)

    if actual_region not in approved:
        raise ValueError(
            f"JURISDICTION VIOLATION: attempted to write {jurisdiction.value!r} data "
            f"to Wasabi region {actual_region!r}. "
            f"Approved regions: {sorted(approved)}. "
            f"Legal basis: {cfg.legal_basis}"
        )


# ---------------------------------------------------------------------------
# Bucket naming convention
# ---------------------------------------------------------------------------

def bucket_name(
    jurisdiction: Jurisdiction,
    purpose: str,
    environment: str = "prod",
) -> str:
    """
    Return the canonical Wasabi bucket name for a given jurisdiction,
    purpose, and environment.

    Wasabi bucket names follow S3 naming rules (lowercase, 3-63 chars,
    no underscores).

    Parameters
    ----------
    jurisdiction : Jurisdiction
    purpose      : str  — e.g. "db", "wal", "media", "audit"
    environment  : str  — e.g. "prod", "staging", "dr"

    Examples
    --------
    >>> bucket_name(Jurisdiction.MGA, "db")
    'acmetocasino-mga-backup-prod-db'
    >>> bucket_name(Jurisdiction.AGCO_ON, "wal", "dr")
    'acmetocasino-on-backup-dr-wal'
    """
    cfg = get_config(jurisdiction)
    return f"{cfg.bucket_prefix}-{environment}-{purpose}"


# ---------------------------------------------------------------------------
# Summary table (useful in runbooks and audit evidence)
# ---------------------------------------------------------------------------

def print_jurisdiction_summary() -> None:
    """Print a human-readable summary of all configured jurisdictions."""
    header = (
        f"{'Jurisdiction':<12} {'Region':<16} {'Location':<40} "
        f"{'Retention':>12} {'X-Region':<10}"
    )
    print(header)
    print("-" * len(header))
    for cfg in JURISDICTION_BACKUP_CONFIG.values():
        xr = "YES" if cfg.cross_region_replica_allowed else "NO"
        print(
            f"{cfg.jurisdiction.value:<12} "
            f"{cfg.wasabi_region:<16} "
            f"{cfg.physical_location:<40} "
            f"{cfg.retention_days:>9}d   "
            f"{xr:<10}"
        )


if __name__ == "__main__":
    print_jurisdiction_summary()
