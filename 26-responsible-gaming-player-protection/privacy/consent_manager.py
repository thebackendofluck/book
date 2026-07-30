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
Consent Manager — GDPR Art.7 / LGPD Art.8 / PIPEDA Principle 3

WHY CONSENT MANAGEMENT IS COMPLEX IN IGAMING:

Consent is only one of six legal bases under GDPR. Operators often incorrectly
treat consent as the universal basis for all processing. In iGaming, the correct
legal basis mapping is:

  - Account management, KYC         → Contract (Art.6(1)(b))
  - AML/fraud detection              → Legal obligation (Art.6(1)(c))
  - Responsible gaming profiling     → Legitimate interest (Art.6(1)(f))
  - Marketing emails / SMS           → Consent (Art.6(1)(a))  ← consent required
  - Analytics / behaviour tracking   → Consent (Art.6(1)(a))  ← consent required
  - Essential cookies                → Not applicable (legitimate interest / contract)
  - Non-essential cookies            → Consent (ePrivacy Directive)
  - Third-party data sharing         → Consent (Art.6(1)(a))  ← consent required

Over-relying on consent creates a problem: if consent is the sole legal basis
for processing and the player withdraws it, ALL that processing must stop. If
the operator has used consent for AML processing (which it shouldn't), it
would be unable to comply with AML obligations.

CONSENT VALIDITY REQUIREMENTS (GDPR Art.7 / Recital 32):
  - Freely given: not bundled with T&Cs; not a condition of service
  - Specific: separate consent for each distinct purpose
  - Informed: player understands what they are consenting to
  - Unambiguous: an affirmative act (not pre-ticked boxes)

WHY WITHDRAWAL MUST BE EASY:
GDPR Art.7(3) requires withdrawal to be "as easy as giving consent". A player
who clicked one checkbox to consent to marketing must be able to click one
checkbox to withdraw. Multi-step unsubscribe flows are non-compliant.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Consent purpose taxonomy
# ---------------------------------------------------------------------------


class ConsentPurpose(str, Enum):
    """
    Enumeration of all processing purposes that require consent.

    Only these purposes use consent as their legal basis. All other processing
    (account management, AML, responsible gaming) uses a different legal basis
    and does NOT require consent — and therefore is NOT affected by consent
    withdrawal.
    """

    MARKETING_EMAIL = "marketing_email"
    """
    WHY CONSENT IS REQUIRED:
    PECR Reg.22 (UK) / ePrivacy Directive Art.13(1): direct marketing by
    electronic mail requires prior consent. This is a specific rule that
    sits alongside GDPR — GDPR consent alone is not sufficient; PECR
    consent must also be obtained separately.
    """

    MARKETING_SMS = "marketing_sms"
    """
    WHY CONSENT IS REQUIRED:
    Same as MARKETING_EMAIL: PECR Reg.22 applies to SMS. Note that LGPD
    (Brazil) and CCPA (California) also require opt-in for marketing SMS.
    """

    MARKETING_PUSH = "marketing_push"
    """
    Push notifications require consent under the ePrivacy Directive and
    under iOS/Android platform policies (which have legal force via their
    T&Cs with app publishers).
    """

    ANALYTICS_BEHAVIOURAL = "analytics_behavioural"
    """
    WHY CONSENT IS REQUIRED:
    Behavioural analytics (tracking player journeys, A/B testing) goes beyond
    what is strictly necessary for the platform to function. It therefore
    requires consent under the ePrivacy Directive (cookies/tracking) and
    GDPR Art.6(1)(a).

    IMPORTANT: This is distinct from RESPONSIBLE GAMING PROFILING, which
    uses legitimate interest as its legal basis and is NOT governed by this
    consent purpose.
    """

    THIRD_PARTY_DATA_SHARING = "third_party_data_sharing"
    """
    WHY CONSENT IS REQUIRED:
    Sharing personal data with third parties for their own purposes (e.g.,
    affiliate networks, data brokers) requires explicit consent. CCPA
    §1798.120 grants the specific right to opt out of "sale" of personal
    information, which includes some forms of data sharing.
    """

    PERSONALISED_BONUSES = "personalised_bonuses"
    """
    Offering bonuses based on behavioural profiling requires consent where
    the profiling is not already covered by legitimate interest. This is a
    commercially driven use that would not pass the balancing test for
    legitimate interest.
    """

    COOKIES_NON_ESSENTIAL = "cookies_non_essential"
    """
    WHY CONSENT IS REQUIRED:
    The ePrivacy Directive (Directive 2002/58/EC) Art.5(3) requires consent
    for the placement of non-essential cookies. "Essential" cookies — those
    strictly necessary for the service requested by the user — do not require
    consent. Everything else does.
    """


# ---------------------------------------------------------------------------
# Consent record model
# ---------------------------------------------------------------------------


@dataclass
class ConsentRecord:
    """
    Immutable record of a consent decision.

    WHY RECORDS ARE IMMUTABLE:
    GDPR Art.7(1) requires the controller to demonstrate that the data subject
    consented. The audit trail must show what the player was shown when they
    consented, what they consented to, and when. Overwriting records would
    undermine this demonstration.
    """

    record_id: str
    player_id: str
    purpose: str
    status: str  # "granted" | "withdrawn"
    given_at: str | None
    withdrawn_at: str | None
    collection_method: str  # "checkbox" | "api" | "verbal" (call centre) | "migration"
    version: str  # version of the consent text shown
    ip_address: str | None = None
    notes: str | None = None


@dataclass
class ConsentStatus:
    """Summary of a player's current consent status across all purposes."""

    player_id: str
    checked_at: str
    consents: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ConsentManager
# ---------------------------------------------------------------------------


class ConsentManager:
    """
    Manages the full consent lifecycle: collection, storage, withdrawal,
    and audit.

    DESIGN PRINCIPLES:

    1. Consent is purpose-specific. The player must be able to grant or
       withdraw consent for each purpose independently.

    2. Consent records are append-only. Each grant or withdrawal creates a
       new record. The current status is derived from the most recent record.

    3. The consent text version is recorded. If the processing purposes change,
       old consents collected under an earlier version may need to be refreshed.

    4. Withdrawal is as easy as granting. A single method call withdraws consent
       for a given purpose. No multi-step flow, no email confirmation required.
    """

    def __init__(self, consent_repo: Any, audit_log: Any) -> None:
        self._repo = consent_repo
        self._audit = audit_log

    async def record_consent(
        self,
        player_id: str,
        purpose: ConsentPurpose,
        collection_method: str = "checkbox",
        version: str = "1.0",
        ip_address: str | None = None,
    ) -> ConsentRecord:
        """
        Record a new consent grant.

        WHY VERSION IS MANDATORY:
        GDPR Art.7(1) requires the controller to demonstrate consent. If a
        regulatory challenge arises, the operator must be able to show exactly
        what consent text the player agreed to. The version number links the
        record to the text in the privacy notice version control system.
        """
        record = ConsentRecord(
            record_id=str(uuid.uuid4()),
            player_id=player_id,
            purpose=purpose.value,
            status="granted",
            given_at=_utcnow(),
            withdrawn_at=None,
            collection_method=collection_method,
            version=version,
            ip_address=ip_address,
        )

        await self._repo.save(record)

        await self._audit.log(
            event_type="consent_granted",
            player_id=player_id,
            details={
                "record_id": record.record_id,
                "purpose": purpose.value,
                "method": collection_method,
                "version": version,
            },
        )

        logger.info(
            "Consent granted",
            extra={"player_id": player_id, "purpose": purpose.value, "version": version},
        )

        return record

    async def revoke_for_purpose(
        self,
        player_id: str,
        purpose: str,
    ) -> ConsentRecord:
        """
        Record a consent withdrawal for a specific purpose.

        GDPR Art.7(3): withdrawal shall not affect the lawfulness of processing
        based on consent before its withdrawal. Processing that occurred BEFORE
        this call was lawful. Processing that occurs AFTER this call requires a
        different legal basis or must stop.

        WHY THIS DOES NOT AFFECT RG PROFILING:
        Responsible gaming profiling uses legitimate interest (Art.6(1)(f)) as
        its legal basis, not consent. Withdrawing consent has no effect on
        legitimate-interest processing. Only an Art.21 objection (not this method)
        could potentially affect RG profiling, and even then the objection can be
        overridden by compelling legitimate grounds (preventing gambling harm).
        """
        record = ConsentRecord(
            record_id=str(uuid.uuid4()),
            player_id=player_id,
            purpose=purpose,
            status="withdrawn",
            given_at=None,
            withdrawn_at=_utcnow(),
            collection_method="player_request",
            version="withdrawal",
        )

        await self._repo.save(record)

        await self._audit.log(
            event_type="consent_withdrawn",
            player_id=player_id,
            details={"record_id": record.record_id, "purpose": purpose},
        )

        logger.info(
            "Consent withdrawn",
            extra={"player_id": player_id, "purpose": purpose},
        )

        return record

    async def get_status(self, player_id: str) -> ConsentStatus:
        """
        Return the current consent status for all purposes.

        Status is derived from the most recent record per purpose.
        """
        all_records = await self._repo.get_all_for_player(player_id)

        # Build most-recent map per purpose
        latest: dict[str, ConsentRecord] = {}
        for r in sorted(all_records, key=lambda x: x.given_at or x.withdrawn_at or ""):
            latest[r.purpose] = r

        consents = {purpose: (latest[purpose].status == "granted") for purpose in latest}

        return ConsentStatus(
            player_id=player_id,
            checked_at=_utcnow(),
            consents=consents,
        )

    async def get_all_records(self, player_id: str) -> list[ConsentRecord]:
        """
        Return the full consent audit trail for a player.
        Used by SARExporter for Art.15/Art.20 exports.
        """
        return await self._repo.get_all_for_player(player_id)

    async def has_valid_consent(self, player_id: str, purpose: ConsentPurpose) -> bool:
        """
        Check whether a player has currently active consent for a given purpose.

        Use this before sending any marketing communication or initiating any
        consent-based processing.
        """
        records = await self._repo.get_for_purpose(player_id, purpose.value)
        if not records:
            return False
        latest = max(records, key=lambda r: r.given_at or r.withdrawn_at or "")
        return latest.status == "granted"

    async def withdraw_all(self, player_id: str) -> list[ConsentRecord]:
        """
        Withdraw all consent for a player.

        Called when a player closes their account or makes a global
        "withdraw all consent" request. Does NOT affect processing
        based on legal obligation or legitimate interest.
        """
        withdrawn = []
        for purpose in ConsentPurpose:
            if await self.has_valid_consent(player_id, purpose):
                record = await self.revoke_for_purpose(player_id, purpose.value)
                withdrawn.append(record)

        logger.info(
            "All consent withdrawn",
            extra={"player_id": player_id, "count": len(withdrawn)},
        )

        return withdrawn


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()
