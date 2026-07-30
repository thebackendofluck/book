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
FastAPI application for the Supplier Integration Control Plane.

This service is the operational API for managing supplier integrations.
It is consumed by the platform back-office, automation scripts, and the
health-check / alerting pipeline.

Route groups
------------
/suppliers                    — CRUD for supplier records
/suppliers/{id}/health        — Single-supplier health check
/suppliers/health             — Health check for all suppliers
/suppliers/{id}/capabilities  — Capability matrix for a supplier
/suppliers/{id}/maintenance   — Schedule and list maintenance windows
/suppliers/degraded           — List currently degraded / unreachable suppliers
/health                       — Service liveness probe

Authentication
--------------
In production, add OAuth2 / API-key middleware here.
The current implementation accepts all requests for simplicity.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Path, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from credential_manager import CredentialManager, credential_manager as default_cred_mgr
from health_monitor import HealthMonitor, monitor as default_monitor
from maintenance import (
    MaintenanceManager,
    OverlappingMaintenanceError,
    maintenance_manager as default_maint_mgr,
)
from models import (
    CallbackPolicy,
    Credentials,
    MaintenanceWindow,
    SupplierCapabilityMatrix,
    SupplierRecord,
    SupplierStatus,
    SupplierType,
    WalletModel,
)
from registry import (
    DuplicateSupplierError,
    SupplierNotFoundError,
    SupplierRegistry,
    registry as default_registry,
)

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
    logger.info("Supplier Control Plane starting up")
    yield
    logger.info("Supplier Control Plane shutting down")


app = FastAPI(
    title="Supplier Integration Control Plane",
    description=(
        "Operational API for managing supplier integrations: "
        "registry CRUD, health monitoring, maintenance windows, "
        "capability matrices, and credential management."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Dependency accessors (replaceable in tests)
# ---------------------------------------------------------------------------


def get_registry() -> SupplierRegistry:
    return default_registry


def get_monitor() -> HealthMonitor:
    return default_monitor


def get_maintenance_manager() -> MaintenanceManager:
    return default_maint_mgr


def get_credential_manager() -> CredentialManager:
    return default_cred_mgr


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class CapabilityMatrixIn(BaseModel):
    games: set[str] = Field(default_factory=set)
    currencies: set[str] = Field(default_factory=set)
    jurisdictions: set[str] = Field(default_factory=set)
    wallet_model: str = WalletModel.SEAMLESS.value
    rtp_certified: bool = False
    max_bet_usd: float = 0.0


class SupplierCreateRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    type: str
    contact_email: str = ""
    capabilities: Optional[CapabilityMatrixIn] = None


class SupplierUpdateRequest(BaseModel):
    name: Optional[str] = None
    contact_email: Optional[str] = None
    capabilities: Optional[CapabilityMatrixIn] = None


class StatusUpdateRequest(BaseModel):
    status: str


class MaintenanceScheduleRequest(BaseModel):
    start: datetime
    end: datetime
    reason: str
    created_by: str = "api"


class MaintenanceOut(BaseModel):
    id: str
    supplier_id: str
    start: datetime
    end: datetime
    reason: str
    created_by: str
    is_active: bool


class SupplierOut(BaseModel):
    id: str
    name: str
    type: str
    status: str
    contact_email: str
    created_at: datetime
    updated_at: datetime
    capabilities: Optional[dict[str, Any]] = None


class HealthOut(BaseModel):
    supplier_id: str
    status: str
    last_check: datetime
    latency_ms: float
    error_rate: float
    message: str
    consecutive_failures: int


# ---------------------------------------------------------------------------
# Helper: convert domain objects to Pydantic response models
# ---------------------------------------------------------------------------


def _supplier_out(record: SupplierRecord) -> SupplierOut:
    cap = None
    if record.capabilities:
        c = record.capabilities
        cap = {
            "games": list(c.games),
            "currencies": list(c.currencies),
            "jurisdictions": list(c.jurisdictions),
            "wallet_model": c.wallet_model.value,
            "rtp_certified": c.rtp_certified,
            "max_bet_usd": c.max_bet_usd,
        }
    return SupplierOut(
        id=record.id,
        name=record.name,
        type=record.type.value,
        status=record.status.value,
        contact_email=record.contact_email,
        created_at=record.created_at,
        updated_at=record.updated_at,
        capabilities=cap,
    )


def _health_out(health) -> HealthOut:
    return HealthOut(
        supplier_id=health.supplier_id,
        status=health.status.value,
        last_check=health.last_check,
        latency_ms=health.latency_ms,
        error_rate=health.error_rate,
        message=health.message,
        consecutive_failures=health.consecutive_failures,
    )


def _maintenance_out(w: MaintenanceWindow) -> MaintenanceOut:
    return MaintenanceOut(
        id=w.id,
        supplier_id=w.supplier_id,
        start=w.start,
        end=w.end,
        reason=w.reason,
        created_by=w.created_by,
        is_active=w.is_active(),
    )


def _build_capabilities(cap_in: Optional[CapabilityMatrixIn], supplier_id: str) -> Optional[SupplierCapabilityMatrix]:
    if cap_in is None:
        return None
    return SupplierCapabilityMatrix(
        supplier_id=supplier_id,
        games=cap_in.games,
        currencies=cap_in.currencies,
        jurisdictions=cap_in.jurisdictions,
        wallet_model=WalletModel(cap_in.wallet_model),
        rtp_certified=cap_in.rtp_certified,
        max_bet_usd=cap_in.max_bet_usd,
    )


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(SupplierNotFoundError)
async def supplier_not_found_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"Supplier not found: {exc}"},
    )


@app.exception_handler(DuplicateSupplierError)
async def duplicate_supplier_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


@app.exception_handler(OverlappingMaintenanceError)
async def overlapping_maintenance_handler(request, exc):
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": str(exc)},
    )


# ---------------------------------------------------------------------------
# Liveness probe
# ---------------------------------------------------------------------------


@app.get("/health", tags=["ops"])
async def service_health():
    """Liveness probe — always returns 200 while the process is running."""
    return {
        "status": "ok",
        "service": "supplier-control-plane",
        "timestamp": time.time(),
        "supplier_count": len(get_registry()),
    }


# ---------------------------------------------------------------------------
# Supplier CRUD
# ---------------------------------------------------------------------------


@app.post("/suppliers", response_model=SupplierOut, status_code=status.HTTP_201_CREATED, tags=["suppliers"])
async def create_supplier(payload: SupplierCreateRequest):
    """Register a new supplier."""
    try:
        supplier_type = SupplierType(payload.type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid supplier type: {payload.type!r}. "
                   f"Valid values: {[t.value for t in SupplierType]}",
        )

    record = SupplierRecord(
        id=payload.id,
        name=payload.name,
        type=supplier_type,
        contact_email=payload.contact_email,
        capabilities=_build_capabilities(payload.capabilities, payload.id),
    )
    reg = get_registry()
    registered = reg.register_supplier(record)
    return _supplier_out(registered)


@app.get("/suppliers", response_model=list[SupplierOut], tags=["suppliers"])
async def list_suppliers(
    type: Optional[str] = Query(None, description="Filter by supplier type"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status"),
    jurisdiction: Optional[str] = Query(None),
    currency: Optional[str] = Query(None),
):
    """List all suppliers, with optional filters."""
    filters: dict[str, Any] = {}
    if type:
        filters["type"] = type
    if status_filter:
        filters["status"] = status_filter
    if jurisdiction:
        filters["jurisdiction"] = jurisdiction
    if currency:
        filters["currency"] = currency

    reg = get_registry()
    records = reg.list_suppliers(filters or None)
    return [_supplier_out(r) for r in records]


@app.get("/suppliers/{supplier_id}", response_model=SupplierOut, tags=["suppliers"])
async def get_supplier(supplier_id: str = Path(...)):
    """Retrieve a single supplier by ID."""
    reg = get_registry()
    record = reg.get_supplier(supplier_id)
    return _supplier_out(record)


@app.patch("/suppliers/{supplier_id}", response_model=SupplierOut, tags=["suppliers"])
async def update_supplier(
    payload: SupplierUpdateRequest,
    supplier_id: str = Path(...),
):
    """Update mutable fields of a supplier record."""
    reg = get_registry()
    record = reg.get_supplier(supplier_id)

    if payload.name is not None:
        record.name = payload.name
    if payload.contact_email is not None:
        record.contact_email = payload.contact_email
    if payload.capabilities is not None:
        record.capabilities = _build_capabilities(payload.capabilities, supplier_id)

    record.updated_at = datetime.now(timezone.utc)
    reg.replace_supplier(record)
    return _supplier_out(record)


@app.delete("/suppliers/{supplier_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["suppliers"])
async def delete_supplier(supplier_id: str = Path(...)):
    """Deregister a supplier."""
    reg = get_registry()
    reg.get_supplier(supplier_id)  # raises 404 if not found
    reg.deregister_supplier(supplier_id)


@app.patch("/suppliers/{supplier_id}/status", response_model=SupplierOut, tags=["suppliers"])
async def update_supplier_status(
    payload: StatusUpdateRequest,
    supplier_id: str = Path(...),
):
    """Update the operational status of a supplier."""
    try:
        new_status = SupplierStatus(payload.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid status: {payload.status!r}. "
                   f"Valid values: {[s.value for s in SupplierStatus]}",
        )
    reg = get_registry()
    record = reg.update_status(supplier_id, new_status)
    return _supplier_out(record)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


@app.get("/suppliers/{supplier_id}/health", response_model=HealthOut, tags=["health"])
async def get_supplier_health(supplier_id: str = Path(...)):
    """Run a health check against a single supplier and return the result."""
    mon = get_monitor()
    health = mon.check_supplier_health(supplier_id)
    return _health_out(health)


@app.get("/suppliers/health", response_model=dict[str, HealthOut], tags=["health"])
async def get_all_health():
    """Run health checks against all registered suppliers."""
    mon = get_monitor()
    results = mon.check_all_suppliers()
    return {sid: _health_out(h) for sid, h in results.items()}


@app.get("/suppliers/degraded", response_model=list[HealthOut], tags=["health"])
async def get_degraded_suppliers():
    """
    Return suppliers currently in DEGRADED or UNREACHABLE state.

    Uses the last cached health reading; call GET /suppliers/health
    first to refresh.
    """
    mon = get_monitor()
    degraded = mon.detect_degraded_suppliers()
    return [_health_out(h) for h in degraded]


# ---------------------------------------------------------------------------
# Capability matrix
# ---------------------------------------------------------------------------


@app.get("/suppliers/{supplier_id}/capabilities", tags=["suppliers"])
async def get_capabilities(supplier_id: str = Path(...)):
    """Return the capability matrix for a supplier."""
    reg = get_registry()
    matrix = reg.get_capability_matrix(supplier_id)
    return {
        "supplier_id": matrix.supplier_id,
        "games": list(matrix.games),
        "currencies": list(matrix.currencies),
        "jurisdictions": list(matrix.jurisdictions),
        "wallet_model": matrix.wallet_model.value,
        "rtp_certified": matrix.rtp_certified,
        "max_bet_usd": matrix.max_bet_usd,
    }


# ---------------------------------------------------------------------------
# Maintenance windows
# ---------------------------------------------------------------------------


@app.post(
    "/suppliers/{supplier_id}/maintenance",
    response_model=MaintenanceOut,
    status_code=status.HTTP_201_CREATED,
    tags=["maintenance"],
)
async def schedule_maintenance(
    payload: MaintenanceScheduleRequest,
    supplier_id: str = Path(...),
):
    """Schedule a maintenance window for a supplier."""
    window = MaintenanceWindow(
        supplier_id=supplier_id,
        start=payload.start,
        end=payload.end,
        reason=payload.reason,
        created_by=payload.created_by,
    )
    mgr = get_maintenance_manager()
    scheduled = mgr.schedule_maintenance(supplier_id, window)
    return _maintenance_out(scheduled)


@app.get(
    "/suppliers/{supplier_id}/maintenance",
    response_model=list[MaintenanceOut],
    tags=["maintenance"],
)
async def list_maintenance(supplier_id: str = Path(...)):
    """List all maintenance windows for a supplier (past, present, future)."""
    get_registry().get_supplier(supplier_id)  # validate exists
    mgr = get_maintenance_manager()
    windows = mgr.list_maintenance(supplier_id)
    return [_maintenance_out(w) for w in windows]


@app.delete(
    "/suppliers/{supplier_id}/maintenance/{window_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["maintenance"],
)
async def cancel_maintenance(
    supplier_id: str = Path(...),
    window_id: str = Path(...),
):
    """Cancel a scheduled maintenance window."""
    mgr = get_maintenance_manager()
    mgr.cancel_maintenance(window_id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import os
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8090")),
        workers=int(os.environ.get("WORKERS", "2")),
        log_level="info",
    )
