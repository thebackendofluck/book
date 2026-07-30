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
main.py — National Self-Exclusion Service (FastAPI).

Provides HTTP endpoints for:
  - Checking a player against the national registry for their jurisdiction
  - Registering a player for self-exclusion (Brazil only via API)
  - Revoking a self-exclusion (Brazil only via API)
  - Querying registry health / status per jurisdiction

The batch processing logic (GamstopProcessor, SpelpausProcessor) that runs
on a schedule is in national_exclusion.py. This HTTP service handles
real-time, single-player queries triggered by operator frontends or
player portals.

Endpoints:
  GET  /check/{player_id_or_cpf}?jurisdiction=GB|SE|DK|BR
  POST /register
  POST /revoke
  GET  /status/{jurisdiction}
  GET  /health
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from models import (
    ExclusionCheck,
    ExclusionStatus,
    Jurisdiction,
    Registry,
    RegistrationRequest,
    RegistryStatusReport,
    RevocationRequest,
)
from registry_router import RegistryRouter

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))
    )
)
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="National Exclusion Service",
    description=(
        "Checks players against national self-exclusion registries "
        "(GamStop/UK, Spelpaus/SE, ROFUS/DK, BNAFAR/BR)."
    ),
    version="1.0.0",
)

_router = RegistryRouter()


# ---------------------------------------------------------------------------
# Pydantic request/response schemas
# ---------------------------------------------------------------------------

class CheckResponse(BaseModel):
    player_id:        str
    registry:         str
    is_excluded:      bool
    checked_at:       str
    exclusion_period: Optional[str] = None


class RegisterBody(BaseModel):
    player_id:    str = Field(..., description="CPF (Brazil) or national ID")
    jurisdiction: Jurisdiction
    duration:     Optional[str] = Field(
        None,
        description="permanent | 1_year | 5_years",
        examples=["permanent"],
    )
    reason: Optional[str] = None


class RevokeBody(BaseModel):
    player_id:    str
    jurisdiction: Jurisdiction
    reason:       Optional[str] = None


class RegistryStatusResponse(BaseModel):
    registry:   str
    healthy:    bool
    latency_ms: Optional[float]
    error:      Optional[str]
    checked_at: Optional[str]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _status_to_response(status: ExclusionStatus) -> CheckResponse:
    return CheckResponse(
        player_id=status.player_id,
        registry=status.registry.value,
        is_excluded=status.is_excluded,
        checked_at=status.checked_at.isoformat(),
        exclusion_period=status.exclusion_period,
    )


def _jurisdiction_to_registry(jur: Jurisdiction) -> Registry:
    return {
        Jurisdiction.GB: Registry.GAMSTOP,
        Jurisdiction.SE: Registry.SPELPAUS,
        Jurisdiction.DK: Registry.ROFUS,
        Jurisdiction.BR: Registry.BRAZIL_NATIONAL,
    }[jur]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/check/{player_id}", response_model=CheckResponse, tags=["exclusion"])
def check_exclusion(
    player_id: str,
    jurisdiction: Jurisdiction = Query(..., description="Player's jurisdiction: GB, SE, DK, BR"),
) -> CheckResponse:
    """
    Check whether a player is currently registered on their national
    self-exclusion registry.

    - **GB** → GamStop (UK Gambling Commission)
    - **SE** → Spelpaus (Spelinspektionen)
    - **DK** → ROFUS (Spillemyndigheden)
    - **BR** → BNAFAR (SEAE / Ministério da Fazenda)
    """
    log.info("check endpoint", player_id=player_id, jurisdiction=jurisdiction)
    request = ExclusionCheck(
        player_id=player_id,
        jurisdiction=jurisdiction,
        registry=_jurisdiction_to_registry(jurisdiction),
    )
    try:
        status = _router.check(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("check failed", player_id=player_id, error=str(exc))
        raise HTTPException(status_code=502, detail="Registry check failed") from exc

    return _status_to_response(status)


@app.post("/register", tags=["exclusion"])
def register_exclusion(body: RegisterBody) -> JSONResponse:
    """
    Register a player for self-exclusion via API.

    Currently only supported for **Brazil (BR)** — GamStop, Spelpaus and
    ROFUS require players to self-register via their own portals.
    """
    log.info("register endpoint", player_id=body.player_id,
             jurisdiction=body.jurisdiction)
    request = RegistrationRequest(
        player_id=body.player_id,
        jurisdiction=body.jurisdiction,
        duration=body.duration,
        reason=body.reason,
    )
    try:
        result = _router.register(request)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("register failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Registration failed") from exc

    return JSONResponse(content=result, status_code=201)


@app.post("/revoke", tags=["exclusion"])
def revoke_exclusion(body: RevokeBody) -> JSONResponse:
    """
    Revoke a player's self-exclusion via API.

    Currently only supported for **Brazil (BR)**.
    Revocations are subject to the registry's mandatory cooling-off period.
    """
    log.info("revoke endpoint", player_id=body.player_id,
             jurisdiction=body.jurisdiction)
    request = RevocationRequest(
        player_id=body.player_id,
        jurisdiction=body.jurisdiction,
        reason=body.reason,
    )
    try:
        result = _router.revoke(request)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        log.error("revoke failed", error=str(exc))
        raise HTTPException(status_code=502, detail="Revocation failed") from exc

    return JSONResponse(content=result)


@app.get("/status/{jurisdiction}", response_model=RegistryStatusResponse, tags=["health"])
def registry_status(jurisdiction: str) -> RegistryStatusResponse:
    """
    Return connectivity status for a specific registry endpoint.

    Performs a lightweight probe (not a real player check) and reports
    latency and error information.
    """
    try:
        jur = Jurisdiction(jurisdiction.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown jurisdiction: {jurisdiction}. Valid: GB, SE, DK, BR"
        )

    registry = _jurisdiction_to_registry(jur)
    # Probe: attempt a dummy check and measure latency
    start = time.monotonic()
    healthy = True
    error: Optional[str] = None

    try:
        _router.check(ExclusionCheck(
            player_id="probe",
            jurisdiction=jur,
            registry=registry,
        ))
    except Exception as exc:
        healthy = False
        error = str(exc)

    latency_ms = round((time.monotonic() - start) * 1000, 2)

    return RegistryStatusResponse(
        registry=registry.value,
        healthy=healthy,
        latency_ms=latency_ms,
        error=error,
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/health", tags=["health"])
def health_check() -> dict:
    """Liveness probe."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        reload=False,
    )
