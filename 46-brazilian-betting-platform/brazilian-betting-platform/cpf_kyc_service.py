# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# REGULATORY REQUIREMENT: Brazil — Lei 14.790/2023 + LGPD + COAF AML
# Regulation:  Lei 14.790/2023 Art. 28 — mandatory KYC before account activation;
#              Portaria SPA/MF No. 722/2024 — CPF verification required; player
#              identity data sent to SIGAP; CNPJ e-certificate authentication;
#              LGPD Lei No. 13.709/2018 — CPF is sensitive personal data; collect
#              only for KYC (data minimisation); hash in SIGAP reports;
#              COAF Res. 36/2021 — KYC/CDD requirements; PEP screening;
#              BACEN CMN Res. 4.656/2018 — identity verification for fin. services;
#              Portaria SPA/MF 2.217/2025 and IN SPA/MF 22/2025 — SIGAP
#              impediment checks for Bolsa Família / BPC beneficiaries.
# 2026 Update: SIGAP Impediments API v2 also consolidates centralized
#              self-exclusion and additional statutory impediment reasons.
# Retention:   KYC documents: 5 years post-account closure (LGPD + COAF)
# Penalty:     Up to 20% of annual revenue for KYC failures;
#              LGPD: up to 2% of Brazilian revenue, max R$50M per infraction
# Jurisdictions: Brazil (SPA/MF, ANPD, COAF, Receita Federal/RFB)
#
# References:
#   Lei 14.790/2023: https://www.planalto.gov.br/ccivil_03/_ato2023-2026/2023/lei/L14790.htm
#   Portaria SPA/MF 722/2024: https://www.in.gov.br/web/dou/-/portaria-spa/mf-n-722
#   LGPD: https://www.planalto.gov.br/ccivil_03/_ato2015-2018/2018/lei/L13709.htm
#   COAF: https://www.gov.br/coaf/
# =============================================================================
"""
CPF Validation & KYC Pipeline -- Brazilian Betting Platform
===========================================================
Implements the full KYC pipeline required by Lei 14.790/2023 and
BACEN/SRF regulations for sports betting operators:

  - CPF number validation (Receita Federal digit check algorithm)
  - Receita Federal API integration (mock; wire to real API in prod)
  - Biometric / facial recognition flow
  - Official SIGAP Impediments API check by CPF
  - Account creation with full KYC gate
  - Periodic re-verification scheduler (every 15 days)
  - LGPD-compliant data handling (minimisation, consent, deletion)

Reference implementation for Chapter 46: Brazilian Betting Platform.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import aiohttp
import structlog
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class KYCError(Exception):
    """Base KYC exception."""


class CPFInvalidError(KYCError):
    """CPF number fails digit validation."""


class CPFBlacklistedError(KYCError):
    """CPF is known-fraud or on self-exclusion list."""


class BiometricMismatchError(KYCError):
    """Facial recognition confidence below threshold."""


class SelfExcludedError(KYCError):
    """Player is on national self-exclusion registry."""


class WelfareBeneficiaryError(KYCError):
    """Legacy name: SIGAP returned at least one betting impediment."""


class WelfareCheckError(KYCError):
    """The mandatory SIGAP impediment check could not be completed."""


class KYCExpiredError(KYCError):
    """KYC re-verification period has elapsed."""


class LGPDConsentError(KYCError):
    """Required LGPD consent not provided."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class KYCStatus(str, enum.Enum):
    PENDING = "pending"
    IDENTITY_VERIFIED = "identity_verified"
    BIOMETRIC_PENDING = "biometric_pending"
    BIOMETRIC_VERIFIED = "biometric_verified"
    EXCLUSION_CHECK_PENDING = "exclusion_check_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUSPENDED = "suspended"
    REVERIFICATION_REQUIRED = "reverification_required"


class GenderCode(str, enum.Enum):
    MALE = "M"
    FEMALE = "F"
    OTHER = "O"
    NOT_STATED = "N"


class DocumentType(str, enum.Enum):
    RG = "rg"
    CNH = "cnh"
    PASSPORT = "passport"
    RNE = "rne"


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class KYCRegistrationRequest(BaseModel):
    """Initial account creation payload."""

    cpf: str = Field(..., description="CPF in format NNN.NNN.NNN-DD or bare digits")
    full_name: str = Field(..., min_length=3, max_length=150)
    date_of_birth: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    email: str = Field(..., pattern=r"^[^@]+@[^@]+\.[^@]+$")
    phone_br: str = Field(..., pattern=r"^\+55\d{10,11}$")
    address_cep: str = Field(..., pattern=r"^\d{5}-?\d{3}$")
    address_street: str
    address_number: str
    address_city: str
    address_state: str = Field(..., min_length=2, max_length=2)
    document_type: DocumentType
    document_number: str
    gender: GenderCode = GenderCode.NOT_STATED
    lgpd_consent: bool = Field(..., description="Must be True to proceed")
    marketing_consent: bool = False

    @field_validator("cpf")
    @classmethod
    def normalise_cpf(cls, v: str) -> str:
        return re.sub(r"\D", "", v)

    @field_validator("lgpd_consent")
    @classmethod
    def require_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("LGPD consent is mandatory")
        return v


class BiometricSubmission(BaseModel):
    """Facial biometric verification payload."""

    player_id: str
    selfie_base64: str = Field(..., description="Base64-encoded JPEG selfie")
    document_front_base64: str
    liveness_token: Optional[str] = None


class ReVerificationRequest(BaseModel):
    player_id: str
    reason: str = "periodic_15_day"


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class KYCRecord:
    """Full KYC record for one player. PII fields are hashed for storage."""

    player_id: str
    cpf_hash: str                  # SHA-256 of raw CPF -- never store plaintext
    full_name: str
    date_of_birth: str
    email: str
    phone_hash: str
    address_cep: str
    address_state: str
    document_type: DocumentType
    document_number_hash: str
    gender: GenderCode
    kyc_status: KYCStatus
    lgpd_consent_at: datetime
    marketing_consent: bool
    created_at: datetime
    updated_at: datetime
    biometric_score: float = 0.0
    last_verified_at: Optional[datetime] = None
    next_verification_due: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_kyc_expired(self) -> bool:
        if self.next_verification_due is None:
            return False
        return datetime.now(timezone.utc) > self.next_verification_due


@dataclass
class ReceiraFederalResult:
    cpf: str
    name_match: bool
    dob_match: bool
    status: str           # "regular" | "suspensa" | "cancelada" | "titular_falecido"
    deceased: bool
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SelfExclusionResult:
    cpf_hash: str
    is_excluded: bool
    exclusion_type: Optional[str]    # "temporary" | "permanent"
    exclusion_end: Optional[datetime]
    source: str                      # "SIGAP" | "APOSTA_RESPONSAVEL" | "CNAE"


@dataclass
class WelfareCheckResult:
    cpf_hash: str
    resultado: str
    motivos: tuple[str, ...]
    request_id: str

    @property
    def restriction_active(self) -> bool:
        return self.resultado == "IMPEDIDO"


# ---------------------------------------------------------------------------
# CPF Validation
# ---------------------------------------------------------------------------


class CPFValidator:
    """
    Implements the official Receita Federal CPF digit verification
    algorithm (mod 11).
    """

    # Known all-same-digit CPFs that pass digit check but are invalid
    _KNOWN_INVALID = {str(d) * 11 for d in range(10)}

    @classmethod
    def validate(cls, cpf: str) -> bool:
        """Returns True if CPF passes digit check, False otherwise."""
        digits = re.sub(r"\D", "", cpf)
        if len(digits) != 11:
            return False
        if digits in cls._KNOWN_INVALID:
            return False
        return cls._check_digit(digits, 10) and cls._check_digit(digits, 11)

    @classmethod
    def _check_digit(cls, digits: str, position: int) -> bool:
        total = sum(
            int(digits[i]) * (position - i) for i in range(position - 1)
        )
        remainder = (total * 10) % 11
        remainder = remainder if remainder < 10 else 0
        return remainder == int(digits[position - 1])

    @classmethod
    def format(cls, cpf: str) -> str:
        """Returns CPF formatted as NNN.NNN.NNN-DD."""
        d = re.sub(r"\D", "", cpf)
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"

    @classmethod
    def hash(cls, cpf: str) -> str:
        """SHA-256 hash of normalised CPF for storage."""
        normalized = re.sub(r"\D", "", cpf)
        return hashlib.sha256(normalized.encode()).hexdigest()


# ---------------------------------------------------------------------------
# External API Stubs (wire real endpoints in production)
# ---------------------------------------------------------------------------


class ReceitaFederalClient:
    """
    Stub for Receita Federal CPF consultation API.
    In production: authenticated HTTPS call with e-CNPJ certificate.
    """

    async def consult(
        self,
        cpf: str,
        full_name: str,
        date_of_birth: str,
    ) -> ReceiraFederalResult:
        """
        Calls the RFB consultation endpoint and validates identity.
        Stub returns positive result; replace with real integration.
        """
        await asyncio.sleep(0.05)  # simulate network latency

        # Production: POST to https://www.receita.fazenda.gov.br/...
        # with mTLS client certificate (e-CNPJ of the operator)
        logger.info("receita_federal_consult", cpf_hash=CPFValidator.hash(cpf))

        # Stub logic: mark deceased for CPF ending in 9999
        bare = re.sub(r"\D", "", cpf)
        if bare.endswith("9999"):
            return ReceiraFederalResult(
                cpf=bare,
                name_match=False,
                dob_match=False,
                status="titular_falecido",
                deceased=True,
            )

        return ReceiraFederalResult(
            cpf=bare,
            name_match=True,
            dob_match=True,
            status="regular",
            deceased=False,
            raw={"name": full_name, "dob": date_of_birth},
        )


class BiometricVerificationClient:
    """
    Stub for biometric (facial recognition) verification.
    In production: wire to AWS Rekognition, FaceID, or local provider.
    """

    CONFIDENCE_THRESHOLD = 0.80

    async def verify(
        self,
        selfie_base64: str,
        document_front_base64: str,
        liveness_token: Optional[str],
    ) -> float:
        """
        Compares selfie to document face.
        Returns confidence score 0.0-1.0.
        """
        await asyncio.sleep(0.1)
        # Stub: return 0.92 unless liveness_token is "FAIL"
        if liveness_token == "FAIL":
            return 0.30
        return 0.92


class SelfExclusionRegistryClient:
    """
    Legacy operator-local exclusion cache.

    The authoritative centralized self-exclusion answer now comes from the
    unified SIGAP Impediments API below. Keep this client only for an internal,
    fail-closed cache or migration layer.
    """

    async def check(self, cpf_hash: str) -> SelfExclusionResult:
        await asyncio.sleep(0.05)
        # Stub: flag CPF hashes starting with "00"
        is_excluded = cpf_hash.startswith("00")
        return SelfExclusionResult(
            cpf_hash=cpf_hash,
            is_excluded=is_excluded,
            exclusion_type="permanent" if is_excluded else None,
            exclusion_end=None,
            source="SIGAP",
        )


class WelfareRegistryClient:
    """
    Checks the official SIGAP Impediments API v2 using a normalized CPF.

    It does not query CadÚnico directly and cannot determine whether a
    particular deposit came from benefit money. ``PROGRAMA_SOCIAL`` means the
    CPF is currently barred under the applicable rules.
    """

    PRODUCTION_URL = (
        "https://sigap-impedidos.fazenda.gov.br"
        "/impedimento/v2/condicao/{cpf}"
    )
    HOMOLOGATION_RESULTS = {
        "28784142090": ("PROGRAMA_SOCIAL",),
        "51077358008": ("AUTOEXCLUSAO_CENTRALIZADA",),
        "10996230572": ("AUTOEXCLUSAO_CENTRALIZADA", "PROGRAMA_SOCIAL"),
        "83941151878": ("PROGRAMA_NOVO_DESENROLA_BRASIL",),
        "15959816679": ("RENEGOCIACAO_FIES",),
        "55851894091": ("PROGRAMA_DESENROLA_ADIMPLENTES",),
        "99458738067": ("PROGRAMA_FIES_EMPREENDEDOR",),
    }

    def __init__(
        self,
        *,
        access_token: Optional[str] = None,
        endpoint: str = PRODUCTION_URL,
        mock: bool = False,
    ) -> None:
        self.access_token = access_token
        self.endpoint = endpoint
        self.mock = mock

    async def check(self, cpf: str) -> WelfareCheckResult:
        bare = re.sub(r"\D", "", cpf)
        if len(bare) != 11:
            raise ValueError("SIGAP requires a normalized 11-digit CPF")
        cpf_hash = CPFValidator.hash(bare)

        if self.mock:
            motivos = self.HOMOLOGATION_RESULTS.get(bare, ())
            return WelfareCheckResult(
                cpf_hash=cpf_hash,
                resultado="IMPEDIDO" if motivos else "NAO_IMPEDIDO",
                motivos=motivos,
                request_id=f"mock-{uuid.uuid4()}",
            )

        if not self.access_token:
            raise WelfareCheckError(
                "SIGAP token unavailable; betting must remain disabled"
            )

        headers = {"Authorization": f"Bearer {self.access_token}"}
        timeout = aiohttp.ClientTimeout(total=5)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    self.endpoint.format(cpf=bare), headers=headers
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise WelfareCheckError(
                "SIGAP check unavailable; betting must remain disabled"
            ) from exc

        resultado = payload.get("resultado")
        motivos = tuple(payload.get("motivos") or ())
        request_id = payload.get("idRequisicao")
        if (
            resultado not in {"IMPEDIDO", "NAO_IMPEDIDO"}
            or not request_id
            or (resultado == "IMPEDIDO" and not motivos)
        ):
            raise WelfareCheckError("Invalid response from SIGAP")

        logger.info(
            "sigap_impediment_check",
            cpf_hash=cpf_hash,
            resultado=resultado,
            motivos=motivos,
            request_id=request_id,
        )
        return WelfareCheckResult(
            cpf_hash=cpf_hash,
            resultado=resultado,
            motivos=motivos,
            request_id=request_id,
        )


# ---------------------------------------------------------------------------
# KYC Record Store (in-memory stub -- use PostgreSQL in production)
# ---------------------------------------------------------------------------


class KYCStore:
    def __init__(self) -> None:
        self._records: Dict[str, KYCRecord] = {}
        self._cpf_index: Dict[str, str] = {}  # cpf_hash -> player_id
        self._lock = asyncio.Lock()

    async def save(self, record: KYCRecord) -> None:
        async with self._lock:
            self._records[record.player_id] = record
            self._cpf_index[record.cpf_hash] = record.player_id

    async def get_by_player(self, player_id: str) -> Optional[KYCRecord]:
        return self._records.get(player_id)

    async def get_by_cpf_hash(self, cpf_hash: str) -> Optional[KYCRecord]:
        pid = self._cpf_index.get(cpf_hash)
        return self._records.get(pid) if pid else None

    async def list_due_reverification(self) -> List[KYCRecord]:
        now = datetime.now(timezone.utc)
        return [
            r for r in self._records.values()
            if r.next_verification_due and r.next_verification_due <= now
            and r.kyc_status == KYCStatus.APPROVED
        ]


# ---------------------------------------------------------------------------
# KYC Pipeline Orchestrator
# ---------------------------------------------------------------------------


class KYCPipeline:
    """
    Orchestrates the full KYC flow from CPF validation through
    biometric verification, self-exclusion check, and account approval.
    """

    RE_VERIFICATION_DAYS = 15
    BIOMETRIC_THRESHOLD = 0.80

    def __init__(
        self,
        store: KYCStore,
        rf_client: ReceitaFederalClient,
        biometric_client: BiometricVerificationClient,
        exclusion_client: SelfExclusionRegistryClient,
        welfare_client: WelfareRegistryClient,
    ) -> None:
        self.store = store
        self.rf = rf_client
        self.biometric = biometric_client
        self.exclusion = exclusion_client
        self.welfare = welfare_client

    async def register(self, req: KYCRegistrationRequest) -> KYCRecord:
        """
        Step 1: Validate CPF, check uniqueness, verify identity
        against Receita Federal, then queue biometric step.
        """
        bare_cpf = re.sub(r"\D", "", req.cpf)

        # 1a. Algorithmic CPF check
        if not CPFValidator.validate(bare_cpf):
            raise CPFInvalidError(f"CPF {bare_cpf[:3]}...{bare_cpf[9:]} failed digit check")

        cpf_hash = CPFValidator.hash(bare_cpf)

        # 1b. Duplicate account check
        existing = await self.store.get_by_cpf_hash(cpf_hash)
        if existing:
            raise KYCError(f"Account already exists for this CPF (player {existing.player_id})")

        # 1c. Receita Federal identity verification
        rf_result = await self.rf.consult(bare_cpf, req.full_name, req.date_of_birth)
        if rf_result.deceased:
            raise CPFInvalidError("CPF belongs to a deceased individual")
        if rf_result.status != "regular":
            raise CPFInvalidError(f"CPF status: {rf_result.status}")
        if not rf_result.name_match:
            raise CPFInvalidError("Name does not match Receita Federal records")

        # 1d. Age check (must be 18+)
        self._assert_minimum_age(req.date_of_birth)

        # 1e. Official SIGAP check at onboarding. The CPF is used transiently;
        # only its hash and the minimum audit evidence are retained.
        impediment = await self.welfare.check(bare_cpf)
        if impediment.restriction_active:
            raise WelfareBeneficiaryError(
                f"SIGAP betting impediment: {', '.join(impediment.motivos)}"
            )

        player_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        record = KYCRecord(
            player_id=player_id,
            cpf_hash=cpf_hash,
            full_name=req.full_name,
            date_of_birth=req.date_of_birth,
            email=req.email,
            phone_hash=hashlib.sha256(req.phone_br.encode()).hexdigest(),
            address_cep=req.address_cep,
            address_state=req.address_state,
            document_type=req.document_type,
            document_number_hash=hashlib.sha256(
                req.document_number.encode()
            ).hexdigest(),
            gender=req.gender,
            kyc_status=KYCStatus.IDENTITY_VERIFIED,
            lgpd_consent_at=now,
            marketing_consent=req.marketing_consent,
            created_at=now,
            updated_at=now,
            metadata={
                "sigap_onboarding": {
                    "resultado": impediment.resultado,
                    "motivos": list(impediment.motivos),
                    "request_id": impediment.request_id,
                    "timestamp": now.isoformat(),
                }
            },
        )
        record.audit_trail.append(
            {
                "event": "registration",
                "rf_status": rf_result.status,
                "name_match": rf_result.name_match,
                "timestamp": now.isoformat(),
            }
        )
        await self.store.save(record)

        logger.info(
            "kyc_registration_complete",
            player_id=player_id,
            kyc_status=record.kyc_status.value,
        )
        return record

    async def submit_biometric(self, submission: BiometricSubmission) -> KYCRecord:
        """
        Step 2: Facial recognition + liveness detection.
        Updates record to BIOMETRIC_VERIFIED or REJECTED.
        """
        record = await self._get_or_404(submission.player_id)

        if record.kyc_status not in (
            KYCStatus.IDENTITY_VERIFIED,
            KYCStatus.BIOMETRIC_PENDING,
        ):
            raise KYCError(
                f"Cannot submit biometric in state {record.kyc_status.value}"
            )

        record.kyc_status = KYCStatus.BIOMETRIC_PENDING
        await self.store.save(record)

        score = await self.biometric.verify(
            submission.selfie_base64,
            submission.document_front_base64,
            submission.liveness_token,
        )
        record.biometric_score = score
        now = datetime.now(timezone.utc)

        if score < self.BIOMETRIC_THRESHOLD:
            record.kyc_status = KYCStatus.REJECTED
            record.rejection_reason = (
                f"Biometric confidence {score:.2f} below threshold {self.BIOMETRIC_THRESHOLD}"
            )
            record.audit_trail.append(
                {
                    "event": "biometric_rejected",
                    "score": score,
                    "timestamp": now.isoformat(),
                }
            )
            await self.store.save(record)
            raise BiometricMismatchError(record.rejection_reason)

        record.kyc_status = KYCStatus.BIOMETRIC_VERIFIED
        record.updated_at = now
        record.audit_trail.append(
            {
                "event": "biometric_verified",
                "score": score,
                "timestamp": now.isoformat(),
            }
        )
        await self.store.save(record)

        # Immediately proceed to exclusion checks
        return await self._run_exclusion_checks(record)

    async def _run_exclusion_checks(self, record: KYCRecord) -> KYCRecord:
        """
        Step 3: operator-local exclusion-cache check.

        The authoritative SIGAP impediment check was already completed with
        the raw CPF during onboarding; a stored hash must never be sent to the
        official endpoint.
        If clear, approve and set re-verification schedule.
        """
        record.kyc_status = KYCStatus.EXCLUSION_CHECK_PENDING
        await self.store.save(record)

        exclusion = await self.exclusion.check(record.cpf_hash)

        now = datetime.now(timezone.utc)

        if exclusion.is_excluded:
            record.kyc_status = KYCStatus.REJECTED
            record.rejection_reason = (
                f"Player is on national self-exclusion registry ({exclusion.source})"
            )
            record.audit_trail.append(
                {
                    "event": "rejected_self_exclusion",
                    "source": exclusion.source,
                    "type": exclusion.exclusion_type,
                    "timestamp": now.isoformat(),
                }
            )
            await self.store.save(record)
            raise SelfExcludedError(record.rejection_reason)

        # All clear -- approve
        record.kyc_status = KYCStatus.APPROVED
        record.last_verified_at = now
        record.next_verification_due = now + timedelta(days=self.RE_VERIFICATION_DAYS)
        record.updated_at = now
        record.audit_trail.append(
            {
                "event": "kyc_approved",
                "next_verification": record.next_verification_due.isoformat(),
                "timestamp": now.isoformat(),
            }
        )
        await self.store.save(record)

        logger.info(
            "kyc_approved",
            player_id=record.player_id,
            next_verification=record.next_verification_due.isoformat(),
        )
        return record

    async def trigger_reverification(
        self, req: ReVerificationRequest
    ) -> KYCRecord:
        """
        Resets approved player to REVERIFICATION_REQUIRED.
        Called by scheduler every 15 days or manually by compliance.
        """
        record = await self._get_or_404(req.player_id)
        if record.kyc_status != KYCStatus.APPROVED:
            raise KYCError(
                f"Re-verification only valid for APPROVED accounts, "
                f"current state: {record.kyc_status.value}"
            )

        record.kyc_status = KYCStatus.REVERIFICATION_REQUIRED
        record.updated_at = datetime.now(timezone.utc)
        record.audit_trail.append(
            {
                "event": "reverification_triggered",
                "reason": req.reason,
                "timestamp": record.updated_at.isoformat(),
            }
        )
        await self.store.save(record)
        logger.info(
            "kyc_reverification_triggered",
            player_id=req.player_id,
            reason=req.reason,
        )
        return record

    async def process_lgpd_deletion(self, player_id: str) -> Dict[str, Any]:
        """
        LGPD Art. 18 right to erasure.
        Anonymises PII fields; retains audit trail for regulatory compliance
        (Lei 14.790/2023 requires 5-year record retention).
        """
        record = await self._get_or_404(player_id)
        now = datetime.now(timezone.utc)
        anonymized = {
            "full_name": "[DELETED]",
            "email": "[DELETED]",
            "phone_hash": "[DELETED]",
            "address_street": "[DELETED]",
            "document_number_hash": "[DELETED]",
        }
        record.full_name = anonymized["full_name"]
        record.email = anonymized["email"]
        record.phone_hash = anonymized["phone_hash"]
        record.document_number_hash = anonymized["document_number_hash"]
        record.kyc_status = KYCStatus.SUSPENDED
        record.updated_at = now
        record.audit_trail.append(
            {
                "event": "lgpd_erasure",
                "requested_at": now.isoformat(),
                "retained_fields": ["cpf_hash", "date_of_birth", "audit_trail"],
            }
        )
        await self.store.save(record)
        logger.info("lgpd_erasure_completed", player_id=player_id)
        return {"player_id": player_id, "status": "anonymized", "timestamp": now.isoformat()}

    def _assert_minimum_age(self, date_of_birth: str) -> None:
        dob = datetime.strptime(date_of_birth, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dob).days
        if age_days < 18 * 365:
            raise KYCError("Player must be at least 18 years old")

    async def _get_or_404(self, player_id: str) -> KYCRecord:
        record = await self.store.get_by_player(player_id)
        if not record:
            raise KYCError(f"Player {player_id} not found")
        return record


# ---------------------------------------------------------------------------
# Re-verification Background Scheduler
# ---------------------------------------------------------------------------


class ReVerificationScheduler:
    """
    Polls the KYC store every hour and flags records whose 15-day
    re-verification window has elapsed.
    """

    def __init__(self, store: KYCStore, pipeline: KYCPipeline) -> None:
        self.store = store
        self.pipeline = pipeline
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        logger.info("reverification_scheduler_started", interval_seconds=3600)

    async def _run(self) -> None:
        while True:
            try:
                await self._check_due()
            except Exception as exc:
                logger.error("reverification_scheduler_error", error=str(exc))
            await asyncio.sleep(3600)

    async def _check_due(self) -> None:
        due = await self.store.list_due_reverification()
        logger.info("reverification_check", due_count=len(due))
        for record in due:
            try:
                await self.pipeline.trigger_reverification(
                    ReVerificationRequest(
                        player_id=record.player_id, reason="periodic_15_day"
                    )
                )
            except Exception as exc:
                logger.error(
                    "reverification_trigger_error",
                    player_id=record.player_id,
                    error=str(exc),
                )

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

kyc_store = KYCStore()
pipeline: Optional[KYCPipeline] = None
scheduler: Optional[ReVerificationScheduler] = None


from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pipeline, scheduler
    pipeline = KYCPipeline(
        store=kyc_store,
        rf_client=ReceitaFederalClient(),
        biometric_client=BiometricVerificationClient(),
        exclusion_client=SelfExclusionRegistryClient(),
        welfare_client=WelfareRegistryClient(
            access_token=os.getenv("SIGAP_ACCESS_TOKEN"),
            mock=os.getenv("SIGAP_MOCK", "false").lower() == "true",
        ),
    )
    scheduler = ReVerificationScheduler(kyc_store, pipeline)
    scheduler.start()
    logger.info("kyc_service_started")
    yield
    scheduler.stop()
    logger.info("kyc_service_shutdown")


app = FastAPI(
    title="CPF KYC Service",
    description="KYC pipeline for Brazilian betting platform",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,  # ty: ignore[invalid-argument-type]
    allow_origins=["https://apostas.acmetocasino.bet.br"],
    allow_methods=["POST", "GET", "DELETE"],
    allow_headers=["*"],
)


@app.post("/v1/kyc/register", response_model=Dict[str, Any])
async def register(req: KYCRegistrationRequest) -> Dict[str, Any]:
    """Step 1: Register new player with CPF and identity verification."""
    record = await pipeline.register(req)  # type: ignore[union-attr]
    return {
        "player_id": record.player_id,
        "kyc_status": record.kyc_status.value,
        "message": "Identity verified. Submit biometric to complete KYC.",
    }


@app.post("/v1/kyc/biometric", response_model=Dict[str, Any])
async def submit_biometric(submission: BiometricSubmission) -> Dict[str, Any]:
    """Step 2: Submit facial biometric for verification."""
    record = await pipeline.submit_biometric(submission)  # type: ignore[union-attr]
    return {
        "player_id": record.player_id,
        "kyc_status": record.kyc_status.value,
        "biometric_score": record.biometric_score,
        "next_verification_due": (
            record.next_verification_due.isoformat()
            if record.next_verification_due
            else None
        ),
    }


@app.get("/v1/kyc/players/{player_id}", response_model=Dict[str, Any])
async def get_player_kyc(player_id: str) -> Dict[str, Any]:
    """Retrieve KYC status for a player."""
    record = await kyc_store.get_by_player(player_id)
    if not record:
        raise HTTPException(status_code=404, detail="Player not found")
    return {
        "player_id": record.player_id,
        "kyc_status": record.kyc_status.value,
        "biometric_score": record.biometric_score,
        "last_verified_at": (
            record.last_verified_at.isoformat() if record.last_verified_at else None
        ),
        "next_verification_due": (
            record.next_verification_due.isoformat()
            if record.next_verification_due
            else None
        ),
        "is_expired": record.is_kyc_expired,
        "rejection_reason": record.rejection_reason,
    }


@app.delete("/v1/kyc/players/{player_id}", response_model=Dict[str, Any])
async def lgpd_erasure(player_id: str) -> Dict[str, Any]:
    """LGPD right to erasure -- anonymise player PII."""
    return await pipeline.process_lgpd_deletion(player_id)  # type: ignore[union-attr]


@app.post("/v1/kyc/reverify", response_model=Dict[str, Any])
async def trigger_reverification(req: ReVerificationRequest) -> Dict[str, Any]:
    """Manually trigger KYC re-verification for a player."""
    record = await pipeline.trigger_reverification(req)  # type: ignore[union-attr]
    return {
        "player_id": record.player_id,
        "kyc_status": record.kyc_status.value,
        "message": "Re-verification triggered. Player must re-submit biometric.",
    }


@app.get("/healthz")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "cpf-kyc-service"}


if __name__ == "__main__":
    uvicorn.run("cpf_kyc_service:app", host="0.0.0.0", port=8002, reload=False)
