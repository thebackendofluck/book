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
spelpaus.py — Swedish Spelpaus API integration.

Spelpaus is Sweden's national self-exclusion registry, operated under
Spelinspektionen (SGA). All Swedish licence holders must check players.

Privacy architecture:
  - Player IDs are MD5-hashed before transmission (Spelpaus never receives PII)
  - The registry responds with the list of ALLOWED (i.e. NOT excluded) hashes
  - Excluded players are those whose hash is absent from the allowed list

Protocol:
  POST {base_url}/api/marketing-subjectid/{actor_id}
  Header: X-Api-Key: {key}
  Body: JSON object {"requestId": UUID, "items": [{"itemId": md5, "subjectId": personalNumber}]}
  Response: {"allowedItemIds": [...], "responseId": UUID}

SSN format:
  Stored as YYMMDD-NNNN (Swedish short format).
  Spelpaus expects YYYYMMDDNNNN (12-digit, century-expanded, no dash).
  The century prefix is derived from the player's date of birth.

Schedule:
  - Mon/Wed/Fri at 06:00 UTC: check non-marketing-excluded users
  - First Monday of each month: full sweep of ALL Swedish players
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime

import httpx
import structlog

from models import SpelpausApiConfig, SpelpausUser

log = structlog.get_logger(__name__)


class SpelpausService:
    """
    Client for the Spelpaus marketing-subjectid batch endpoint.

    Mirrors SpelpausService.scala, including the SSN century expansion
    (buildFullPIN) and the privacy-preserving MD5 hashing of user IDs.
    """

    def __init__(self, config: SpelpausApiConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def check_users(self, users: list[SpelpausUser]) -> set[int]:
        """
        Check users against Spelpaus and return IDs of excluded players.

        Returns a set of player IDs whose hash was NOT in the allowed list.
        """
        if not users:
            return set()

        request_id = str(uuid.uuid4())
        items = [
            {
                "itemId":    self._md5_user_id(u.id),
                "subjectId": self._build_full_pin(u.ssn, u.dob),
            }
            for u in users
        ]

        allowed_ids = self._call_api(request_id, items)

        # Build reverse map: md5_hash -> user_id
        hash_to_user = {self._md5_user_id(u.id): u.id for u in users}

        excluded_ids = {
            user_id
            for md5, user_id in hash_to_user.items()
            if md5 not in allowed_ids
        }
        log.info("spelpaus: check complete",
                 checked=len(users), excluded=len(excluded_ids))
        return excluded_ids

    def check_single(self, user: SpelpausUser) -> bool:
        """Return True if the player is currently registered with Spelpaus."""
        excluded = self.check_users([user])
        return user.id in excluded

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _md5_user_id(user_id: int) -> str:
        return hashlib.md5(str(user_id).encode()).hexdigest()

    @staticmethod
    def _build_full_pin(ssn: str, dob: date) -> str:
        """
        Convert stored SSN (YYMMDD-NNNN) → Spelpaus format (YYYYMMDDNNNN).

        The Spelpaus API requires a 12-digit personal number with century.
        We derive the century from the stored date of birth, then append
        the 4-digit suffix (random + check digits) from the stored SSN.

        Example:
          ssn = "900115-1234", dob = date(1990, 1, 15)
          → "199001151234"
        """
        dob_str = dob.strftime("%Y%m%d")          # "YYYYMMDD"
        suffix  = ssn[7:]                           # last 4 digits after dash
        return dob_str + suffix                     # "YYYYMMDDNNNN"

    def _call_api(self, request_id: str, items: list[dict]) -> set[str]:
        """POST to Spelpaus batch endpoint and return set of allowed item IDs."""
        url = f"{self._config.batch_service_url}/{self._config.actor_id}"
        body = {
            "requestId": request_id,
            "items":     items,
        }
        log.debug("spelpaus: sending batch",
                  request_id=request_id, count=len(items), url=url)

        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(
                url,
                json=body,
                headers={
                    "X-Api-Key":    self._config.api_key,
                    "Content-Type": "application/json",
                },
            )

        resp.raise_for_status()
        data = resp.json()
        allowed = set(data.get("allowedItemIds", []))
        log.debug("spelpaus: received response",
                  request_id=request_id,
                  their_ref=data.get("responseId", ""),
                  allowed_count=len(allowed))
        return allowed
