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
Regulatory report generation per jurisdiction (UKGC, MGA, DGE).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import TokenData, require_permission
from models import Jurisdiction, RegulatoryReport

router = APIRouter(prefix="/regulatory-reports", tags=["Regulatory Reports"])

# ---------------------------------------------------------------------------
# Report type catalogue per jurisdiction
# ---------------------------------------------------------------------------

REPORT_CATALOGUE: dict[str, List[str]] = {
    Jurisdiction.UKGC.value: [
        "monthly_gambling_returns",
        "rg_data_return",
        "aml_suspicious_activity",
        "social_responsibility_levy",
        "problem_gambling_prevalence",
    ],
    Jurisdiction.MGA.value: [
        "monthly_player_data",
        "quarterly_revenue_return",
        "aml_compliance_report",
        "player_fund_protection",
    ],
    Jurisdiction.DGE.value: [
        "monthly_gross_revenue",
        "patron_dispute_log",
        "exclusion_list_submission",
        "internet_gaming_report",
    ],
}

# ---------------------------------------------------------------------------
# Simulated report store
# ---------------------------------------------------------------------------

_REPORTS: Dict[str, dict] = {
    "RPT-001": {
        "report_id": "RPT-001",
        "jurisdiction": Jurisdiction.UKGC.value,
        "report_type": "monthly_gambling_returns",
        "period_start": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "period_end": datetime(2024, 1, 31, tzinfo=timezone.utc),
        "generated_at": datetime(2024, 2, 5, tzinfo=timezone.utc),
        "generated_by": "compliance_agent",
        "status": "submitted",
        "file_path": "/reports/ukgc/2024-01-monthly.csv",
        "submission_reference": "UKGC-2024-01-ACC",
    },
}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/catalogue", summary="List available report types per jurisdiction")
async def list_report_types(
    jurisdiction: Optional[Jurisdiction] = Query(None),
    current: TokenData = Depends(require_permission("compliance:read")),
) -> dict:
    if jurisdiction:
        return {jurisdiction.value: REPORT_CATALOGUE.get(jurisdiction.value, [])}
    return REPORT_CATALOGUE


@router.get("/", response_model=List[RegulatoryReport], summary="List generated regulatory reports")
async def list_reports(
    jurisdiction: Optional[Jurisdiction] = Query(None),
    report_type: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    current: TokenData = Depends(require_permission("compliance:read")),
) -> List[RegulatoryReport]:
    results = list(_REPORTS.values())
    if jurisdiction:
        results = [r for r in results if r["jurisdiction"] == jurisdiction.value]
    if report_type:
        results = [r for r in results if r["report_type"] == report_type]
    if status_filter:
        results = [r for r in results if r["status"] == status_filter]
    return [RegulatoryReport(**r) for r in results]


@router.post("/generate", response_model=RegulatoryReport, summary="Generate a new regulatory report")
async def generate_report(
    jurisdiction: Jurisdiction,
    report_type: str,
    period_start: datetime,
    period_end: datetime,
    current: TokenData = Depends(require_permission("compliance:write")),
) -> RegulatoryReport:
    available = REPORT_CATALOGUE.get(jurisdiction.value, [])
    if report_type not in available:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Report type '{report_type}' not available for {jurisdiction.value}. "
                   f"Available: {available}",
        )
    if period_start >= period_end:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="period_start must be before period_end",
        )
    report_id = f"RPT-{uuid.uuid4().hex[:6].upper()}"
    raw = {
        "report_id": report_id,
        "jurisdiction": jurisdiction.value,
        "report_type": report_type,
        "period_start": period_start,
        "period_end": period_end,
        "generated_at": datetime.now(timezone.utc),
        "generated_by": current.username,
        "status": "draft",
        "file_path": f"/reports/{jurisdiction.value.lower()}/{report_id}.csv",
        "submission_reference": None,
    }
    _REPORTS[report_id] = raw
    return RegulatoryReport(**raw)


@router.patch("/{report_id}/submit", summary="Mark a report as submitted to the regulator")
async def submit_report(
    report_id: str,
    submission_reference: str = Query(..., min_length=3),
    current: TokenData = Depends(require_permission("compliance:write")),
) -> RegulatoryReport:
    raw = _REPORTS.get(report_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    if raw["status"] == "submitted":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Report already submitted")
    raw["status"] = "submitted"
    raw["submission_reference"] = submission_reference
    return RegulatoryReport(**raw)


@router.get("/{report_id}", response_model=RegulatoryReport, summary="Get a regulatory report")
async def get_report(
    report_id: str,
    current: TokenData = Depends(require_permission("compliance:read")),
) -> RegulatoryReport:
    raw = _REPORTS.get(report_id)
    if not raw:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return RegulatoryReport(**raw)
