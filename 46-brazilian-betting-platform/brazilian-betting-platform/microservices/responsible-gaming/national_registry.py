# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Responsible Gaming Service — National Self-Exclusion Registry
=============================================================
Client for the Brazilian national self-exclusion platform
"Aposta Responsável" (operated by SPA/MF under Lei 14.790/2023).

Operators are legally required to:
  1. Register all self-exclusion requests with the national registry
     within 24 h of receipt.
  2. Check the registry before every account activation and login.
  3. Permanently exclude any player found on the national list.

API endpoints (production):
  POST /exclusions           — register a new exclusion
  DELETE /exclusions/{id}    — revoke after cooling-off expires
  GET  /exclusions/{cpf_hash} — check whether a CPF is excluded

Authentication: OAuth 2.0 client_credentials with GOV.BR IDP.
CPF is transmitted as SHA-256 hash (LGPD compliance).

Stub behaviour:
  - cpf_hash starting with "00"  → registered as permanently excluded
  - cpf_hash starting with "0a"  → registered as temporarily excluded
  - Revoke always succeeds for non-permanent records
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class RegistryError(RuntimeError):
    """National registry returned an unexpected error."""


class RevocationBlockedError(RuntimeError):
    """Cannot revoke: exclusion is permanent or cooling-off has not expired."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Revocation blocked: {reason}")


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class RegistryCheckResult:
    """Result from a national registry exclusion lookup."""

    cpf_hash: str
    is_excluded: bool
    exclusion_id: Optional[str]
    exclusion_type: Optional[str]   # "temporary" | "permanent"
    started_at: Optional[datetime]
    ends_at: Optional[datetime]
    source: str = "APOSTA_RESPONSAVEL"


@dataclass
class RegistryRegistrationResult:
    """Acknowledgement from registering a new exclusion."""

    registry_id: str
    cpf_hash: str
    exclusion_type: str
    registered_at: datetime
    accepted: bool
    message: str


# ---------------------------------------------------------------------------
# National Registry Client
# ---------------------------------------------------------------------------


class NationalRegistryClient:
    """
    Async client for the Aposta Responsável national self-exclusion registry.

    Simulates:
      - Network latency (50 ms)
      - Persistent state within a single process for integration testing
    """

    _NETWORK_LATENCY_S: float = 0.05

    def __init__(self) -> None:
        # In-memory store simulates the national registry database
        self._store: Dict[str, RegistryCheckResult] = {}

    async def check(self, cpf_hash: str) -> RegistryCheckResult:
        """
        Check whether a CPF hash is on the national self-exclusion list.

        Production: GET /exclusions/{cpf_hash} with bearer token.
        """
        await asyncio.sleep(self._NETWORK_LATENCY_S)

        # Check in-memory store first (populated by register())
        if cpf_hash in self._store:
            result = self._store[cpf_hash]
            logger.info(
                "registry_check",
                cpf_hash=cpf_hash[:8],
                is_excluded=result.is_excluded,
                source="in_memory",
            )
            return result

        # Stub: flag cpf_hash prefixes without prior registration
        now = datetime.now(timezone.utc)
        if cpf_hash.startswith("00"):
            result = RegistryCheckResult(
                cpf_hash=cpf_hash,
                is_excluded=True,
                exclusion_id=str(uuid.uuid4()),
                exclusion_type="permanent",
                started_at=now - timedelta(days=30),
                ends_at=None,
            )
            self._store[cpf_hash] = result
            return result

        if cpf_hash.startswith("0a"):
            result = RegistryCheckResult(
                cpf_hash=cpf_hash,
                is_excluded=True,
                exclusion_id=str(uuid.uuid4()),
                exclusion_type="temporary",
                started_at=now - timedelta(days=7),
                ends_at=now + timedelta(days=23),
            )
            self._store[cpf_hash] = result
            return result

        # Not excluded
        result = RegistryCheckResult(
            cpf_hash=cpf_hash,
            is_excluded=False,
            exclusion_id=None,
            exclusion_type=None,
            started_at=None,
            ends_at=None,
        )
        logger.info("registry_check", cpf_hash=cpf_hash[:8], is_excluded=False)
        return result

    async def register(
        self,
        cpf_hash: str,
        exclusion_type: str,
        duration_days: Optional[int] = None,
    ) -> RegistryRegistrationResult:
        """
        Register a new self-exclusion with the national registry.

        Production: POST /exclusions with JSON body.
        """
        await asyncio.sleep(self._NETWORK_LATENCY_S)

        registry_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        ends_at = now + timedelta(days=duration_days) if duration_days else None

        self._store[cpf_hash] = RegistryCheckResult(
            cpf_hash=cpf_hash,
            is_excluded=True,
            exclusion_id=registry_id,
            exclusion_type=exclusion_type,
            started_at=now,
            ends_at=ends_at,
        )

        logger.info(
            "registry_registered",
            cpf_hash=cpf_hash[:8],
            registry_id=registry_id,
            exclusion_type=exclusion_type,
            ends_at=ends_at.isoformat() if ends_at else "permanent",
        )

        return RegistryRegistrationResult(
            registry_id=registry_id,
            cpf_hash=cpf_hash,
            exclusion_type=exclusion_type,
            registered_at=now,
            accepted=True,
            message="Self-exclusion registered with national registry",
        )

    async def revoke(self, cpf_hash: str) -> bool:
        """
        Revoke a temporary self-exclusion after the cooling-off period.

        Returns True on success.
        Raises RevocationBlockedError if the exclusion is permanent or
        the cooling-off period has not yet expired.
        """
        await asyncio.sleep(self._NETWORK_LATENCY_S)

        existing = self._store.get(cpf_hash)

        if existing is None or not existing.is_excluded:
            return True  # Nothing to revoke

        if existing.exclusion_type == "permanent":
            raise RevocationBlockedError(
                "Permanent self-exclusions cannot be revoked"
            )

        if existing.ends_at and datetime.now(timezone.utc) < existing.ends_at:
            raise RevocationBlockedError(
                f"Cooling-off period expires at {existing.ends_at.isoformat()}"
            )

        del self._store[cpf_hash]
        logger.info("registry_revoked", cpf_hash=cpf_hash[:8])
        return True
