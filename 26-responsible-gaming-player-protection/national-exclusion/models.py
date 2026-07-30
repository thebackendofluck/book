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
models.py — Domain models for national self-exclusion service.

Covers four registries:
  - GamStop (UK / UKGC)
  - Spelpaus (Sweden / SGA)
  - ROFUS (Denmark / Spillemyndigheden)
  - Brazil National (SEAE / Ministry of Finance)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Registry enumeration
# ---------------------------------------------------------------------------

class Registry(str, Enum):
    GAMSTOP = "gamstop"
    SPELPAUS = "spelpaus"
    ROFUS = "rofus"
    BRAZIL_NATIONAL = "brazil_national"


class Jurisdiction(str, Enum):
    GB = "GB"   # United Kingdom
    SE = "SE"   # Sweden
    DK = "DK"   # Denmark
    BR = "BR"   # Brazil


# ---------------------------------------------------------------------------
# Per-registry user models
# ---------------------------------------------------------------------------

@dataclass
class GamstopUser:
    """UK player record sent to GamStop batch endpoint."""
    id:         int
    first_name: str
    last_name:  str
    dob:        str          # ISO date string "YYYY-MM-DD"
    email:      Optional[str]
    postcode:   str
    mobile:     Optional[str]


@dataclass
class SpelpausUser:
    """Swedish player record — ID is MD5-hashed before transmission."""
    id:  int
    ssn: str    # Personnummer format: YYMMDD-NNNN
    dob: date


@dataclass
class RofusUser:
    """Danish player record for ROFUS CPR check."""
    id:  int
    cpr: str    # Danish CPR number: DDMMYY-NNNN


@dataclass
class BrazilUser:
    """Brazilian player record for national self-exclusion registry."""
    id:  int
    cpf: str    # Brazilian CPF: 000.000.000-00


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

@dataclass
class ExclusionCheck:
    """Input for an ad-hoc exclusion check via the HTTP API."""
    player_id:    str
    jurisdiction: Jurisdiction
    registry:     Registry


@dataclass
class ExclusionStatus:
    """Result of an exclusion check for a single player."""
    player_id:         str
    registry:          Registry
    is_excluded:       bool
    checked_at:        datetime
    exclusion_period:  Optional[str] = None   # e.g. "6 months", "permanent"
    raw_response:      Optional[dict] = None


@dataclass
class RegistrationRequest:
    """Player self-exclusion registration (operator-initiated)."""
    player_id:    str
    jurisdiction: Jurisdiction
    duration:     Optional[str] = None   # "6_months", "1_year", "5_years", "permanent"
    reason:       Optional[str] = None


@dataclass
class RevocationRequest:
    """Remove a player from a national exclusion registry (where permitted)."""
    player_id:    str
    jurisdiction: Jurisdiction
    reason:       Optional[str] = None


@dataclass
class RegistryStatusReport:
    """Health / summary of a registry endpoint."""
    registry:   Registry
    healthy:    bool
    latency_ms: Optional[float]
    error:      Optional[str] = None
    checked_at: Optional[datetime] = None


@dataclass
class ProcessorResult:
    """Internal result produced by a batch processor."""
    registry_name:  str
    users_checked:  int
    users_excluded: int
    errors:         list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------

@dataclass
class GamstopApiConfig:
    batch_service_url:        str
    api_key:                  str
    batch_size:               int = 1_000
    response_timeout_seconds: int = 30


@dataclass
class SpelpausApiConfig:
    batch_service_url:        str
    api_key:                  str
    actor_id:                 str
    batch_size:               int = 10_000
    response_timeout_seconds: int = 30


@dataclass
class RofusApiConfig:
    base_url:                 str
    api_key:                  str
    operator_id:              str
    response_timeout_seconds: int = 30


@dataclass
class BrazilApiConfig:
    base_url:                 str
    api_key:                  str
    response_timeout_seconds: int = 30
