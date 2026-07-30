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
router.py — FastAPI API router for the AcmetoCasino platform.

Registers all route groups (game, wallet, auth, responsible gaming) on a
single FastAPI application instance.  Each route module is kept in its own
file; this module is responsible only for composition.

Architecture:
  - /api/v1/auth/*       — Session management, login/logout, JWT refresh
  - /api/v1/games/*      — Game catalogue, launch URLs, game-round events
  - /api/v1/wallet/*     — Balance queries, deposit initiation, withdrawal
  - /api/v1/rg/*         — Responsible gaming limits, self-exclusion, reality checks
  - /api/v1/compliance/* — Regulatory reporting endpoints (DGE, MGA, UKGC)
  - /health              — Kubernetes liveness/readiness probe
  - /metrics             — Prometheus scrape endpoint (via starlette-prometheus)

Reference: Chapter 10 — Complete Platform Architecture
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("platform.router")


# ---------------------------------------------------------------------------
# Shared dependency: request context
# ---------------------------------------------------------------------------

class RequestContext(BaseModel):
    """Minimal request-scoped context passed to route handlers."""
    request_id: str
    brand: str
    jurisdiction: str
    timestamp: str


def _get_request_context(request: Request) -> RequestContext:
    """
    Extract request context from headers injected by the API gateway (Kong).

    Kong injects:
      X-Request-ID     — UUID generated at the edge
      X-Brand          — casino brand identifier (e.g. acmetocasino, acmegate)
      X-Jurisdiction   — regulatory jurisdiction (e.g. nj, pa, uk, mt)
    """
    return RequestContext(
        request_id=request.headers.get("X-Request-ID", ""),
        brand=request.headers.get("X-Brand", "default"),
        jurisdiction=request.headers.get("X-Jurisdiction", ""),
        timestamp=datetime.now(tz=timezone.utc).isoformat(),
    )


# ---------------------------------------------------------------------------
# Auth router — /api/v1/auth
# ---------------------------------------------------------------------------

auth_router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=100)
    password: str = Field(..., min_length=8)
    mfa_token: str | None = Field(None, description="TOTP token for MFA-enabled accounts")


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Token TTL in seconds")
    player_id: str
    jurisdiction: str
    session_id: str


class SessionResponse(BaseModel):
    player_id: str
    brand: str
    jurisdiction: str
    session_id: str
    created_at: str
    reality_check_due_at: str | None = None


@auth_router.post("/login", response_model=LoginResponse)
async def login(
    body: LoginRequest,
    ctx: RequestContext = Depends(_get_request_context),
) -> LoginResponse:
    """
    Authenticate a player and issue a short-lived JWT access token.

    The token is signed with the platform's HSM-backed Ed25519 key.
    MFA is required for accounts with MFA enabled.

    Responses:
      200 — Authenticated, returns JWT
      401 — Invalid credentials
      403 — Account suspended or self-excluded
      429 — Rate limit exceeded (credential stuffing protection)
    """
    # Authentication logic is delegated to the PAM (Player Account Management)
    # service via gRPC.  The router validates input and forwards the request.
    log.info(
        "auth_login_attempt brand=%s jurisdiction=%s request_id=%s",
        ctx.brand,
        ctx.jurisdiction,
        ctx.request_id,
    )
    # Stub response for documentation purposes
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Auth service integration — connect to PAM gRPC endpoint",
    )


@auth_router.post("/logout")
async def logout(
    ctx: RequestContext = Depends(_get_request_context),
) -> dict[str, str]:
    """Invalidate the current session token."""
    return {"status": "logged_out", "request_id": ctx.request_id}


@auth_router.get("/session", response_model=SessionResponse)
async def get_session(
    ctx: RequestContext = Depends(_get_request_context),
) -> SessionResponse:
    """Return current session details including reality check schedule."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Session service integration",
    )


@auth_router.post("/refresh")
async def refresh_token(
    ctx: RequestContext = Depends(_get_request_context),
) -> dict[str, Any]:
    """Issue a new access token using a valid refresh token."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Token refresh — PAM service",
    )


# ---------------------------------------------------------------------------
# Games router — /api/v1/games
# ---------------------------------------------------------------------------

game_router = APIRouter(prefix="/api/v1/games", tags=["games"])


class GameLaunchRequest(BaseModel):
    game_id: str = Field(..., description="Platform-internal game UUID")
    mode: str = Field(default="real", pattern="^(real|demo)$")
    return_url: str | None = Field(None, description="URL to return to after the session")
    language: str = Field(default="en", max_length=5)


class GameLaunchResponse(BaseModel):
    launch_url: str = Field(description="Signed game launch URL from the provider")
    session_token: str
    game_id: str
    provider: str
    expires_at: str


class GameSummary(BaseModel):
    game_id: str
    name: str
    provider: str
    category: str
    rtp: float | None = None
    volatility: str | None = None
    jurisdictions: list[str]
    active: bool


@game_router.get("/", response_model=list[GameSummary])
async def list_games(
    category: str | None = None,
    provider: str | None = None,
    ctx: RequestContext = Depends(_get_request_context),
) -> list[GameSummary]:
    """
    Return the game catalogue for the requesting brand and jurisdiction.

    Only games licensed for the player's jurisdiction are returned.
    Results are cached in Redis with a 60-second TTL.
    """
    log.info(
        "games_list brand=%s jurisdiction=%s category=%s provider=%s",
        ctx.brand,
        ctx.jurisdiction,
        category,
        provider,
    )
    # Delegated to the Game Aggregation Layer (GAL) microservice
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Game catalogue — GAL service",
    )


@game_router.post("/launch", response_model=GameLaunchResponse)
async def launch_game(
    body: GameLaunchRequest,
    ctx: RequestContext = Depends(_get_request_context),
) -> GameLaunchResponse:
    """
    Generate a signed launch URL for a game session.

    The GAL authenticates with the provider (Evolution, Pragmatic, NetEnt,
    etc.) and returns a time-limited launch URL containing the session token.
    Responsible gaming limits are checked before launch.
    """
    log.info(
        "game_launch game_id=%s mode=%s brand=%s jurisdiction=%s",
        body.game_id,
        body.mode,
        ctx.brand,
        ctx.jurisdiction,
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Game launch — GAL service",
    )


@game_router.get("/{game_id}", response_model=GameSummary)
async def get_game(
    game_id: str,
    ctx: RequestContext = Depends(_get_request_context),
) -> GameSummary:
    """Return metadata for a single game."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Game metadata — GAL service",
    )


# ---------------------------------------------------------------------------
# Wallet router — /api/v1/wallet
# ---------------------------------------------------------------------------

wallet_router = APIRouter(prefix="/api/v1/wallet", tags=["wallet"])


class BalanceResponse(BaseModel):
    player_id: str
    balance: float = Field(description="Current balance in player's currency")
    bonus_balance: float = Field(default=0.0)
    currency: str
    jurisdiction: str
    as_of: str


class DepositRequest(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    payment_method: str = Field(..., description="Payment method identifier (e.g. visa, pix, card-token-xxx)")
    return_url: str | None = None
    cancel_url: str | None = None


class DepositResponse(BaseModel):
    deposit_id: str
    status: str = Field(description="pending | processing | approved | declined")
    redirect_url: str | None = Field(None, description="PSP redirect URL for 3DS/hosted-payment flows")
    amount: float
    currency: str


class WithdrawalRequest(BaseModel):
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=3, max_length=3)
    payment_method: str
    account_details: dict[str, str] = Field(default_factory=dict)


class WithdrawalResponse(BaseModel):
    withdrawal_id: str
    status: str
    estimated_arrival: str | None = None
    amount: float
    currency: str


@wallet_router.get("/balance", response_model=BalanceResponse)
async def get_balance(
    ctx: RequestContext = Depends(_get_request_context),
) -> BalanceResponse:
    """
    Return the current wallet balance for the authenticated player.

    Balance is read from the primary PostgreSQL spoke database for the player's
    jurisdiction and cached in Redis for 5 seconds.  The bonus balance reflects
    active bonus funds with wagering requirements outstanding.
    """
    log.info("wallet_balance brand=%s jurisdiction=%s", ctx.brand, ctx.jurisdiction)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Wallet balance — wallet service",
    )


@wallet_router.post("/deposit", response_model=DepositResponse)
async def initiate_deposit(
    body: DepositRequest,
    ctx: RequestContext = Depends(_get_request_context),
) -> DepositResponse:
    """
    Initiate a deposit via the configured PSP for the player's jurisdiction.

    Deposit limits (daily, weekly, monthly) are enforced before routing to
    the PSP.  AML checks run asynchronously post-deposit.
    """
    log.info(
        "wallet_deposit amount=%s currency=%s method=%s jurisdiction=%s",
        body.amount,
        body.currency,
        body.payment_method,
        ctx.jurisdiction,
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Deposit initiation — payment engine",
    )


@wallet_router.post("/withdraw", response_model=WithdrawalResponse)
async def initiate_withdrawal(
    body: WithdrawalRequest,
    ctx: RequestContext = Depends(_get_request_context),
) -> WithdrawalResponse:
    """
    Submit a withdrawal request for compliance review and PSP processing.

    Withdrawal requests in regulated US markets (NJ, PA) are queued for
    3-day compliance review before PSP disbursement.
    KYC must be verified before any withdrawal is approved.
    """
    log.info(
        "wallet_withdrawal amount=%s currency=%s jurisdiction=%s",
        body.amount,
        body.currency,
        ctx.jurisdiction,
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Withdrawal — payment engine + compliance review",
    )


# ---------------------------------------------------------------------------
# Responsible Gaming router — /api/v1/rg
# ---------------------------------------------------------------------------

rg_router = APIRouter(prefix="/api/v1/rg", tags=["responsible-gaming"])


class DepositLimit(BaseModel):
    period: str = Field(pattern="^(daily|weekly|monthly)$")
    amount: float = Field(..., gt=0)
    currency: str


class SelfExclusionRequest(BaseModel):
    duration_days: int | None = Field(None, description="Temporary exclusion duration; None = permanent")
    reason: str | None = None


@rg_router.get("/limits")
async def get_limits(
    ctx: RequestContext = Depends(_get_request_context),
) -> dict[str, Any]:
    """Return all active responsible gaming limits for the authenticated player."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="RG limits — limits service",
    )


@rg_router.post("/limits/deposit")
async def set_deposit_limit(
    body: DepositLimit,
    ctx: RequestContext = Depends(_get_request_context),
) -> dict[str, Any]:
    """
    Set or update a deposit limit.

    Limits take effect immediately when lowering; increases are subject to a
    24-hour cooling-off period per UKGC and NJ DGE requirements.
    """
    log.info(
        "rg_deposit_limit_set period=%s amount=%s jurisdiction=%s",
        body.period,
        body.amount,
        ctx.jurisdiction,
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Deposit limit — limits service",
    )


@rg_router.post("/self-exclusion")
async def self_exclude(
    body: SelfExclusionRequest,
    ctx: RequestContext = Depends(_get_request_context),
) -> dict[str, Any]:
    """
    Self-exclude the authenticated player.

    Permanent self-exclusion is registered in the jurisdiction's exclusion
    database (GAMSTOP for UK, iCBE for NJ) and propagated to all brands
    within the operator's portfolio within 24 hours.
    """
    log.info(
        "rg_self_exclusion duration=%s jurisdiction=%s",
        body.duration_days,
        ctx.jurisdiction,
    )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Self-exclusion — exclusion service",
    )


# ---------------------------------------------------------------------------
# Compliance router — /api/v1/compliance
# ---------------------------------------------------------------------------

compliance_router = APIRouter(prefix="/api/v1/compliance", tags=["compliance"])


@compliance_router.get("/reality-check")
async def get_reality_check_status(
    ctx: RequestContext = Depends(_get_request_context),
) -> dict[str, Any]:
    """Return reality check schedule and last acknowledgement timestamp."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Reality check — session service",
    )


@compliance_router.post("/reality-check/acknowledge")
async def acknowledge_reality_check(
    ctx: RequestContext = Depends(_get_request_context),
) -> dict[str, Any]:
    """Record player acknowledgement of the reality check message."""
    log.info("rg_reality_check_ack jurisdiction=%s", ctx.jurisdiction)
    return {"acknowledged": True, "timestamp": datetime.now(tz=timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(
    title: str = "AcmetoCasino Platform API",
    version: str = os.getenv("API_VERSION", "1.0.0"),
    debug: bool = os.getenv("DEBUG", "false").lower() == "true",
) -> FastAPI:
    """
    Create and configure the FastAPI application.

    Registers all route groups, middleware, and exception handlers.

    Args:
        title:   API title for OpenAPI documentation.
        version: API version string.
        debug:   Enable debug mode (do not use in production).

    Returns:
        Configured FastAPI application.
    """
    app = FastAPI(
        title=title,
        version=version,
        debug=debug,
        docs_url="/api/docs" if debug else None,
        redoc_url="/api/redoc" if debug else None,
        openapi_url="/api/openapi.json" if debug else None,
    )

    # --- Routers ---
    app.include_router(auth_router)
    app.include_router(game_router)
    app.include_router(wallet_router)
    app.include_router(rg_router)
    app.include_router(compliance_router)

    # --- Health probe ---
    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "timestamp": datetime.now(tz=timezone.utc).isoformat()}

    # --- Request timing middleware ---
    @app.middleware("http")
    async def request_timing(request: Request, call_next: Any) -> Any:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000
        response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"
        return response

    # --- Global exception handler ---
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception path=%s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_server_error", "request_id": request.headers.get("X-Request-ID", "")},
        )

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (used by uvicorn)
# ---------------------------------------------------------------------------

app = create_app()
