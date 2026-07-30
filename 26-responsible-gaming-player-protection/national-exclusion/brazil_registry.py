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
brazil_registry.py — Brazilian national self-exclusion registry integration.

Brazil's sports betting and online gaming market was regulated under
Law 14,790/2023. The national self-exclusion registry (BNAFAR — Base Nacional
de Apostadores com Restrição de Acesso) is operated by SEAE (Secretaria de
Acompanhamento Econômico) under the Ministry of Finance.

Protocol (SEAE REST API):
  GET  {base_url}/api/v1/exclusion/{cpf}
       Check if a CPF is on the exclusion list.
       Response: {"excluded": bool, "registered_at": "ISO-datetime" | null}

  POST {base_url}/api/v1/exclusion/register
       Self-register a player (operator-assisted self-exclusion).
       Body: {"cpf": "...", "duration": "permanent"|"1_year"|"5_years", "reason": "..."}
       Response: {"registration_id": "UUID", "effective_from": "ISO-datetime"}

  DELETE {base_url}/api/v1/exclusion/{cpf}
       Revoke a self-exclusion (subject to cooling-off period).
       Response: {"revoked": bool, "effective_from": "ISO-datetime"}

  POST {base_url}/api/v1/exclusion/batch-check
       Batch CPF check.
       Body: {"cpfs": ["000.000.000-00", ...]}
       Response: {"results": [{"cpf": ..., "excluded": bool}, ...]}

CPF (Cadastro de Pessoas Físicas):
  Brazil's individual taxpayer identification number.
  Format: 000.000.000-00 (with dots and dash, or 11 raw digits).

Notes:
  - Chapter 46 of the book covers the full Brazil regulatory framework.
  - BNAFAR was referenced in the Chapter 26 responsible-gambling design.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

import httpx
import structlog

from models import BrazilApiConfig, BrazilUser

log = structlog.get_logger(__name__)

# Regex that matches CPF in either format: "000.000.000-00" or "00000000000"
_CPF_PATTERN = re.compile(r"^\d{3}\.\d{3}\.\d{3}-\d{2}$|^\d{11}$")


def normalise_cpf(cpf: str) -> str:
    """Strip non-digit characters, returning the raw 11-digit CPF."""
    return re.sub(r"\D", "", cpf)


def format_cpf(cpf: str) -> str:
    """Format an 11-digit CPF to the canonical dotted form 000.000.000-00."""
    raw = normalise_cpf(cpf)
    if len(raw) != 11:
        raise ValueError(f"Invalid CPF length: {cpf!r}")
    return f"{raw[:3]}.{raw[3:6]}.{raw[6:9]}-{raw[9:]}"


class BrazilRegistryService:
    """
    Client for the Brazilian BNAFAR (national self-exclusion) API.

    Supports individual lookups, batch checks, registrations and revocations.
    CPF validation is applied locally before any API call.
    """

    def __init__(self, config: BrazilApiConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_single(self, user: BrazilUser) -> tuple[bool, Optional[str]]:
        """
        Check whether a player is on the Brazilian exclusion registry.

        Returns:
            (is_excluded, registered_at_iso_string)
        """
        cpf = normalise_cpf(user.cpf)
        self._validate_cpf_length(cpf, user.id)

        log.debug("brazil: single CPF check", user_id=user.id)
        data = self._get_exclusion(cpf)
        excluded    = data.get("excluded", False)
        registered  = data.get("registered_at")
        log.info("brazil: check result",
                 user_id=user.id, excluded=excluded, registered_at=registered)
        return excluded, registered

    def check_batch(self, users: list[BrazilUser]) -> dict[int, bool]:
        """
        Batch check a list of players.

        Returns a dict mapping player_id -> is_excluded.
        """
        if not users:
            return {}

        cpf_to_user = {normalise_cpf(u.cpf): u for u in users}
        results = self._post_batch(list(cpf_to_user.keys()))

        outcome: dict[int, bool] = {}
        for entry in results:
            cpf      = normalise_cpf(entry.get("cpf", ""))
            excluded = entry.get("excluded", False)
            user = cpf_to_user.get(cpf)
            if user:
                outcome[user.id] = excluded

        excluded_count = sum(1 for v in outcome.values() if v)
        log.info("brazil: batch check complete",
                 checked=len(users), excluded=excluded_count)
        return outcome

    def get_excluded_users(self, users: list[BrazilUser]) -> list[BrazilUser]:
        """Return players currently listed on BNAFAR."""
        results = self.check_batch(users)
        return [u for u in users if results.get(u.id, False)]

    def register(self, user: BrazilUser, duration: str = "permanent",
                 reason: Optional[str] = None) -> dict:
        """
        Register a player for self-exclusion.

        duration: "permanent" | "1_year" | "5_years"
        Returns the API registration response (registration_id, effective_from).
        """
        cpf = normalise_cpf(user.cpf)
        log.info("brazil: registering exclusion", user_id=user.id, duration=duration)
        body: dict = {"cpf": format_cpf(cpf), "duration": duration}
        if reason:
            body["reason"] = reason
        return self._post_register(body)

    def revoke(self, user: BrazilUser, reason: Optional[str] = None) -> dict:
        """
        Revoke a player's self-exclusion (subject to cooling-off period).

        Returns the API revocation response.
        """
        cpf = normalise_cpf(user.cpf)
        log.info("brazil: revoking exclusion", user_id=user.id)
        return self._delete_exclusion(cpf, reason)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_cpf_length(cpf: str, user_id: int) -> None:
        if len(cpf) != 11:
            raise ValueError(
                f"Invalid CPF for user {user_id}: expected 11 digits, got {len(cpf)}"
            )

    def _get_exclusion(self, cpf_raw: str) -> dict:
        url = f"{self._config.base_url}/api/v1/exclusion/{format_cpf(cpf_raw)}"
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.get(url, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def _post_batch(self, cpfs_raw: list[str]) -> list[dict]:
        url = f"{self._config.base_url}/api/v1/exclusion/batch-check"
        formatted = [format_cpf(c) for c in cpfs_raw]
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(url, json={"cpfs": formatted}, headers=self._headers())
        resp.raise_for_status()
        return resp.json().get("results", [])

    def _post_register(self, body: dict) -> dict:
        url = f"{self._config.base_url}/api/v1/exclusion/register"
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(url, json=body, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def _delete_exclusion(self, cpf_raw: str, reason: Optional[str]) -> dict:
        url = f"{self._config.base_url}/api/v1/exclusion/{format_cpf(cpf_raw)}"
        body = {"reason": reason} if reason else {}
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.request("DELETE", url, json=body, headers=self._headers())
        resp.raise_for_status()
        return resp.json()

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key":    self._config.api_key,
            "Content-Type": "application/json",
        }
