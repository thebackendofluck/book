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
M-04: Application audit log — FastAPI write path to audit_log table.

Populates the existing `audit_log` table (defined in init.sql) on:
- Player logins
- Withdrawals / deposits
- KYC status changes
- Admin actions

Also dispatches structured syslog to Wazuh via RFC 5424 UDP syslog
for real-time SIEM ingestion. Wazuh on ops-host (10.0.10.26:514)
has a custom decoder for the `casino_audit` program name.

Usage:
    from app.audit import record_audit

    record_audit(
        actor="player:<uuid>",
        action="login",
        resource_type="player",
        resource_id=str(player_id),
        details={"ip": request_ip},
        ip_address=request_ip,
    )
"""

import logging
import socket
import struct
import time
from typing import Any

from app.database import get_cursor

logger = logging.getLogger(__name__)

# Wazuh syslog endpoint — Wazuh agent on casino host forwards to manager
WAZUH_SYSLOG_HOST = "127.0.0.1"
WAZUH_SYSLOG_PORT = 514
SYSLOG_PROGRAM = "casino_audit"

# RFC 5424 facility.severity: LOCAL1 (17) + NOTICE (5) = 17*8+5 = 141
_SYSLOG_PRI = 141


def _send_syslog(message: str) -> None:
    """Fire-and-forget UDP syslog to Wazuh local agent."""
    try:
        hostname = socket.gethostname()
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        # RFC 5424 format: <PRI>VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID MSG
        packet = f"<{_SYSLOG_PRI}>1 {ts} {hostname} {SYSLOG_PROGRAM} - - - {message}"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(packet.encode("utf-8", errors="replace"),
                        (WAZUH_SYSLOG_HOST, WAZUH_SYSLOG_PORT))
    except Exception:
        logger.warning("Failed to send syslog to Wazuh", exc_info=True)


def record_audit(
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> None:
    """
    Write one row to audit_log and dispatch a syslog event.

    actor: identity string, e.g. "player:<uuid>", "ops:<sub>", "system"
    action: verb, e.g. "login", "logout", "withdrawal", "deposit",
            "kyc_approved", "kyc_rejected", "password_change",
            "self_exclusion", "admin_view_player"
    resource_type: "player", "wallet", "kyc", "system"
    resource_id: UUID or identifier of the affected object
    details: arbitrary JSON metadata (sanitized — never log passwords/hashes)
    ip_address: client IP, used for LGPD geolocation audit trail
    """
    import json as _json

    safe_details = details or {}
    # Strip any accidentally included sensitive keys
    for key in ("password", "password_hash", "token", "secret", "card_number", "cvv"):
        safe_details.pop(key, None)

    try:
        with get_cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                    (actor, action, resource_type, resource_id, details, ip_address)
                VALUES (%s, %s, %s, %s, %s, %s::inet)
                """,
                (
                    actor,
                    action,
                    resource_type,
                    resource_id,
                    _json.dumps(safe_details),
                    ip_address,
                ),
            )
    except Exception:
        logger.error(
            "audit_log write failed for actor=%s action=%s",
            actor, action,
            exc_info=True,
        )
        # Syslog still sent even if DB write fails
    else:
        logger.info("audit: actor=%s action=%s resource=%s/%s",
                    actor, action, resource_type, resource_id)

    # Wazuh syslog dispatch
    syslog_msg = (
        f'actor="{actor}" action="{action}" resource_type="{resource_type}" '
        f'resource_id="{resource_id}" ip="{ip_address}" details={_json.dumps(safe_details)}'
    )
    _send_syslog(syslog_msg)
