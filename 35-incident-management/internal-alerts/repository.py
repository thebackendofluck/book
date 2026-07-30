# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Alert Repository
# Source: Production casino platform (sanitized)
# Chapter 35 - Incident Management
#
# PostgreSQL data access layer for the internal_alerts schema.
# =============================================================================

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from models import Alert, AlertStatus, AlertType, EmailAddress

logger = logging.getLogger(__name__)


class AlertRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def create_alert(self, alert: Alert) -> Optional[int]:
        """
        Insert an alert record subject to the alert type's max_frequency_seconds
        deduplication window. Returns the new row id, or None if suppressed.
        """
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Fetch the alert type to determine the deduplication window
                cur.execute(
                    "SELECT max_frequency_seconds FROM alert_types WHERE name = %s",
                    (alert.alert_type,),
                )
                row = cur.fetchone()
                max_gap_seconds = row["max_frequency_seconds"] if row else 0
                cutoff = datetime.now(timezone.utc) - timedelta(seconds=max_gap_seconds)

                # Check if a recent alert already exists for this type + user
                cur.execute(
                    """
                    SELECT id FROM internal_alerts.alerts
                    WHERE alert_type = %s
                      AND (user_id IS NULL OR user_id = %s)
                      AND last_update > %s
                    LIMIT 1
                    """,
                    (alert.alert_type, alert.user_id, cutoff),
                )
                if cur.fetchone():
                    logger.debug(
                        "Alert suppressed by deduplication: type=%s user_id=%s",
                        alert.alert_type, alert.user_id,
                    )
                    return None

                # Insert the alert
                cur.execute(
                    """
                    INSERT INTO internal_alerts.alerts
                        (alert_type, user_id, brand_id, params, status, last_update)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        alert.alert_type,
                        alert.user_id,
                        alert.brand_id,
                        json.dumps(alert.params) if alert.params else None,
                        alert.status.value,
                        alert.last_update,
                    ),
                )
                result = cur.fetchone()
                return result["id"] if result else None

    def find_alerts_to_send(self) -> list[Alert]:
        """Return all pending alerts that have not yet been sent."""
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, alert_type, user_id, brand_id, params, status, last_update
                    FROM internal_alerts.alerts
                    WHERE status = %s
                    ORDER BY last_update
                    LIMIT 100
                    """,
                    (AlertStatus.PENDING.value,),
                )
                rows = cur.fetchall()
                return [
                    Alert(
                        id=r["id"],
                        alert_type=r["alert_type"],
                        user_id=r["user_id"],
                        brand_id=r["brand_id"],
                        params=json.loads(r["params"]) if r["params"] else None,
                        status=AlertStatus(r["status"]),
                        last_update=r["last_update"],
                    )
                    for r in rows
                ]

    def update_status(self, alert_id: int, status: AlertStatus) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE internal_alerts.alerts SET status = %s, last_update = NOW() WHERE id = %s",
                    (status.value, alert_id),
                )


class AlertTypeRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def find_alert_type_by_name(self, name: str) -> Optional[AlertType]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT name, recipient, template_name, jurisdiction_id, max_frequency_seconds "
                    "FROM alert_types WHERE name = %s",
                    (name,),
                )
                row = cur.fetchone()
                return AlertType(**row) if row else None


class EmailAddressRepository:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    def _connect(self):
        return psycopg2.connect(self._dsn)

    def find_address_by_name_and_jurisdiction(
        self, name: str, jurisdiction_id: Optional[str]
    ) -> Optional[EmailAddress]:
        with self._connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT name, jurisdiction_id, address
                    FROM email_addresses
                    WHERE name = %s AND (jurisdiction_id = %s OR jurisdiction_id IS NULL)
                    ORDER BY jurisdiction_id NULLS LAST
                    LIMIT 1
                    """,
                    (name, jurisdiction_id),
                )
                row = cur.fetchone()
                return EmailAddress(**row) if row else None
