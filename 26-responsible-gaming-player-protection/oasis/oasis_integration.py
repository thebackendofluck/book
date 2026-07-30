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
oasis_integration.py — Germany OASIS self-exclusion system integration.

Jurisdiction:       Federal Republic of Germany (all 16 Bundesländer)
Regulator:          Gemeinsame Glücksspielbehörde der Länder (GGL)
                    Administered by: Regierungspräsidium Darmstadt
Regulation refs:
  - Glücksspielstaatsvertrag 2021 (GlüStV 2021) § 8 — player protection
    https://www.gluecksspielbehoerde.de/gluecksspielrecht/
    gluecksspielstaatsvertrag-2021
  - GGL OASIS technical specification (Technische Richtlinie OASIS)
    https://www.gluecksspielbehoerde.de/download/
  - § 6a GlüStV 2021 — monthly deposit limit €1,000 across all operators
    (LUGAS — Limitierungs- und Sperrsystem)
  - GGL operator obligations circular (2024)
    https://www.gluecksspielbehoerde.de/lizenzierung/gluecksspielanbieter/
Penalties:
  - Fines up to €500,000 per infringement (GlüStV § 28a)
  - Licence revocation by GGL
  - Criminal liability for operating without valid exclusion checks (§ 284 StGB)

Key requirements:
  - Query OASIS at every player login
  - Query OASIS at registration (before account creation)
  - Register exclusions within 1 business day of player request
  - €1,000/month cross-operator deposit limit enforced via LUGAS
  - Self-exclusion is nationwide — valid across all 16 Länder simultaneously
  - Panic button: immediate self-exclusion available at all times
  - 5.2 billion OASIS queries recorded in 2026 (367,000 active exclusions)

Book chapter:  Chapter 26 — Responsible Gaming Systems
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MONTHLY_DEPOSIT_LIMIT_EUR: Decimal = Decimal("1000.00")   # GlüStV § 6a / LUGAS
_OASIS_TIMEOUT_SECONDS: int = 5
_OASIS_RETRY_ATTEMPTS: int = 3
_FAIL_CLOSED: bool = True   # block player when OASIS is unreachable


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class OasisStatus(str, Enum):
    NOT_EXCLUDED = "not_excluded"      # green — player may proceed
    EXCLUDED = "excluded"              # red — block all access
    PENDING = "pending"                # exclusion registered, processing
    API_UNAVAILABLE = "api_unavailable"


class ExclusionDuration(str, Enum):
    ONE_YEAR = "1_year"
    THREE_YEARS = "3_years"
    FIVE_YEARS = "5_years"
    PERMANENT = "permanent"


class ExclusionType(str, Enum):
    SELF = "self_exclusion"
    THIRD_PARTY = "third_party"       # guardian / court order
    OPERATOR = "operator_initiated"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class OasisConfig:
    api_base_url: str                  # GGL-provided endpoint
    operator_id: str                   # GGL operator licence ID
    api_key: str                       # store in vault, never hardcode
    timeout_seconds: int = _OASIS_TIMEOUT_SECONDS
    retry_attempts: int = _OASIS_RETRY_ATTEMPTS


@dataclass
class OasisPlayer:
    """
    Player identity record for OASIS query.

    German players are identified by their Personalausweis (national ID)
    number or passport number.  The ID number is hashed before transmission.
    """
    player_id: str                     # internal operator ID
    id_document_hash: str              # SHA-256 of national ID or passport
    first_name: str
    last_name: str
    date_of_birth: str                 # ISO-8601: YYYY-MM-DD
    bundesland: str                    # ISO 3166-2:DE code, e.g. "DE-BY"


@dataclass
class OasisCheckResult:
    check_id: str
    player_id: str
    status: OasisStatus
    checked_at: datetime
    access_allowed: bool
    exclusion_end_date: Optional[datetime] = None
    bundesland_of_registration: Optional[str] = None
    raw_response: Optional[dict[str, Any]] = None
    error_detail: Optional[str] = None


@dataclass
class OasisExclusionRegistration:
    registration_id: str
    player_id: str
    exclusion_type: ExclusionType
    duration: ExclusionDuration
    requested_at: datetime
    effective_at: Optional[datetime] = None
    oasis_reference: Optional[str] = None
    success: bool = False
    error_detail: Optional[str] = None


@dataclass
class LugasDepositCheck:
    """
    LUGAS cross-operator deposit limit check result.

    LUGAS (Limitierungs- und Sperrsystem) enforces the GlüStV § 6a
    €1,000/month aggregate limit across all German-licensed operators.
    """
    check_id: str
    player_id: str
    current_month_deposits_eur: Decimal
    requested_deposit_eur: Decimal
    limit_eur: Decimal
    allowed: bool
    remaining_allowance_eur: Decimal
    checked_at: datetime


# ---------------------------------------------------------------------------
# OASIS API client
# ---------------------------------------------------------------------------

class OasisClient:
    """
    HTTP client for the GGL OASIS v3 API.

    The OASIS system is a federated cross-state database.  A single
    query covers all 16 Bundesländer simultaneously — there is no
    per-Bundesland query needed.
    """

    def __init__(self, config: OasisConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Exclusion check
    # ------------------------------------------------------------------

    def check_player(self, player: OasisPlayer) -> OasisCheckResult:
        """
        Real-time exclusion status check.  Must be performed at registration
        and at every login.
        """
        check_id = f"OASIS-{uuid.uuid4().hex[:12].upper()}"
        log.debug("oasis: querying exclusion status",
                  check_id=check_id, player_id=player.player_id)

        for attempt in range(self._config.retry_attempts):
            try:
                response = self._post_check_request(player, check_id)
                return self._parse_response(check_id, player.player_id, response)
            except httpx.TimeoutException:
                log.warning("oasis: timeout", attempt=attempt + 1,
                            player_id=player.player_id)
                if attempt < self._config.retry_attempts - 1:
                    time.sleep(0.5 * (attempt + 1))
            except httpx.HTTPStatusError as exc:
                log.error("oasis: HTTP error",
                          status=exc.response.status_code,
                          player_id=player.player_id)
                break
            except Exception as exc:
                log.error("oasis: unexpected error",
                          error=str(exc), player_id=player.player_id)
                break

        return OasisCheckResult(
            check_id=check_id,
            player_id=player.player_id,
            status=OasisStatus.API_UNAVAILABLE,
            checked_at=datetime.now(timezone.utc),
            access_allowed=not _FAIL_CLOSED,
            error_detail="OASIS API unavailable after retries",
        )

    def register_exclusion(
        self,
        player: OasisPlayer,
        duration: ExclusionDuration,
        exclusion_type: ExclusionType = ExclusionType.SELF,
    ) -> OasisExclusionRegistration:
        """
        Register a player self-exclusion in OASIS.

        The registration is immediately valid across all 16 Bundesländer.
        Operators must process within 1 business day.
        """
        reg_id = f"OREG-{uuid.uuid4().hex[:12].upper()}"
        log.info("oasis: registering exclusion",
                 registration_id=reg_id,
                 player_id=player.player_id,
                 duration=duration.value,
                 type=exclusion_type.value)

        try:
            payload: dict[str, Any] = {
                "operatorId": self._config.operator_id,
                "idDocumentHash": player.id_document_hash,
                "firstName": player.first_name,
                "lastName": player.last_name,
                "dateOfBirth": player.date_of_birth,
                "bundesland": player.bundesland,
                "exclusionType": exclusion_type.value,
                "duration": duration.value,
                "requestedAt": datetime.now(timezone.utc).isoformat(),
            }
            with httpx.Client(timeout=self._config.timeout_seconds) as client:
                resp = client.post(
                    f"{self._config.api_base_url}/exclusions",
                    json=payload,
                    headers=self._headers(),
                )
            resp.raise_for_status()
            data = resp.json()

            return OasisExclusionRegistration(
                registration_id=reg_id,
                player_id=player.player_id,
                exclusion_type=exclusion_type,
                duration=duration,
                requested_at=datetime.now(timezone.utc),
                effective_at=datetime.now(timezone.utc),
                oasis_reference=data.get("oasisReference"),
                success=True,
            )

        except Exception as exc:
            log.error("oasis: exclusion registration failed",
                      registration_id=reg_id, error=str(exc))
            return OasisExclusionRegistration(
                registration_id=reg_id,
                player_id=player.player_id,
                exclusion_type=exclusion_type,
                duration=duration,
                requested_at=datetime.now(timezone.utc),
                success=False,
                error_detail=str(exc),
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _post_check_request(
        self, player: OasisPlayer, check_id: str
    ) -> dict[str, Any]:
        payload = {
            "idDocumentHash": player.id_document_hash,
            "dateOfBirth": player.date_of_birth,
            "operatorId": self._config.operator_id,
            "checkId": check_id,
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        }
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            resp = client.post(
                f"{self._config.api_base_url}/checks",
                json=payload,
                headers=self._headers(),
            )
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def _parse_response(
        check_id: str, player_id: str, data: dict[str, Any]
    ) -> OasisCheckResult:
        excluded = data.get("excluded", False)
        status = OasisStatus.EXCLUDED if excluded else OasisStatus.NOT_EXCLUDED

        end_date: Optional[datetime] = None
        if excluded and data.get("exclusionEndDate"):
            end_date = datetime.fromisoformat(data["exclusionEndDate"])

        return OasisCheckResult(
            check_id=check_id,
            player_id=player_id,
            status=status,
            checked_at=datetime.now(timezone.utc),
            access_allowed=not excluded,
            exclusion_end_date=end_date,
            bundesland_of_registration=data.get("bundeslandOfRegistration"),
            raw_response=data,
        )

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key": self._config.api_key,
            "X-Operator-Id": self._config.operator_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }


# ---------------------------------------------------------------------------
# LUGAS deposit limit checker
# ---------------------------------------------------------------------------

class LugasService:
    """
    Checks the cross-operator monthly deposit limit mandated by GlüStV § 6a.

    LUGAS (Limitierungs- und Sperrsystem) is a separate system from OASIS.
    It aggregates deposits across all German-licensed operators and enforces
    the €1,000/month limit.  Operators must query LUGAS before every deposit.
    """

    def __init__(self, config: OasisConfig) -> None:
        self._config = config

    def check_deposit_allowed(
        self,
        player: OasisPlayer,
        requested_deposit_eur: Decimal,
    ) -> LugasDepositCheck:
        """
        Verify that the requested deposit does not breach the cross-operator
        monthly aggregate limit.

        In production: call the GGL LUGAS API.  Stub returns a synthetic
        result for demonstration.
        """
        check_id = f"LUGAS-{uuid.uuid4().hex[:10].upper()}"

        # --- LUGAS API stub -------------------------------------------------
        # In production: GET /lugas/deposits?playerId=<hash>&month=<YYYY-MM>
        current_month_deposits = Decimal("350.00")   # stub
        # --------------------------------------------------------------------

        remaining = MONTHLY_DEPOSIT_LIMIT_EUR - current_month_deposits
        allowed = requested_deposit_eur <= remaining

        result = LugasDepositCheck(
            check_id=check_id,
            player_id=player.player_id,
            current_month_deposits_eur=current_month_deposits,
            requested_deposit_eur=requested_deposit_eur,
            limit_eur=MONTHLY_DEPOSIT_LIMIT_EUR,
            allowed=allowed,
            remaining_allowance_eur=max(Decimal("0"), remaining),
            checked_at=datetime.now(timezone.utc),
        )

        if not allowed:
            log.warning(
                "lugas: deposit would exceed monthly limit",
                player_id=player.player_id,
                requested=str(requested_deposit_eur),
                current_month=str(current_month_deposits),
                limit=str(MONTHLY_DEPOSIT_LIMIT_EUR),
            )

        return result


# ---------------------------------------------------------------------------
# High-level service
# ---------------------------------------------------------------------------

class OasisService:
    """
    Application-level service combining OASIS exclusion checks and
    LUGAS deposit limit enforcement for German-market operations.
    """

    def __init__(self, oasis_client: OasisClient, lugas: LugasService) -> None:
        self._oasis = oasis_client
        self._lugas = lugas

    def check_at_login(self, player: OasisPlayer) -> OasisCheckResult:
        result = self._oasis.check_player(player)
        level = "warning" if not result.access_allowed else "info"
        log.bind(
            event_type="login_check",
            player_id=player.player_id,
            oasis_status=result.status.value,
            access_allowed=result.access_allowed,
        ).log(level, "oasis: login check complete")
        return result

    def check_at_registration(self, player: OasisPlayer) -> OasisCheckResult:
        result = self._oasis.check_player(player)
        log.info("oasis: registration check complete",
                 player_id=player.player_id,
                 access_allowed=result.access_allowed)
        return result

    def register_panic_button(
        self, player: OasisPlayer
    ) -> OasisExclusionRegistration:
        """
        Immediate self-exclusion via the panic button.
        Defaulted to permanent exclusion; player may request shorter on appeal.
        """
        return self._oasis.register_exclusion(
            player,
            duration=ExclusionDuration.PERMANENT,
            exclusion_type=ExclusionType.SELF,
        )

    def validate_deposit(
        self, player: OasisPlayer, amount_eur: Decimal
    ) -> LugasDepositCheck:
        """Check LUGAS cross-operator monthly limit before processing a deposit."""
        return self._lugas.check_deposit_allowed(player, amount_eur)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    import hashlib

    config = OasisConfig(
        api_base_url="https://oasis-api.ggl.de/v3",
        operator_id="GGL-OP-98765",
        api_key="secret-ggl-api-key",
    )
    client = OasisClient(config)
    lugas = LugasService(config)
    service = OasisService(client, lugas)

    player = OasisPlayer(
        player_id="player-de-1234",
        id_document_hash=hashlib.sha256(b"T220001293").hexdigest(),
        first_name="Max",
        last_name="Mustermann",
        date_of_birth="1990-03-22",
        bundesland="DE-BY",
    )

    print("Germany OASIS integration demo")
    print(f"Player: {player.player_id}")
    print(f"Monthly deposit limit: €{MONTHLY_DEPOSIT_LIMIT_EUR}")
    print("In production, this would query the live GGL OASIS and LUGAS APIs.")


if __name__ == "__main__":
    _demo()
