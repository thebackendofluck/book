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
oasis_verification_service.py — Germany OASIS pre-bet verification with caching.

Jurisdiction:       Federal Republic of Germany (all 16 Bundeslaender)
Regulator:          GGL (Gemeinsame Gluecksspielbehoerde der Laender)
                    Administered by: Regierungspraesidium Darmstadt
Regulation refs:
  - GlueStV 2021 section 8 — player exclusion
  - GGL OASIS technical specification (Technische Richtlinie OASIS)
  - GlueStV 2021 section 6a — EUR 1,000/month deposit limit (LUGAS)
Penalties:
  - Up to EUR 500,000 per infringement (GlueStV section 28a)
  - Licence revocation by GGL

Scale:
  - 100M+ verification checks per month (2024)
  - 350,000+ active exclusions (2024)
  - 40,000-55,000 24h self-block triggers per month

Key requirements:
  - Pre-bet verification: check OASIS before every bet placement
  - Caching: negative results TTL 5 min; positive results TTL 0 (always recheck)
  - Panic button: immediate 24h self-block from any screen
  - Fail closed when OASIS unavailable

Book chapter:  Chapter 26b — Self-Exclusion Registries Implementation
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CACHE_TTL_NEGATIVE_SECONDS = 300    # 5 minutes for NOT_EXCLUDED
CACHE_TTL_POSITIVE_SECONDS = 0      # never cache EXCLUDED — always recheck
OASIS_TIMEOUT_SECONDS = 3
OASIS_RETRY_ATTEMPTS = 2
FAIL_CLOSED = True


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class OasisStatus(str, Enum):
    NOT_EXCLUDED = "not_excluded"
    EXCLUDED = "excluded"
    SELF_BLOCKED_24H = "self_blocked_24h"
    API_UNAVAILABLE = "api_unavailable"


@dataclass
class OasisConfig:
    api_base_url: str
    operator_id: str
    api_key: str
    redis_url: str = "redis://localhost:6379/0"
    timeout_seconds: int = OASIS_TIMEOUT_SECONDS
    retry_attempts: int = OASIS_RETRY_ATTEMPTS


@dataclass
class PlayerIdentity:
    player_id: str
    document_hash: str       # SHA-256 of Personalausweis or passport number
    date_of_birth: str       # ISO-8601


@dataclass
class VerificationResult:
    check_id: str
    player_id: str
    status: OasisStatus
    access_allowed: bool
    from_cache: bool
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    exclusion_end_date: Optional[datetime] = None
    error_detail: Optional[str] = None


@dataclass
class SelfBlockResult:
    block_id: str
    player_id: str
    blocked_until: datetime
    success: bool
    error_detail: Optional[str] = None


# ---------------------------------------------------------------------------
# In-memory cache (Redis stub)
# ---------------------------------------------------------------------------

class ExclusionCache:
    """
    In-memory cache simulating Redis for demonstration.

    In production: use Redis with SETEX for TTL-based expiry.
    Key format: oasis:status:{document_hash}
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[str, float]] = {}

    def get(self, document_hash: str) -> Optional[str]:
        key = f"oasis:status:{document_hash}"
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, document_hash: str, status: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            # TTL 0 = do not cache
            return
        key = f"oasis:status:{document_hash}"
        self._store[key] = (status, time.monotonic() + ttl_seconds)

    def delete(self, document_hash: str) -> None:
        key = f"oasis:status:{document_hash}"
        self._store.pop(key, None)


# ---------------------------------------------------------------------------
# 24h self-block store (in-memory stub)
# ---------------------------------------------------------------------------

class SelfBlockStore:
    """
    Tracks active 24-hour self-blocks.

    In production: stored in the primary database with a scheduled job
    to release expired blocks.
    """

    def __init__(self) -> None:
        self._blocks: dict[str, datetime] = {}

    def is_blocked(self, player_id: str) -> bool:
        expires = self._blocks.get(player_id)
        if expires is None:
            return False
        if datetime.now(timezone.utc) >= expires:
            del self._blocks[player_id]
            return False
        return True

    def activate(self, player_id: str) -> datetime:
        blocked_until = datetime.now(timezone.utc) + timedelta(hours=24)
        self._blocks[player_id] = blocked_until
        return blocked_until


# ---------------------------------------------------------------------------
# OASIS verification service
# ---------------------------------------------------------------------------

class OasisVerificationService:
    """
    High-throughput OASIS verification with caching layer.

    Designed for 100M+ checks/month:
      - Negative results cached 5 min (reduces OASIS load ~95%)
      - Positive results never cached (always recheck excluded players)
      - 24h self-block checked locally (no OASIS round-trip needed)
      - Fail closed on OASIS errors
    """

    def __init__(self, config: OasisConfig) -> None:
        self._config = config
        self._cache = ExclusionCache()
        self._self_blocks = SelfBlockStore()

    # ------------------------------------------------------------------
    # Pre-bet verification (called before every bet)
    # ------------------------------------------------------------------

    def verify_pre_bet(self, player: PlayerIdentity) -> VerificationResult:
        """
        GlueStV 2021 section 8: verify player is not excluded before
        accepting any bet.  This is the hot path — must be fast.
        """
        check_id = f"OASIS-BET-{uuid.uuid4().hex[:10].upper()}"

        # 1. Check local 24h self-block first (no network call)
        if self._self_blocks.is_blocked(player.player_id):
            return VerificationResult(
                check_id=check_id,
                player_id=player.player_id,
                status=OasisStatus.SELF_BLOCKED_24H,
                access_allowed=False,
                from_cache=True,
            )

        # 2. Check cache
        cached = self._cache.get(player.document_hash)
        if cached == OasisStatus.NOT_EXCLUDED.value:
            return VerificationResult(
                check_id=check_id,
                player_id=player.player_id,
                status=OasisStatus.NOT_EXCLUDED,
                access_allowed=True,
                from_cache=True,
            )

        # 3. Query OASIS
        return self._query_oasis(check_id, player)

    # ------------------------------------------------------------------
    # Login verification
    # ------------------------------------------------------------------

    def verify_at_login(self, player: PlayerIdentity) -> VerificationResult:
        """Always query OASIS at login — bypass cache."""
        check_id = f"OASIS-LOGIN-{uuid.uuid4().hex[:10].upper()}"

        if self._self_blocks.is_blocked(player.player_id):
            return VerificationResult(
                check_id=check_id,
                player_id=player.player_id,
                status=OasisStatus.SELF_BLOCKED_24H,
                access_allowed=False,
                from_cache=True,
            )

        # Invalidate cache to force fresh check
        self._cache.delete(player.document_hash)
        return self._query_oasis(check_id, player)

    # ------------------------------------------------------------------
    # 24-hour panic button
    # ------------------------------------------------------------------

    def activate_panic_button(self, player: PlayerIdentity) -> SelfBlockResult:
        """
        Immediate 24h self-block.  GlueStV 2021 mandates this feature
        be accessible from every screen.  Takes effect instantly — no
        OASIS round-trip required for enforcement.
        """
        block_id = f"BLOCK-{uuid.uuid4().hex[:10].upper()}"
        try:
            blocked_until = self._self_blocks.activate(player.player_id)
            # Invalidate cache so the player cannot bet via cached status
            self._cache.delete(player.document_hash)
            log.info("oasis: panic button activated",
                     block_id=block_id, player_id=player.player_id,
                     blocked_until=blocked_until.isoformat())
            return SelfBlockResult(
                block_id=block_id,
                player_id=player.player_id,
                blocked_until=blocked_until,
                success=True,
            )
        except Exception as exc:
            log.error("oasis: panic button failed",
                      block_id=block_id, error=str(exc))
            return SelfBlockResult(
                block_id=block_id,
                player_id=player.player_id,
                blocked_until=datetime.now(timezone.utc),
                success=False,
                error_detail=str(exc),
            )

    # ------------------------------------------------------------------
    # Private: OASIS API query
    # ------------------------------------------------------------------

    def _query_oasis(
        self, check_id: str, player: PlayerIdentity
    ) -> VerificationResult:
        for attempt in range(self._config.retry_attempts):
            try:
                data = self._call_api(player, check_id)
                excluded = data.get("excluded", False)
                status = (
                    OasisStatus.EXCLUDED if excluded
                    else OasisStatus.NOT_EXCLUDED
                )

                # Cache negative results only
                if status == OasisStatus.NOT_EXCLUDED:
                    self._cache.set(
                        player.document_hash,
                        status.value,
                        CACHE_TTL_NEGATIVE_SECONDS,
                    )

                end_date: Optional[datetime] = None
                if excluded and data.get("exclusionEndDate"):
                    end_date = datetime.fromisoformat(data["exclusionEndDate"])

                result = VerificationResult(
                    check_id=check_id,
                    player_id=player.player_id,
                    status=status,
                    access_allowed=not excluded,
                    from_cache=False,
                    exclusion_end_date=end_date,
                )
                self._audit_log(check_id, player, result)
                return result

            except httpx.TimeoutException:
                log.warning("oasis: timeout", attempt=attempt + 1,
                            player_id=player.player_id)
                if attempt < self._config.retry_attempts - 1:
                    time.sleep(0.3 * (attempt + 1))
            except Exception as exc:
                log.error("oasis: query failed",
                          error=str(exc), player_id=player.player_id)
                break

        # Fail closed
        result = VerificationResult(
            check_id=check_id,
            player_id=player.player_id,
            status=OasisStatus.API_UNAVAILABLE,
            access_allowed=not FAIL_CLOSED,
            from_cache=False,
            error_detail="OASIS API unreachable after retries",
        )
        self._audit_log(check_id, player, result)
        return result

    def _call_api(
        self, player: PlayerIdentity, check_id: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "idDocumentHash": player.document_hash,
            "dateOfBirth": player.date_of_birth,
            "operatorId": self._config.operator_id,
            "checkId": check_id,
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        }
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            resp = client.post(
                f"{self._config.api_base_url}/checks",
                json=payload,
                headers={
                    "X-Api-Key": self._config.api_key,
                    "X-Operator-Id": self._config.operator_id,
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    @staticmethod
    def _audit_log(
        check_id: str, player: PlayerIdentity, result: VerificationResult
    ) -> None:
        log.info(
            "oasis: audit",
            check_id=check_id,
            player_id=player.player_id,
            status=result.status.value,
            access_allowed=result.access_allowed,
            from_cache=result.from_cache,
            checked_at=result.checked_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    config = OasisConfig(
        api_base_url="https://oasis-api.ggl.de/v3",
        operator_id="GGL-OP-12345",
        api_key="secret-ggl-key",
    )
    service = OasisVerificationService(config)

    player = PlayerIdentity(
        player_id="plr-de-001",
        document_hash=hashlib.sha256(b"T220001293").hexdigest(),
        date_of_birth="1990-03-22",
    )

    print("OASIS pre-bet verification service demo")
    print(f"Player: {player.player_id}")
    print(f"Cache TTL (negative): {CACHE_TTL_NEGATIVE_SECONDS}s")
    print(f"Cache TTL (positive): {CACHE_TTL_POSITIVE_SECONDS}s (never cache)")
    print(f"Fail-closed: {FAIL_CLOSED}")
    print("In production, this handles 100M+ checks/month with Redis caching.")


if __name__ == "__main__":
    _demo()
