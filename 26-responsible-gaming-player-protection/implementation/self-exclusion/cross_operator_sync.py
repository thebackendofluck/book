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
Cross-Operator Exclusion Synchronization
Chapter 10 - Responsible Gaming and Player Protection

Synchronizes self-exclusion data across multiple operator brands/platforms.
Large operators (e.g., Flutter, Entain, Kindred) often run multiple brands
that must share exclusion data to prevent excluded players from simply
switching to a sister site.

Compliance References:
- UKGC LCCP 3.5.2: Multi-operator self-exclusion sharing
- UKGC: All brands under same licence must share exclusion registers
- MGA: Operators with multiple licences must implement cross-brand exclusion

Architecture:
    Brand A exclusion --> Redis Pub/Sub --> Sync Service --> Brand B, C, D
                                                        --> National scheme check
                                                        --> Shared exclusion DB

    Alternative: Webhook-based sync for operators on separate infrastructure.

Usage:
    sync = CrossOperatorSync(db_pool, redis, operator_brands)
    await sync.start_listener()  # Run as background service
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import asyncpg  # ty:ignore[unresolved-import]
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


@dataclass
class OperatorBrand:
    """Configuration for a sister brand in the operator group."""
    brand_id: str
    brand_name: str
    # Webhook URL for push-based sync (external brands)
    webhook_url: Optional[str] = None
    # API key for authenticating with the brand's API
    api_key: Optional[str] = None
    # Whether this brand shares the same database (internal sync)
    shared_database: bool = False


@dataclass
class ExclusionSyncEvent:
    """Event payload for cross-operator exclusion sync."""
    player_id: str
    # Hashed PII for matching across brands (SHA-256 of email+dob)
    player_hash: str
    exclusion_type: str
    started_at: str
    expires_at: str
    source_brand: str
    national_scheme_ref: Optional[str] = None


class CrossOperatorSync:
    """
    Synchronizes self-exclusion across operator brands.

    Two sync modes:
    1. Internal (shared DB): Direct database writes for sister brands
       on the same infrastructure.
    2. External (webhook): HTTP push notifications for brands on
       separate infrastructure.

    Player matching uses a SHA-256 hash of normalized (email + date_of_birth)
    to avoid sharing PII between systems that may have separate data
    controllers under GDPR.
    """

    CHANNEL = "rg:cross_operator_exclusion"

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis: aioredis.Redis,
        brands: list[OperatorBrand],
        own_brand_id: str,
    ):
        self.db = db_pool
        self.redis = redis
        self.brands = {b.brand_id: b for b in brands}
        self.own_brand_id = own_brand_id

    @staticmethod
    def compute_player_hash(email: str, date_of_birth: str) -> str:
        """
        Generate a cross-brand player identifier from PII.
        Normalized: lowercase email + ISO date of birth.
        This avoids sharing raw PII between brands under GDPR.
        """
        normalized = f"{email.strip().lower()}:{date_of_birth.strip()}"
        return hashlib.sha256(normalized.encode()).hexdigest()

    async def start_listener(self) -> None:
        """
        Start listening for exclusion events from other brands.
        Run this as a long-lived background service.
        """
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(self.CHANNEL)
        logger.info("Cross-operator sync listener started for brand %s", self.own_brand_id)

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    await self._handle_sync_event(data)
                except Exception:
                    logger.exception("Error processing cross-operator sync event")
        finally:
            await pubsub.unsubscribe(self.CHANNEL)

    async def publish_exclusion(
        self,
        player_id: str,
        player_email: str,
        player_dob: str,
        exclusion_type: str,
        started_at: datetime,
        expires_at: datetime,
        national_scheme_ref: Optional[str] = None,
    ) -> None:
        """Publish an exclusion event to all sister brands."""
        event = ExclusionSyncEvent(
            player_id=player_id,
            player_hash=self.compute_player_hash(player_email, player_dob),
            exclusion_type=exclusion_type,
            started_at=started_at.isoformat(),
            expires_at=expires_at.isoformat(),
            source_brand=self.own_brand_id,
            national_scheme_ref=national_scheme_ref,
        )

        # Publish to Redis for internal brands
        await self.redis.publish(self.CHANNEL, json.dumps(event.__dict__))

        # Push to external brands via webhooks
        for brand in self.brands.values():
            if brand.brand_id == self.own_brand_id:
                continue
            if brand.webhook_url:
                asyncio.create_task(
                    self._send_webhook(brand, event)
                )

        # Record sync event for audit
        await self._record_sync_event(event, "published")

        logger.info(
            "Exclusion published for cross-operator sync: player_hash=%s brands=%d",
            event.player_hash[:16], len(self.brands) - 1,
        )

    async def _handle_sync_event(self, data: dict) -> None:
        """Process an incoming exclusion sync event from another brand."""
        source_brand = data.get("source_brand")
        if source_brand == self.own_brand_id:
            return  # Ignore own events

        player_hash = data.get("player_hash")
        if not player_hash:
            return

        # Look up local player by hash
        local_player = await self._find_player_by_hash(player_hash)
        if not local_player:
            logger.debug(
                "No matching local player for hash %s from brand %s",
                player_hash[:16], source_brand,
            )
            return

        # Apply exclusion locally
        await self._apply_cross_brand_exclusion(
            player_id=local_player["player_id"],
            exclusion_type=data.get("exclusion_type", "self_exclusion"),
            started_at=data.get("started_at"),  # ty:ignore[invalid-argument-type]
            expires_at=data.get("expires_at"),  # ty:ignore[invalid-argument-type]
            source_brand=source_brand,  # ty:ignore[invalid-argument-type]
        )

        await self._record_sync_event(
            ExclusionSyncEvent(**data), "received_and_applied"
        )

        logger.info(
            "Cross-brand exclusion applied: local_player=%s source=%s",
            local_player["player_id"], source_brand,
        )

    async def _find_player_by_hash(self, player_hash: str) -> Optional[dict]:
        """Find local player matching the cross-brand hash."""
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT player_id, email, date_of_birth
                FROM players
                WHERE player_hash = $1
                LIMIT 1
            """, player_hash)
        return dict(row) if row else None

    async def _apply_cross_brand_exclusion(
        self,
        player_id: str,
        exclusion_type: str,
        started_at: str,
        expires_at: str,
        source_brand: str,
    ) -> None:
        """Apply exclusion received from a sister brand."""
        async with self.db.acquire() as conn:
            # Check if already excluded
            existing = await conn.fetchrow("""
                SELECT exclusion_id FROM player_exclusions
                WHERE player_id = $1 AND status = 'active'
            """, player_id)

            if existing:
                logger.info("Player %s already excluded locally", player_id)
                return

            # Insert cross-brand exclusion
            await conn.execute("""
                INSERT INTO player_exclusions
                    (player_id, exclusion_type, status, started_at, expires_at,
                     duration_months, reason, created_at)
                VALUES ($1, $2, 'active', $3, $4,
                        (EXTRACT(YEAR FROM AGE($4::timestamptz, $3::timestamptz)) * 12
                         + EXTRACT(MONTH FROM AGE($4::timestamptz, $3::timestamptz)))::int,
                        $5, NOW())
            """,
                player_id,
                exclusion_type,
                started_at,
                expires_at,
                f"cross_brand_sync:{source_brand}",
            )

            # Close account
            await conn.execute("""
                UPDATE player_accounts
                SET status = 'closed',
                    closure_reason = 'cross_brand_exclusion',
                    closed_at = NOW()
                WHERE player_id = $1 AND status != 'closed'
            """, player_id)

        # Block login in Redis
        expires_dt = datetime.fromisoformat(expires_at)
        ttl = int((expires_dt - datetime.now(timezone.utc)).total_seconds())
        if ttl > 0:
            await self.redis.set(
                f"rg:excluded:{player_id}",
                json.dumps({
                    "status": "active",
                    "exclusion_type": exclusion_type,
                    "source": f"cross_brand:{source_brand}",
                    "expires_at": expires_at,
                }),
                ex=ttl,
            )

        # Terminate active sessions
        await self.redis.publish(
            "rg:session_terminate",
            json.dumps({"player_id": player_id, "reason": "cross_brand_exclusion"}),
        )

    WEBHOOK_MAX_ATTEMPTS = 4
    WEBHOOK_BACKOFF_BASE_SECONDS = 2

    async def _send_webhook(
        self, brand: OperatorBrand, event: ExclusionSyncEvent
    ) -> None:
        """
        Send exclusion event to external brand via webhook.

        Retries with backoff before giving up. A single failed attempt
        previously meant a sister brand never learned about the exclusion;
        on exhausting all attempts this now records a dead-letter audit
        entry so the miss is visible and can be manually or programmatically
        recovered, rather than only appearing in application logs.
        """
        import aiohttp

        payload = {
            "event_type": "player_exclusion",
            "player_hash": event.player_hash,
            "exclusion_type": event.exclusion_type,
            "started_at": event.started_at,
            "expires_at": event.expires_at,
            "source_brand": event.source_brand,
        }

        for attempt in range(self.WEBHOOK_MAX_ATTEMPTS):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        brand.webhook_url,  # ty:ignore[invalid-argument-type]
                        json=payload,
                        headers={
                            "Authorization": f"Bearer {brand.api_key}",
                            "Content-Type": "application/json",
                            "X-Event-Type": "player_exclusion",
                        },
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status in (200, 201, 202):
                            return
                        body = await resp.text()
                        logger.error(
                            "Webhook failed for brand %s (attempt %d/%d): %d %s",
                            brand.brand_id, attempt + 1, self.WEBHOOK_MAX_ATTEMPTS,
                            resp.status, body,
                        )
            except Exception:
                logger.exception(
                    "Webhook delivery failed for brand %s (attempt %d/%d)",
                    brand.brand_id, attempt + 1, self.WEBHOOK_MAX_ATTEMPTS,
                )
            await asyncio.sleep(self.WEBHOOK_BACKOFF_BASE_SECONDS * (2 ** attempt))

        logger.error(
            "Webhook exhausted all retries for brand %s, player_hash=%s -- "
            "recording dead-letter",
            brand.brand_id, event.player_hash[:16],
        )
        await self._record_sync_event(event, f"webhook_dead_letter:{brand.brand_id}")

    async def _record_sync_event(
        self, event: ExclusionSyncEvent, action: str
    ) -> None:
        """Audit log for all cross-operator sync events."""
        try:
            async with self.db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO cross_operator_sync_log
                        (player_hash, exclusion_type, source_brand,
                         action, event_data, created_at)
                    VALUES ($1, $2, $3, $4, $5::jsonb, NOW())
                """,
                    event.player_hash,
                    event.exclusion_type,
                    event.source_brand,
                    action,
                    json.dumps(event.__dict__),
                )
        except Exception:
            logger.exception("Failed to record sync event")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
-- Player hash column for cross-brand matching
ALTER TABLE players ADD COLUMN IF NOT EXISTS player_hash VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_player_hash ON players (player_hash);

-- Sync audit log
CREATE TABLE IF NOT EXISTS cross_operator_sync_log (
    log_id          BIGSERIAL PRIMARY KEY,
    player_hash     VARCHAR(64) NOT NULL,
    exclusion_type  VARCHAR(30) NOT NULL,
    source_brand    VARCHAR(50) NOT NULL,
    action          VARCHAR(30) NOT NULL,
    event_data      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_log_hash
    ON cross_operator_sync_log (player_hash, created_at DESC);

COMMENT ON TABLE cross_operator_sync_log IS
    'Audit trail for cross-operator exclusion sync events. GDPR: uses hashed PII only.';
"""
