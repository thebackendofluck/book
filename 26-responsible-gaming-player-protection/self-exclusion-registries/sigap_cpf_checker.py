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
sigap_cpf_checker.py — Brazil SIGAP self-exclusion integration.

Jurisdiction:       Federative Republic of Brazil
Regulator:          SPA/MF (Secretaria de Premios e Apostas / Ministerio da Fazenda)
Regulation refs:
  - Normative Instruction 31 (November 2025) — SIGAP operational requirements
  - Portaria SPA/MF 1231 — licensed operator obligations
  - SIGAP launched December 10, 2025
Penalties:
  - Licence suspension or revocation per SPA/MF framework
  - Operators had 30 days from NI 31 publication to implement

Key requirements:
  - CPF-based identity verification (11-digit Cadastro de Pessoas Fisicas)
  - Three verification touchpoints:
      1. Registration (before account creation)
      2. First daily login
      3. Every 15 days for active accounts (batch sweep)
  - Self-exclusion periods: 1-12 months or indefinite
  - Stop accepting bets immediately upon exclusion
  - Terminate account within 3 calendar days
  - Return balance within 2 business days of termination

Book chapter:  Chapter 26b — Self-Exclusion Registries Implementation
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SIGAP_TIMEOUT_SECONDS = 5
_SIGAP_RETRY_ATTEMPTS = 3
_FAIL_CLOSED = True
_ACCOUNT_TERMINATION_DAYS = 3
_BALANCE_RETURN_BUSINESS_DAYS = 2
_SWEEP_INTERVAL_DAYS = 15


# ---------------------------------------------------------------------------
# CPF validation
# ---------------------------------------------------------------------------

def validate_cpf(cpf: str) -> bool:
    """
    Validate a Brazilian CPF using the modulo-11 check digit algorithm.

    CPF format: 11 digits (XXX.XXX.XXX-XX or plain 11 digits).
    """
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11:
        return False
    # Reject known-invalid sequences (all same digit)
    if len(set(digits)) == 1:
        return False

    # First check digit
    total = sum(int(digits[i]) * (10 - i) for i in range(9))
    remainder = total % 11
    first_check = 0 if remainder < 2 else 11 - remainder
    if int(digits[9]) != first_check:
        return False

    # Second check digit
    total = sum(int(digits[i]) * (11 - i) for i in range(10))
    remainder = total % 11
    second_check = 0 if remainder < 2 else 11 - remainder
    return int(digits[10]) == second_check


# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class SigapStatus(str, Enum):
    NOT_EXCLUDED = "not_excluded"
    EXCLUDED = "excluded"
    API_UNAVAILABLE = "api_unavailable"


class ExclusionPeriod(str, Enum):
    ONE_MONTH = "1_month"
    THREE_MONTHS = "3_months"
    SIX_MONTHS = "6_months"
    TWELVE_MONTHS = "12_months"
    INDEFINITE = "indefinite"


class TerminationStatus(str, Enum):
    PENDING = "pending"
    BETS_BLOCKED = "bets_blocked"
    ACCOUNT_TERMINATED = "account_terminated"
    BALANCE_RETURNED = "balance_returned"
    COMPLETE = "complete"


@dataclass
class SigapConfig:
    api_base_url: str
    operator_cnpj: str      # operator's CNPJ (legal entity ID)
    api_key: str
    timeout_seconds: int = _SIGAP_TIMEOUT_SECONDS
    retry_attempts: int = _SIGAP_RETRY_ATTEMPTS


@dataclass
class BrazilianPlayer:
    player_id: str
    cpf: str                 # 11-digit CPF
    full_name: str
    date_of_birth: str       # ISO-8601
    last_sigap_check: Optional[datetime] = None
    last_login_date: Optional[date] = None


@dataclass
class SigapCheckResult:
    check_id: str
    player_id: str
    cpf_masked: str          # XXX.***.***-XX for logging
    status: SigapStatus
    access_allowed: bool
    check_type: str          # "registration", "daily_login", "15_day_sweep"
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    exclusion_period: Optional[ExclusionPeriod] = None
    exclusion_end_date: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class AccountTermination:
    termination_id: str
    player_id: str
    status: TerminationStatus
    exclusion_detected_at: datetime
    bets_blocked_at: Optional[datetime] = None
    account_terminated_at: Optional[datetime] = None
    balance_returned_at: Optional[datetime] = None
    balance_amount_brl: Optional[str] = None
    termination_deadline: Optional[datetime] = None
    balance_return_deadline: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _mask_cpf(cpf: str) -> str:
    digits = re.sub(r"\D", "", cpf)
    if len(digits) != 11:
        return "***.***.***-**"
    return f"{digits[:3]}.***.***.{digits[9:]}"


# ---------------------------------------------------------------------------
# SIGAP service
# ---------------------------------------------------------------------------

class SigapService:
    """
    Brazil SIGAP integration implementing three verification touchpoints.

    Touchpoint 1: Registration — before account creation.
    Touchpoint 2: First daily login — first login of each calendar day.
    Touchpoint 3: 15-day sweep — batch job for all active accounts.
    """

    def __init__(self, config: SigapConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Touchpoint 1: registration
    # ------------------------------------------------------------------

    def check_at_registration(self, player: BrazilianPlayer) -> SigapCheckResult:
        """NI 31: verify CPF against SIGAP before account creation."""
        if not validate_cpf(player.cpf):
            return SigapCheckResult(
                check_id=f"SIGAP-REG-{uuid.uuid4().hex[:10].upper()}",
                player_id=player.player_id,
                cpf_masked=_mask_cpf(player.cpf),
                status=SigapStatus.API_UNAVAILABLE,
                access_allowed=False,
                check_type="registration",
                error_detail="Invalid CPF format",
            )
        return self._query_sigap(player, "registration")

    # ------------------------------------------------------------------
    # Touchpoint 2: first daily login
    # ------------------------------------------------------------------

    def check_at_daily_login(self, player: BrazilianPlayer) -> Optional[SigapCheckResult]:
        """
        NI 31: verify at the first login of each calendar day.

        Returns None if the player has already been checked today.
        """
        today = date.today()
        if player.last_login_date == today:
            return None  # already checked today
        return self._query_sigap(player, "daily_login")

    # ------------------------------------------------------------------
    # Touchpoint 3: 15-day sweep (batch)
    # ------------------------------------------------------------------

    def run_15_day_sweep(
        self, active_players: list[BrazilianPlayer]
    ) -> list[SigapCheckResult]:
        """
        NI 31: re-verify all active accounts every 15 days.

        In production: scheduled as a cron job, processes players in
        batches to respect API rate limits.
        """
        results: list[SigapCheckResult] = []
        now = datetime.now(timezone.utc)
        threshold = now - timedelta(days=_SWEEP_INTERVAL_DAYS)

        for player in active_players:
            if (player.last_sigap_check is not None
                    and player.last_sigap_check > threshold):
                continue  # checked within 15 days, skip
            result = self._query_sigap(player, "15_day_sweep")
            results.append(result)

        log.info("sigap: 15-day sweep complete",
                 total_players=len(active_players),
                 checked=len(results))
        return results

    # ------------------------------------------------------------------
    # Account termination workflow
    # ------------------------------------------------------------------

    def initiate_termination(
        self, player: BrazilianPlayer, balance_brl: str
    ) -> AccountTermination:
        """
        NI 31 account termination timeline:
          Day 0: stop bets immediately
          Day 0-3: terminate account
          Day 1-5: return balance (2 business days after termination)
        """
        now = datetime.now(timezone.utc)
        term_id = f"TERM-{uuid.uuid4().hex[:10].upper()}"

        termination = AccountTermination(
            termination_id=term_id,
            player_id=player.player_id,
            status=TerminationStatus.BETS_BLOCKED,
            exclusion_detected_at=now,
            bets_blocked_at=now,
            balance_amount_brl=balance_brl,
            termination_deadline=now + timedelta(days=_ACCOUNT_TERMINATION_DAYS),
            balance_return_deadline=now + timedelta(
                days=_ACCOUNT_TERMINATION_DAYS + _BALANCE_RETURN_BUSINESS_DAYS + 2
            ),  # +2 for weekend buffer
        )

        log.info(
            "sigap: termination initiated",
            termination_id=term_id,
            player_id=player.player_id,
            balance_brl=balance_brl,
            termination_deadline=termination.termination_deadline.isoformat()  # type: ignore[union-attr]
            if termination.termination_deadline else None,
        )
        return termination

    # ------------------------------------------------------------------
    # Private: SIGAP API query
    # ------------------------------------------------------------------

    def _query_sigap(
        self, player: BrazilianPlayer, check_type: str
    ) -> SigapCheckResult:
        check_id = f"SIGAP-{check_type.upper()[:3]}-{uuid.uuid4().hex[:10].upper()}"
        cpf_digits = re.sub(r"\D", "", player.cpf)

        for attempt in range(self._config.retry_attempts):
            try:
                data = self._call_api(cpf_digits, check_id)
                excluded = data.get("excluded", False)
                status = (
                    SigapStatus.EXCLUDED if excluded
                    else SigapStatus.NOT_EXCLUDED
                )
                result = SigapCheckResult(
                    check_id=check_id,
                    player_id=player.player_id,
                    cpf_masked=_mask_cpf(player.cpf),
                    status=status,
                    access_allowed=not excluded,
                    check_type=check_type,
                    exclusion_period=ExclusionPeriod(data["exclusionPeriod"])
                    if excluded and data.get("exclusionPeriod") else None,
                    exclusion_end_date=data.get("exclusionEndDate"),
                )
                self._audit_log(result)
                return result

            except httpx.TimeoutException:
                log.warning("sigap: timeout", attempt=attempt + 1,
                            player_id=player.player_id)
            except Exception as exc:
                log.error("sigap: query failed",
                          error=str(exc), player_id=player.player_id)
                break

        # Fail closed
        result = SigapCheckResult(
            check_id=check_id,
            player_id=player.player_id,
            cpf_masked=_mask_cpf(player.cpf),
            status=SigapStatus.API_UNAVAILABLE,
            access_allowed=not _FAIL_CLOSED,
            check_type=check_type,
            error_detail="SIGAP API unreachable after retries",
        )
        self._audit_log(result)
        return result

    def _call_api(self, cpf_digits: str, check_id: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cpf": cpf_digits,
            "operatorCnpj": self._config.operator_cnpj,
            "checkId": check_id,
            "requestedAt": datetime.now(timezone.utc).isoformat(),
        }
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            resp = client.post(
                f"{self._config.api_base_url}/exclusions/verify",
                json=payload,
                headers={
                    "X-Api-Key": self._config.api_key,
                    "Content-Type": "application/json",
                },
            )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return data

    @staticmethod
    def _audit_log(result: SigapCheckResult) -> None:
        log.info(
            "sigap: audit",
            check_id=result.check_id,
            player_id=result.player_id,
            cpf_masked=result.cpf_masked,
            check_type=result.check_type,
            status=result.status.value,
            access_allowed=result.access_allowed,
            checked_at=result.checked_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _demo() -> None:
    config = SigapConfig(
        api_base_url="https://sigap.example.invalid/api/v1",
        operator_cnpj="12.345.678/0001-90",
        api_key="secret-sigap-key",
    )
    service = SigapService(config)

    # Valid CPF for testing: 529.982.247-25 (passes modulo-11)
    player = BrazilianPlayer(
        player_id="plr-br-001",
        cpf="52998224725",
        full_name="Joao da Silva",
        date_of_birth="1985-07-20",
    )

    print("Brazil SIGAP integration demo")
    print(f"Player: {player.player_id}")
    print(f"CPF valid: {validate_cpf(player.cpf)}")
    print(f"CPF masked: {_mask_cpf(player.cpf)}")
    print(f"Sweep interval: {_SWEEP_INTERVAL_DAYS} days")
    print(f"Account termination deadline: {_ACCOUNT_TERMINATION_DAYS} days")
    print(f"Balance return deadline: {_BALANCE_RETURN_BUSINESS_DAYS} business days")
    print("In production, this would query the live SIGAP API.")


if __name__ == "__main__":
    _demo()
