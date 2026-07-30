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
gamstop_integration.py — UK GAMSTOP self-exclusion integration service.

Jurisdiction:       United Kingdom
Regulator:          UK Gambling Commission (UKGC)
Regulation refs:
  - LCCP condition 17.1.1 — self-exclusion obligations
    https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp
  - GAMSTOP operator technical integration guide
    https://www.gamstop.co.uk/operator/technical-integration
  - UKGC 2025 fining framework: up to 15% of GGY for serious breaches
Penalties:
  - 888 UK: GBP 9.4M (2022) — multiple ID/exclusion failures
  - Entain: GBP 585M (2023) — systemic compliance collapse

Key requirements:
  - Check at registration AND every login (LCCP 17.1.1)
  - Fail closed: block if GAMSTOP unreachable
  - Marketing suppression: immediate across all channels
  - 24h to enforce exclusion after GAMSTOP notification
  - Re-opening: 7-day cooling-off period
  - Batch API: up to 1,000 players per POST, rate limit 1 req/s

Book chapter:  Chapter 26b — Self-Exclusion Registries Implementation
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXCLUDED_FLAG = "Y"
_RATE_LIMIT_SECONDS = 1.0
_MAX_BATCH_SIZE = 1000
_DEFAULT_TIMEOUT = 5


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class GamstopStatus(str, Enum):
    NOT_EXCLUDED = "not_excluded"
    EXCLUDED = "excluded"
    API_UNAVAILABLE = "api_unavailable"


class ExclusionPeriod(str, Enum):
    SIX_MONTHS = "6_months"
    ONE_YEAR = "1_year"
    FIVE_YEARS = "5_years"


@dataclass
class GamstopConfig:
    batch_url: str
    api_key: str
    timeout_seconds: int = _DEFAULT_TIMEOUT
    retry_attempts: int = 3
    fail_closed: bool = True


@dataclass
class GamstopPlayer:
    player_id: str
    first_name: str
    last_name: str
    date_of_birth: str      # ISO-8601
    postcode: str
    email: Optional[str] = None
    mobile: Optional[str] = None


@dataclass
class GamstopCheckResult:
    check_id: str
    player_id: str
    status: GamstopStatus
    access_allowed: bool
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    exclusion_end_date: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class MarketingSuppression:
    player_id: str
    channels_suppressed: list[str]
    suppressed_at: datetime
    confirmation_ids: dict[str, str]


# ---------------------------------------------------------------------------
# GAMSTOP service
# ---------------------------------------------------------------------------

class GamstopService:
    """
    Production GAMSTOP integration.

    Implements:
      - Single-player check (wraps the batch endpoint)
      - Fail-closed unavailability handling
      - Marketing suppression event dispatch
      - Audit logging for every check
    """

    def __init__(self, config: GamstopConfig) -> None:
        self._config = config
        self._last_request_at: float = 0.0

    # ------------------------------------------------------------------
    # Public: registration check
    # ------------------------------------------------------------------

    def check_at_registration(self, player: GamstopPlayer) -> GamstopCheckResult:
        """LCCP 17.1.1: check GAMSTOP before account creation."""
        result = self._check_single(player, query_type="registration")
        if result.status == GamstopStatus.EXCLUDED:
            log.warning("gamstop: excluded player attempted registration",
                        player_id=player.player_id)
        return result

    # ------------------------------------------------------------------
    # Public: login check
    # ------------------------------------------------------------------

    def check_at_login(self, player: GamstopPlayer) -> GamstopCheckResult:
        """LCCP 17.1.1: check GAMSTOP at every login."""
        result = self._check_single(player, query_type="login")
        if result.status == GamstopStatus.EXCLUDED:
            self._trigger_marketing_suppression(player)
        return result

    # ------------------------------------------------------------------
    # Public: marketing suppression
    # ------------------------------------------------------------------

    def _trigger_marketing_suppression(
        self, player: GamstopPlayer
    ) -> MarketingSuppression:
        """
        Dispatch suppression events to all marketing channels.

        In production: publish a PlayerExcluded event to Kafka/RabbitMQ
        consumed by email ESP, push gateway, SMS provider, and affiliate
        management platform.
        """
        channels = ["email", "push", "sms", "affiliate", "direct_mail"]
        confirmation_ids: dict[str, str] = {}

        for channel in channels:
            conf_id = f"SUP-{channel.upper()}-{uuid.uuid4().hex[:8]}"
            confirmation_ids[channel] = conf_id
            log.info("gamstop: marketing suppressed",
                     player_id=player.player_id, channel=channel,
                     confirmation_id=conf_id)

        return MarketingSuppression(
            player_id=player.player_id,
            channels_suppressed=channels,
            suppressed_at=datetime.now(timezone.utc),
            confirmation_ids=confirmation_ids,
        )

    # ------------------------------------------------------------------
    # Private: single-player check via batch endpoint
    # ------------------------------------------------------------------

    def _check_single(
        self, player: GamstopPlayer, query_type: str
    ) -> GamstopCheckResult:
        check_id = f"GS-{uuid.uuid4().hex[:12].upper()}"
        payload = self._build_payload(player)

        for attempt in range(self._config.retry_attempts):
            try:
                self._enforce_rate_limit()
                response = self._call_api([payload])
                is_excluded = (
                    len(response) > 0
                    and response[0].get("exclusionStatus") == _EXCLUDED_FLAG
                )
                status = (
                    GamstopStatus.EXCLUDED if is_excluded
                    else GamstopStatus.NOT_EXCLUDED
                )
                result = GamstopCheckResult(
                    check_id=check_id,
                    player_id=player.player_id,
                    status=status,
                    access_allowed=not is_excluded,
                    exclusion_end_date=response[0].get("exclusionEndDate")
                    if is_excluded and response else None,
                )
                self._audit_log(check_id, player.player_id, query_type, result)
                return result

            except httpx.TimeoutException:
                log.warning("gamstop: timeout",
                            attempt=attempt + 1, player_id=player.player_id)
                if attempt < self._config.retry_attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
            except Exception as exc:
                log.error("gamstop: request failed",
                          error=str(exc), player_id=player.player_id)
                break

        # Fail closed: block access when GAMSTOP is unreachable
        result = GamstopCheckResult(
            check_id=check_id,
            player_id=player.player_id,
            status=GamstopStatus.API_UNAVAILABLE,
            access_allowed=not self._config.fail_closed,
            error_detail="GAMSTOP API unreachable after retries",
        )
        self._audit_log(check_id, player.player_id, query_type, result)
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_payload(player: GamstopPlayer) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "firstName": player.first_name,
            "lastName": player.last_name,
            "dateOfBirth": player.date_of_birth,
            "postcode": player.postcode,
        }
        if player.email and 2 <= len(player.email) <= 254:
            payload["email"] = player.email
        if player.mobile:
            payload["mobile"] = player.mobile
        return payload

    def _enforce_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = _RATE_LIMIT_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _call_api(self, payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
        request_id = str(uuid.uuid4())
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            resp = client.post(
                self._config.batch_url,
                json=payload,
                headers={
                    "X-Trace-Id": request_id,
                    "X-Api-Key": self._config.api_key,
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        data: list[dict[str, Any]] = resp.json()
        return data

    @staticmethod
    def _audit_log(
        check_id: str,
        player_id: str,
        query_type: str,
        result: GamstopCheckResult,
    ) -> None:
        log.info(
            "gamstop: audit",
            check_id=check_id,
            player_id=player_id,
            query_type=query_type,
            status=result.status.value,
            access_allowed=result.access_allowed,
            checked_at=result.checked_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    config = GamstopConfig(
        batch_url="https://api.gamstop.co.uk/v1/exclusion/batch",
        api_key="secret-gamstop-key",
    )
    service = GamstopService(config)

    player = GamstopPlayer(
        player_id="plr-uk-001",
        first_name="James",
        last_name="Wilson",
        date_of_birth="1988-09-14",
        postcode="SW1A 1AA",
        email="james.wilson@example.co.uk",
    )

    print("GAMSTOP integration demo")
    print(f"Player: {player.player_id}")
    print(f"Fail-closed policy: {config.fail_closed}")
    print("In production, this would query the live GAMSTOP batch API.")


if __name__ == "__main__":
    _demo()
