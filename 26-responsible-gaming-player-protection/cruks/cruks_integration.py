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
cruks_integration.py — Netherlands CRUKS self-exclusion registry integration.

Jurisdiction:       Netherlands
Regulator:          Kansspelautoriteit (KSA)
Regulation refs:
  - Wet op de kansspelen (Wok) Article 33h — mandatory CRUKS consultation
    https://wetten.overheid.nl/BWBR0002469/
  - KSA Remote Gambling System Assessment Scheme v2.1 (2023)
    https://kansspelautoriteit.nl/publish/library/30/
    ksa_remote_gambling_system_assessment_scheme_2-1_eng_wr_2.pdf
  - KSA CRUKS API specification (updated July 2024)
    https://kansspelautoriteit.nl/kansspelen/speelautomaten/cruks/
  - KSA operator guidance on CRUKS consultation obligations (2025)
    https://kansspelautoriteit.nl/handhaving/vergunninghouders/cruks-koppeling/
Penalties:
  - Fine up to €830,000 per violation (Wok Article 35a)
  - Licence suspension or revocation
  - CRUKS failure = player must be blocked; failure to block = automatic violation

Key requirements:
  - Check player at registration (before any play)
  - Check player at every login (real-time)
  - If CRUKS API is unavailable, player access MUST be blocked (fail-closed)
  - Daily batch re-verification of all active players
  - Self-exclusion registration must be processed within 24 hours
  - Minimum exclusion period: 6 months; can be permanent

Book chapter:  Chapter 26 — Responsible Gaming Systems
"""
from __future__ import annotations

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

_CRUKS_CHECK_TIMEOUT_SECONDS: int = 5
_CRUKS_RETRY_ATTEMPTS: int = 3
_CRUKS_RETRY_BACKOFF_SECONDS: float = 0.5

# If CRUKS is unreachable, this controls whether to allow or block the player.
# Per KSA regulations: must be BLOCK (fail-closed).
_FAIL_CLOSED: bool = True


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class CruksStatus(str, Enum):
    NOT_REGISTERED = "not_registered"   # player may proceed
    REGISTERED = "registered"           # player is excluded — block access
    CHECK_FAILED = "check_failed"       # API error — block if fail-closed
    API_UNAVAILABLE = "api_unavailable" # system down — must block per KSA


class ExclusionDuration(str, Enum):
    SIX_MONTHS = "P6M"
    ONE_YEAR = "P1Y"
    TWO_YEARS = "P2Y"
    FIVE_YEARS = "P5Y"
    PERMANENT = "permanent"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class CruksConfig:
    """Runtime configuration for the CRUKS API client."""
    api_base_url: str                      # e.g. "https://api.cruks.nl/v2"
    client_id: str                         # KSA-issued client ID
    client_secret: str                     # KSA-issued secret (store in vault)
    operator_licence_number: str           # Vergunningsnummer
    timeout_seconds: int = _CRUKS_CHECK_TIMEOUT_SECONDS
    retry_attempts: int = _CRUKS_RETRY_ATTEMPTS


@dataclass
class CruksPlayer:
    """Player identity record required by the CRUKS API."""
    player_id: str                         # internal operator ID
    bsn_hash: str                          # SHA-256 of BSN — never send raw BSN
    first_name: str
    last_name: str
    date_of_birth: str                     # ISO-8601: YYYY-MM-DD
    postcode: str                          # Dutch postcode: 1234 AB


@dataclass
class CruksCheckResult:
    """Result of a single CRUKS exclusion check."""
    check_id: str
    player_id: str
    status: CruksStatus
    checked_at: datetime
    access_allowed: bool
    exclusion_end_date: Optional[datetime] = None
    raw_response: Optional[dict[str, Any]] = None
    error_detail: Optional[str] = None


@dataclass
class CruksRegistrationResult:
    """Result of registering a player self-exclusion in CRUKS."""
    registration_id: str
    player_id: str
    success: bool
    registered_at: Optional[datetime] = None
    cruks_reference: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class BatchReCheckReport:
    """Summary report from a daily batch re-verification run."""
    run_id: str
    started_at: datetime
    completed_at: Optional[datetime]
    total_players: int
    newly_excluded: int
    check_errors: int
    players_blocked: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CRUKS API client
# ---------------------------------------------------------------------------

class CruksClient:
    """
    Low-level HTTP client for the KSA CRUKS v2 API.

    The CRUKS API uses OAuth 2.0 client-credentials for authentication.
    All player identifiers are hashed (SHA-256 of BSN) before transmission.

    CRITICAL: If the API returns any non-200 response or times out, the
    caller must treat the player as excluded (fail-closed per KSA mandate).
    """

    def __init__(self, config: CruksConfig) -> None:
        self._config = config
        self._access_token: Optional[str] = None
        self._token_expiry: datetime = datetime.min.replace(tzinfo=timezone.utc)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def check_player(self, player: CruksPlayer) -> CruksCheckResult:
        """
        Perform a real-time CRUKS exclusion check for a single player.

        Used at registration and at every login.  Must complete within
        the operator's session start latency budget (KSA: < 5 seconds).
        """
        check_id = f"CRUKS-{uuid.uuid4().hex[:12].upper()}"
        log.debug("cruks: checking player", check_id=check_id,
                  player_id=player.player_id)

        for attempt in range(self._config.retry_attempts):
            try:
                token = self._ensure_token()
                response = self._post_check(player, token)
                return self._parse_check_response(
                    check_id, player.player_id, response
                )
            except httpx.TimeoutException:
                log.warning("cruks: timeout on check attempt",
                            attempt=attempt + 1, player_id=player.player_id)
                if attempt < self._config.retry_attempts - 1:
                    time.sleep(_CRUKS_RETRY_BACKOFF_SECONDS * (attempt + 1))
            except httpx.HTTPStatusError as exc:
                log.error("cruks: HTTP error during check",
                          status_code=exc.response.status_code,
                          player_id=player.player_id)
                break
            except Exception as exc:
                log.error("cruks: unexpected error during check",
                          error=str(exc), player_id=player.player_id)
                break

        # All retries exhausted or unrecoverable error
        return CruksCheckResult(
            check_id=check_id,
            player_id=player.player_id,
            status=CruksStatus.API_UNAVAILABLE,
            checked_at=datetime.now(timezone.utc),
            access_allowed=not _FAIL_CLOSED,  # False when fail-closed
            error_detail="CRUKS API unavailable after retries",
        )

    def register_exclusion(
        self,
        player: CruksPlayer,
        duration: ExclusionDuration,
        reason: Optional[str] = None,
    ) -> CruksRegistrationResult:
        """
        Register a player self-exclusion in the CRUKS central register.

        Must be processed within 24 hours of the player request per Wok.
        """
        registration_id = f"CREG-{uuid.uuid4().hex[:12].upper()}"
        log.info("cruks: registering exclusion",
                 registration_id=registration_id,
                 player_id=player.player_id,
                 duration=duration.value)

        try:
            token = self._ensure_token()
            payload: dict[str, Any] = {
                "bsnHash": player.bsn_hash,
                "firstName": player.first_name,
                "lastName": player.last_name,
                "dateOfBirth": player.date_of_birth,
                "postcode": player.postcode,
                "duration": duration.value,
                "operatorLicenceNumber": self._config.operator_licence_number,
                "requestedAt": datetime.now(timezone.utc).isoformat(),
            }
            if reason:
                payload["reason"] = reason

            with httpx.Client(timeout=self._config.timeout_seconds) as client:
                resp = client.post(
                    f"{self._config.api_base_url}/registrations",
                    json=payload,
                    headers=self._auth_headers(token),
                )
            resp.raise_for_status()
            data = resp.json()

            return CruksRegistrationResult(
                registration_id=registration_id,
                player_id=player.player_id,
                success=True,
                registered_at=datetime.now(timezone.utc),
                cruks_reference=data.get("cruksReference"),
            )

        except Exception as exc:
            log.error("cruks: exclusion registration failed",
                      registration_id=registration_id,
                      player_id=player.player_id,
                      error=str(exc))
            return CruksRegistrationResult(
                registration_id=registration_id,
                player_id=player.player_id,
                success=False,
                error_detail=str(exc),
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_token(self) -> str:
        """Obtain or refresh OAuth 2.0 access token."""
        if (
            self._access_token
            and datetime.now(timezone.utc) < self._token_expiry - timedelta(seconds=30)
        ):
            return self._access_token

        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{self._config.api_base_url}/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                    "scope": "cruks:check cruks:register",
                },
            )
        resp.raise_for_status()
        token_data = resp.json()
        self._access_token = token_data["access_token"]
        self._token_expiry = datetime.now(timezone.utc) + timedelta(
            seconds=token_data.get("expires_in", 3600)
        )
        return self._access_token

    def _post_check(
        self, player: CruksPlayer, token: str
    ) -> dict[str, Any]:
        payload = {
            "bsnHash": player.bsn_hash,
            "dateOfBirth": player.date_of_birth,
            "operatorLicenceNumber": self._config.operator_licence_number,
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        }
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            resp = client.post(
                f"{self._config.api_base_url}/checks",
                json=payload,
                headers=self._auth_headers(token),
            )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _parse_check_response(
        check_id: str,
        player_id: str,
        data: dict[str, Any],
    ) -> CruksCheckResult:
        is_registered = data.get("registered", False)
        status = CruksStatus.REGISTERED if is_registered else CruksStatus.NOT_REGISTERED
        access_allowed = not is_registered

        exclusion_end: Optional[datetime] = None
        if is_registered and data.get("exclusionEndDate"):
            exclusion_end = datetime.fromisoformat(data["exclusionEndDate"])

        return CruksCheckResult(
            check_id=check_id,
            player_id=player_id,
            status=status,
            checked_at=datetime.now(timezone.utc),
            access_allowed=access_allowed,
            exclusion_end_date=exclusion_end,
            raw_response=data,
        )

    @staticmethod
    def _auth_headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


# ---------------------------------------------------------------------------
# High-level service (used by application layer)
# ---------------------------------------------------------------------------

class CruksService:
    """
    Application-level CRUKS integration service.

    Handles registration checks, login checks, self-exclusion registration,
    and daily batch re-verification with appropriate error handling.
    """

    def __init__(self, client: CruksClient) -> None:
        self._client = client

    def check_at_registration(self, player: CruksPlayer) -> CruksCheckResult:
        """
        Check a new player against CRUKS before allowing account creation.
        Returns a result that the registration flow must honour.
        """
        result = self._client.check_player(player)
        self._log_check("registration", result)
        return result

    def check_at_login(self, player: CruksPlayer) -> CruksCheckResult:
        """
        Check an existing player at each login attempt.

        A player registered in CRUKS since their last login must be blocked.
        """
        result = self._client.check_player(player)
        self._log_check("login", result)
        return result

    def register_self_exclusion(
        self,
        player: CruksPlayer,
        duration: ExclusionDuration = ExclusionDuration.SIX_MONTHS,
    ) -> CruksRegistrationResult:
        """Register a player-initiated self-exclusion in CRUKS."""
        result = self._client.register_exclusion(player, duration,
                                                  reason="player_self_exclusion")
        if result.success:
            log.info("cruks: self-exclusion registered",
                     player_id=player.player_id,
                     duration=duration.value,
                     cruks_reference=result.cruks_reference)
        else:
            log.error("cruks: self-exclusion registration failed",
                      player_id=player.player_id,
                      error=result.error_detail)
        return result

    def run_daily_batch(
        self, players: list[CruksPlayer]
    ) -> BatchReCheckReport:
        """
        Re-verify all active players against CRUKS daily.

        Players found newly registered in CRUKS must be immediately
        blocked in the operator platform.
        """
        run_id = f"BATCH-{uuid.uuid4().hex[:10].upper()}"
        started_at = datetime.now(timezone.utc)
        newly_excluded: list[str] = []
        errors = 0

        log.info("cruks: starting daily batch",
                 run_id=run_id, player_count=len(players))

        for player in players:
            result = self._client.check_player(player)
            if result.status == CruksStatus.REGISTERED:
                newly_excluded.append(player.player_id)
                log.warning("cruks: player found registered in CRUKS — blocking",
                            run_id=run_id, player_id=player.player_id)
                # TODO: call AccountService.block(player.player_id, reason="cruks")
            elif result.status in (
                CruksStatus.CHECK_FAILED, CruksStatus.API_UNAVAILABLE
            ):
                errors += 1
                log.error("cruks: check error in batch",
                          run_id=run_id, player_id=player.player_id,
                          status=result.status.value)

        return BatchReCheckReport(
            run_id=run_id,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc),
            total_players=len(players),
            newly_excluded=len(newly_excluded),
            check_errors=errors,
            players_blocked=newly_excluded,
        )

    @staticmethod
    def _log_check(event_type: str, result: CruksCheckResult) -> None:
        if result.access_allowed:
            log.info("cruks: check passed",
                     event_type=event_type,
                     player_id=result.player_id,
                     check_id=result.check_id)
        else:
            log.warning("cruks: access denied",
                        event_type=event_type,
                        player_id=result.player_id,
                        check_id=result.check_id,
                        status=result.status.value)


# ---------------------------------------------------------------------------
# Module-level demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    import hashlib

    config = CruksConfig(
        api_base_url="https://api.cruks.nl/v2",
        client_id="operator-client-id",
        client_secret="operator-secret",
        operator_licence_number="KSA-12345-2020",
    )
    client = CruksClient(config)
    service = CruksService(client)

    player = CruksPlayer(
        player_id="player-nl-9001",
        bsn_hash=hashlib.sha256(b"123456789").hexdigest(),
        first_name="Jan",
        last_name="de Vries",
        date_of_birth="1985-07-15",
        postcode="1234 AB",
    )

    print(f"CRUKS integration demo — player: {player.player_id}")
    print("In production, check_at_registration and check_at_login would")
    print("call the live KSA CRUKS API with real player data.")


if __name__ == "__main__":
    _demo()
