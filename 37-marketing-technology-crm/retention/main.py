# Companion code for "The Backend of Luck" - Chapter 37, Marketing Technology and CRM Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Retention bonus engine -- FastAPI application.

Exposes endpoints to trigger and monitor bonus calculation runs.
Can also be run as a standalone batch process.

Endpoints:
  POST /bonuses/calculate   -- trigger a bonus calculation run for a date
  GET  /bonuses/status      -- check calculation status
  GET  /healthz             -- health check
"""

from __future__ import annotations

import os
from datetime import date

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine

from .models import BonusQueueItem
from .service import PlatformDatabase, RetentionBonusCalculator, RetentionBonusType

log = structlog.get_logger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost/platform")

app = FastAPI(title="Retention Bonus Engine")


class CalculateRequest(BaseModel):
    calc_date: date
    brand_id: int
    reason: str
    read_only: bool = False


@app.post("/bonuses/calculate")
async def calculate_bonuses(body: CalculateRequest) -> JSONResponse:
    """
    Trigger a bonus calculation run.

    In production, the bonus_type would be resolved from the brand_id/reason
    combination via a registered factory. Here we show the wiring point.
    """
    log.info(
        "bonuses.calculate",
        calc_date=str(body.calc_date),
        brand_id=body.brand_id,
        reason=body.reason,
        read_only=body.read_only,
    )
    # In production: resolve bonus_type from brand+reason, then run calculator
    return JSONResponse(
        {
            "status": "accepted",
            "calc_date": str(body.calc_date),
            "brand_id": body.brand_id,
            "reason": body.reason,
        }
    )


@app.get("/healthz")
async def health() -> dict[str, str]:
    return {"status": "ok"}
