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
Dashboard overview — real-time KPIs (active players, deposits, GGR).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from auth import TokenData, require_permission
from models import KPISnapshot

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])

# ---------------------------------------------------------------------------
# Simulated live data snapshot (replace with DB aggregation queries)
# ---------------------------------------------------------------------------


def _get_current_kpis() -> KPISnapshot:
    """
    In production this would run real-time queries against the operational DB.
    The values here are representative of a mid-size operator on an average day.
    """
    now = datetime.now(timezone.utc)
    return KPISnapshot(
        snapshot_at=now,
        brand="AcmetoCasino",
        active_players_today=1247,
        new_registrations_today=34,
        deposits_today=87450.00,
        withdrawals_today=41200.00,
        ggr_today=round(87450.00 - 41200.00, 2),
        pending_kyc_reviews=12,
        pending_withdrawals=7,
        pending_rg_actions=3,
        pending_sow_requests=5,
        open_complaints=8,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/kpis", response_model=KPISnapshot, summary="Real-time KPI snapshot")
async def get_kpis(
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> KPISnapshot:
    return _get_current_kpis()


@router.get("/summary", summary="High-level backoffice summary for the current day")
async def get_summary(
    current: TokenData = Depends(require_permission("dashboard:read")),
) -> dict:
    kpis = _get_current_kpis()
    total_pending_actions = (
        kpis.pending_kyc_reviews
        + kpis.pending_withdrawals
        + kpis.pending_rg_actions
        + kpis.pending_sow_requests
        + kpis.open_complaints
    )
    return {
        "brand": kpis.brand,
        "as_of": kpis.snapshot_at.isoformat(),
        "active_players_today": kpis.active_players_today,
        "new_registrations_today": kpis.new_registrations_today,
        "revenue": {
            "deposits_today": kpis.deposits_today,
            "withdrawals_today": kpis.withdrawals_today,
            "ggr_today": kpis.ggr_today,
        },
        "pending_actions": {
            "total": total_pending_actions,
            "kyc_reviews": kpis.pending_kyc_reviews,
            "withdrawals": kpis.pending_withdrawals,
            "rg_actions": kpis.pending_rg_actions,
            "sow_requests": kpis.pending_sow_requests,
            "complaints": kpis.open_complaints,
        },
        "operator": {
            "name": "AcmetoCasino",
            "environment": "production",
        },
    }


@router.get("/health", summary="Platform health check (no auth required)", include_in_schema=True)
async def health_check() -> dict:
    return {
        "status": "ok",
        "platform": "AcmetoCasino Backoffice Admin",
        "version": "1.0.0",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
