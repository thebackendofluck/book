# Companion code for "The Backend of Luck" - Chapter 19, Anti-Fraud System Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
main.py – FastAPI service for the risk-alerting service.

Exposes REST endpoints for:
  - Alert management (list, update status, update description)
  - Manual alert submission (for testing)
  - Health check

The consumer loop is started as a background task when the application
is launched via `uvicorn main:app`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict, List, Optional

import structlog

from fastapi import FastAPI, HTTPException, Path, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .alert_engine import evaluate_deposit_rules, get_alert_description, set_alert_description
from .kafka_consumer import EventStore, process_deposit_event
from .models import (
    AlertDescription,
    AlertListResponse,
    AlertPriority,
    AlertStatus,
    DepositEvent,
    PaymentStatusChangeEvent,
    RiskAlert,
    StoredAlert,
    UpdateAlertDescriptionRequest,
    UpdateAlertRequest,
)
from .notification import NotificationDispatcher

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(os.environ.get("LOG_LEVEL", "INFO"))
    )
)
log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Shared application state
# ---------------------------------------------------------------------------

_store = EventStore()
_dispatcher = NotificationDispatcher()

# In-memory alert repository (replace with PostgreSQL in production)
_alerts: Dict[str, StoredAlert] = {}


# ---------------------------------------------------------------------------
# Background consumer task
# ---------------------------------------------------------------------------


async def _consumer_task() -> None:
    """Run the Kafka consumer in a thread pool so it doesn't block the event loop."""
    loop = asyncio.get_running_loop()
    from .kafka_consumer import run_consumer

    await loop.run_in_executor(None, lambda: run_consumer(store=_store, dispatcher=_dispatcher))


# ---------------------------------------------------------------------------
# App factory / lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start Kafka consumer on startup; clean up on shutdown."""
    task = asyncio.create_task(_consumer_task())
    log.info("risk_alerting_starting")
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        log.info("kafka_consumer_task_cancelled")


app = FastAPI(
    title="Risk Alerting Service",
    description=(
        "Monitors payment velocity, deposit/withdrawal patterns, and shared "
        "payment instruments to generate fraud and risk alerts."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/health", tags=["health"])
async def health() -> Dict:
    return {"status": "ok", "service": "risk-alerting"}


# ---------------------------------------------------------------------------
# Alert CRUD
# ---------------------------------------------------------------------------


@app.get("/alerts", response_model=AlertListResponse, tags=["alerts"])
async def list_alerts(
    status: Optional[AlertStatus] = Query(None),
    user_id: Optional[str] = Query(None),
    alert_name: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AlertListResponse:
    """Return stored alerts with optional filtering."""
    results = list(_alerts.values())
    if status:
        results = [a for a in results if a.status == status]
    if user_id:
        results = [a for a in results if a.user_id == user_id]
    if alert_name:
        results = [a for a in results if a.alert_name == alert_name]
    total = len(results)
    page = results[offset: offset + limit]
    return AlertListResponse(alerts=page, total=total)


@app.get("/alerts/{alert_id}", response_model=StoredAlert, tags=["alerts"])
async def get_alert(alert_id: str = Path(...)) -> StoredAlert:
    alert = _alerts.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@app.put("/alerts/{alert_id}", response_model=StoredAlert, tags=["alerts"])
async def update_alert(
    body: UpdateAlertRequest,
    alert_id: str = Path(...),
) -> StoredAlert:
    """Update alert status, agent assignment, or comment."""
    alert = _alerts.get(alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if body.status is not None:
        alert.status = body.status
    if body.agent_id is not None:
        alert.agent_id = body.agent_id
    if body.comment is not None:
        alert.comment = body.comment
    alert.updated_at = datetime.now(timezone.utc)
    _alerts[alert_id] = alert
    return alert


# ---------------------------------------------------------------------------
# Alert descriptions
# ---------------------------------------------------------------------------


@app.get("/alert-descriptions/{alert_name}", response_model=AlertDescription, tags=["descriptions"])
async def get_alert_desc(alert_name: str = Path(...)) -> AlertDescription:
    desc = get_alert_description(alert_name)
    if not desc:
        raise HTTPException(status_code=404, detail="Alert description not found")
    return desc


@app.put("/alert-descriptions/{alert_name}", response_model=AlertDescription, tags=["descriptions"])
async def update_alert_description(
    body: UpdateAlertDescriptionRequest,
    alert_name: str = Path(...),
) -> AlertDescription:
    """Update configurable parameters for an alert rule."""
    desc = get_alert_description(alert_name)
    if not desc:
        raise HTTPException(status_code=404, detail="Alert description not found")
    if body.title is not None:
        desc = desc.model_copy(update={"title": body.title})
    if body.description is not None:
        desc = desc.model_copy(update={"description": body.description})
    if body.priority is not None:
        desc = desc.model_copy(update={"priority": body.priority})
    if body.enabled is not None:
        desc = desc.model_copy(update={"enabled": body.enabled})
    if body.threshold is not None:
        desc = desc.model_copy(update={"threshold": body.threshold})
    if body.window_minutes is not None:
        desc = desc.model_copy(update={"window_minutes": body.window_minutes})
    set_alert_description(desc)
    return desc


# ---------------------------------------------------------------------------
# Manual alert submission endpoint (for testing / back-office use)
# ---------------------------------------------------------------------------


class ManualAlertRequest(BaseModel):
    user_id: int
    alert_name: str
    message: str
    priority: AlertPriority = AlertPriority.P3
    details: Dict[str, str] = {}


@app.post("/alerts/manual", response_model=StoredAlert, status_code=201, tags=["alerts"])
async def submit_manual_alert(body: ManualAlertRequest) -> StoredAlert:
    """Create an alert manually (useful for back-office operations)."""
    risk_alert = RiskAlert(
        message=body.message,
        alert_name=body.alert_name,
        priority=body.priority,
        details=body.details,
        user_ids=[str(body.user_id)],
    )
    stored = StoredAlert(
        id=risk_alert.id,
        alert_name=risk_alert.alert_name,
        message=risk_alert.message,
        details=risk_alert.details,
        user_id=str(body.user_id),
        priority=risk_alert.priority.value if risk_alert.priority else AlertPriority.P5.value,
        status=AlertStatus.NEW,
    )
    _alerts[stored.id] = stored
    _dispatcher.dispatch(risk_alert)
    return stored
