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
PAM Service — CPF Validator + Receita Federal Client
=====================================================
Implements:
  - Official Receita Federal CPF mod-11 digit check algorithm
  - Formatting, normalisation and SHA-256 hashing helpers
  - Async Receita Federal consultation client (mock stub; wire real
    endpoint with e-CNPJ mTLS certificate in production)

Reference: Instrução Normativa RFB 1.548/2015 and Lei 14.790/2023 Art. 6.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class CPFInvalidError(ValueError):
    """CPF fails the mod-11 digit check."""


class CPFDeceasedError(ValueError):
    """CPF belongs to a deceased individual."""


class CPFStatusError(ValueError):
    """CPF status is not 'regular' (e.g. suspended, cancelled)."""


class CPFNameMismatchError(ValueError):
    """Submitted name does not match Receita Federal record."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ReceitaFederalResult:
    """Parsed response from a Receita Federal CPF consultation."""

    cpf: str
    name_match: bool
    dob_match: bool
    status: str  # "regular" | "suspensa" | "cancelada" | "titular_falecido"
    deceased: bool
    raw: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# CPF Validator
# ---------------------------------------------------------------------------


class CPFValidator:
    """
    Implements the official Receita Federal CPF digit verification
    algorithm (mod 11) as specified in IN RFB 1.548/2015.

    The algorithm:
      1. Multiply the first 9 digits by weights 10..2, sum, multiply by 10,
         take mod 11; if result >= 10 the check digit is 0.
      2. Repeat with the first 10 digits weighted 11..2 for the second digit.
    """

    # Known all-same-digit CPFs that satisfy mod-11 but are not valid
    _KNOWN_INVALID: frozenset[str] = frozenset(str(d) * 11 for d in range(10))

    @classmethod
    def validate(cls, cpf: str) -> bool:
        """Return True if CPF passes the digit check; False otherwise."""
        digits = re.sub(r"\D", "", cpf)
        if len(digits) != 11:
            return False
        if digits in cls._KNOWN_INVALID:
            return False
        return cls._check_digit(digits, 10) and cls._check_digit(digits, 11)

    @classmethod
    def _check_digit(cls, digits: str, position: int) -> bool:
        total = sum(int(digits[i]) * (position - i) for i in range(position - 1))
        remainder = (total * 10) % 11
        remainder = 0 if remainder >= 10 else remainder
        return remainder == int(digits[position - 1])

    @classmethod
    def normalise(cls, cpf: str) -> str:
        """Strip all non-digit characters and return the bare 11-digit string."""
        return re.sub(r"\D", "", cpf)

    @classmethod
    def format(cls, cpf: str) -> str:
        """Return CPF formatted as NNN.NNN.NNN-DD."""
        d = cls.normalise(cpf)
        if len(d) != 11:
            raise ValueError(f"CPF must be 11 digits, got {len(d)}")
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"

    @classmethod
    def hash(cls, cpf: str) -> str:
        """Return SHA-256 hex digest of the normalised CPF.

        This is the value stored in the database — never the raw CPF.
        """
        normalised = cls.normalise(cpf)
        return hashlib.sha256(normalised.encode("ascii")).hexdigest()

    @classmethod
    def validate_or_raise(cls, cpf: str) -> str:
        """Validate and return normalised CPF, or raise CPFInvalidError."""
        normalised = cls.normalise(cpf)
        if not cls.validate(normalised):
            raise CPFInvalidError(
                f"CPF {normalised[:3]}.***.***-{normalised[9:]} failed digit check"
            )
        return normalised


# ---------------------------------------------------------------------------
# Receita Federal Client (async, mock stub)
# ---------------------------------------------------------------------------


class ReceitaFederalClient:
    """
    Async client for Receita Federal CPF consultation.

    Production wiring:
      - POST https://www.receita.fazenda.gov.br/...
      - Authenticated with mTLS using the operator's e-CNPJ certificate
      - Rate-limited per RFB fair-use policy
      - Response cached for 24 h to reduce RF load

    Stub behaviour (safe for tests / development):
      - CPF ending in '9999'  → deceased
      - CPF ending in '8888'  → status 'suspensa'
      - Any other valid CPF   → regular / name matches
    """

    _NETWORK_LATENCY_S: float = 0.05  # simulated latency

    async def consult(
        self,
        cpf: str,
        full_name: str,
        date_of_birth: str,
    ) -> ReceitaFederalResult:
        """Query Receita Federal for CPF status and identity match.

        Args:
            cpf:           bare 11-digit CPF (pre-validated)
            full_name:     player's full name as submitted
            date_of_birth: ISO-8601 date string YYYY-MM-DD

        Returns:
            ReceitaFederalResult with consultation outcome.
        """
        await asyncio.sleep(self._NETWORK_LATENCY_S)

        bare = CPFValidator.normalise(cpf)
        logger.info(
            "receita_federal_consult",
            cpf_hash=CPFValidator.hash(bare),
            dob=date_of_birth,
        )

        # Stub: simulate deceased CPF
        if bare.endswith("9999"):
            return ReceitaFederalResult(
                cpf=bare,
                name_match=False,
                dob_match=False,
                status="titular_falecido",
                deceased=True,
            )

        # Stub: simulate suspended CPF
        if bare.endswith("8888"):
            return ReceitaFederalResult(
                cpf=bare,
                name_match=False,
                dob_match=False,
                status="suspensa",
                deceased=False,
            )

        # Stub: happy path
        return ReceitaFederalResult(
            cpf=bare,
            name_match=True,
            dob_match=True,
            status="regular",
            deceased=False,
            raw={"name": full_name, "dob": date_of_birth, "source": "mock"},
        )

    async def consult_or_raise(
        self,
        cpf: str,
        full_name: str,
        date_of_birth: str,
    ) -> ReceitaFederalResult:
        """Like consult() but raises typed exceptions on adverse outcomes."""
        result = await self.consult(cpf, full_name, date_of_birth)

        if result.deceased:
            raise CPFDeceasedError("CPF belongs to a deceased individual")
        if result.status != "regular":
            raise CPFStatusError(f"CPF status is '{result.status}', expected 'regular'")
        if not result.name_match:
            raise CPFNameMismatchError(
                "Submitted name does not match Receita Federal records"
            )
        return result
