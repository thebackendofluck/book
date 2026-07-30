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
PIX Payment Gateway Service -- Brazilian Betting Platform
=========================================================
Complete PIX payment integration service implementing:
  - PIX QR Code generation (static and dynamic)
  - Payment confirmation webhook handler
  - Withdrawal / payout processing
  - Reconciliation engine
  - PSP abstraction layer (Celcoin, Asaas, Transfeera)
  - Payment state machine (pending → confirmed → settled / failed → refunded)
  - Rate limiting and fraud checks
  - Structured logging and full audit trail

Reference implementation for Chapter 46: Brazilian Betting Platform.

Compliance: BACEN Resolution 1/2020, Lei 14.790/2023, LGPD.
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import structlog
import uvicorn
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from tenacity import (
    AsyncRetrying,
    RetryError,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Custom Exceptions
# ---------------------------------------------------------------------------


class PixGatewayError(Exception):
    """Base exception for all PIX gateway errors."""


class PSPConnectionError(PixGatewayError):
    """Raised when PSP is unreachable."""


class InvalidPixKeyError(PixGatewayError):
    """Raised when a PIX key fails validation."""


class DuplicateTransactionError(PixGatewayError):
    """Raised on idempotency collision."""


class FraudCheckFailedError(PixGatewayError):
    """Raised when fraud score exceeds threshold."""


class InsufficientFundsError(PixGatewayError):
    """Raised for payout with insufficient operator balance."""


class ReconciliationError(PixGatewayError):
    """Raised when PSP totals differ from internal ledger."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PaymentState(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    CONFIRMED = "confirmed"
    SETTLED = "settled"
    FAILED = "failed"
    REFUNDED = "refunded"
    EXPIRED = "expired"


class PixKeyType(str, enum.Enum):
    CPF = "cpf"
    CNPJ = "cnpj"
    EMAIL = "email"
    PHONE = "phone"
    RANDOM = "random"


class PSPProvider(str, enum.Enum):
    CELCOIN = "celcoin"
    ASAAS = "asaas"
    TRANSFEERA = "transfeera"


class QRCodeType(str, enum.Enum):
    STATIC = "static"
    DYNAMIC = "dynamic"


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------


class PixDepositRequest(BaseModel):
    """Initiates a PIX deposit (player deposits funds)."""

    player_id: str = Field(..., min_length=1, max_length=64)
    amount_brl: float = Field(..., gt=0, le=500_000, description="Amount in BRL centavos / 100")
    description: str = Field(default="Depósito BetBR", max_length=140)
    expiration_seconds: int = Field(default=3600, ge=60, le=86400)
    metadata: Optional[Dict[str, Any]] = None

    @field_validator("amount_brl")
    @classmethod
    def round_to_cents(cls, v: float) -> float:
        return round(v, 2)


class PixWithdrawalRequest(BaseModel):
    """Initiates a PIX payout to a player."""

    player_id: str = Field(..., min_length=1, max_length=64)
    amount_brl: float = Field(..., gt=0, le=100_000)
    pix_key: str = Field(..., min_length=1, max_length=77)
    pix_key_type: PixKeyType
    recipient_name: str = Field(..., min_length=2, max_length=120)
    recipient_cpf_cnpj: str = Field(..., min_length=11, max_length=14)
    description: str = Field(default="Saque BetBR", max_length=140)

    @field_validator("amount_brl")
    @classmethod
    def round_to_cents(cls, v: float) -> float:
        return round(v, 2)


class WebhookPayload(BaseModel):
    """Incoming PIX notification from PSP."""

    event_type: str
    transaction_id: str
    e2e_id: Optional[str] = None
    amount_brl: float
    payer_name: Optional[str] = None
    payer_document: Optional[str] = None
    timestamp: datetime
    end_to_end_id: Optional[str] = None
    status: str
    additional_info: Optional[Dict[str, Any]] = None


class ReconciliationResult(BaseModel):
    """Result of a PSP reconciliation run."""

    run_id: str
    period_start: datetime
    period_end: datetime
    psp_provider: PSPProvider
    psp_total_brl: float
    internal_total_brl: float
    matched_count: int
    unmatched_psp: List[str]
    unmatched_internal: List[str]
    discrepancy_brl: float
    status: str  # "balanced" | "discrepancy" | "error"


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------


@dataclass
class PaymentRecord:
    """Internal ledger record for a single PIX payment."""

    payment_id: str
    player_id: str
    amount_brl: float
    direction: str  # "deposit" | "withdrawal"
    state: PaymentState
    psp_provider: PSPProvider
    e2e_id: Optional[str]
    qr_code: Optional[str]
    qr_code_type: Optional[QRCodeType]
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]
    settled_at: Optional[datetime]
    audit_trail: List[Dict[str, Any]] = field(default_factory=list)
    fraud_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PSPCredentials:
    provider: PSPProvider
    api_key: str
    api_secret: str
    base_url: str
    webhook_secret: str
    timeout_seconds: int = 30
    max_retries: int = 3


# ---------------------------------------------------------------------------
# PSP Abstraction Layer
# ---------------------------------------------------------------------------


class BasePSPAdapter:
    """Abstract base for PSP adapters."""

    provider: PSPProvider

    async def generate_qr_code(
        self,
        payment_id: str,
        amount_brl: float,
        description: str,
        expiration_seconds: int,
        qr_type: QRCodeType,
    ) -> Tuple[str, str]:
        """Returns (qr_code_string, e2e_id)."""
        raise NotImplementedError

    async def process_payout(
        self,
        payment_id: str,
        amount_brl: float,
        pix_key: str,
        pix_key_type: PixKeyType,
        recipient_name: str,
        recipient_document: str,
        description: str,
    ) -> Tuple[str, str]:
        """Returns (e2e_id, status)."""
        raise NotImplementedError

    async def query_payment_status(self, e2e_id: str) -> Dict[str, Any]:
        raise NotImplementedError

    async def list_transactions(
        self, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        raise NotImplementedError


class CelcoinAdapter(BasePSPAdapter):
    """Celcoin PIX adapter -- reference: https://developers.celcoin.com.br/"""

    provider = PSPProvider.CELCOIN

    def __init__(self, creds: PSPCredentials) -> None:
        self.creds = creds
        self._session: Optional[aiohttp.ClientSession] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=self.creds.base_url,
                timeout=aiohttp.ClientTimeout(total=self.creds.timeout_seconds),
                headers={"Content-Type": "application/json"},
            )
        return self._session

    async def _ensure_token(self) -> str:
        """OAuth2 client credentials flow for Celcoin."""
        if time.time() < self._token_expires_at - 60:
            return self._access_token  # type: ignore[return-value]

        session = await self._get_session()
        async with session.post(
            "/v5/token",
            data={
                "client_id": self.creds.api_key,
                "client_secret": self.creds.api_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 3600)
            return self._access_token

    async def generate_qr_code(
        self,
        payment_id: str,
        amount_brl: float,
        description: str,
        expiration_seconds: int,
        qr_type: QRCodeType,
    ) -> Tuple[str, str]:
        token = await self._ensure_token()
        session = await self._get_session()
        payload = {
            "clientCode": payment_id,
            "paymentType": "STATIC" if qr_type == QRCodeType.STATIC else "DYNAMIC",
            "amount": amount_brl,
            "key": self.creds.api_key,  # operator PIX key
            "message": description,
            "expiracao": expiration_seconds,
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.creds.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                async with session.post(
                    "/pix/v1/cobv",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status >= 500:
                        raise PSPConnectionError(f"Celcoin 5xx: {resp.status}")
                    resp.raise_for_status()
                    data = await resp.json()
                    return data["payload"], data["txid"]

        raise PSPConnectionError("Celcoin QR generation exhausted retries")

    async def process_payout(
        self,
        payment_id: str,
        amount_brl: float,
        pix_key: str,
        pix_key_type: PixKeyType,
        recipient_name: str,
        recipient_document: str,
        description: str,
    ) -> Tuple[str, str]:
        token = await self._ensure_token()
        session = await self._get_session()
        payload = {
            "clientCode": payment_id,
            "amount": amount_brl,
            "pixKey": pix_key,
            "pixKeyType": pix_key_type.value.upper(),
            "name": recipient_name,
            "document": recipient_document,
            "description": description,
            "message": description,
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.creds.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                async with session.post(
                    "/pix/v1/payment",
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status >= 500:
                        raise PSPConnectionError(f"Celcoin 5xx payout: {resp.status}")
                    resp.raise_for_status()
                    data = await resp.json()
                    return data["transactionId"], data["status"]

        raise PSPConnectionError("Celcoin payout exhausted retries")

    async def query_payment_status(self, e2e_id: str) -> Dict[str, Any]:
        token = await self._ensure_token()
        session = await self._get_session()
        async with session.get(
            f"/pix/v1/cobv/{e2e_id}",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def list_transactions(
        self, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        token = await self._ensure_token()
        session = await self._get_session()
        params = {
            "inicio": start.isoformat(),
            "fim": end.isoformat(),
            "paginaAtual": 0,
            "itensPorPagina": 1000,
        }
        async with session.get(
            "/pix/v1/cobv",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("cobs", [])

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        expected = hmac.new(
            self.creds.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


class AsaasAdapter(BasePSPAdapter):
    """Asaas PIX adapter -- reference: https://docs.asaas.com/"""

    provider = PSPProvider.ASAAS

    def __init__(self, creds: PSPCredentials) -> None:
        self.creds = creds
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=self.creds.base_url,
                timeout=aiohttp.ClientTimeout(total=self.creds.timeout_seconds),
                headers={
                    "Content-Type": "application/json",
                    "access_token": self.creds.api_key,
                },
            )
        return self._session

    async def generate_qr_code(
        self,
        payment_id: str,
        amount_brl: float,
        description: str,
        expiration_seconds: int,
        qr_type: QRCodeType,
    ) -> Tuple[str, str]:
        session = await self._get_session()
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=expiration_seconds)
        ).isoformat()
        payload = {
            "billingType": "PIX",
            "value": amount_brl,
            "dueDate": expires_at[:10],
            "description": description,
            "externalReference": payment_id,
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.creds.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                async with session.post("/api/v3/payments", json=payload) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    payment_asaas_id = data["id"]
                async with session.get(
                    f"/api/v3/payments/{payment_asaas_id}/pixQrCode"
                ) as resp:
                    resp.raise_for_status()
                    qr_data = await resp.json()
                    return qr_data["payload"], payment_asaas_id

        raise PSPConnectionError("Asaas QR generation exhausted retries")

    async def process_payout(
        self,
        payment_id: str,
        amount_brl: float,
        pix_key: str,
        pix_key_type: PixKeyType,
        recipient_name: str,
        recipient_document: str,
        description: str,
    ) -> Tuple[str, str]:
        session = await self._get_session()
        payload = {
            "value": amount_brl,
            "pixAddressKey": pix_key,
            "pixAddressKeyType": pix_key_type.value.upper(),
            "description": description,
            "scheduleDate": datetime.now(timezone.utc).date().isoformat(),
            "externalReference": payment_id,
        }
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.creds.max_retries),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            reraise=True,
        ):
            with attempt:
                async with session.post("/api/v3/transfers", json=payload) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    return data["id"], data["status"]

        raise PSPConnectionError("Asaas payout exhausted retries")

    async def query_payment_status(self, e2e_id: str) -> Dict[str, Any]:
        session = await self._get_session()
        async with session.get(f"/api/v3/payments/{e2e_id}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def list_transactions(
        self, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        session = await self._get_session()
        params = {
            "dateCreated[ge]": start.date().isoformat(),
            "dateCreated[le]": end.date().isoformat(),
            "limit": 100,
        }
        async with session.get("/api/v3/payments", params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("data", [])

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        expected = hmac.new(
            self.creds.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


class TransfeeraAdapter(BasePSPAdapter):
    """Transfeera PIX adapter -- reference: https://developers.transfeera.com/"""

    provider = PSPProvider.TRANSFEERA

    def __init__(self, creds: PSPCredentials) -> None:
        self.creds = creds
        self._session: Optional[aiohttp.ClientSession] = None
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                base_url=self.creds.base_url,
                timeout=aiohttp.ClientTimeout(total=self.creds.timeout_seconds),
            )
        return self._session

    async def _ensure_token(self) -> str:
        if time.time() < self._token_expires_at - 60:
            return self._access_token  # type: ignore[return-value]
        session = await self._get_session()
        async with session.post(
            "/connect/token",
            json={
                "grant_type": "client_credentials",
                "client_id": self.creds.api_key,
                "client_secret": self.creds.api_secret,
                "scope": "pix",
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            self._access_token = data["access_token"]
            self._token_expires_at = time.time() + data.get("expires_in", 3600)
            return self._access_token

    async def generate_qr_code(
        self,
        payment_id: str,
        amount_brl: float,
        description: str,
        expiration_seconds: int,
        qr_type: QRCodeType,
    ) -> Tuple[str, str]:
        token = await self._ensure_token()
        session = await self._get_session()
        payload = {
            "externalId": payment_id,
            "amount": amount_brl,
            "description": description,
            "expiration": expiration_seconds,
            "type": "COB" if qr_type == QRCodeType.DYNAMIC else "COBV",
        }
        async with session.post(
            "/v1/pix/charges",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["qrCode"]["brCode"], data["id"]

    async def process_payout(
        self,
        payment_id: str,
        amount_brl: float,
        pix_key: str,
        pix_key_type: PixKeyType,
        recipient_name: str,
        recipient_document: str,
        description: str,
    ) -> Tuple[str, str]:
        token = await self._ensure_token()
        session = await self._get_session()
        payload = {
            "externalId": payment_id,
            "pixKey": pix_key,
            "pixKeyType": pix_key_type.value.upper(),
            "amount": amount_brl,
            "description": description,
        }
        async with session.post(
            "/v1/pix/transfers",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["e2eId"], data["status"]

    async def query_payment_status(self, e2e_id: str) -> Dict[str, Any]:
        token = await self._ensure_token()
        session = await self._get_session()
        async with session.get(
            f"/v1/pix/charges/{e2e_id}",
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def list_transactions(
        self, start: datetime, end: datetime
    ) -> List[Dict[str, Any]]:
        token = await self._ensure_token()
        session = await self._get_session()
        params = {"startDate": start.isoformat(), "endDate": end.isoformat()}
        async with session.get(
            "/v1/pix/charges",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data.get("items", [])

    def verify_webhook_signature(self, payload: bytes, signature: str) -> bool:
        expected = hmac.new(
            self.creds.webhook_secret.encode(),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# Payment State Machine
# ---------------------------------------------------------------------------


class PaymentStateMachine:
    """
    Enforces legal PIX payment state transitions.

    Allowed transitions:
      pending     -> processing, expired
      processing  -> confirmed, failed
      confirmed   -> settled, refunded
      settled     -> (terminal)
      failed      -> refunded
      refunded    -> (terminal)
      expired     -> (terminal)
    """

    _allowed: Dict[PaymentState, List[PaymentState]] = {
        PaymentState.PENDING: [PaymentState.PROCESSING, PaymentState.EXPIRED],
        PaymentState.PROCESSING: [PaymentState.CONFIRMED, PaymentState.FAILED],
        PaymentState.CONFIRMED: [PaymentState.SETTLED, PaymentState.REFUNDED],
        PaymentState.SETTLED: [],
        PaymentState.FAILED: [PaymentState.REFUNDED],
        PaymentState.REFUNDED: [],
        PaymentState.EXPIRED: [],
    }

    @classmethod
    def transition(
        cls,
        record: PaymentRecord,
        new_state: PaymentState,
        actor: str = "system",
        reason: str = "",
    ) -> None:
        allowed = cls._allowed.get(record.state, [])
        if new_state not in allowed:
            raise PixGatewayError(
                f"Invalid transition {record.state} -> {new_state} "
                f"for payment {record.payment_id}"
            )
        old_state = record.state
        record.state = new_state
        record.updated_at = datetime.now(timezone.utc)
        if new_state == PaymentState.SETTLED:
            record.settled_at = record.updated_at

        audit_entry = {
            "event": "state_transition",
            "from": old_state.value,
            "to": new_state.value,
            "actor": actor,
            "reason": reason,
            "timestamp": record.updated_at.isoformat(),
        }
        record.audit_trail.append(audit_entry)
        logger.info(
            "payment_state_transition",
            payment_id=record.payment_id,
            from_state=old_state.value,
            to_state=new_state.value,
            actor=actor,
        )


# ---------------------------------------------------------------------------
# Fraud Checks
# ---------------------------------------------------------------------------


class PixFraudChecker:
    """
    Lightweight real-time fraud scoring for PIX transactions.
    In production this calls your ML fraud service.
    """

    # Thresholds
    MAX_DAILY_DEPOSITS = 20
    MAX_DAILY_DEPOSIT_BRL = 50_000.0
    MAX_HOURLY_DEPOSITS = 5
    HIGH_RISK_SCORE_THRESHOLD = 0.75

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def score_deposit(
        self,
        player_id: str,
        amount_brl: float,
        ip_address: str,
    ) -> float:
        """
        Returns fraud score 0.0 (clean) to 1.0 (high risk).
        Checks: velocity, amount, IP reputation.
        """
        score = 0.0

        # Velocity check -- daily deposit count
        daily_key = f"pix:deposits:daily:{player_id}:{datetime.now(timezone.utc).date()}"
        daily_count = await self._redis_incr(daily_key, ttl=86400)
        if daily_count > self.MAX_DAILY_DEPOSITS:
            score += 0.4
        elif daily_count > self.MAX_DAILY_DEPOSITS // 2:
            score += 0.15

        # Hourly velocity
        hour_key = f"pix:deposits:hourly:{player_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H')}"
        hourly_count = await self._redis_incr(hour_key, ttl=3600)
        if hourly_count > self.MAX_HOURLY_DEPOSITS:
            score += 0.3

        # Amount check -- very high single deposit
        if amount_brl >= 20_000:
            score += 0.25
        elif amount_brl >= 10_000:
            score += 0.10

        # IP reputation (stub -- wire in MaxMind or Sift in production)
        if await self._is_suspicious_ip(ip_address):
            score += 0.35

        return min(score, 1.0)

    async def _redis_incr(self, key: str, ttl: int) -> int:
        """Increment counter and set TTL. Stub returns 1 if redis unavailable."""
        try:
            val = await self.redis.incr(key)
            await self.redis.expire(key, ttl)
            return val
        except Exception:
            return 1

    async def _is_suspicious_ip(self, ip: str) -> bool:
        """Stub: wire into MaxMind GeoIP or IP reputation API."""
        suspicious_prefixes = ["185.220.", "45.142.", "198.54."]
        return any(ip.startswith(p) for p in suspicious_prefixes)


# ---------------------------------------------------------------------------
# In-Memory Payment Store (replace with PostgreSQL in production)
# ---------------------------------------------------------------------------


class PaymentStore:
    """Thread-safe in-memory store. Replace with asyncpg + PostgreSQL."""

    def __init__(self) -> None:
        self._records: Dict[str, PaymentRecord] = {}
        self._lock = asyncio.Lock()

    async def save(self, record: PaymentRecord) -> None:
        async with self._lock:
            self._records[record.payment_id] = record

    async def get(self, payment_id: str) -> Optional[PaymentRecord]:
        return self._records.get(payment_id)

    async def get_by_e2e(self, e2e_id: str) -> Optional[PaymentRecord]:
        for r in self._records.values():
            if r.e2e_id == e2e_id:
                return r
        return None

    async def list_by_period(
        self, start: datetime, end: datetime
    ) -> List[PaymentRecord]:
        return [
            r
            for r in self._records.values()
            if start <= r.created_at <= end
        ]


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token-bucket rate limiter backed by Redis (stub)."""

    def __init__(self, redis_client: Any, max_per_minute: int = 60) -> None:
        self.redis = redis_client
        self.max_per_minute = max_per_minute

    async def check(self, key: str) -> bool:
        """Returns True if request is allowed."""
        try:
            bucket_key = f"rate_limit:{key}:{int(time.time() // 60)}"
            count = await self.redis.incr(bucket_key)
            if count == 1:
                await self.redis.expire(bucket_key, 60)
            return count <= self.max_per_minute
        except Exception:
            return True  # Fail open if Redis is unavailable


# ---------------------------------------------------------------------------
# PIX Payment Gateway Service
# ---------------------------------------------------------------------------


class PixPaymentGateway:
    """
    Core PIX gateway orchestrating PSP adapters, state machine,
    fraud checks, and reconciliation.
    """

    def __init__(
        self,
        adapters: Dict[PSPProvider, BasePSPAdapter],
        store: PaymentStore,
        fraud_checker: PixFraudChecker,
        rate_limiter: RateLimiter,
        primary_psp: PSPProvider = PSPProvider.CELCOIN,
    ) -> None:
        self.adapters = adapters
        self.store = store
        self.fraud_checker = fraud_checker
        self.rate_limiter = rate_limiter
        self.primary_psp = primary_psp

    def _get_adapter(self, provider: PSPProvider) -> BasePSPAdapter:
        adapter = self.adapters.get(provider)
        if not adapter:
            raise PixGatewayError(f"No adapter registered for {provider}")
        return adapter

    async def create_deposit(
        self,
        req: PixDepositRequest,
        ip_address: str = "0.0.0.0",
        qr_type: QRCodeType = QRCodeType.DYNAMIC,
    ) -> PaymentRecord:
        """
        Creates a PIX deposit charge and returns the payment record
        with QR code string ready for display.
        """
        # Rate limiting
        if not await self.rate_limiter.check(f"deposit:{req.player_id}"):
            raise PixGatewayError(f"Rate limit exceeded for player {req.player_id}")

        # Fraud check
        fraud_score = await self.fraud_checker.score_deposit(
            req.player_id, req.amount_brl, ip_address
        )
        if fraud_score >= PixFraudChecker.HIGH_RISK_SCORE_THRESHOLD:
            logger.warning(
                "pix_deposit_blocked_fraud",
                player_id=req.player_id,
                fraud_score=fraud_score,
            )
            raise FraudCheckFailedError(
                f"Deposit blocked: fraud score {fraud_score:.2f}"
            )

        payment_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        record = PaymentRecord(
            payment_id=payment_id,
            player_id=req.player_id,
            amount_brl=req.amount_brl,
            direction="deposit",
            state=PaymentState.PENDING,
            psp_provider=self.primary_psp,
            e2e_id=None,
            qr_code=None,
            qr_code_type=qr_type,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=req.expiration_seconds),
            settled_at=None,
            fraud_score=fraud_score,
            metadata=req.metadata or {},
        )
        record.audit_trail.append(
            {
                "event": "deposit_initiated",
                "player_id": req.player_id,
                "amount_brl": req.amount_brl,
                "ip_address": ip_address,
                "fraud_score": fraud_score,
                "timestamp": now.isoformat(),
            }
        )
        await self.store.save(record)

        # Generate QR code via PSP
        try:
            adapter = self._get_adapter(self.primary_psp)
            PaymentStateMachine.transition(record, PaymentState.PROCESSING, "gateway")
            qr_code, e2e_id = await adapter.generate_qr_code(
                payment_id=payment_id,
                amount_brl=req.amount_brl,
                description=req.description,
                expiration_seconds=req.expiration_seconds,
                qr_type=qr_type,
            )
            record.qr_code = qr_code
            record.e2e_id = e2e_id
        except (PSPConnectionError, RetryError) as exc:
            PaymentStateMachine.transition(
                record, PaymentState.FAILED, "gateway", str(exc)
            )
            await self.store.save(record)
            raise

        await self.store.save(record)
        logger.info(
            "pix_deposit_created",
            payment_id=payment_id,
            player_id=req.player_id,
            amount_brl=req.amount_brl,
            psp=self.primary_psp.value,
        )
        return record

    async def process_withdrawal(self, req: PixWithdrawalRequest) -> PaymentRecord:
        """
        Initiates a PIX payout from operator to player.
        Includes balance check stub and state transitions.
        """
        if not await self.rate_limiter.check(f"withdrawal:{req.player_id}"):
            raise PixGatewayError(f"Rate limit exceeded for player {req.player_id}")

        payment_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        record = PaymentRecord(
            payment_id=payment_id,
            player_id=req.player_id,
            amount_brl=req.amount_brl,
            direction="withdrawal",
            state=PaymentState.PENDING,
            psp_provider=self.primary_psp,
            e2e_id=None,
            qr_code=None,
            qr_code_type=None,
            created_at=now,
            updated_at=now,
            expires_at=None,
            settled_at=None,
        )
        await self.store.save(record)

        try:
            adapter = self._get_adapter(self.primary_psp)
            PaymentStateMachine.transition(record, PaymentState.PROCESSING, "gateway")
            e2e_id, status = await adapter.process_payout(
                payment_id=payment_id,
                amount_brl=req.amount_brl,
                pix_key=req.pix_key,
                pix_key_type=req.pix_key_type,
                recipient_name=req.recipient_name,
                recipient_document=req.recipient_cpf_cnpj,
                description=req.description,
            )
            record.e2e_id = e2e_id

            if status.upper() in ("APPROVED", "CONFIRMED", "COMPLETED", "PROCESSED"):
                PaymentStateMachine.transition(record, PaymentState.CONFIRMED, "psp")
                PaymentStateMachine.transition(record, PaymentState.SETTLED, "psp")
            else:
                PaymentStateMachine.transition(
                    record, PaymentState.FAILED, "psp", f"PSP status: {status}"
                )
        except Exception as exc:
            PaymentStateMachine.transition(
                record, PaymentState.FAILED, "gateway", str(exc)
            )
            await self.store.save(record)
            raise

        await self.store.save(record)
        logger.info(
            "pix_withdrawal_processed",
            payment_id=payment_id,
            player_id=req.player_id,
            amount_brl=req.amount_brl,
            state=record.state.value,
        )
        return record

    async def handle_webhook(
        self,
        payload_bytes: bytes,
        signature: str,
        provider: PSPProvider,
    ) -> None:
        """
        Processes incoming PIX confirmation webhook from PSP.
        Validates signature, maps to internal record, transitions state.
        """
        adapter = self._get_adapter(provider)
        if not adapter.verify_webhook_signature(payload_bytes, signature):
            logger.warning("pix_webhook_invalid_signature", provider=provider.value)
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

        data = json.loads(payload_bytes)
        e2e_id = data.get("e2eId") or data.get("transactionId") or data.get("id", "")
        status = data.get("status", "").upper()
        amount = float(data.get("amount", 0))

        record = await self.store.get_by_e2e(e2e_id)
        if not record:
            logger.warning("pix_webhook_unknown_e2e", e2e_id=e2e_id)
            return

        if status in ("CONFIRMED", "APPROVED", "CONCLUIDA", "SETTLED"):
            if record.state == PaymentState.PROCESSING:
                PaymentStateMachine.transition(record, PaymentState.CONFIRMED, "psp_webhook")
                PaymentStateMachine.transition(record, PaymentState.SETTLED, "psp_webhook")
        elif status in ("FAILED", "DEVOLVIDA", "ERROR"):
            if record.state in (PaymentState.PENDING, PaymentState.PROCESSING):
                PaymentStateMachine.transition(
                    record, PaymentState.FAILED, "psp_webhook", status
                )

        record.audit_trail.append(
            {
                "event": "webhook_received",
                "provider": provider.value,
                "e2e_id": e2e_id,
                "psp_status": status,
                "psp_amount": amount,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        await self.store.save(record)

    async def reconcile(
        self,
        provider: PSPProvider,
        period_hours: int = 24,
    ) -> ReconciliationResult:
        """
        Compares PSP transaction list against internal ledger.
        Flags unmatched or mismatched amounts.
        """
        run_id = str(uuid.uuid4())
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=period_hours)

        adapter = self._get_adapter(provider)
        psp_transactions = await adapter.list_transactions(start, end)
        internal_records = await self.store.list_by_period(start, end)

        psp_map: Dict[str, float] = {}
        for tx in psp_transactions:
            tx_id = tx.get("e2eId") or tx.get("id", "")
            psp_map[tx_id] = float(tx.get("amount", 0))

        internal_map: Dict[str, float] = {
            r.e2e_id: r.amount_brl
            for r in internal_records
            if r.e2e_id and r.psp_provider == provider
        }

        psp_total = sum(psp_map.values())
        internal_total = sum(internal_map.values())
        unmatched_psp = [k for k in psp_map if k not in internal_map]
        unmatched_internal = [k for k in internal_map if k not in psp_map]
        discrepancy = round(abs(psp_total - internal_total), 2)

        status = "balanced" if discrepancy < 0.01 and not unmatched_psp and not unmatched_internal else "discrepancy"

        if status == "discrepancy":
            logger.error(
                "pix_reconciliation_discrepancy",
                run_id=run_id,
                psp=provider.value,
                discrepancy_brl=discrepancy,
                unmatched_psp=len(unmatched_psp),
                unmatched_internal=len(unmatched_internal),
            )

        return ReconciliationResult(
            run_id=run_id,
            period_start=start,
            period_end=end,
            psp_provider=provider,
            psp_total_brl=psp_total,
            internal_total_brl=internal_total,
            matched_count=len(psp_map) - len(unmatched_psp),
            unmatched_psp=unmatched_psp,
            unmatched_internal=unmatched_internal,
            discrepancy_brl=discrepancy,
            status=status,
        )


# ---------------------------------------------------------------------------
# FastAPI Application
# ---------------------------------------------------------------------------

payment_store = PaymentStore()
gateway: Optional[PixPaymentGateway] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global gateway
    # In production: load credentials from AWS Secrets Manager / Vault
    creds = PSPCredentials(
        provider=PSPProvider.CELCOIN,
        api_key="CELCOIN_API_KEY",
        api_secret="CELCOIN_API_SECRET",
        base_url="https://sandbox.openfinance.celcoin.dev",
        webhook_secret="CELCOIN_WEBHOOK_SECRET",
    )
    adapters = {PSPProvider.CELCOIN: CelcoinAdapter(creds)}
    fraud_checker = PixFraudChecker(redis_client=None)  # wire real Redis
    rate_limiter = RateLimiter(redis_client=None)
    gateway = PixPaymentGateway(
        adapters=adapters,
        store=payment_store,
        fraud_checker=fraud_checker,
        rate_limiter=rate_limiter,
    )
    logger.info("pix_gateway_started")
    yield
    logger.info("pix_gateway_shutdown")


app = FastAPI(
    title="PIX Payment Gateway",
    description="Brazilian PIX payment service for betting operators",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,  # ty: ignore[invalid-argument-type]
    allow_origins=["https://apostas.acmetocasino.bet.br"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)


@app.post("/v1/pix/deposits", response_model=Dict[str, Any])
async def create_deposit(
    req: PixDepositRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    """Initiate a PIX deposit charge and return QR code."""
    ip = request.client.host if request.client else "0.0.0.0"
    record = await gateway.create_deposit(req, ip_address=ip)  # type: ignore[union-attr]
    return {
        "payment_id": record.payment_id,
        "qr_code": record.qr_code,
        "e2e_id": record.e2e_id,
        "state": record.state.value,
        "amount_brl": record.amount_brl,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
    }


@app.post("/v1/pix/withdrawals", response_model=Dict[str, Any])
async def create_withdrawal(req: PixWithdrawalRequest) -> Dict[str, Any]:
    """Process a PIX payout to a player."""
    record = await gateway.process_withdrawal(req)  # type: ignore[union-attr]
    return {
        "payment_id": record.payment_id,
        "e2e_id": record.e2e_id,
        "state": record.state.value,
        "amount_brl": record.amount_brl,
        "settled_at": record.settled_at.isoformat() if record.settled_at else None,
    }


@app.post("/v1/pix/webhooks/{provider}")
async def pix_webhook(
    provider: PSPProvider,
    request: Request,
    x_signature: str = Header(default=""),
) -> JSONResponse:
    """Receive PIX payment notification from PSP."""
    payload = await request.body()
    await gateway.handle_webhook(payload, x_signature, provider)  # type: ignore[union-attr]
    return JSONResponse({"status": "ok"})


@app.get("/v1/pix/payments/{payment_id}", response_model=Dict[str, Any])
async def get_payment(payment_id: str) -> Dict[str, Any]:
    """Retrieve payment record by ID."""
    record = await payment_store.get(payment_id)
    if not record:
        raise HTTPException(status_code=404, detail="Payment not found")
    return {
        "payment_id": record.payment_id,
        "player_id": record.player_id,
        "amount_brl": record.amount_brl,
        "state": record.state.value,
        "direction": record.direction,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "settled_at": record.settled_at.isoformat() if record.settled_at else None,
        "fraud_score": record.fraud_score,
    }


@app.post("/v1/pix/reconcile/{provider}", response_model=Dict[str, Any])
async def run_reconciliation(
    provider: PSPProvider,
    period_hours: int = 24,
) -> Dict[str, Any]:
    """Run PSP reconciliation for the given period."""
    result = await gateway.reconcile(provider, period_hours)  # type: ignore[union-attr]
    return result.model_dump()


@app.get("/healthz")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "pix-payment-gateway"}


if __name__ == "__main__":
    uvicorn.run("pix_payment_gateway:app", host="0.0.0.0", port=8001, reload=False)
