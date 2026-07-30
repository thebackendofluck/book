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
rofus.py — Danish ROFUS API integration.

ROFUS (Register Over Frivilligt Udelukkede Spillere) is Denmark's national
self-exclusion register, operated by Spillemyndigheden (the Danish Gambling
Authority). All Danish B2C licence holders must check players before allowing
participation.

Protocol (Spillemyndigheden REST API):
  POST {base_url}/api/v1/exclusion/check
  Header: X-Api-Key: {key}
  Header: X-Operator-Id: {operator_id}
  Body: {"cpr": "DDMMYY-NNNN"}
  Response: {"excluded": true|false, "until": "YYYY-MM-DD" | null}

  Batch check:
  POST {base_url}/api/v1/exclusion/batch-check
  Body: {"cprs": ["DDMMYY-NNNN", ...]}
  Response: {"results": [{"cpr": ..., "excluded": bool, "until": ...}, ...]}

CPR number:
  Danish CPR (Det Centrale Personregister) is the national ID:
  format DDMMYY-NNNN. The last digit is a check digit.

Notes:
  - ROFUS does not support MD5 hashing — CPR is sent in plaintext over TLS.
  - Operators may only register self-exclusions via the Spillemyndigheden
    online portal, not via API.
  - The "until" date is null for permanent exclusions (min 1 year, max permanent).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import httpx
import structlog

from models import RofusApiConfig, RofusUser

log = structlog.get_logger(__name__)


class RofusService:
    """
    Client for the ROFUS exclusion check API.

    Supports single-player lookups and batch checks.
    Exclusion status is returned with an optional end date
    (null = permanent or open-ended exclusion).
    """

    def __init__(self, config: RofusApiConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_single(self, user: RofusUser) -> tuple[bool, Optional[str]]:
        """
        Check a single player against ROFUS.

        Returns:
            (is_excluded, exclusion_until_date)
            exclusion_until_date is an ISO date string or None (permanent).
        """
        log.debug("rofus: single check", user_id=user.id)
        result = self._call_single(user.cpr)
        excluded = result.get("excluded", False)
        until    = result.get("until")
        log.info("rofus: check result",
                 user_id=user.id, excluded=excluded, until=until)
        return excluded, until

    def check_batch(self, users: list[RofusUser]) -> dict[int, tuple[bool, Optional[str]]]:
        """
        Batch check a list of players against ROFUS.

        Returns a dict mapping player_id -> (is_excluded, exclusion_until).
        """
        if not users:
            return {}

        cpr_to_user = {u.cpr: u for u in users}
        results = self._call_batch(list(cpr_to_user.keys()))

        outcome: dict[int, tuple[bool, Optional[str]]] = {}
        for entry in results:
            cpr      = entry.get("cpr", "")
            excluded = entry.get("excluded", False)
            until    = entry.get("until")
            user = cpr_to_user.get(cpr)
            if user:
                outcome[user.id] = (excluded, until)

        excluded_count = sum(1 for exc, _ in outcome.values() if exc)
        log.info("rofus: batch check complete",
                 checked=len(users), excluded=excluded_count)
        return outcome

    def get_excluded_users(self, users: list[RofusUser]) -> list[RofusUser]:
        """Return only the players currently registered with ROFUS."""
        results = self.check_batch(users)
        return [u for u in users if results.get(u.id, (False, None))[0]]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _call_single(self, cpr: str) -> dict:
        url = f"{self._config.base_url}/api/v1/exclusion/check"
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(
                url,
                json={"cpr": cpr},
                headers=self._headers(),
            )
        resp.raise_for_status()
        return resp.json()

    def _call_batch(self, cprs: list[str]) -> list[dict]:
        url = f"{self._config.base_url}/api/v1/exclusion/batch-check"
        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(
                url,
                json={"cprs": cprs},
                headers=self._headers(),
            )
        resp.raise_for_status()
        return resp.json().get("results", [])

    def _headers(self) -> dict[str, str]:
        return {
            "X-Api-Key":     self._config.api_key,
            "X-Operator-Id": self._config.operator_id,
            "Content-Type":  "application/json",
        }
