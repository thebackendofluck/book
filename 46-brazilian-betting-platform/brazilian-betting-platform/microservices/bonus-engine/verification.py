# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""External-truth verification ports for the Bonus Engine.

Two things this service must never trust from a client request body:
  1. Wagering progress — must come from a settled bet/ledger event.
  2. Deposit-gated eligibility — must come from a real deposit record.

Both are modeled as small ports (ABCs) with an HTTP adapter that calls the
platform's settlement and wallet services. Tests substitute fakes via
FastAPI's `app.dependency_overrides`.
"""
from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

import httpx

from models import WageringContribution

logger = logging.getLogger(__name__)


# ── Settled bets (wagering credit) ──────────────────────────────────────────

class SettledBetNotFoundError(Exception):
    """The referenced bet does not exist, or is not yet settled."""


class SettledBetOwnershipError(Exception):
    """The settled bet does not belong to the claiming CPF."""


@dataclass(frozen=True)
class SettledBet:
    bet_id: str
    cpf: str
    stake: Decimal
    bet_type: WageringContribution


class SettledBetProvider(ABC):
    """Port for retrieving settled-bet facts from the bet/ledger service.

    Wagering progress must always be derived from this port, never from
    values a caller sends in the request body.
    """

    @abstractmethod
    async def get_settled_bet(self, bet_id: str, cpf: str) -> SettledBet:
        ...


class HttpSettledBetProvider(SettledBetProvider):
    """Production adapter — calls the settlement/ledger service."""

    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        self._base_url = base_url or os.getenv("SETTLEMENT_SERVICE_URL", "")
        self._timeout = timeout

    async def get_settled_bet(self, bet_id: str, cpf: str) -> SettledBet:
        if not self._base_url:
            # Fail closed: without a configured ledger endpoint we cannot
            # verify any wager, so refuse to silently trust the caller.
            logger.error("SETTLEMENT_SERVICE_URL not configured; wagering credit refused")
            raise SettledBetNotFoundError(
                "settlement service not configured (SETTLEMENT_SERVICE_URL unset)"
            )
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.get(f"/bets/{bet_id}")
        if resp.status_code == 404:
            raise SettledBetNotFoundError(f"bet {bet_id} not found")
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "SETTLED":
            raise SettledBetNotFoundError(f"bet {bet_id} is not settled")
        if str(data.get("cpf")) != str(cpf):
            raise SettledBetOwnershipError(f"bet {bet_id} does not belong to cpf")
        return SettledBet(
            bet_id=bet_id,
            cpf=str(data["cpf"]),
            stake=Decimal(str(data["stake"])),
            bet_type=WageringContribution(data["bet_type"]),
        )


async def get_settled_bet_provider() -> SettledBetProvider:
    """FastAPI dependency — overridden with a fake in tests."""
    return HttpSettledBetProvider()


# ── Deposits (min_deposit eligibility) ──────────────────────────────────────

class DepositVerificationUnavailableError(Exception):
    """The wallet/deposit service could not be reached or is not configured."""


class DepositVerificationProvider(ABC):
    """Port for verifying a player's confirmed deposit total."""

    @abstractmethod
    async def total_confirmed_deposits(self, cpf: str) -> Decimal:
        ...


class HttpDepositVerificationProvider(DepositVerificationProvider):
    """Production adapter — calls the wallet service."""

    def __init__(self, base_url: str | None = None, timeout: float = 5.0) -> None:
        self._base_url = base_url or os.getenv("WALLET_SERVICE_URL", "")
        self._timeout = timeout

    async def total_confirmed_deposits(self, cpf: str) -> Decimal:
        if not self._base_url:
            logger.error("WALLET_SERVICE_URL not configured; min_deposit check refused")
            raise DepositVerificationUnavailableError(
                "wallet service not configured (WALLET_SERVICE_URL unset)"
            )
        async with httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout) as client:
            resp = await client.get(f"/wallets/{cpf}/deposits/confirmed-total")
        resp.raise_for_status()
        return Decimal(str(resp.json()["total"]))


async def get_deposit_provider() -> DepositVerificationProvider:
    """FastAPI dependency — overridden with a fake in tests."""
    return HttpDepositVerificationProvider()
