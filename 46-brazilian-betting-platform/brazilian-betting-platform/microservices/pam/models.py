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
PAM Service — Pydantic v2 Models
=================================
Request/response schemas and domain models for the Player Account
Management microservice. CPF values are stored as SHA-256 hashes. A raw,
normalized CPF is used transiently only for mandatory official consultations.

Lei 14.790/2023 / LGPD (Lei 13.709/2018) compliant data shapes.
"""

from __future__ import annotations

import enum
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PlayerStatus(str, enum.Enum):
    PENDING = "pending"
    IDENTITY_VERIFIED = "identity_verified"
    BIOMETRIC_PENDING = "biometric_pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    BLOCKED = "blocked"
    REVERIFICATION_REQUIRED = "reverification_required"
    DELETED = "deleted"


class DocumentType(str, enum.Enum):
    RG = "rg"
    CNH = "cnh"
    PASSPORT = "passport"
    RNE = "rne"


class GenderCode(str, enum.Enum):
    MALE = "M"
    FEMALE = "F"
    OTHER = "O"
    NOT_STATED = "N"


class StatusAction(str, enum.Enum):
    ACTIVATE = "activate"
    SUSPEND = "suspend"
    BLOCK = "block"


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class PlayerRegisterRequest(BaseModel):
    """Payload for POST /players/register."""

    cpf: str = Field(..., description="CPF — NNN.NNN.NNN-DD or bare 11 digits")
    full_name: str = Field(..., min_length=3, max_length=150)
    date_of_birth: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    phone_br: str = Field(..., pattern=r"^\+55\d{10,11}$")
    address_cep: str = Field(..., pattern=r"^\d{5}-?\d{3}$")
    address_street: str = Field(..., min_length=2, max_length=200)
    address_number: str = Field(..., min_length=1, max_length=20)
    address_city: str = Field(..., min_length=2, max_length=100)
    address_state: str = Field(..., min_length=2, max_length=2)
    document_type: DocumentType
    document_number: str = Field(..., min_length=5, max_length=30)
    gender: GenderCode = GenderCode.NOT_STATED
    lgpd_consent: bool = Field(..., description="Must be True — mandatory per LGPD")
    marketing_consent: bool = False

    @field_validator("cpf")
    @classmethod
    def normalise_cpf(cls, v: str) -> str:
        return re.sub(r"\D", "", v)

    @field_validator("lgpd_consent")
    @classmethod
    def require_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("LGPD consent is mandatory to proceed")
        return v

    @field_validator("address_state")
    @classmethod
    def uppercase_state(cls, v: str) -> str:
        return v.upper()


class BiometricVerifyRequest(BaseModel):
    """Payload for POST /players/{cpf}/verify-biometric."""

    selfie_base64: str = Field(..., description="Base64-encoded JPEG selfie")
    document_front_base64: str = Field(..., description="Base64-encoded document front")
    liveness_token: Optional[str] = None


class StatusUpdateRequest(BaseModel):
    """Payload for PUT /players/{cpf}/status."""

    action: StatusAction
    reason: str = Field(..., min_length=5, max_length=500)
    operator_id: str = Field(..., description="Internal operator who made the change")


class ReverifyRequest(BaseModel):
    """Payload for POST /players/{cpf}/reverify."""

    reason: str = "periodic_15_day"
    triggered_by: str = "system"


# ---------------------------------------------------------------------------
# Response / Domain Models
# ---------------------------------------------------------------------------


class CPFValidation(BaseModel):
    """Result of a Receita Federal CPF consultation."""

    cpf_hash: str
    name_match: bool
    dob_match: bool
    status: str  # "regular" | "suspensa" | "cancelada" | "titular_falecido"
    deceased: bool
    raw: Dict[str, Any] = Field(default_factory=dict)


class BiometricResult(BaseModel):
    """Result of a biometric verification attempt."""

    confidence_score: float
    passed: bool
    provider: str
    checked_at: datetime


class WelfareStatus(BaseModel):
    """Minimal result of a SIGAP Impediments API v2 consultation."""

    cpf_hash: str
    resultado: str
    motivos: List[str] = Field(default_factory=list)
    request_id: str
    restriction_active: bool
    checked_at: datetime


class SessionInfo(BaseModel):
    """Active session descriptor."""

    session_id: str
    started_at: datetime
    last_seen_at: datetime
    ip_address: Optional[str] = None
    device_fingerprint: Optional[str] = None


class PlayerProfile(BaseModel):
    """Public player profile — no raw PII."""

    player_id: str
    cpf_hash: str
    full_name: str
    email: str
    address_state: str
    address_cep: str
    status: PlayerStatus
    created_at: datetime
    last_verified_at: Optional[datetime] = None
    next_verification_due: Optional[datetime] = None
    biometric_score: float = 0.0
    rejection_reason: Optional[str] = None
    audit_trail: List[Dict[str, Any]] = Field(default_factory=list)


class PlayerRegisterResponse(BaseModel):
    player_id: str
    status: PlayerStatus
    message: str


class BiometricVerifyResponse(BaseModel):
    player_id: str
    status: PlayerStatus
    biometric_score: float
    next_verification_due: Optional[datetime] = None


class StatusUpdateResponse(BaseModel):
    player_id: str
    previous_status: PlayerStatus
    current_status: PlayerStatus
    updated_at: datetime


class WelfareCheckResponse(BaseModel):
    player_id: str
    welfare_status: WelfareStatus
    access_permitted: bool


class ReverifyResponse(BaseModel):
    player_id: str
    status: PlayerStatus
    message: str


class LGPDErasureResponse(BaseModel):
    player_id: str
    status: str
    anonymized_at: datetime
    retained_for_compliance: List[str]
