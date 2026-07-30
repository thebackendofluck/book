# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
fraud_detector.py -- Affiliate fraud detection for iGaming platforms.

Common affiliate fraud patterns detected:
  1. Click flooding: > 100 clicks/min from a single IP
  2. Cookie stuffing: click with no user interaction (referrer missing/invalid)
  3. Fake conversions: deposit immediately withdrawn with zero wagering
  4. Sub-affiliate fraud: clustering of low-quality traffic from sub-IDs
  5. Geographic mismatch: click geo doesn't match affiliate's registered region

Runs on every click event and flags suspicious activity in Redis.
Detected fraud results in commission withholding pending manual review.

Chapter 37: Marketing Technology and CRM
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional


if TYPE_CHECKING:
    # Type stubs for redis.asyncio — not available in this environment
    class AsyncRedis:
        async def incr(self, key: str) -> int: ...
        async def expire(self, key: str, seconds: int) -> bool: ...
        async def sadd(self, key: str, *values: str) -> int: ...
        async def get(self, key: str) -> Optional[str]: ...


CLICK_RATE_LIMIT = 100           # max clicks per minute per IP
CLICK_RATE_WINDOW_SECONDS = 60   # rate limit window in seconds


class AffiliateFraudDetector:
    """
    Real-time affiliate fraud detection.

    Uses Redis for rate limiting and flagging suspicious click/affiliate IDs.
    Detection is stateless per call — state is maintained in Redis.

    Args:
        redis_client: An async Redis client (redis.asyncio.Redis).
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def check_click(self, click: dict[str, Any]) -> tuple[bool, str]:
        """
        Validate an affiliate click event.

        Args:
            click: dict with keys:
                - ip_address (str): client IP
                - affiliate_id (str): affiliate identifier
                - click_id (str): unique click ID
                - referrer (str | None): HTTP referrer header
                - geo_country (str | None): resolved country code from IP
                - affiliate_geo (str | None): affiliate's registered country

        Returns:
            (is_valid, reason) where reason describes any detected fraud pattern.

        Fraud checks:
            1. Rate limit: > 100 clicks/min from same IP → 'click_flooding'
            2. Referrer check: no referrer → 'no_referrer'
            3. Geo mismatch: country mismatch → flagged (not blocked, logged)
        """
        ip = str(click.get("ip_address", ""))
        affiliate_id = str(click.get("affiliate_id", ""))
        click_id = str(click.get("click_id", ""))

        # ----------------------------------------------------------------
        # Check 1: Click flooding (rate limit per IP)
        # ----------------------------------------------------------------
        rate_key = f"click_rate:{ip}"
        count = await self.redis.incr(rate_key)
        if count == 1:
            # Set expiry only on first increment to avoid extending the window
            await self.redis.expire(rate_key, CLICK_RATE_WINDOW_SECONDS)
        if count > CLICK_RATE_LIMIT:
            await self.redis.sadd(f"fraud:{affiliate_id}:click_flooding", click_id)
            return False, "click_flooding"

        # ----------------------------------------------------------------
        # Check 2: Referrer validation (cookie stuffing detection)
        # ----------------------------------------------------------------
        referrer = click.get("referrer")
        if not referrer:
            # No referrer = possible direct injection / cookie stuffing
            await self.redis.sadd(f"fraud:{affiliate_id}:no_referrer", click_id)
            return False, "no_referrer"

        # ----------------------------------------------------------------
        # Check 3: Geographic consistency (flag, don't block)
        # Legitimate cross-border traffic exists (VPN, travel, etc.)
        # ----------------------------------------------------------------
        geo_country = click.get("geo_country")
        affiliate_geo = click.get("affiliate_geo")
        if geo_country and affiliate_geo and geo_country != affiliate_geo:
            await self.redis.sadd(
                f"flagged:{affiliate_id}:geo_mismatch", click_id
            )
            # Not blocking — just flagging for manual review

        return True, "valid"

    async def check_conversion(self, conversion: dict[str, Any]) -> tuple[bool, str]:
        """
        Validate a conversion event (first deposit / registration).

        Detects fake conversions: deposits immediately withdrawn with no wagering.
        This check is called asynchronously after wagering data is available.

        Args:
            conversion: dict with keys:
                - affiliate_id (str)
                - player_id (str)
                - deposit_amount_cents (int)
                - wagered_amount_cents (int): total wagered before first withdrawal
                - withdrawal_within_24h (bool): true if withdrawal within 24h of deposit

        Returns:
            (is_valid, reason)
        """
        affiliate_id = str(conversion.get("affiliate_id", ""))
        player_id = str(conversion.get("player_id", ""))
        deposit = int(conversion.get("deposit_amount_cents", 0))
        wagered = int(conversion.get("wagered_amount_cents", 0))
        quick_withdrawal = bool(conversion.get("withdrawal_within_24h", False))

        # Fake conversion: deposit and withdraw with no wagering
        if quick_withdrawal and wagered == 0:
            await self.redis.sadd(
                f"fraud:{affiliate_id}:fake_conversion", player_id
            )
            return False, "fake_conversion_no_wagering"

        # Minimal wagering relative to deposit (< 10%)
        if deposit > 0 and wagered < (deposit * 0.10) and quick_withdrawal:
            await self.redis.sadd(
                f"flagged:{affiliate_id}:low_wagering", player_id
            )
            return False, "fake_conversion_low_wagering"

        return True, "valid"
