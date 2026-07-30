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
Age and Identity Verification Integration
Chapter 10 - Responsible Gaming and Player Protection

Integrates with identity verification providers (Onfido, Jumio, GBG)
to verify player age and identity during registration and at key triggers.

Compliance References:
- UKGC LCCP 17.1.1: Age verification must occur before gambling permitted
- UKGC: Verification within 72h of registration (no gambling until verified)
- MGA: Age verification mandatory before first deposit
- UK Gambling Act 2005 s.46: Offence to allow under-18 to gamble
- UKGC: Source of funds checks for deposits over thresholds

Architecture:
    Registration --> Pre-checks (Watchlist/PEP)
               --> Document Upload (if required)
               --> Provider API (Onfido/Jumio)
               --> Decision Engine
               --> Account Status Update
               --> Ongoing Monitoring

Usage:
    verifier = AgeVerificationService(db_pool, onfido_client)
    result = await verifier.verify_player(player_id, player_data)
    if not result.verified:
        await restrict_account(player_id)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from enum import Enum
from typing import Optional, Protocol

import asyncpg  # ty:ignore[unresolved-import]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class VerificationStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    VERIFIED = "verified"
    FAILED = "failed"
    MANUAL_REVIEW = "manual_review"
    BLOCKED = "blocked"         # Underage or watchlist match


class VerificationLevel(str, Enum):
    """
    Tiered verification levels. UKGC requires Level 2+ before gambling.
    Enhanced due diligence at Level 3 for AML compliance.
    """
    BASIC = "basic"             # Email + phone verified
    STANDARD = "standard"       # ID verified, age confirmed
    ENHANCED = "enhanced"       # Source of funds, proof of address


class DocumentType(str, Enum):
    PASSPORT = "passport"
    DRIVING_LICENCE = "driving_licence"
    NATIONAL_ID = "national_id"
    PROOF_OF_ADDRESS = "proof_of_address"
    SOURCE_OF_FUNDS = "source_of_funds"


@dataclass
class PlayerIdentity:
    player_id: str
    first_name: str
    last_name: str
    date_of_birth: str      # YYYY-MM-DD
    email: str
    phone: Optional[str] = None
    address_line_1: Optional[str] = None
    address_line_2: Optional[str] = None
    city: Optional[str] = None
    postcode: Optional[str] = None
    country: str = "GB"
    nationality: Optional[str] = None


@dataclass
class VerificationResult:
    player_id: str
    status: VerificationStatus
    verified: bool
    age_confirmed: bool = False
    estimated_age: Optional[int] = None
    identity_match_score: float = 0.0
    pep_match: bool = False          # Politically Exposed Person
    sanctions_match: bool = False
    adverse_media: bool = False
    provider: str = ""
    provider_reference: str = ""
    checks_performed: list[str] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)
    requires_manual_review: bool = False
    review_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Provider Protocol
# ---------------------------------------------------------------------------

class IdentityProvider(Protocol):
    async def create_applicant(self, identity: PlayerIdentity) -> str:
        """Create applicant and return provider reference ID."""
        ...

    async def run_check(self, applicant_id: str, check_type: str) -> dict:
        """Run a verification check and return results."""
        ...

    async def get_check_result(self, check_id: str) -> dict:
        """Poll for check result (async providers)."""
        ...


# ---------------------------------------------------------------------------
# Onfido Client
# ---------------------------------------------------------------------------

class OnfidoClient:
    """
    Onfido identity verification integration.
    https://documentation.onfido.com/

    Onfido provides:
    - Document verification (passport, driving licence, ID card)
    - Facial biometric check (liveness + face match)
    - Data comparison (name, DOB, address)
    - PEP/sanctions screening
    - Proof of address verification
    """

    def __init__(self, api_token: str, region: str = "eu"):
        self.api_token = api_token
        self.base_url = f"https://api.{region}.onfido.com/v3.6"

    async def create_applicant(self, identity: PlayerIdentity) -> str:
        import aiohttp

        payload = {
            "first_name": identity.first_name,
            "last_name": identity.last_name,
            "dob": identity.date_of_birth,
            "email": identity.email,
            "phone_number": identity.phone,
            "address": {
                "building_number": "",
                "street": identity.address_line_1 or "",
                "town": identity.city or "",
                "postcode": identity.postcode or "",
                "country": identity.country,
            },
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/applicants",
                json=payload,
                headers={
                    "Authorization": f"Token token={self.api_token}",
                    "Content-Type": "application/json",
                },
            ) as resp:
                data = await resp.json()
                if resp.status == 201:
                    return data["id"]
                raise RuntimeError(f"Onfido create applicant failed: {data}")

    async def run_check(self, applicant_id: str, check_type: str = "standard") -> dict:
        import aiohttp

        report_names = ["document", "identity_enhanced"]
        if check_type == "enhanced":
            report_names.append("proof_of_address")
            report_names.append("watchlist_enhanced")
        else:
            report_names.append("watchlist_standard")

        payload = {
            "applicant_id": applicant_id,
            "report_names": report_names,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/checks",
                json=payload,
                headers={"Authorization": f"Token token={self.api_token}"},
            ) as resp:
                data = await resp.json()
                if resp.status == 201:
                    return {
                        "check_id": data["id"],
                        "status": data["status"],
                        "report_ids": [r["id"] for r in data.get("report_ids", [])],
                    }
                raise RuntimeError(f"Onfido check failed: {data}")

    async def get_check_result(self, check_id: str) -> dict:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/checks/{check_id}",
                headers={"Authorization": f"Token token={self.api_token}"},
            ) as resp:
                data = await resp.json()
                return {
                    "status": data["status"],
                    "result": data.get("result"),
                    "reports": data.get("report_ids", []),
                }


# ---------------------------------------------------------------------------
# Jumio Client
# ---------------------------------------------------------------------------

class JumioClient:
    """
    Jumio identity verification integration.
    Alternative provider for redundancy and coverage.
    """

    def __init__(self, api_token: str, api_secret: str):
        self.api_token = api_token
        self.api_secret = api_secret
        self.base_url = "https://account.amer-1.jumio.ai/api/v1"

    async def create_applicant(self, identity: PlayerIdentity) -> str:
        import aiohttp

        payload = {
            "customerInternalReference": identity.player_id,
            "userReference": identity.email,
            "callbackUrl": "https://api.example.com/webhooks/jumio",
            "workflowDefinition": {
                "key": 2,  # ID verification + selfie
            },
        }

        auth = aiohttp.BasicAuth(self.api_token, self.api_secret)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.base_url}/accounts",
                json=payload,
                auth=auth,
            ) as resp:
                data = await resp.json()
                return data.get("accountId", "")

    async def run_check(self, applicant_id: str, check_type: str = "standard") -> dict:
        # Jumio uses a redirect flow; check_type maps to workflow
        return {
            "check_id": applicant_id,
            "status": "pending",
            "redirect_url": f"https://web.jumio.com/web/v4/{applicant_id}",
        }

    async def get_check_result(self, check_id: str) -> dict:
        import aiohttp

        auth = aiohttp.BasicAuth(self.api_token, self.api_secret)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self.base_url}/accounts/{check_id}/workflow-executions",
                auth=auth,
            ) as resp:
                data = await resp.json()
                if data.get("workflow", {}).get("status") == "PROCESSED":
                    decision = data.get("decision", {})
                    return {
                        "status": "complete",
                        "result": "clear" if decision.get("type") == "PASSED" else "consider",
                    }
                return {"status": "pending"}


# ---------------------------------------------------------------------------
# Age Verification Service
# ---------------------------------------------------------------------------

class AgeVerificationService:
    """
    Manages the full age and identity verification lifecycle.

    UKGC requirements:
    - Age must be verified before ANY gambling is permitted
    - Operators have 72 hours from registration to verify
    - Until verified: deposits allowed but NO gambling
    - If verification fails: account must be closed, deposits returned
    - Must use a reliable third-party data source
    - Ongoing monitoring for PEP/sanctions changes
    """

    MINIMUM_AGE = 18  # UK/EU standard

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        primary_provider: IdentityProvider,
        fallback_provider: Optional[IdentityProvider] = None,
    ):
        self.db = db_pool
        self.primary = primary_provider
        self.fallback = fallback_provider

    async def verify_player(
        self,
        player_id: str,
        identity: PlayerIdentity,
        level: VerificationLevel = VerificationLevel.STANDARD,
    ) -> VerificationResult:
        """
        Run age and identity verification for a player.

        Steps:
        1. Check age from DOB (immediate reject if under 18)
        2. Create applicant with verification provider
        3. Run identity checks (document, watchlist, biometric)
        4. Process results
        5. Update account status
        6. Persist for audit
        """
        result = VerificationResult(
            player_id=player_id,
            status=VerificationStatus.IN_PROGRESS,
            verified=False,
        )

        # Step 1: Immediate age check from DOB
        try:
            dob = date.fromisoformat(identity.date_of_birth)
            today = date.today()
            age = today.year - dob.year - (
                (today.month, today.day) < (dob.month, dob.day)
            )
            result.estimated_age = age

            if age < self.MINIMUM_AGE:
                result.status = VerificationStatus.BLOCKED
                result.age_confirmed = False
                result.failure_reasons.append(
                    f"Player is {age} years old. Minimum age is {self.MINIMUM_AGE}."
                )
                await self._block_underage_account(player_id, age)
                await self._persist_result(result)
                return result
        except (ValueError, TypeError) as e:
            result.failure_reasons.append(f"Invalid date of birth: {e}")

        # Step 2-3: Run provider checks
        try:
            provider_result = await self._run_provider_check(
                identity, level, provider=self.primary, provider_name="onfido"
            )
        except Exception as e:
            logger.error("Primary provider failed: %s", e)
            if self.fallback:
                try:
                    provider_result = await self._run_provider_check(
                        identity, level, provider=self.fallback, provider_name="jumio"
                    )
                except Exception as e2:
                    logger.error("Fallback provider also failed: %s", e2)
                    result.status = VerificationStatus.MANUAL_REVIEW
                    result.requires_manual_review = True
                    result.review_reasons.append("Both verification providers failed")
                    await self._persist_result(result)
                    return result
            else:
                result.status = VerificationStatus.MANUAL_REVIEW
                result.requires_manual_review = True
                result.review_reasons.append(f"Provider check failed: {e}")
                await self._persist_result(result)
                return result

        # Step 4: Process results
        result.provider = provider_result.get("provider", "")
        result.provider_reference = provider_result.get("reference", "")
        result.checks_performed = provider_result.get("checks", [])

        check_result = provider_result.get("result", "")
        if check_result == "clear":
            result.status = VerificationStatus.VERIFIED
            result.verified = True
            result.age_confirmed = True
            result.identity_match_score = provider_result.get("match_score", 1.0)
        elif check_result == "consider":
            result.status = VerificationStatus.MANUAL_REVIEW
            result.requires_manual_review = True
            result.review_reasons.extend(
                provider_result.get("consider_reasons", ["Partial match"])
            )
        else:
            result.status = VerificationStatus.FAILED
            result.failure_reasons.extend(
                provider_result.get("failure_reasons", ["Verification failed"])
            )

        # PEP/Sanctions flags
        result.pep_match = provider_result.get("pep_match", False)
        result.sanctions_match = provider_result.get("sanctions_match", False)
        if result.pep_match or result.sanctions_match:
            result.requires_manual_review = True
            result.review_reasons.append(
                "PEP/sanctions match detected - manual review required"
            )

        # Step 5: Update account status
        await self._update_account_verification(player_id, result)

        # Step 6: Persist
        await self._persist_result(result)

        logger.info(
            "Verification complete: player=%s status=%s verified=%s age=%s",
            player_id, result.status.value, result.verified, result.estimated_age,
        )

        return result

    async def check_verification_status(self, player_id: str) -> Optional[dict]:
        """Check current verification status for a player."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT status, verified, age_confirmed, provider, provider_reference,
                       pep_match, sanctions_match, created_at
                FROM player_verifications
                WHERE player_id = $1
                ORDER BY created_at DESC LIMIT 1
            """, player_id)

        if not row:
            return None
        return dict(row)

    async def is_verified(self, player_id: str) -> bool:
        """Quick check if player is verified (for gate checks)."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT verified FROM player_verifications
                WHERE player_id = $1 AND verified = TRUE
                LIMIT 1
            """, player_id)
        return row is not None

    # -------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------

    async def _run_provider_check(
        self,
        identity: PlayerIdentity,
        level: VerificationLevel,
        provider: IdentityProvider,
        provider_name: str,
    ) -> dict:
        """Run verification through a provider."""
        applicant_id = await provider.create_applicant(identity)
        check_type = "enhanced" if level == VerificationLevel.ENHANCED else "standard"
        check = await provider.run_check(applicant_id, check_type)
        check_id = check.get("check_id", "")

        # Poll for result (most providers are async)
        max_attempts = 30
        for attempt in range(max_attempts):
            result = await provider.get_check_result(check_id)
            if result.get("status") in ("complete", "PROCESSED"):
                return {
                    "provider": provider_name,
                    "reference": check_id,
                    "result": result.get("result", "consider"),
                    "checks": check.get("report_names", []),
                    "match_score": result.get("match_score", 0.0),
                    "pep_match": result.get("pep_match", False),
                    "sanctions_match": result.get("sanctions_match", False),
                }
            await asyncio.sleep(2)

        # Timed out
        return {
            "provider": provider_name,
            "reference": check_id,
            "result": "consider",
            "consider_reasons": ["Verification timed out - manual review needed"],
        }

    async def _block_underage_account(self, player_id: str, age: int) -> None:
        """
        Block underage account and trigger compliance reporting.
        UK Gambling Act 2005 s.46: offence to allow under-18 to gamble.
        """
        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE player_accounts
                SET status = 'blocked',
                    block_reason = 'underage',
                    blocked_at = NOW()
                WHERE player_id = $1
            """, player_id)

            # Log underage attempt for regulatory reporting
            await conn.execute("""
                INSERT INTO underage_attempts
                    (player_id, estimated_age, detected_at)
                VALUES ($1, $2, NOW())
            """, player_id, age)

        logger.critical(
            "UNDERAGE ACCOUNT BLOCKED: player=%s age=%d", player_id, age
        )

    async def _update_account_verification(
        self, player_id: str, result: VerificationResult
    ) -> None:
        async with self.db.acquire() as conn:
            if result.verified:
                await conn.execute("""
                    UPDATE player_accounts
                    SET verification_status = 'verified',
                        verified_at = NOW(),
                        kyc_level = 'standard'
                    WHERE player_id = $1
                """, player_id)
            elif result.status == VerificationStatus.FAILED:
                # UKGC: if verification fails, close account and return deposits
                await conn.execute("""
                    UPDATE player_accounts
                    SET verification_status = 'failed',
                        status = 'suspended',
                        suspension_reason = 'verification_failed'
                    WHERE player_id = $1
                """, player_id)

    async def _persist_result(self, result: VerificationResult) -> None:
        import json

        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO player_verifications
                    (player_id, status, verified, age_confirmed, estimated_age,
                     identity_match_score, pep_match, sanctions_match,
                     provider, provider_reference,
                     checks_performed, failure_reasons, review_reasons,
                     created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, NOW())
            """,
                result.player_id,
                result.status.value,
                result.verified,
                result.age_confirmed,
                result.estimated_age,
                result.identity_match_score,
                result.pep_match,
                result.sanctions_match,
                result.provider,
                result.provider_reference,
                result.checks_performed,
                result.failure_reasons,
                result.review_reasons,
            )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS player_verifications (
    verification_id     BIGSERIAL PRIMARY KEY,
    player_id           VARCHAR(64) NOT NULL,
    status              VARCHAR(20) NOT NULL,
    verified            BOOLEAN NOT NULL DEFAULT FALSE,
    age_confirmed       BOOLEAN NOT NULL DEFAULT FALSE,
    estimated_age       INTEGER,
    identity_match_score NUMERIC(4,2) DEFAULT 0,
    pep_match           BOOLEAN DEFAULT FALSE,
    sanctions_match     BOOLEAN DEFAULT FALSE,
    provider            VARCHAR(20),
    provider_reference  VARCHAR(100),
    checks_performed    TEXT[],
    failure_reasons     TEXT[],
    review_reasons      TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_verification_player
    ON player_verifications (player_id, created_at DESC);

CREATE TABLE IF NOT EXISTS underage_attempts (
    attempt_id          BIGSERIAL PRIMARY KEY,
    player_id           VARCHAR(64) NOT NULL,
    estimated_age       INTEGER NOT NULL,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE player_verifications IS
    'Age and identity verification records. UKGC LCCP 17.1.1 compliance.';
COMMENT ON TABLE underage_attempts IS
    'Underage gambling attempts. Must be reported to UKGC.';
"""
