# Companion code for "The Backend of Luck" - Chapter 33, Operational Playbooks.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
AcmetoCasino Backoffice Admin Platform
FastAPI application entry point — unifies all modules.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from auth import TokenRequest, TokenResponse, login

# ---------------------------------------------------------------------------
# Import all sub-routers
# ---------------------------------------------------------------------------
from players.service import router as players_router
from players.kyc import router as kyc_router
from players.affordability import router as affordability_router
from players.dormant import router as dormant_router

from compliance.regulatory_reports import router as regulatory_reports_router
from compliance.sow_tracker import router as sow_tracker_router
from compliance.rg_audit import router as rg_audit_router

from finance.withdrawal_queue import router as withdrawal_queue_router
from finance.cashout_processor import router as cashout_processor_router
from finance.reports import router as finance_reports_router

from security.access_control import router as access_control_router
from security.audit_log import router as audit_log_router
from security.ip_blocking import router as ip_blocking_router

from crm.player_segments import router as player_segments_router
from crm.campaign_manager import router as campaign_manager_router
from crm.bonus_management import router as bonus_management_router

from dashboard.overview import router as overview_router
from dashboard.alerts import router as alerts_router

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="AcmetoCasino Backoffice Admin",
    description=(
        "Unified backoffice administration platform covering player management, "
        "KYC, compliance reporting, finance, CRM, and responsible gaming. "
        "All endpoints require JWT authentication. Role-based access enforced throughout."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# ---------------------------------------------------------------------------
# CORS (restrict to internal admin origins in production)
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "https://admin.acmetocasino.internal"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An unexpected error occurred",
            "type": type(exc).__name__,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# Authentication endpoint
# ---------------------------------------------------------------------------


@app.post("/auth/token", response_model=TokenResponse, tags=["Auth"], summary="Obtain JWT access token")
async def get_token(request_data: TokenRequest) -> TokenResponse:
    return login(request_data)


# ---------------------------------------------------------------------------
# Include all routers
# ---------------------------------------------------------------------------

# Players
app.include_router(players_router)
app.include_router(kyc_router)
app.include_router(affordability_router)
app.include_router(dormant_router)

# Compliance
app.include_router(regulatory_reports_router)
app.include_router(sow_tracker_router)
app.include_router(rg_audit_router)

# Finance
app.include_router(withdrawal_queue_router)
app.include_router(cashout_processor_router)
app.include_router(finance_reports_router)

# Security
app.include_router(access_control_router)
app.include_router(audit_log_router)
app.include_router(ip_blocking_router)

# CRM
app.include_router(player_segments_router)
app.include_router(campaign_manager_router)
app.include_router(bonus_management_router)

# Dashboard
app.include_router(overview_router)
app.include_router(alerts_router)

# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "platform": "AcmetoCasino Backoffice Admin",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/dashboard/health",
    }
