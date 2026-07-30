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
Batch cashout processor — process multiple approved withdrawals in one operation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from auth import AdminRole, TokenData, require_permission, require_roles

router = APIRouter(prefix="/cashouts", tags=["Cashout Processor"])

# ---------------------------------------------------------------------------
# Batch cashout store
# ---------------------------------------------------------------------------

_BATCH_JOBS: Dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Simulated payment gateway result
# ---------------------------------------------------------------------------


def _simulate_payment_gateway(withdrawal_id: str, amount: float) -> dict:
    """Simulate a payment gateway call. Returns success for most transactions."""
    # Simulate failure for amounts over 10,000
    if amount > 10000:
        return {"status": "failed", "gateway_ref": None, "error": "Limit exceeded"}
    gateway_ref = f"GW-{uuid.uuid4().hex[:8].upper()}"
    return {"status": "success", "gateway_ref": gateway_ref, "error": None}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/batch", summary="Create and execute a batch cashout job")
async def run_batch_cashout(
    withdrawal_ids: List[str],
    dry_run: bool = Query(False),
    current: TokenData = Depends(require_permission("finance:approve")),
) -> dict:
    if not withdrawal_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one withdrawal_id is required",
        )
    if len(withdrawal_ids) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Batch limit is 200 withdrawals per job",
        )

    batch_id = f"BATCH-{uuid.uuid4().hex[:8].upper()}"
    started_at = datetime.now(timezone.utc)
    results = []

    # Import here to avoid circular imports in the module-level namespace
    from finance.withdrawal_queue import _WITHDRAWALS

    for wid in withdrawal_ids:
        raw = _WITHDRAWALS.get(wid)
        if not raw:
            results.append({"withdrawal_id": wid, "status": "error", "reason": "Not found"})
            continue
        if raw["status"] != "approved":
            results.append({
                "withdrawal_id": wid,
                "status": "skipped",
                "reason": f"Not in approved state (current: {raw['status']})",
            })
            continue

        if dry_run:
            results.append({
                "withdrawal_id": wid,
                "player_id": raw["player_id"],
                "amount": raw["amount"],
                "status": "dry_run_ok",
                "gateway_ref": None,
            })
            continue

        gw = _simulate_payment_gateway(wid, raw["amount"])
        if gw["status"] == "success":
            raw["status"] = "completed"
            results.append({
                "withdrawal_id": wid,
                "player_id": raw["player_id"],
                "amount": raw["amount"],
                "status": "completed",
                "gateway_ref": gw["gateway_ref"],
            })
        else:
            raw["status"] = "failed"
            results.append({
                "withdrawal_id": wid,
                "player_id": raw["player_id"],
                "amount": raw["amount"],
                "status": "failed",
                "reason": gw["error"],
            })

    success_count = sum(1 for r in results if r["status"] in ("completed", "dry_run_ok"))
    fail_count = sum(1 for r in results if r["status"] in ("failed", "error"))
    total_processed_value = sum(
        r["amount"] for r in results
        if r.get("amount") and r["status"] in ("completed", "dry_run_ok")
    )

    job = {
        "batch_id": batch_id,
        "started_at": started_at.isoformat(),
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "executed_by": current.username,
        "dry_run": dry_run,
        "total_requested": len(withdrawal_ids),
        "success": success_count,
        "failed": fail_count,
        "skipped": len(results) - success_count - fail_count,
        "total_value_processed_gbp": round(total_processed_value, 2),
        "results": results,
    }
    _BATCH_JOBS[batch_id] = job
    return job


@router.get("/batch/{batch_id}", summary="Get results of a batch cashout job")
async def get_batch_job(
    batch_id: str,
    current: TokenData = Depends(require_permission("finance:read")),
) -> dict:
    job = _BATCH_JOBS.get(batch_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Batch job not found")
    return job


@router.get("/batch", summary="List all batch cashout jobs")
async def list_batch_jobs(
    current: TokenData = Depends(require_permission("finance:read")),
) -> List[dict]:
    return [
        {k: v for k, v in job.items() if k != "results"}
        for job in _BATCH_JOBS.values()
    ]
