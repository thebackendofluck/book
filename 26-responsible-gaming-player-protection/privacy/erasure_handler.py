# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
GDPR Art.17 Erasure Handler for iGaming — Pseudonymisation Implementation

WHY PSEUDONYMISATION RATHER THAN DELETION:

AMLD6 Art.40 and UK MLR 2017 Reg.40 require obliged entities (which includes
gambling operators in most jurisdictions) to retain copies of documents and
information obtained during customer due diligence for five years after the
end of the business relationship.

MGA Technical Standards require KYC records to be available for regulatory
audit for five years post-account-closure.

UKGC LCCP Social Responsibility Code 3.4 requires retention of responsible
gaming interaction records.

Full deletion would simultaneously:
  (a) Breach AML law (criminal liability under POCA 2002 / AMLD6 Art.59)
  (b) Obstruct potential law enforcement requests
  (c) Destroy evidence needed to defend disputed transactions
  (d) Remove self-exclusion records that prevent re-registration

GDPR Recital 26 and Art.4(5) resolve this conflict: pseudonymised data
where the re-identification key has been destroyed falls outside the GDPR's
scope. The transaction skeleton (amounts, dates, hashed IDs, risk flags)
is no longer personal data once the mapping table is destroyed.

SELF-EXCLUSION RECORDS:
These survive the pseudonymisation process unchanged.
GDPR Art.9(2)(c) permits processing special-category health-adjacent data
when necessary to protect vital interests. Self-exclusion records represent
the player's own prior instruction not to be permitted to gamble. Destroying
them would contradict those instructions and create regulatory liability.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------

# PII fields that CAN be erased (replaced with hashes, then key destroyed)
ERASABLE_FIELDS: frozenset[str] = frozenset(
    [
        "first_name",
        "last_name",
        "email",
        "phone",
        "address_line_1",
        "address_line_2",
        "city",
        "postcode",
        "country_of_birth",
        "date_of_birth",
        "national_id_number",
        "passport_number",
        "ip_addresses",         # stored as JSON array
        "device_fingerprints",  # stored as JSON array
        "profile_photo_url",
        "chat_transcripts",     # pseudonymised in-place
    ]
)

# Fields that MUST be retained (regulatory obligation)
# These are never modified during pseudonymisation.
RETAINED_FIELDS: frozenset[str] = frozenset(
    [
        "player_id",                 # internal reference — not PII post-pseudonymisation
        "player_id_hash",            # SHA-256(player_id + salt) — for cross-system deduplication
        "account_status",            # "pseudonymised" after erasure
        "registration_date",         # needed for 5-year retention clock
        "account_closure_date",      # start of retention period
        "kyc_verification_status",   # AMLD6: must know if KYC was completed
        "kyc_verification_date",     # AMLD6 audit record
        "transaction_history",       # AMLD6 Art.40: full transaction skeleton
        "self_exclusion_status",     # UKGC LCCP / MGA Directive — NEVER erasable
        "self_exclusion_start",      # regulatory audit requirement
        "self_exclusion_end",        # regulatory audit requirement
        "self_exclusion_jurisdiction",
        "aml_alerts",                # FATF/AMLD6: suspicious activity records
        "aml_alerts_reported",       # whether SAR was filed
        "responsible_gaming_flags",  # MGA / UKGC: intervention history
        "pgsi_scores",               # Health-adjacent — retained under vital interest basis
        "risk_level_history",        # UKGC LCCP audit requirement
        "deposit_limit_history",     # Regulatory audit requirement
        "session_limit_history",     # Regulatory audit requirement
    ]
)

# Nulled fields — these contain PII but have no regulatory value post-erasure
# They are set to NULL rather than hashed, reducing data footprint
NULLED_FIELDS: frozenset[str] = frozenset(
    [
        "phone",
        "address_line_1",
        "address_line_2",
        "city",
        "postcode",
        "ip_addresses",
        "device_fingerprints",
        "profile_photo_url",
        "chat_transcripts",
    ]
)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class ErasureResult:
    """
    Records the outcome of a pseudonymisation operation.
    The certificate can be provided to the player as confirmation.
    """

    player_id: str
    completed_at: str
    fields_pseudonymised: list[str]
    fields_nulled: list[str]
    fields_retained: list[str]
    certificate: str  # opaque reference for audit trail


# ---------------------------------------------------------------------------
# ErasureHandler
# ---------------------------------------------------------------------------


class ErasureHandler:
    """
    Implements GDPR Art.17 for iGaming by pseudonymising PII rather than
    deleting records.

    The handler uses HMAC-SHA-256 with a per-erasure salt to hash PII fields.
    The salt is stored temporarily in a separate mapping table, then destroyed
    after the erasure is confirmed. Without the salt, the hashes cannot be
    reversed and the records cease to be personal data under GDPR Art.4(1).

    WHY HMAC RATHER THAN PLAIN SHA-256:
    Plain SHA-256 of common values (e.g., email addresses) is vulnerable to
    rainbow table attacks. Using HMAC with a unique per-erasure salt makes
    the hashes computationally infeasible to reverse.

    WHY THE SALT IS DESTROYED:
    GDPR Recital 26 requires that pseudonymisation achieves a state where
    personal data "can no longer be attributed to a specific data subject
    without the use of additional information". Destroying the salt ensures
    no such additional information exists.
    """

    def __init__(self, player_repo: Any, audit_log: Any) -> None:
        self._players = player_repo
        self._audit = audit_log

    async def pseudonymise(self, player_id: str) -> ErasureResult:
        """
        Replace PII fields with HMAC-SHA-256 hashes, null technical PII,
        and preserve all regulatory fields unchanged.

        Steps:
          1. Generate a per-erasure salt (crypto-random, 32 bytes)
          2. Compute HMAC-SHA-256 for each erasable field
          3. Write hashes to database, NULL technical fields
          4. Set account_status = "pseudonymised"
          5. Destroy the salt (not persisted after this function returns)
          6. Write erasure certificate to audit log

        Returns ErasureResult containing the certificate reference.
        """
        # Step 1: Generate ephemeral salt — never persisted
        erasure_salt = os.urandom(32)

        player = await self._players.get_by_id(player_id)
        if not player:
            raise ValueError(f"Player {player_id} not found")

        profile = player if isinstance(player, dict) else player.__dict__

        fields_pseudonymised: list[str] = []
        fields_nulled: list[str] = []
        updates: dict[str, Any] = {}

        for f in ERASABLE_FIELDS:
            current_value = profile.get(f)
            if current_value is None:
                continue

            if f in NULLED_FIELDS:
                updates[f] = None
                fields_nulled.append(f)
            else:
                hashed = _hmac_hash(str(current_value), erasure_salt)
                updates[f] = hashed
                fields_pseudonymised.append(f)

        updates["account_status"] = "pseudonymised"
        updates["pseudonymised_at"] = datetime.now(UTC).isoformat()

        # Step 2–4: Write to database
        await self._players.bulk_update(player_id, updates)

        # Step 5: Salt is not persisted — it goes out of scope here
        # This is the critical step that makes the pseudonymisation irreversible
        del erasure_salt

        # Step 6: Write audit certificate
        certificate = _generate_certificate(player_id)

        await self._audit.log(
            event_type="erasure_completed",
            player_id=player_id,
            details={
                "certificate": certificate,
                "fields_pseudonymised": fields_pseudonymised,
                "fields_nulled": fields_nulled,
                "fields_retained": sorted(RETAINED_FIELDS),
                "salt_destroyed": True,
            },
        )

        logger.info(
            "Erasure (pseudonymisation) completed",
            extra={
                "player_id": player_id,
                "certificate": certificate,
                "fields_count": len(fields_pseudonymised) + len(fields_nulled),
            },
        )

        return ErasureResult(
            player_id=player_id,
            completed_at=datetime.now(UTC).isoformat(),
            fields_pseudonymised=fields_pseudonymised,
            fields_nulled=fields_nulled,
            fields_retained=sorted(RETAINED_FIELDS),
            certificate=certificate,
        )

    async def can_erase(self, player_id: str) -> tuple[bool, list[str]]:
        """
        Check whether an erasure request can proceed.

        Returns (can_proceed, list_of_blockers).
        Blockers may include:
          - Active balance (must be withdrawn first)
          - Pending withdrawal (must be processed first)
          - Open dispute or chargeback
          - Active bonus with wagering requirement
          - Ongoing AML investigation
        """
        blockers: list[str] = []

        player = await self._players.get_by_id(player_id)
        if not player:
            return False, ["Player account not found."]

        profile = player if isinstance(player, dict) else player.__dict__

        balance = float(profile.get("balance", 0))
        if balance > 0:
            blockers.append(
                f"Active balance of {balance} must be withdrawn before erasure. "
                "Withdrawals are always permitted regardless of account status."
            )

        pending_withdrawal = profile.get("pending_withdrawal_amount")
        if pending_withdrawal and float(pending_withdrawal) > 0:
            blockers.append("Pending withdrawal must be processed first.")

        open_dispute = profile.get("open_dispute_count", 0)
        if int(open_dispute) > 0:
            blockers.append("Open dispute must be resolved before erasure.")

        if profile.get("aml_investigation_active"):
            blockers.append(
                "Active AML investigation: erasure cannot proceed until investigation "
                "is closed. This may take up to 60 days."
            )

        return len(blockers) == 0, blockers


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hmac_hash(value: str, salt: bytes) -> str:
    """
    Compute HMAC-SHA-256 of a string value using a given salt.

    The resulting hash is hex-encoded and prefixed with 'sha256:' to
    make the algorithm explicit in stored records.
    """
    digest = hmac.new(salt, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"sha256:{digest}"


def _generate_certificate(player_id: str) -> str:
    """
    Generate an opaque erasure certificate reference.
    Format: ERASURE-<timestamp>-<random-suffix>
    """
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    suffix = uuid.uuid4().hex[:8].upper()
    return f"ERASURE-{ts}-{suffix}"
