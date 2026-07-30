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
gamstop.py — UK GamStop API integration.

GamStop is the UK's national self-exclusion scheme, mandated by the UKGC.
Operators with a UK Gambling Commission licence must check all UK-registered
players against the GamStop registry.

Protocol:
  - POST to the batch endpoint with an array of PII objects (name, DOB, postcode)
  - API returns per-user exclusionStatus: "Y" = excluded, "N" = not excluded
  - Rate limit: 1 request per second per IP address
  - Max batch size: 1,000 players per request
  - No pre-hashing — PII is transmitted over TLS

Reference:
  https://www.gamstop.co.uk/operator/technical-integration
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

import httpx
import structlog

from models import GamstopApiConfig, GamstopUser

log = structlog.get_logger(__name__)

# GamStop exclusion flag in API response
_EXCLUDED_FLAG = "Y"

# Hard API rate limit: 1 req/s per IP
_RATE_LIMIT_SECONDS = 1.0


class GamstopService:
    """
    Client for the GamStop batch exclusion endpoint.

    The API accepts up to 1,000 players per POST request and returns
    an array of exclusion results in the same order as the request.

    Validation rules (from GamStop spec):
      - firstName:   2-255 characters
      - lastName:    2-255 characters
      - dateOfBirth: ISO-8601 (YYYY-MM-DD)
      - postcode:    5-10 characters (spaces not significant)
      - email:       2-254 characters (optional — omitted if invalid)
      - mobile:      UK mobile, E.164 format (optional)
    """

    def __init__(self, config: GamstopApiConfig) -> None:
        self._config = config
        self._last_request_at: float = 0.0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_excluded_users(self, users: list[GamstopUser]) -> list[GamstopUser]:
        """
        Query GamStop for a list of users and return only the excluded ones.

        Invalid users (missing/out-of-range mandatory fields) are silently
        skipped — mirroring the original UserValidator.isValid behaviour.
        """
        valid_users = [u for u in users if self._is_valid(u)]
        if not valid_users:
            return []

        payload = [self._to_payload(u) for u in valid_users]
        results = self._call_api(payload)

        excluded_indices = {
            i for i, r in enumerate(results)
            if r.get("exclusionStatus") == _EXCLUDED_FLAG
        }
        return [u for i, u in enumerate(valid_users) if i in excluded_indices]

    def check_single(self, user: GamstopUser) -> bool:
        """Return True if the player is currently registered with GamStop."""
        results = self.get_excluded_users([user])
        return len(results) > 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_valid(user: GamstopUser) -> bool:
        """Apply GamStop field-length validation rules."""
        if not (2 <= len(user.first_name) <= 255):
            log.warning("gamstop: invalid firstName, skipping", user_id=user.id,
                        first_name=user.first_name)
            return False
        if not (2 <= len(user.last_name) <= 255):
            log.warning("gamstop: invalid lastName, skipping", user_id=user.id,
                        last_name=user.last_name)
            return False
        if not (5 <= len(user.postcode.replace(" ", "")) <= 10):
            log.warning("gamstop: invalid postcode, skipping", user_id=user.id,
                        postcode=user.postcode)
            return False
        return True

    @staticmethod
    def _to_payload(user: GamstopUser) -> dict[str, Any]:
        """Serialise a GamstopUser to the API payload format."""
        payload: dict[str, Any] = {
            "firstName":   user.first_name,
            "lastName":    user.last_name,
            "dateOfBirth": user.dob,
            "postcode":    user.postcode,
        }
        if user.email and 2 <= len(user.email) <= 254:
            payload["email"] = user.email
        if user.mobile:
            payload["mobile"] = user.mobile
        return payload

    def _enforce_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait = _RATE_LIMIT_SECONDS - elapsed
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _call_api(self, payload: list[dict]) -> list[dict]:
        """POST to the GamStop batch endpoint, respecting the rate limit."""
        request_id = str(uuid.uuid4())
        self._enforce_rate_limit()

        log.debug("gamstop: sending batch request",
                  request_id=request_id, batch_size=len(payload))

        with httpx.Client(timeout=self._config.response_timeout_seconds) as client:
            resp = client.post(
                self._config.batch_service_url,
                json=payload,
                headers={
                    "X-Trace-Id":    request_id,
                    "X-Api-Key":     self._config.api_key,
                    "Content-Type":  "application/json",
                },
            )

        resp.raise_for_status()
        data = resp.json()
        log.debug("gamstop: received response",
                  request_id=request_id, result_count=len(data))
        return data
