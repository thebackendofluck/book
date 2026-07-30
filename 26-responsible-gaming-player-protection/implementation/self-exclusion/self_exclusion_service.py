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
Self-Exclusion Service
Chapter 10 - Responsible Gaming and Player Protection

Manages player self-exclusion with integration to national exclusion schemes
(GAMSTOP for UK, OASIS for Germany, ROFUS for Denmark, Spelpaus for Sweden)
and cross-operator synchronization.

Compliance References:
- UKGC LCCP 3.5.1: Self-exclusion scheme participation mandatory
- UKGC LCCP 3.5.3: GAMSTOP integration required for all GB licensees
- MGA Directive 3 of 2018: Self-exclusion implementation requirements
- UKGC: Minimum self-exclusion period 6 months, maximum 5 years
- UKGC: Accounts must be closed during exclusion (not just suspended)
- MGA: Player can request 1-month "cooling off" or 6-month minimum exclusion

Architecture:
    Player Request --> Validation --> Account Closure
                                 --> National Scheme Registration (GAMSTOP)
                                 --> Cross-operator Notification
                                 --> Marketing Suppression
                                 --> Balance Withdrawal Processing

Usage:
    service = SelfExclusionService(db_pool, redis, gamstop_client)
    result = await service.self_exclude(
        player_id="player_123",
        duration_months=6,
        reason="player_request",
    )
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional, Protocol

import asyncpg  # ty:ignore[unresolved-import]
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

CROSS_OPERATOR_SYNC_MAX_ATTEMPTS = 5
CROSS_OPERATOR_SYNC_BACKOFF_BASE_SECONDS = 2


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class ExclusionType(str, Enum):
    SELF_EXCLUSION = "self_exclusion"        # Player-initiated (UKGC 3.5.1)
    OPERATOR_EXCLUSION = "operator_exclusion"  # Operator-initiated (RG concern)
    REGULATORY = "regulatory"                 # Regulator-mandated
    NATIONAL_SCHEME = "national_scheme"       # Via GAMSTOP/OASIS/etc.


class ExclusionStatus(str, Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"           # Only by regulator
    PENDING_REINSTATEMENT = "pending_reinstatement"


class NationalScheme(str, Enum):
    GAMSTOP = "gamstop"     # UK - mandatory for UKGC licensees
    OASIS = "oasis"         # Germany
    ROFUS = "rofus"         # Denmark
    SPELPAUS = "spelpaus"   # Sweden
    CRUKS = "cruks"         # Netherlands


@dataclass
class ExclusionRecord:
    exclusion_id: Optional[int]
    player_id: str
    exclusion_type: ExclusionType
    status: ExclusionStatus
    started_at: datetime
    expires_at: datetime
    duration_months: int
    reason: str
    national_scheme: Optional[NationalScheme] = None
    national_scheme_ref: Optional[str] = None
    balance_at_exclusion: float = 0.0
    balance_withdrawn: bool = False
    marketing_suppressed: bool = False
    accounts_closed: list[str] = field(default_factory=list)


@dataclass
class ExclusionResult:
    success: bool
    exclusion_id: Optional[int] = None
    record: Optional[ExclusionRecord] = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# National Scheme Client Protocol
# ---------------------------------------------------------------------------

class NationalSchemeClient(Protocol):
    """Interface for national self-exclusion scheme APIs."""

    async def register_exclusion(
        self, player_data: dict, duration_months: int
    ) -> dict:
        """Register player with national scheme. Returns reference ID."""
        ...

    async def check_exclusion(self, player_data: dict) -> Optional[dict]:
        """Check if player is on national exclusion register."""
        ...

    async def revoke_exclusion(self, reference_id: str) -> bool:
        """Revoke an exclusion (only if permitted by scheme rules)."""
        ...


# ---------------------------------------------------------------------------
# GAMSTOP Client Implementation
# ---------------------------------------------------------------------------

class GAMSTOPClient:
    """
    Client for GAMSTOP API integration.
    GAMSTOP is the UK's national online self-exclusion scheme.
    All UKGC-licensed operators MUST participate (LCCP 3.5.3).

    API Documentation: https://www.gamstop.co.uk/for-operators
    """

    def __init__(self, api_key: str, api_url: str = "https://api.gamstop.co.uk/v2"):
        self.api_key = api_key
        self.api_url = api_url

    async def register_exclusion(
        self, player_data: dict, duration_months: int
    ) -> dict:
        """
        Register self-exclusion with GAMSTOP.

        Required player_data fields:
        - first_name, last_name
        - date_of_birth (YYYY-MM-DD)
        - email
        - postcode
        - at least one of: mobile_number, landline_number

        Duration options: 6 months, 1 year, 5 years
        """
        import aiohttp

        # GAMSTOP only accepts specific durations
        valid_durations = {6: "6_MONTHS", 12: "1_YEAR", 60: "5_YEARS"}
        gamstop_duration = valid_durations.get(duration_months)
        if not gamstop_duration:
            # Round up to nearest valid duration
            if duration_months <= 6:
                gamstop_duration = "6_MONTHS"
            elif duration_months <= 12:
                gamstop_duration = "1_YEAR"
            else:
                gamstop_duration = "5_YEARS"

        payload = {
            "firstName": player_data["first_name"],
            "lastName": player_data["last_name"],
            "dateOfBirth": player_data["date_of_birth"],
            "email": player_data["email"],
            "postcode": player_data.get("postcode", ""),
            "mobileNumber": player_data.get("mobile_number", ""),
            "exclusionDuration": gamstop_duration,
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.api_url}/exclusions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 201:
                    data = await resp.json()
                    return {
                        "reference_id": data["exclusionId"],
                        "status": "registered",
                        "expires_at": data.get("expiresAt"),
                    }
                else:
                    error = await resp.text()
                    raise RuntimeError(
                        f"GAMSTOP registration failed ({resp.status}): {error}"
                    )

    async def check_exclusion(self, player_data: dict) -> Optional[dict]:
        """
        Check if a player is on GAMSTOP register.
        MUST be called during registration and login (UKGC requirement).
        """
        import aiohttp

        payload = {
            "firstName": player_data["first_name"],
            "lastName": player_data["last_name"],
            "dateOfBirth": player_data["date_of_birth"],
            "email": player_data["email"],
            "postcode": player_data.get("postcode", ""),
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.api_url}/check",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("excluded", False):
                        return {
                            "excluded": True,
                            "expires_at": data.get("expiresAt"),
                            "reference_id": data.get("exclusionId"),
                        }
                    return None
                elif resp.status == 404:
                    return None
                else:
                    error = await resp.text()
                    logger.error("GAMSTOP check failed: %s %s", resp.status, error)
                    # Fail closed: treat as excluded if GAMSTOP is unreachable
                    # UKGC expects operators to err on the side of caution
                    raise RuntimeError(f"GAMSTOP check failed: {resp.status}")


# ---------------------------------------------------------------------------
# Self-Exclusion Service
# ---------------------------------------------------------------------------

class SelfExclusionService:
    """
    Comprehensive self-exclusion management.

    When a player self-excludes, the following MUST happen:
    1. All active sessions terminated immediately
    2. Account closed (not just suspended - UKGC requirement)
    3. All pending bets voided and stakes returned
    4. Player balance offered for withdrawal (within 30 days)
    5. All marketing communications stopped
    6. National scheme registration (GAMSTOP for UK)
    7. Cross-operator notification (if multi-brand operator)
    8. Login blocked for duration of exclusion
    9. No promotional material sent for duration + 1 year

    UKGC: Minimum exclusion period is 6 months. No early termination.
    After expiry, player must proactively request reinstatement.
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis: aioredis.Redis,
        national_scheme_client: Optional[NationalSchemeClient] = None,
        scheme_name: Optional[NationalScheme] = None,
    ):
        self.db = db_pool
        self.redis = redis
        self.national_client = national_scheme_client
        self.scheme_name = scheme_name

    async def self_exclude(
        self,
        player_id: str,
        duration_months: int,
        reason: str = "player_request",
        player_data: Optional[dict] = None,
    ) -> ExclusionResult:
        """
        Process a self-exclusion request.

        UKGC minimum duration: 6 months
        UKGC maximum duration: 5 years (or indefinite)
        """
        errors = []
        warnings = []

        # Validate duration
        if duration_months < 6:
            return ExclusionResult(
                success=False,
                errors=["Minimum self-exclusion period is 6 months (UKGC LCCP 3.5.1)"],
            )

        # Check if already excluded
        existing = await self._get_active_exclusion(player_id)
        if existing:
            return ExclusionResult(
                success=False,
                errors=["Player is already self-excluded"],
                record=existing,
            )

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(days=duration_months * 30)

        # Step 1: Terminate all active sessions
        await self._terminate_sessions(player_id)

        # Step 2: Get balance for withdrawal processing
        balance = await self._get_player_balance(player_id)

        # Step 3: Close account
        await self._close_account(player_id)

        # Step 4: Void pending bets, return stakes
        voided_count = await self._void_pending_bets(player_id)
        if voided_count > 0:
            warnings.append(f"{voided_count} pending bets voided, stakes returned")

        # Step 5: Suppress marketing
        await self._suppress_marketing(player_id, expires_at)

        # Step 6: Register with national scheme
        national_ref = None
        if self.national_client and player_data:
            try:
                scheme_result = await self.national_client.register_exclusion(
                    player_data, duration_months
                )
                national_ref = scheme_result.get("reference_id")
            except Exception as e:
                logger.error("National scheme registration failed: %s", e)
                errors.append(f"National scheme registration failed: {e}")
                # Continue with local exclusion even if national scheme fails

        # Step 7: Block login
        await self._block_login(player_id, expires_at)

        # Step 8: Persist exclusion record
        record = ExclusionRecord(
            exclusion_id=None,
            player_id=player_id,
            exclusion_type=ExclusionType.SELF_EXCLUSION,
            status=ExclusionStatus.ACTIVE,
            started_at=now,
            expires_at=expires_at,
            duration_months=duration_months,
            reason=reason,
            national_scheme=self.scheme_name,
            national_scheme_ref=national_ref,
            balance_at_exclusion=balance,
            marketing_suppressed=True,
        )
        exclusion_id = await self._persist_exclusion(record)
        record.exclusion_id = exclusion_id

        # Step 9: Notify cross-operator sync (async, non-blocking)
        asyncio.create_task(self._notify_cross_operator(player_id, record))

        logger.info(
            "Self-exclusion processed: player=%s duration=%d months expires=%s",
            player_id, duration_months, expires_at.isoformat(),
        )

        return ExclusionResult(
            success=True,
            exclusion_id=exclusion_id,
            record=record,
            errors=errors,
            warnings=warnings,
        )

    async def check_exclusion_status(self, player_id: str) -> Optional[ExclusionRecord]:
        """
        Check if player is currently excluded.
        MUST be called at login and registration (UKGC requirement).
        """
        # Check local cache first (Redis). Key must match the writer in
        # _block_login (rg:excluded:) and cross_operator_sync.
        cache_key = f"rg:excluded:{player_id}"
        cached = await self.redis.get(cache_key)
        if cached:
            data = json.loads(cached)
            if data.get("status") == "active":
                expires_at = datetime.fromisoformat(data["expires_at"])
                if expires_at > datetime.now(timezone.utc):
                    return ExclusionRecord(
                        exclusion_id=data.get("exclusion_id"),
                        player_id=player_id,
                        exclusion_type=ExclusionType(data["exclusion_type"]),
                        status=ExclusionStatus.ACTIVE,
                        started_at=datetime.fromisoformat(data["started_at"]),
                        expires_at=expires_at,
                        duration_months=data.get("duration_months", 6),
                        reason=data.get("reason", ""),
                    )

        # Check database
        return await self._get_active_exclusion(player_id)

    async def check_gamstop(self, player_data: dict) -> Optional[dict]:
        """
        Check GAMSTOP register. UKGC requires this check at:
        - Account registration
        - Every login
        - Periodically for existing accounts
        """
        if not self.national_client:
            logger.warning("No national scheme client configured")
            return None

        return await self.national_client.check_exclusion(player_data)

    async def process_reinstatement_request(
        self, player_id: str
    ) -> dict:
        """
        Process a request to reinstate after exclusion expiry.

        UKGC rules:
        - Reinstatement is NOT automatic after expiry
        - Player must proactively request reinstatement
        - Operator must apply 24h cooling-off before reinstatement
        - Operator should conduct welfare check before reinstating
        """
        exclusion = await self._get_active_exclusion(player_id)
        if not exclusion:
            return {"status": "no_active_exclusion"}

        if exclusion.expires_at > datetime.now(timezone.utc):
            remaining_days = (exclusion.expires_at - datetime.now(timezone.utc)).days
            return {
                "status": "still_excluded",
                "expires_at": exclusion.expires_at.isoformat(),
                "remaining_days": remaining_days,
            }

        # Exclusion has expired - apply 24h reinstatement delay
        reinstatement_at = datetime.now(timezone.utc) + timedelta(hours=24)

        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE player_exclusions
                SET status = 'pending_reinstatement',
                    reinstatement_requested_at = NOW(),
                    reinstatement_effective_at = $2,
                    updated_at = NOW()
                WHERE player_id = $1 AND status = 'active'
            """, player_id, reinstatement_at)

        return {
            "status": "pending_reinstatement",
            "reinstatement_at": reinstatement_at.isoformat(),
            "message": "Your reinstatement request has been received. "
                       "A 24-hour cooling-off period applies. "
                       "Our team may contact you for a welfare check.",
        }

    # -------------------------------------------------------------------
    # Internal operations
    # -------------------------------------------------------------------

    async def _terminate_sessions(self, player_id: str) -> None:
        """Force-close all active sessions and websockets."""
        session_key = f"rg:session:{player_id}"
        await self.redis.delete(session_key)
        # Publish session termination event for websocket servers
        await self.redis.publish(
            "rg:session_terminate",
            json.dumps({"player_id": player_id, "reason": "self_exclusion"}),
        )

    async def _get_player_balance(self, player_id: str) -> float:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT balance FROM player_accounts WHERE player_id = $1",
                player_id,
            )
        return float(row["balance"]) if row else 0.0

    async def _close_account(self, player_id: str) -> None:
        """
        Close the player account. UKGC: accounts must be CLOSED, not suspended.
        """
        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE player_accounts
                SET status = 'closed',
                    closure_reason = 'self_exclusion',
                    closed_at = NOW(),
                    updated_at = NOW()
                WHERE player_id = $1
            """, player_id)

    async def _void_pending_bets(self, player_id: str) -> int:
        """Void all unsettled bets and return stakes."""
        async with self.db.acquire() as conn:
            result = await conn.execute("""
                UPDATE player_bets
                SET status = 'voided',
                    void_reason = 'self_exclusion',
                    voided_at = NOW()
                WHERE player_id = $1 AND status = 'pending'
            """, player_id)
            # Parse "UPDATE N" to get count
            count = int(result.split()[-1]) if result else 0

            # Return stakes to balance
            if count > 0:
                await conn.execute("""
                    UPDATE player_accounts
                    SET balance = balance + COALESCE(
                        (SELECT SUM(stake) FROM player_bets
                         WHERE player_id = $1 AND void_reason = 'self_exclusion'),
                        0
                    )
                    WHERE player_id = $1
                """, player_id)

        return count

    async def _suppress_marketing(self, player_id: str, until: datetime) -> None:
        """
        Suppress all marketing. UKGC: no marketing for duration + minimum 1 year.
        """
        marketing_suppression_until = until + timedelta(days=365)
        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO marketing_suppressions
                    (player_id, reason, suppressed_until, created_at)
                VALUES ($1, 'self_exclusion', $2, NOW())
                ON CONFLICT (player_id)
                DO UPDATE SET suppressed_until = GREATEST(
                    marketing_suppressions.suppressed_until, $2
                ), updated_at = NOW()
            """, player_id, marketing_suppression_until)

    async def _block_login(self, player_id: str, until: datetime) -> None:
        """Block login via Redis with expiry matching exclusion duration."""
        ttl_seconds = int((until - datetime.now(timezone.utc)).total_seconds())
        block_key = f"rg:excluded:{player_id}"
        await self.redis.set(block_key, json.dumps({
            "status": "active",
            "exclusion_type": "self_exclusion",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": until.isoformat(),
        }), ex=max(1, ttl_seconds))

    async def _persist_exclusion(self, record: ExclusionRecord) -> int:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO player_exclusions
                    (player_id, exclusion_type, status, started_at, expires_at,
                     duration_months, reason, national_scheme, national_scheme_ref,
                     balance_at_exclusion, marketing_suppressed, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW())
                RETURNING exclusion_id
            """,
                record.player_id,
                record.exclusion_type.value,
                record.status.value,
                record.started_at,
                record.expires_at,
                record.duration_months,
                record.reason,
                record.national_scheme.value if record.national_scheme else None,
                record.national_scheme_ref,
                record.balance_at_exclusion,
                record.marketing_suppressed,
            )
        return row["exclusion_id"]

    async def _get_active_exclusion(self, player_id: str) -> Optional[ExclusionRecord]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT exclusion_id, exclusion_type, status, started_at,
                       expires_at, duration_months, reason,
                       national_scheme, national_scheme_ref,
                       balance_at_exclusion, marketing_suppressed
                FROM player_exclusions
                WHERE player_id = $1 AND status = 'active'
                ORDER BY started_at DESC LIMIT 1
            """, player_id)

        if not row:
            return None

        return ExclusionRecord(
            exclusion_id=row["exclusion_id"],
            player_id=player_id,
            exclusion_type=ExclusionType(row["exclusion_type"]),
            status=ExclusionStatus(row["status"]),
            started_at=row["started_at"],
            expires_at=row["expires_at"],
            duration_months=row["duration_months"],
            reason=row["reason"],
            national_scheme=NationalScheme(row["national_scheme"]) if row["national_scheme"] else None,
            national_scheme_ref=row["national_scheme_ref"],
            balance_at_exclusion=float(row["balance_at_exclusion"]),
            marketing_suppressed=row["marketing_suppressed"],
        )

    async def _notify_cross_operator(
        self, player_id: str, record: ExclusionRecord
    ) -> None:
        """
        Notify cross-operator sync service (see cross_operator_sync.py).

        A bare fire-and-forget publish is not durable: a transient Redis
        outage would previously drop the event silently, leaving sister
        brands unaware that this player is excluded. This retries with
        backoff and, if every attempt fails, persists a durable pending
        record so a scheduled job can recover and re-publish it instead of
        losing the event outright.
        """
        payload = {
            "player_id": player_id,
            "exclusion_type": record.exclusion_type.value,
            "started_at": record.started_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
            "national_scheme_ref": record.national_scheme_ref,
        }

        for attempt in range(CROSS_OPERATOR_SYNC_MAX_ATTEMPTS):
            try:
                await self.redis.publish(
                    "rg:cross_operator_exclusion", json.dumps(payload)
                )
                return
            except Exception as exc:
                logger.warning(
                    "Cross-operator notification attempt %d/%d failed for %s: %s",
                    attempt + 1, CROSS_OPERATOR_SYNC_MAX_ATTEMPTS, player_id, exc,
                )
                await asyncio.sleep(
                    CROSS_OPERATOR_SYNC_BACKOFF_BASE_SECONDS * (2 ** attempt)
                )

        try:
            async with self.db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO cross_operator_sync_pending
                        (player_id, payload, status, attempts, created_at, updated_at)
                    VALUES ($1, $2::jsonb, 'pending', $3, NOW(), NOW())
                """, player_id, json.dumps(payload), CROSS_OPERATOR_SYNC_MAX_ATTEMPTS)
            logger.error(
                "Cross-operator notification exhausted retries for %s; "
                "persisted to cross_operator_sync_pending for later recovery",
                player_id,
            )
        except Exception:
            logger.exception(
                "Cross-operator notification failed AND dead-letter persistence "
                "failed for %s -- event is lost",
                player_id,
            )

    async def retry_pending_cross_operator_notifications(self, limit: int = 100) -> int:
        """
        Recover cross-operator notifications that exhausted their inline
        retries. Run this as a periodic scheduled job (e.g. every few
        minutes). Returns the number of rows successfully re-delivered.
        """
        delivered = 0
        async with self.db.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, player_id, payload FROM cross_operator_sync_pending
                WHERE status = 'pending'
                ORDER BY created_at
                LIMIT $1
            """, limit)

            for row in rows:
                try:
                    await self.redis.publish(
                        "rg:cross_operator_exclusion", row["payload"]
                    )
                    await conn.execute("""
                        UPDATE cross_operator_sync_pending
                        SET status = 'delivered', updated_at = NOW()
                        WHERE id = $1
                    """, row["id"])
                    delivered += 1
                except Exception:
                    logger.exception(
                        "Retry of pending cross-operator notification %s failed",
                        row["id"],
                    )
        return delivered


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS player_exclusions (
    exclusion_id        BIGSERIAL PRIMARY KEY,
    player_id           VARCHAR(64) NOT NULL,
    exclusion_type      VARCHAR(30) NOT NULL,
    status              VARCHAR(30) NOT NULL DEFAULT 'active',
    started_at          TIMESTAMPTZ NOT NULL,
    expires_at          TIMESTAMPTZ NOT NULL,
    duration_months     INTEGER NOT NULL,
    reason              VARCHAR(200),
    national_scheme     VARCHAR(20),
    national_scheme_ref VARCHAR(100),
    balance_at_exclusion NUMERIC(14,2) DEFAULT 0,
    balance_withdrawn   BOOLEAN DEFAULT FALSE,
    marketing_suppressed BOOLEAN DEFAULT TRUE,
    reinstatement_requested_at TIMESTAMPTZ,
    reinstatement_effective_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_exclusion_player_status
    ON player_exclusions (player_id, status);

CREATE TABLE IF NOT EXISTS marketing_suppressions (
    player_id           VARCHAR(64) PRIMARY KEY,
    reason              VARCHAR(50) NOT NULL,
    suppressed_until    TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ
);

-- Durable outbox for cross-operator exclusion notifications that
-- exhausted their inline retries. Recovered by
-- retry_pending_cross_operator_notifications().
CREATE TABLE IF NOT EXISTS cross_operator_sync_pending (
    id                  BIGSERIAL PRIMARY KEY,
    player_id           VARCHAR(64) NOT NULL,
    payload             JSONB NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'pending',
    attempts            INTEGER NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cross_operator_sync_pending_status
    ON cross_operator_sync_pending (status, created_at);

COMMENT ON TABLE player_exclusions IS
    'Self-exclusion records. Minimum 6 months per UKGC. No early termination.';
"""
