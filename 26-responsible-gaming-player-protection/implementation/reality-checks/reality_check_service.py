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
Reality Check Service
Chapter 10 - Responsible Gaming and Player Protection

Implements configurable reality check timers that display session duration,
spending summaries, and responsible gambling messaging at regular intervals.

Compliance References:
- UKGC LCCP 3.3.2: Reality checks showing elapsed time and activity summary
- MGA PPD 2018: Mandatory session notifications at regular intervals
- Swedish Gambling Act: Reality check every 60 minutes (mandatory)
- UKGC: Default interval must be set to 60 minutes or less
- UKGC: Player can configure shorter intervals but NOT longer than operator default

Architecture:
    Session Start --> Timer Scheduled in Redis
                 --> Timer fires --> Fetch session stats
                                --> Build reality check message
                                --> Push via WebSocket to client
                                --> Record acknowledgment
                                --> Reschedule next check

Usage:
    service = RealityCheckService(db_pool, redis, websocket_manager)
    await service.schedule_check(player_id, session_id)
    # When timer fires:
    await service.deliver_reality_check(player_id, session_id)
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

import asyncpg  # ty:ignore[unresolved-import]
import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class RealityCheckConfig:
    """
    Reality check configuration per jurisdiction.

    UKGC: Default 60 min, player can reduce but not increase beyond default.
    MGA: Recommended 60 min, operator can set default.
    Sweden: Mandatory 60 min, cannot be disabled.
    """
    # Default interval in minutes
    default_interval_minutes: int = 60
    # Minimum interval the player can set (prevent abuse)
    min_interval_minutes: int = 15
    # Maximum interval (regulatory cap)
    max_interval_minutes: int = 60
    # Whether the player can disable reality checks
    allow_disable: bool = False
    # Whether to show net win/loss in the check
    show_net_result: bool = True
    # Whether to show deposit total in the check
    show_deposits: bool = True
    # Whether to pause the game during reality check display
    pause_game: bool = True
    # Seconds the player must wait before dismissing (anti-click-through)
    min_display_seconds: int = 5


JURISDICTION_CONFIGS = {
    "UKGC": RealityCheckConfig(
        default_interval_minutes=60,
        max_interval_minutes=60,
        allow_disable=False,
        show_net_result=True,
        pause_game=True,
        min_display_seconds=5,
    ),
    "MGA": RealityCheckConfig(
        default_interval_minutes=60,
        max_interval_minutes=120,
        allow_disable=False,
        show_net_result=True,
        pause_game=True,
    ),
    "SGA": RealityCheckConfig(  # Sweden
        default_interval_minutes=60,
        max_interval_minutes=60,
        allow_disable=False,
        show_net_result=True,
        show_deposits=True,
        pause_game=True,
        min_display_seconds=10,
    ),
}


@dataclass
class RealityCheckMessage:
    """Data payload sent to the player during a reality check."""
    player_id: str
    session_id: str
    elapsed_minutes: int
    total_wagered: float
    total_won: float
    total_lost: float
    net_result: float
    deposits_this_session: float
    game_type: str
    check_number: int  # How many checks this session
    # Actions the player can take
    available_actions: list[str]
    # Minimum seconds before the player can dismiss
    min_display_seconds: int = 5


class WebSocketManager(Protocol):
    """Interface for pushing reality checks to connected clients."""

    async def send_to_player(self, player_id: str, event: str, data: dict) -> bool:
        """Send event to player's active WebSocket connection."""
        ...


# ---------------------------------------------------------------------------
# Reality Check Service
# ---------------------------------------------------------------------------

class RealityCheckService:
    """
    Manages reality check scheduling, delivery, and acknowledgment tracking.

    Flow:
    1. On session start: schedule first reality check
    2. Timer fires: gather session stats, push message to client
    3. Game pauses, player sees summary with options:
       - Continue playing
       - Set a limit
       - Take a break (cool-off)
       - Log out
       - Visit help resources
    4. Player acknowledges, game resumes
    5. Reschedule next check
    6. All interactions are logged for UKGC compliance

    UKGC expects reality checks to be "meaningful" - not just a dismissible
    popup. The check should show real data and offer genuine choices.
    """

    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis: aioredis.Redis,
        ws_manager: WebSocketManager,
        jurisdiction: str = "UKGC",
    ):
        self.db = db_pool
        self.redis = redis
        self.ws = ws_manager
        self.config = JURISDICTION_CONFIGS.get(jurisdiction, RealityCheckConfig())

    async def set_player_interval(
        self, player_id: str, interval_minutes: int
    ) -> dict:
        """
        Allow player to customize their reality check interval.
        UKGC: Player can set shorter intervals but not longer than default.
        """
        if interval_minutes < self.config.min_interval_minutes:
            interval_minutes = self.config.min_interval_minutes
        if interval_minutes > self.config.max_interval_minutes:
            return {
                "status": "rejected",
                "reason": f"Maximum interval is {self.config.max_interval_minutes} minutes",
                "max_allowed": self.config.max_interval_minutes,
            }

        key = f"rg:rc_interval:{player_id}"
        await self.redis.set(key, str(interval_minutes))

        async with self.db.acquire() as conn:
            await conn.execute("""
                INSERT INTO player_rg_preferences (player_id, reality_check_interval_min, updated_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (player_id)
                DO UPDATE SET reality_check_interval_min = $2, updated_at = NOW()
            """, player_id, interval_minutes)

        return {
            "status": "set",
            "interval_minutes": interval_minutes,
        }

    async def get_player_interval(self, player_id: str) -> int:
        """Get player's configured reality check interval."""
        key = f"rg:rc_interval:{player_id}"
        cached = await self.redis.get(key)
        if cached:
            return int(cached)
        return self.config.default_interval_minutes

    async def schedule_check(self, player_id: str, session_id: str) -> None:
        """
        Schedule the first reality check for a session.
        Called when a player starts a new gaming session.
        """
        interval = await self.get_player_interval(player_id)

        timer_key = f"rg:rc_timer:{player_id}:{session_id}"
        await self.redis.set(
            timer_key,
            json.dumps({
                "player_id": player_id,
                "session_id": session_id,
                "check_number": 1,
                "scheduled_at": datetime.now(timezone.utc).isoformat(),
                "fires_at": (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(),
            }),
            ex=interval * 60 + 60,  # TTL slightly longer than interval
        )

        # Add to sorted set for timer processing
        fire_time = datetime.now(timezone.utc) + timedelta(minutes=interval)
        await self.redis.zadd(
            "rg:rc_pending",
            {f"{player_id}:{session_id}": fire_time.timestamp()},
        )

        logger.info(
            "Reality check scheduled: player=%s session=%s interval=%d min",
            player_id, session_id, interval,
        )

    async def process_pending_checks(self) -> int:
        """
        Process all reality checks that are due.
        Run this as a scheduled job every 10-30 seconds.
        """
        now = datetime.now(timezone.utc).timestamp()
        # Get all entries with score (fire_time) <= now
        due_checks = await self.redis.zrangebyscore(
            "rg:rc_pending", 0, now, start=0, num=100
        )

        count = 0
        for entry in due_checks:
            entry_str = entry.decode() if isinstance(entry, bytes) else entry
            parts = entry_str.split(":", 1)
            if len(parts) != 2:
                continue
            player_id, session_id = parts

            try:
                await self.deliver_reality_check(player_id, session_id)
                count += 1
            except Exception:
                logger.exception(
                    "Failed to deliver reality check: player=%s session=%s",
                    player_id, session_id,
                )

            # Remove from pending set
            await self.redis.zrem("rg:rc_pending", entry)

        return count

    async def deliver_reality_check(
        self, player_id: str, session_id: str
    ) -> Optional[RealityCheckMessage]:
        """
        Build and deliver a reality check message to the player.
        """
        # Fetch session stats
        stats = await self._get_session_stats(player_id, session_id)
        if not stats:
            logger.debug("No active session for reality check: %s", player_id)
            return None

        # Get check number from timer data
        timer_key = f"rg:rc_timer:{player_id}:{session_id}"
        timer_data = await self.redis.get(timer_key)
        check_number = 1
        if timer_data:
            parsed = json.loads(timer_data)
            check_number = parsed.get("check_number", 1)

        elapsed_minutes = int(stats.get("elapsed_seconds", 0) / 60)

        message = RealityCheckMessage(
            player_id=player_id,
            session_id=session_id,
            elapsed_minutes=elapsed_minutes,
            total_wagered=stats.get("total_wagered", 0.0),
            total_won=stats.get("total_won", 0.0),
            total_lost=stats.get("total_lost", 0.0),
            net_result=stats.get("total_won", 0.0) - stats.get("total_lost", 0.0),
            deposits_this_session=stats.get("deposits_this_session", 0.0),
            game_type=stats.get("game_type", "unknown"),
            check_number=check_number,
            available_actions=[
                "continue_playing",
                "set_limit",
                "take_break",
                "log_out",
                "visit_help",
            ],
            min_display_seconds=self.config.min_display_seconds,
        )

        # Push to client via WebSocket
        delivered = await self.ws.send_to_player(
            player_id,
            "reality_check",
            {
                "session_id": session_id,
                "elapsed_minutes": message.elapsed_minutes,
                "total_wagered": f"{message.total_wagered:.2f}",
                "total_won": f"{message.total_won:.2f}",
                "net_result": f"{message.net_result:+.2f}",
                "deposits_this_session": f"{message.deposits_this_session:.2f}",
                "game_type": message.game_type,
                "check_number": message.check_number,
                "actions": message.available_actions,
                "min_display_seconds": message.min_display_seconds,
                "pause_game": self.config.pause_game,
                "message": (
                    f"You have been playing for {elapsed_minutes} minutes. "
                    f"Net result: {message.net_result:+.2f}. "
                    "Remember to gamble responsibly."
                ),
                "help_urls": {
                    "gamcare": "https://www.gamcare.org.uk",
                    "begambleaware": "https://www.begambleaware.org",
                    "gamstop": "https://www.gamstop.co.uk",
                },
            },
        )

        # Log the check delivery
        await self._log_reality_check(message, delivered)

        # Schedule next check
        if delivered:
            await self._schedule_next_check(player_id, session_id, check_number + 1)

        return message

    async def record_acknowledgment(
        self,
        player_id: str,
        session_id: str,
        check_number: int,
        action_taken: str,
        display_duration_seconds: int,
    ) -> dict:
        """
        Record player's response to a reality check.
        UKGC: must track what action the player took after seeing the check.
        """
        # Validate minimum display time (anti-click-through)
        if display_duration_seconds < self.config.min_display_seconds:
            logger.warning(
                "Reality check dismissed too quickly: player=%s duration=%ds (min=%ds)",
                player_id, display_duration_seconds, self.config.min_display_seconds,
            )

        async with self.db.acquire() as conn:
            await conn.execute("""
                UPDATE reality_check_log
                SET acknowledged_at = NOW(),
                    action_taken = $4,
                    display_duration_seconds = $5
                WHERE player_id = $1 AND session_id = $2 AND check_number = $3
                  AND acknowledged_at IS NULL
            """, player_id, session_id, check_number, action_taken, display_duration_seconds)

        # If player chose to take a break or log out, handle that
        if action_taken in ("take_break", "log_out"):
            await self.redis.publish(
                "rg:session_terminate",
                json.dumps({
                    "player_id": player_id,
                    "reason": f"reality_check_{action_taken}",
                }),
            )

        return {"status": "recorded", "action": action_taken}

    # -------------------------------------------------------------------
    # Internals
    # -------------------------------------------------------------------

    async def _get_session_stats(
        self, player_id: str, session_id: str
    ) -> Optional[dict]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT
                    EXTRACT(EPOCH FROM (NOW() - started_at)) AS elapsed_seconds,
                    total_wagered, total_won, total_lost, game_type,
                    COALESCE(
                        (SELECT SUM(amount) FROM player_deposits
                         WHERE player_id = ps.player_id
                           AND created_at >= ps.started_at),
                        0
                    ) AS deposits_this_session
                FROM player_sessions ps
                WHERE player_id = $1 AND session_id = $2 AND ended_at IS NULL
            """, player_id, session_id)
        return dict(row) if row else None

    async def _schedule_next_check(
        self, player_id: str, session_id: str, check_number: int
    ) -> None:
        interval = await self.get_player_interval(player_id)
        timer_key = f"rg:rc_timer:{player_id}:{session_id}"
        fire_time = datetime.now(timezone.utc) + timedelta(minutes=interval)

        await self.redis.set(
            timer_key,
            json.dumps({
                "player_id": player_id,
                "session_id": session_id,
                "check_number": check_number,
                "scheduled_at": datetime.now(timezone.utc).isoformat(),
                "fires_at": fire_time.isoformat(),
            }),
            ex=interval * 60 + 60,
        )
        await self.redis.zadd(
            "rg:rc_pending",
            {f"{player_id}:{session_id}": fire_time.timestamp()},
        )

    async def _log_reality_check(
        self, message: RealityCheckMessage, delivered: bool
    ) -> None:
        """
        Log reality check delivery for compliance auditing.
        UKGC expects evidence that reality checks are being delivered
        and that players are engaging with them.
        """
        try:
            async with self.db.acquire() as conn:
                await conn.execute("""
                    INSERT INTO reality_check_log
                        (player_id, session_id, check_number, elapsed_minutes,
                         total_wagered, net_result, delivered, delivered_at, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                """,
                    message.player_id,
                    message.session_id,
                    message.check_number,
                    message.elapsed_minutes,
                    message.total_wagered,
                    message.net_result,
                    delivered,
                    datetime.now(timezone.utc) if delivered else None,
                )
        except Exception:
            logger.exception("Failed to log reality check for %s", message.player_id)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS player_rg_preferences (
    player_id                   VARCHAR(64) PRIMARY KEY,
    reality_check_interval_min  INTEGER NOT NULL DEFAULT 60,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reality_check_log (
    log_id                      BIGSERIAL PRIMARY KEY,
    player_id                   VARCHAR(64) NOT NULL,
    session_id                  VARCHAR(64) NOT NULL,
    check_number                INTEGER NOT NULL,
    elapsed_minutes             INTEGER NOT NULL,
    total_wagered               NUMERIC(14,2),
    net_result                  NUMERIC(14,2),
    delivered                   BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at                TIMESTAMPTZ,
    acknowledged_at             TIMESTAMPTZ,
    action_taken                VARCHAR(30),
    display_duration_seconds    INTEGER,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rc_log_player_session
    ON reality_check_log (player_id, session_id, check_number);

COMMENT ON TABLE reality_check_log IS
    'Reality check delivery and acknowledgment log. UKGC LCCP 3.3.2 compliance.';
"""
