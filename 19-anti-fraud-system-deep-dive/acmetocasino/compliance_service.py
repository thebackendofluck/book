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
Compliance service: KYC verification and AML monitoring.
"""

import logging
import uuid
from decimal import Decimal
from typing import Any

from app.database import get_cursor
from app.events.publisher import CHANNELS, publish_event
from app.metrics import aml_alerts_total, kyc_checks_total

logger = logging.getLogger(__name__)


# ---------- KYC ----------

def get_pending_kyc(limit: int = 50) -> list[dict[str, Any]]:
    """Retrieve all pending KYC checks, newest first."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT id, player_id, document_type, document_ref,
                   status, reviewer_id, notes, submitted_at, reviewed_at
            FROM kyc_checks
            WHERE status = 'pending'
            ORDER BY submitted_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def submit_kyc(
    player_id: uuid.UUID,
    document_type: str,
    document_ref: str,
) -> dict[str, Any]:
    """Submit a KYC document for verification."""
    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO kyc_checks (player_id, document_type, document_ref)
            VALUES (%s, %s, %s)
            RETURNING id, player_id, document_type, document_ref,
                      status, reviewer_id, notes, submitted_at, reviewed_at
            """,
            (str(player_id), document_type, document_ref),
        )
        check = dict(cur.fetchone())

    kyc_checks_total.labels(status="pending").inc()

    publish_event(
        CHANNELS["compliance"],
        "kyc.submitted",
        {"player_id": str(player_id), "check_id": str(check["id"]), "document_type": document_type},
    )
    logger.info("KYC submitted for player %s: %s", player_id, document_type)
    return check


def verify_kyc(
    player_id: uuid.UUID,
    kyc_status: str,
    reviewer_id: uuid.UUID,
    notes: str | None = None,
) -> dict[str, Any]:
    """
    Verify or reject the latest KYC check for a player.
    Also updates the player's kyc_status.
    """
    if kyc_status not in ("approved", "rejected"):
        raise ValueError("Status must be 'approved' or 'rejected'")

    with get_cursor() as cur:
        # Update the latest pending KYC check (PostgreSQL subquery for LIMIT)
        cur.execute(
            """
            UPDATE kyc_checks
            SET status = %s, reviewer_id = %s, notes = %s, reviewed_at = now()
            WHERE id = (
                SELECT id FROM kyc_checks
                WHERE player_id = %s AND status = 'pending'
                ORDER BY submitted_at DESC
                LIMIT 1
            )
            RETURNING id, player_id, document_type, document_ref,
                      status, reviewer_id, notes, submitted_at, reviewed_at
            """,
            (kyc_status, str(reviewer_id), notes, str(player_id)),
        )
        check = cur.fetchone()
        if check is None:
            raise ValueError("No pending KYC check found for this player")
        check = dict(check)

        # Update player kyc_status
        new_player_status = "verified" if kyc_status == "approved" else "rejected"
        cur.execute(
            "UPDATE players SET kyc_status = %s, updated_at = now() WHERE id = %s",
            (new_player_status, str(player_id)),
        )

    kyc_checks_total.labels(status=kyc_status).inc()

    publish_event(
        CHANNELS["compliance"],
        f"kyc.{kyc_status}",
        {"player_id": str(player_id), "check_id": str(check["id"])},
    )
    logger.info("KYC %s for player %s by reviewer %s", kyc_status, player_id, reviewer_id)
    return check


# ---------- AML ----------

def check_velocity(player_id: uuid.UUID) -> dict[str, Any]:
    """
    Check player transaction velocity for suspicious activity.
    Returns risk indicators and may auto-create an AML alert.
    """
    with get_cursor() as cur:
        # Deposits in last hour
        cur.execute(
            """
            SELECT COUNT(*) AS cnt, COALESCE(SUM(amount), 0) AS total
            FROM wallet_events
            WHERE player_id = %s
              AND event_type = 'DEPOSIT'
              AND created_at > now() - interval '1 hour'
            """,
            (str(player_id),),
        )
        dep = cur.fetchone()

        # Bets in last minute
        cur.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM wallet_events
            WHERE player_id = %s
              AND event_type = 'BET'
              AND created_at > now() - interval '1 minute'
            """,
            (str(player_id),),
        )
        bets = cur.fetchone()

        # Total deposited in 24h
        cur.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM wallet_events
            WHERE player_id = %s
              AND event_type = 'DEPOSIT'
              AND created_at > now() - interval '24 hours'
            """,
            (str(player_id),),
        )
        dep_24h = cur.fetchone()

    deps_per_hour = dep["cnt"]
    bets_per_min = bets["cnt"]
    total_24h = Decimal(str(dep_24h["total"]))

    # Determine risk level
    risk = "low"
    alert_type = None
    if deps_per_hour >= 10 or bets_per_min >= 30:
        risk = "high"
        alert_type = "velocity_breach"
    elif total_24h >= Decimal("10000"):
        risk = "high"
        alert_type = "large_deposit_volume"
    elif deps_per_hour >= 5 or bets_per_min >= 15:
        risk = "medium"
        alert_type = "elevated_activity"
    elif total_24h >= Decimal("5000"):
        risk = "medium"
        alert_type = "moderate_deposit_volume"

    # Auto-create alert for medium/high
    if alert_type:
        create_alert(
            player_id=player_id,
            alert_type=alert_type,
            severity=risk,
            details={
                "deposits_per_hour": deps_per_hour,
                "bets_per_minute": bets_per_min,
                "total_deposited_24h": str(total_24h),
            },
        )

    return {
        "player_id": player_id,
        "velocity_deposits_per_hour": deps_per_hour,
        "velocity_bets_per_minute": bets_per_min,
        "total_deposited_24h": total_24h,
        "risk_level": risk,
    }


def create_alert(
    player_id: uuid.UUID,
    alert_type: str,
    severity: str,
    details: dict,
) -> dict[str, Any]:
    """Create an AML alert."""
    import json

    with get_cursor() as cur:
        cur.execute(
            """
            INSERT INTO aml_alerts (player_id, alert_type, severity, details)
            VALUES (%s, %s, %s, %s::jsonb)
            RETURNING id, player_id, alert_type, severity, details,
                      status, reviewer_id, reviewed_at, created_at
            """,
            (str(player_id), alert_type, severity, json.dumps(details, default=str)),
        )
        alert = dict(cur.fetchone())

    aml_alerts_total.labels(alert_type=alert_type, severity=severity).inc()

    publish_event(
        CHANNELS["compliance"],
        "aml.alert_created",
        {
            "alert_id": str(alert["id"]),
            "player_id": str(player_id),
            "alert_type": alert_type,
            "severity": severity,
        },
    )
    logger.warning("AML alert created: %s (%s) for player %s", alert_type, severity, player_id)
    return alert


def get_alerts(
    status_filter: str | None = None,
    severity_filter: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Retrieve AML alerts with optional filtering."""
    conditions = []
    params: list = []

    if status_filter:
        conditions.append("status = %s")
        params.append(status_filter)
    if severity_filter:
        conditions.append("severity = %s")
        params.append(severity_filter)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.extend([limit])

    with get_cursor() as cur:
        cur.execute(
            f"""
            SELECT id, player_id, alert_type, severity, details,
                   status, reviewer_id, reviewed_at, created_at
            FROM aml_alerts
            {where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    return [dict(r) for r in rows]


def review_alert(
    alert_id: uuid.UUID,
    new_status: str,
    reviewer_id: uuid.UUID,
    notes: str | None = None,
) -> dict[str, Any]:
    """Review and update an AML alert status."""
    valid_statuses = {"resolved", "escalated", "false_positive"}
    if new_status not in valid_statuses:
        raise ValueError(f"Status must be one of: {valid_statuses}")

    with get_cursor() as cur:
        cur.execute(
            """
            UPDATE aml_alerts
            SET status = %s, reviewer_id = %s, reviewed_at = now(),
                details = details || %s::jsonb
            WHERE id = %s
            RETURNING id, player_id, alert_type, severity, details,
                      status, reviewer_id, reviewed_at, created_at
            """,
            (
                new_status,
                str(reviewer_id),
                f'{{"review_notes": "{notes or ""}"}}',
                str(alert_id),
            ),
        )
        alert = cur.fetchone()

    if alert is None:
        raise ValueError("Alert not found")

    alert = dict(alert)
    publish_event(
        CHANNELS["compliance"],
        f"aml.alert_{new_status}",
        {"alert_id": str(alert_id), "reviewer_id": str(reviewer_id)},
    )
    logger.info("AML alert %s reviewed as %s by %s", alert_id, new_status, reviewer_id)
    return alert
