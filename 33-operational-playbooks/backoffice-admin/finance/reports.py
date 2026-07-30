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
Financial reports — revenue by brand, GGR, NGR, tax summaries.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import Jurisdiction, RevenueReport

router = APIRouter(prefix="/finance-reports", tags=["Finance Reports"])

# ---------------------------------------------------------------------------
# Tax rates per jurisdiction (simplified)
# ---------------------------------------------------------------------------

TAX_RATES: dict[str, float] = {
    Jurisdiction.UKGC.value: 0.21,  # Remote Gaming Duty
    Jurisdiction.MGA.value: 0.05,
    Jurisdiction.DGE.value: 0.15,
    Jurisdiction.KAHNAWAKE.value: 0.0,
    Jurisdiction.CURACAO.value: 0.02,
}

# ---------------------------------------------------------------------------
# Simulated report store
# ---------------------------------------------------------------------------

_REVENUE_REPORTS: Dict[str, dict] = {
    "FIN-001": {
        "report_id": "FIN-001",
        "brand": "AcmetoCasino",
        "jurisdiction": Jurisdiction.UKGC.value,
        "period_start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "period_end": datetime(2024, 1, 31, tzinfo=timezone.utc),
        "total_deposits": 450000.0,
        "total_withdrawals": 310000.0,
        "bonus_cost": 28000.0,
        "ggr": 140000.0,
        "ngr": 112000.0,
        "tax_rate": 0.21,
        "tax_amount": round(112000.0 * 0.21, 2),
        "active_players": 1820,
        "new_players": 145,
        "generated_at": datetime(2024, 2, 2, tzinfo=timezone.utc),
    },
}


def _compute_ngr(ggr: float, bonus_cost: float) -> float:
    return round(ggr - bonus_cost, 2)


def _compute_tax(ngr: float, jurisdiction: str) -> dict:
    rate = TAX_RATES.get(jurisdiction, 0.0)
    return {"tax_rate": rate, "tax_amount": round(ngr * rate, 2)}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/", response_model=List[RevenueReport], summary="List revenue reports")
async def list_reports(
    brand: Optional[str] = Query(None),
    jurisdiction: Optional[Jurisdiction] = Query(None),
    current: TokenData = Depends(require_permission("finance:read")),
) -> List[RevenueReport]:
    results = list(_REVENUE_REPORTS.values())
    if brand:
        results = [r for r in results if r["brand"].lower() == brand.lower()]
    if jurisdiction:
        results = [r for r in results if r["jurisdiction"] == jurisdiction.value]
    return [RevenueReport(**r) for r in results]


@router.post("/generate", response_model=RevenueReport, summary="Generate a revenue report for a period")
async def generate_revenue_report(
    brand: str = Query("AcmetoCasino"),
    jurisdiction: Jurisdiction = Query(...),
    period_start: datetime = Query(...),
    period_end: datetime = Query(...),
    total_deposits: float = Query(..., ge=0),
    total_withdrawals: float = Query(..., ge=0),
    bonus_cost: float = Query(..., ge=0),
    active_players: int = Query(..., ge=0),
    new_players: int = Query(..., ge=0),
    current: TokenData = Depends(require_permission("finance:write")),
) -> RevenueReport:
    if period_start >= period_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_start must be before period_end",
        )
    ggr = round(total_deposits - total_withdrawals, 2)
    ngr = _compute_ngr(ggr, bonus_cost)
    tax_data = _compute_tax(ngr, jurisdiction.value)
    report_id = f"FIN-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "report_id": report_id,
        "brand": brand,
        "jurisdiction": jurisdiction.value,
        "period_start": period_start,
        "period_end": period_end,
        "total_deposits": total_deposits,
        "total_withdrawals": total_withdrawals,
        "bonus_cost": bonus_cost,
        "ggr": ggr,
        "ngr": ngr,
        "tax_rate": tax_data["tax_rate"],
        "tax_amount": tax_data["tax_amount"],
        "active_players": active_players,
        "new_players": new_players,
        "generated_at": datetime.now(timezone.utc),
    }
    _REVENUE_REPORTS[report_id] = raw
    return RevenueReport(**raw)


@router.get("/{report_id}", response_model=RevenueReport, summary="Get a specific revenue report")
async def get_report(
    report_id: str,
    current: TokenData = Depends(require_permission("finance:read")),
) -> RevenueReport:
    raw = _REVENUE_REPORTS.get(report_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return RevenueReport(**raw)


@router.get("/summary/ggr", summary="Aggregated GGR summary across all brands/jurisdictions")
async def ggr_summary(
    current: TokenData = Depends(require_permission("finance:read")),
) -> dict:
    reports = list(_REVENUE_REPORTS.values())
    return {
        "total_ggr": round(sum(r["ggr"] for r in reports), 2),
        "total_ngr": round(sum(r["ngr"] for r in reports), 2),
        "total_tax": round(sum(r["tax_amount"] for r in reports), 2),
        "total_deposits": round(sum(r["total_deposits"] for r in reports), 2),
        "total_withdrawals": round(sum(r["total_withdrawals"] for r in reports), 2),
        "report_count": len(reports),
        "as_of": datetime.now(timezone.utc).isoformat(),
    }
