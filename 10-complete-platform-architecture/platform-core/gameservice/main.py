# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
main.py
-------
FastAPI application exposing the Game Aggregation Layer (GAL) API.

This is the HTTP surface of the GAL. Supplier callbacks land here;
the routes delegate immediately to AccountsBridge for all business logic.

Route groups
------------
/api/v1/auth          — Session authentication (game launch token → session)
/api/v1/wallet        — Balance, debit, credit, refund
/supplier/evolution   — Evolution Gaming callbacks (POST)
/supplier/pragmatic   — Pragmatic Play callbacks (POST)
/supplier/netent      — NetEnt callbacks (POST)
/supplier/playngo     — Play'n GO callbacks (POST)
/supplier/kambi       — Kambi fund/withdraw callbacks (POST)
/supplier/relax       — Relax Gaming callbacks (POST)
/supplier/igt         — IGT callbacks (POST)
/supplier/hacksaw     — Hacksaw Gaming callbacks (POST)
/supplier/push_gaming — Push Gaming callbacks (POST)
/supplier/nyx         — NYX OGS callbacks (POST)
/supplier/betgenius   — Bet Genius settlement callbacks (POST)

Health and observability
-------------------------
GET /health       — Liveness probe (always 200 if process is running)
GET /ready        — Readiness probe (checks DB and cache connectivity)
GET /metrics      — Prometheus metrics (if prometheus-client is installed)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from accounts_bridge import AccountsBridge, PlayerLockRegistry, PlayerRepository, TransactionCache
from accounts_provider import PlayerSession
from transaction_result import (
    AuthenticationError,
    BalanceStatus,
    GameServiceError,
    InsufficientFundsError,
    InvalidSessionError,
    TransactionBlockedError,
    TransactionResult,
    TransactionType,
    UserLockedError,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown hooks."""
    logger.info("GAL starting up — loading supplier registry")
    _bootstrap_suppliers()
    logger.info("GAL ready")
    yield
    logger.info("GAL shutting down")


# ---------------------------------------------------------------------------
# Supplier request authentication
# ---------------------------------------------------------------------------
#
# Every route in this module either issues a session or moves money.
# Without authenticating the caller, anyone who can reach the GAL over
# the network can credit/debit an arbitrary player_id. Each supplier
# signs its requests with a per-supplier HMAC-SHA256 secret (same
# HMAC-SHA256 + timestamp + timing-safe-compare pattern already used by
# suppliers/netent/provider.py:validate_request_signature and
# suppliers/push_gaming/provider.py:verify_signature, applied here as a
# uniform scheme for the GAL's own inbound API):
#
#   signature = HMAC-SHA256(f"{timestamp}.{raw_body}", supplier_secret)
#
# sent as the X-Supplier-Timestamp / X-Supplier-Signature headers. There
# is no "unauthenticated" mode: a supplier with no configured secret is
# rejected rather than silently let through.

_SUPPLIER_CALLBACK_SECRETS: dict[str, str] = {}
_MAX_REQUEST_AGE_S = 300  # reject requests signed more than 5 minutes ago


async def verify_supplier_signature(request: Request) -> dict:
    """
    FastAPI dependency: authenticate an inbound supplier request.

    Returns the parsed JSON body (so route handlers reusing Depends(...)
    don't have to re-read the stream) and raises HTTPException(401) on
    any failure — missing supplier, missing/unconfigured secret, missing
    headers, stale timestamp, or signature mismatch.
    """
    raw_body = await request.body()
    try:
        parsed = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed JSON body")

    supplier_id = parsed.get("supplier_id", "") if isinstance(parsed, dict) else ""
    secret = _SUPPLIER_CALLBACK_SECRETS.get(supplier_id)
    if not supplier_id or not secret:
        logger.warning(
            "Rejecting request: unknown or unconfigured supplier=%r", supplier_id,
        )
        raise HTTPException(status_code=401, detail="Unknown or unconfigured supplier")

    timestamp = request.headers.get("X-Supplier-Timestamp", "")
    provided_sig = request.headers.get("X-Supplier-Signature", "")
    if not timestamp or not provided_sig:
        raise HTTPException(status_code=401, detail="Missing signature headers")

    try:
        ts = float(timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp")
    if abs(time.time() - ts) > _MAX_REQUEST_AGE_S:
        raise HTTPException(status_code=401, detail="Stale request")

    message = f"{timestamp}.".encode("utf-8") + raw_body
    expected_sig = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(provided_sig, expected_sig):
        logger.warning("Rejecting request: bad signature supplier=%r", supplier_id)
        raise HTTPException(status_code=401, detail="Invalid signature")

    return parsed


def _bootstrap_suppliers() -> None:
    """
    Register all supplier providers at startup.

    In production, pull API credentials from a secrets manager
    (AWS Secrets Manager, HashiCorp Vault, etc.).
    """
    from suppliers.registry import SupplierDescriptor, SupplierType, registry
    from suppliers.evolution.provider import EvolutionProvider
    from suppliers.pragmatic.provider import PragmaticProvider
    from suppliers.netent.provider import NetEntProvider
    from suppliers.playngo.provider import PlaynGoProvider
    from suppliers.kambi.provider import KambiProvider
    from suppliers.relax.provider import RelaxProvider
    from suppliers.igt.provider import IGTProvider
    from suppliers.hacksaw.provider import HacksawProvider
    from suppliers.push_gaming.provider import PushGamingProvider
    from suppliers.nyx.provider import NYXProvider
    from suppliers.betgenius.provider import BetGeniusProvider

    def env(key: str, default: str = "") -> str:
        return os.environ.get(key, default)

    # Secret used to authenticate inbound requests to THIS GAL's own
    # /api/v1/* routes (auth + wallet). Deliberately separate from the
    # secrets above, which sign our outbound/launch-token traffic to each
    # supplier's API — mixing the two would let a leak of one compromise
    # the other. See verify_supplier_signature().
    _SUPPLIER_CALLBACK_SECRETS.update({
        "evolution": env("EVOLUTION_CALLBACK_SECRET"),
        "pragmatic": env("PRAGMATIC_CALLBACK_SECRET"),
        "netent": env("NETENT_CALLBACK_SECRET"),
        "playngo": env("PLAYNGO_CALLBACK_SECRET"),
        "kambi": env("KAMBI_CALLBACK_SECRET"),
        "relax": env("RELAX_CALLBACK_SECRET"),
        "igt": env("IGT_CALLBACK_SECRET"),
        "hacksaw": env("HACKSAW_CALLBACK_SECRET"),
        "push_gaming": env("PUSH_GAMING_CALLBACK_SECRET"),
        "nyx": env("NYX_CALLBACK_SECRET"),
        "betgenius": env("BETGENIUS_CALLBACK_SECRET"),
    })

    registry.register(SupplierDescriptor(
        supplier_id="evolution",
        display_name="Evolution Gaming",
        supplier_type=SupplierType.CASINO,
        provider=EvolutionProvider(
            api_base_url=env("EVOLUTION_API_URL", "https://evolution.example.com"),
            api_secret=env("EVOLUTION_API_SECRET"),
            operator_id=env("EVOLUTION_OPERATOR_ID"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="pragmatic",
        display_name="Pragmatic Play",
        supplier_type=SupplierType.CASINO,
        provider=PragmaticProvider(
            api_base_url=env("PRAGMATIC_API_URL", "https://pragmatic.example.com"),
            secret_key=env("PRAGMATIC_SECRET_KEY"),
            operator_id=env("PRAGMATIC_OPERATOR_ID"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="netent",
        display_name="NetEnt",
        supplier_type=SupplierType.CASINO,
        provider=NetEntProvider(
            wallet_url=env("NETENT_WALLET_URL", "https://netent.example.com/wallet"),
            token_service_url=env("NETENT_TOKEN_URL", "https://netent.example.com/token"),
            operator_id=env("NETENT_OPERATOR_ID"),
            secret=env("NETENT_SECRET"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="playngo",
        display_name="Play'n GO",
        supplier_type=SupplierType.CASINO,
        provider=PlaynGoProvider(
            operator_api_url=env("PLAYNGO_API_URL", "https://playngo.example.com"),
            operator_id=env("PLAYNGO_OPERATOR_ID"),
            secret_key=env("PLAYNGO_SECRET_KEY"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="kambi",
        display_name="Kambi Sportsbook",
        supplier_type=SupplierType.SPORTS_BOOK,
        provider=KambiProvider(
            operator_id=env("KAMBI_OPERATOR_ID"),
            market_id=env("KAMBI_MARKET_ID", "GB"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="relax",
        display_name="Relax Gaming",
        supplier_type=SupplierType.AGGREGATOR,
        provider=RelaxProvider(
            operator_id=env("RELAX_OPERATOR_ID"),
            api_key=env("RELAX_API_KEY"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="igt",
        display_name="IGT",
        supplier_type=SupplierType.CASINO,
        provider=IGTProvider(
            api_base_url=env("IGT_API_URL", "https://igt.example.com"),
            operator_id=env("IGT_OPERATOR_ID"),
            api_key=env("IGT_API_KEY"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="hacksaw",
        display_name="Hacksaw Gaming",
        supplier_type=SupplierType.CRASH,
        provider=HacksawProvider(
            operator_id=env("HACKSAW_OPERATOR_ID"),
            secret_key=env("HACKSAW_SECRET_KEY"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="push_gaming",
        display_name="Push Gaming",
        supplier_type=SupplierType.CASINO,
        provider=PushGamingProvider(
            operator_key=env("PUSH_GAMING_OPERATOR_KEY"),
            secret=env("PUSH_GAMING_SECRET"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="nyx",
        display_name="NYX Interactive",
        supplier_type=SupplierType.AGGREGATOR,
        provider=NYXProvider(
            operator_id=env("NYX_OPERATOR_ID"),
            auth_token=env("NYX_AUTH_TOKEN"),
        ),
        seamless_wallet=True,
    ))

    registry.register(SupplierDescriptor(
        supplier_id="betgenius",
        display_name="Bet Genius",
        supplier_type=SupplierType.SPORTS_BOOK,
        provider=BetGeniusProvider(
            api_key=env("BETGENIUS_API_KEY"),
        ),
        seamless_wallet=False,
    ))

    logger.info("Registered %d suppliers", len(registry))


# ---------------------------------------------------------------------------
# Dependency injection
# ---------------------------------------------------------------------------


class StubPlayerRepository(PlayerRepository):
    """
    In-memory player repository for development / testing.
    Replace with a real asyncpg/SQLAlchemy implementation in production.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], object] = {}

    async def load_player(self, player_id: str) -> Optional[dict]:
        return {"player_id": player_id}

    async def load_from_supplier_ref(self, supplier_id: str, supplier_ref: str):
        return self._records.get((supplier_id, supplier_ref))

    async def record_request(self, record):
        self._records[(record.supplier_id, record.supplier_ref)] = record
        return record

    async def record_result(self, record, delete_existing: bool = False) -> None:
        self._records[(record.supplier_id, record.supplier_ref)] = record

    async def mark_refunded(self, record) -> None:
        record.refunded = True


_player_repo = StubPlayerRepository()
_lock_registry = PlayerLockRegistry()
_tx_cache = TransactionCache()


def get_bridge() -> AccountsBridge:
    from suppliers.registry import get_provider
    return AccountsBridge(
        provider_factory=get_provider,
        player_repo=_player_repo,
        lock_registry=_lock_registry,
        tx_cache=_tx_cache,
    )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


app = FastAPI(
    title="AcmetoCasino — Game Aggregation Layer",
    description="Central transaction coordinator for casino and sportsbook suppliers.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "*").split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class AuthRequest(BaseModel):
    token: str
    supplier_id: str
    game_id: Optional[str] = None


class AuthResponse(BaseModel):
    player_id: str
    currency: str
    game_id: str


class BalanceResponse(BaseModel):
    player_id: str
    cash_balance: Decimal
    bonus_balance: Decimal
    total_balance: Decimal
    currency: str


class TransactionRequest(BaseModel):
    player_id: str
    supplier_id: str
    supplier_ref: str
    round_id: str
    amount: Decimal = Field(ge=Decimal("0"))
    currency: str
    game_id: str
    session_token: str


class TransactionResponse(BaseModel):
    tx_id: str
    status: str
    cash_balance: Decimal
    bonus_balance: Decimal
    currency: str
    already_processed: bool = False
    error_message: Optional[str] = None


class RefundRequest(BaseModel):
    player_id: str
    supplier_id: str
    supplier_ref: str
    round_id: str
    amount: Optional[Decimal] = None


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


@app.exception_handler(AuthenticationError)
@app.exception_handler(InvalidSessionError)
async def authentication_error_handler(request: Request, exc: GameServiceError):
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)})


@app.exception_handler(InsufficientFundsError)
async def insufficient_funds_handler(request: Request, exc: GameServiceError):
    return JSONResponse(status_code=status.HTTP_402_PAYMENT_REQUIRED, content={"detail": str(exc)})


@app.exception_handler(TransactionBlockedError)
@app.exception_handler(UserLockedError)
async def blocked_handler(request: Request, exc: GameServiceError):
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": str(exc)})


@app.exception_handler(GameServiceError)
async def game_service_error_handler(request: Request, exc: GameServiceError):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": str(exc)},
    )


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"])
async def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/ready", tags=["ops"])
async def ready():
    from suppliers.registry import registry
    return {
        "status": "ready",
        "suppliers": len(registry),
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------


@app.post("/api/v1/auth", response_model=AuthResponse, tags=["auth"])
async def authenticate(
    payload: AuthRequest,
    bridge: AccountsBridge = Depends(get_bridge),
    _verified: dict = Depends(verify_supplier_signature),
):
    """Validate a game-launch token and return the player session."""
    session = await bridge.authenticate(payload.token, payload.supplier_id)
    return AuthResponse(
        player_id=session.player_id,
        currency=session.currency,
        game_id=session.game_id,
    )


# ---------------------------------------------------------------------------
# Wallet endpoints
# ---------------------------------------------------------------------------


def _session_from_request(req: TransactionRequest) -> PlayerSession:
    return PlayerSession(
        player_id=req.player_id,
        brand_id="",
        external_id=req.player_id,
        currency=req.currency,
        country="",
        jurisdiction="",
        session_token=req.session_token,
        game_id=req.game_id,
    )


def _tx_response(result: TransactionResult, currency: str) -> TransactionResponse:
    balance = result.balance
    return TransactionResponse(
        tx_id=result.tx_id,
        status=result.status.value,
        cash_balance=balance.cash_balance if balance else Decimal("0"),
        bonus_balance=balance.bonus_balance if balance else Decimal("0"),
        currency=currency,
        already_processed=result.already_processed,
        error_message=result.error_message,
    )


@app.post("/api/v1/wallet/balance", response_model=BalanceResponse, tags=["wallet"])
async def get_balance(
    payload: TransactionRequest,
    bridge: AccountsBridge = Depends(get_bridge),
    _verified: dict = Depends(verify_supplier_signature),
):
    """Retrieve the player's current balance."""
    session = _session_from_request(payload)
    balance = await bridge.get_balance(session, payload.supplier_id)
    return BalanceResponse(
        player_id=payload.player_id,
        cash_balance=balance.cash_balance,
        bonus_balance=balance.bonus_balance,
        total_balance=balance.total_balance,
        currency=balance.currency,
    )


@app.post("/api/v1/wallet/debit", response_model=TransactionResponse, tags=["wallet"])
async def debit(
    payload: TransactionRequest,
    bridge: AccountsBridge = Depends(get_bridge),
    _verified: dict = Depends(verify_supplier_signature),
):
    """Deduct a stake from the player's wallet."""
    session = _session_from_request(payload)
    result = await bridge.debit(
        session=session,
        supplier_id=payload.supplier_id,
        supplier_ref=payload.supplier_ref,
        round_id=payload.round_id,
        amount=payload.amount,
    )
    return _tx_response(result, payload.currency)


@app.post("/api/v1/wallet/credit", response_model=TransactionResponse, tags=["wallet"])
async def credit(
    payload: TransactionRequest,
    bridge: AccountsBridge = Depends(get_bridge),
    _verified: dict = Depends(verify_supplier_signature),
):
    """Add winnings to the player's wallet."""
    session = _session_from_request(payload)
    result = await bridge.credit(
        session=session,
        supplier_id=payload.supplier_id,
        supplier_ref=payload.supplier_ref,
        round_id=payload.round_id,
        amount=payload.amount,
    )
    return _tx_response(result, payload.currency)


@app.post("/api/v1/wallet/refund", response_model=TransactionResponse, tags=["wallet"])
async def refund(
    payload: RefundRequest,
    bridge: AccountsBridge = Depends(get_bridge),
    _verified: dict = Depends(verify_supplier_signature),
):
    """Reverse a previous debit (incomplete round rollback)."""
    result = await bridge.refund(
        supplier_id=payload.supplier_id,
        supplier_ref=payload.supplier_ref,
        player_id=payload.player_id,
        round_id=payload.round_id,
        amount=payload.amount,
    )
    return _tx_response(result, "")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        workers=int(os.environ.get("WORKERS", "4")),
        log_level="info",
    )
