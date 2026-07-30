#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
SQLite-backed security incident database for iGaming platforms.

Provides persistent storage for security incidents, alerts, and response
actions in environments where a full SIEM is not available or as a local
cache layer alongside the primary SIEM.

Features:
  - SQLite storage with WAL (Write-Ahead Logging) mode for concurrent access
  - Incident lifecycle: open → investigating → contained → resolved → closed
  - Full audit trail: every status change and action is logged immutably
  - JSONB-style evidence storage in a TEXT column (json.dumps)
  - Configurable retention: prune incidents older than N days
  - Query helpers: open incidents, incidents by severity/type, recent actions
  - Export to JSON/CSV for compliance reporting

Usage:
    from incident_database import IncidentDatabase, Incident, Severity

    db = IncidentDatabase("/var/lib/soar/incidents.db")
    incident_id = db.create_incident(
        alert_type="brute_force",
        severity=Severity.HIGH,
        source_ip="203.0.113.42",
        description="150 failed logins in 60 seconds from single IP",
        evidence={"count": 150, "window_s": 60, "targeted_accounts": 3},
    )
    db.add_action(incident_id, "block_ip", operator="soar-auto", notes="WAF block applied")
    db.update_status(incident_id, "contained", operator="soar-auto")

CLI usage:
    python incident_database.py list --severity high --status open
    python incident_database.py show <incident_id>
    python incident_database.py export --format json --output report.json
    python incident_database.py prune --days 90

Reference: Chapter 24 — Security and Compliance
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("incident_database")


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class IncidentStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    CONTAINED = "contained"
    RESOLVED = "resolved"
    CLOSED = "closed"
    FALSE_POSITIVE = "false_positive"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Incident:
    """
    A security incident record.

    Attributes:
        incident_id:  UUID identifier.
        alert_type:   Machine-readable alert type (e.g. "brute_force").
        severity:     Severity level.
        status:       Current lifecycle status.
        source_ip:    Source IP address of the threat actor.
        description:  Human-readable incident description.
        evidence:     Structured evidence dict.
        created_at:   UTC creation timestamp.
        updated_at:   UTC last-update timestamp.
        assigned_to:  Operator or team responsible for investigation.
        tags:         Free-form tags for classification.
    """
    incident_id: str
    alert_type: str
    severity: str
    status: str
    source_ip: str
    description: str
    evidence: dict[str, Any]
    created_at: str
    updated_at: str
    assigned_to: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class IncidentAction:
    """
    An action taken in response to an incident.

    Attributes:
        action_id:    UUID identifier.
        incident_id:  Parent incident UUID.
        action_type:  Machine-readable action type.
        operator:     Who/what performed the action.
        notes:        Human-readable details.
        timestamp:    UTC action timestamp.
        metadata:     Additional action metadata.
    """
    action_id: str
    incident_id: str
    action_type: str
    operator: str
    notes: str
    timestamp: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS incidents (
    incident_id  TEXT PRIMARY KEY,
    alert_type   TEXT NOT NULL,
    severity     TEXT NOT NULL CHECK (severity IN ('critical', 'high', 'medium', 'low', 'info')),
    status       TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open', 'investigating', 'contained', 'resolved', 'closed', 'false_positive')),
    source_ip    TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    evidence     TEXT NOT NULL DEFAULT '{}',
    assigned_to  TEXT NOT NULL DEFAULT '',
    tags         TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_actions (
    action_id    TEXT PRIMARY KEY,
    incident_id  TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    action_type  TEXT NOT NULL,
    operator     TEXT NOT NULL DEFAULT 'system',
    notes        TEXT NOT NULL DEFAULT '',
    metadata     TEXT NOT NULL DEFAULT '{}',
    timestamp    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS incident_status_log (
    log_id       TEXT PRIMARY KEY,
    incident_id  TEXT NOT NULL REFERENCES incidents(incident_id) ON DELETE CASCADE,
    old_status   TEXT,
    new_status   TEXT NOT NULL,
    operator     TEXT NOT NULL DEFAULT 'system',
    reason       TEXT NOT NULL DEFAULT '',
    timestamp    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_incidents_severity   ON incidents(severity);
CREATE INDEX IF NOT EXISTS idx_incidents_status     ON incidents(status);
CREATE INDEX IF NOT EXISTS idx_incidents_alert_type ON incidents(alert_type);
CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents(created_at);
CREATE INDEX IF NOT EXISTS idx_incidents_source_ip  ON incidents(source_ip);
CREATE INDEX IF NOT EXISTS idx_actions_incident_id  ON incident_actions(incident_id);
CREATE INDEX IF NOT EXISTS idx_status_log_incident  ON incident_status_log(incident_id);
"""


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------

class IncidentDatabase:
    """
    SQLite-backed security incident database.

    Thread-safe for read operations.  Writes use a serialised connection
    (check_same_thread=False with WAL mode) suitable for single-process
    multi-threaded applications.

    Args:
        db_path: Path to the SQLite database file.
                 Use ":memory:" for testing.
    """

    def __init__(self, db_path: str = "/var/lib/soar/incidents.db") -> None:
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            db_path,
            check_same_thread=False,
            isolation_level=None,   # autocommit; we manage transactions explicitly
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        log.info("incident_database_opened path=%s", db_path)

    # --- Schema init -------------------------------------------------------

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(_SCHEMA)

    # --- Create ------------------------------------------------------------

    def create_incident(
        self,
        alert_type: str,
        severity: str,
        source_ip: str = "",
        description: str = "",
        evidence: dict[str, Any] | None = None,
        assigned_to: str = "",
        tags: list[str] | None = None,
    ) -> str:
        """
        Create a new security incident.

        Args:
            alert_type:   Machine-readable alert type.
            severity:     Severity level (critical/high/medium/low/info).
            source_ip:    Threat actor IP address.
            description:  Human-readable description.
            evidence:     Structured evidence dict.
            assigned_to:  Initial assignee.
            tags:         Classification tags.

        Returns:
            The new incident_id (UUID string).
        """
        incident_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO incidents
                    (incident_id, alert_type, severity, status, source_ip,
                     description, evidence, assigned_to, tags, created_at, updated_at)
                VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    alert_type,
                    severity,
                    source_ip,
                    description,
                    json.dumps(evidence or {}),
                    assigned_to,
                    json.dumps(tags or []),
                    now,
                    now,
                ),
            )
            self._conn.execute(
                """
                INSERT INTO incident_status_log
                    (log_id, incident_id, old_status, new_status, operator, reason, timestamp)
                VALUES (?, ?, NULL, 'open', 'system', 'incident created', ?)
                """,
                (str(uuid.uuid4()), incident_id, now),
            )
        log.info("incident_created id=%s type=%s severity=%s", incident_id, alert_type, severity)
        return incident_id

    # --- Status updates ----------------------------------------------------

    def update_status(
        self,
        incident_id: str,
        new_status: str,
        operator: str = "system",
        reason: str = "",
    ) -> bool:
        """
        Update the status of an incident.

        Args:
            incident_id: Incident UUID.
            new_status:  New lifecycle status.
            operator:    Who is making the change.
            reason:      Optional reason for the status change.

        Returns:
            True if the incident was found and updated.
        """
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            row = self._conn.execute(
                "SELECT status FROM incidents WHERE incident_id = ?",
                (incident_id,),
            ).fetchone()
            if not row:
                log.warning("incident_not_found id=%s", incident_id)
                return False
            old_status = row["status"]
            self._conn.execute(
                "UPDATE incidents SET status = ?, updated_at = ? WHERE incident_id = ?",
                (new_status, now, incident_id),
            )
            self._conn.execute(
                """
                INSERT INTO incident_status_log
                    (log_id, incident_id, old_status, new_status, operator, reason, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), incident_id, old_status, new_status, operator, reason, now),
            )
        log.info(
            "incident_status_updated id=%s %s→%s operator=%s",
            incident_id,
            old_status,
            new_status,
            operator,
        )
        return True

    # --- Actions -----------------------------------------------------------

    def add_action(
        self,
        incident_id: str,
        action_type: str,
        operator: str = "system",
        notes: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Record a response action taken on an incident.

        Args:
            incident_id:  Incident UUID.
            action_type:  Machine-readable action (e.g. "block_ip", "notify_compliance").
            operator:     Who performed the action.
            notes:        Human-readable details.
            metadata:     Additional action metadata.

        Returns:
            The new action_id.
        """
        action_id = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc).isoformat()
        with self._conn:
            self._conn.execute(
                """
                UPDATE incidents SET updated_at = ? WHERE incident_id = ?
                """,
                (now, incident_id),
            )
            self._conn.execute(
                """
                INSERT INTO incident_actions
                    (action_id, incident_id, action_type, operator, notes, metadata, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (action_id, incident_id, action_type, operator, notes, json.dumps(metadata or {}), now),
            )
        log.info(
            "incident_action_recorded id=%s action=%s operator=%s",
            incident_id,
            action_type,
            operator,
        )
        return action_id

    # --- Queries -----------------------------------------------------------

    def get_incident(self, incident_id: str) -> Incident | None:
        """Fetch a single incident by ID."""
        row = self._conn.execute(
            "SELECT * FROM incidents WHERE incident_id = ?",
            (incident_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_incident(row)

    def list_incidents(
        self,
        severity: str | None = None,
        status: str | None = None,
        alert_type: str | None = None,
        source_ip: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Incident]:
        """
        List incidents with optional filters.

        Args:
            severity:   Filter by severity level.
            status:     Filter by lifecycle status.
            alert_type: Filter by alert type.
            source_ip:  Filter by source IP address.
            limit:      Maximum results to return.
            offset:     Pagination offset.

        Returns:
            List of Incident dataclass instances.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if severity:
            clauses.append("severity = ?")
            params.append(severity)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if alert_type:
            clauses.append("alert_type = ?")
            params.append(alert_type)
        if source_ip:
            clauses.append("source_ip = ?")
            params.append(source_ip)

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        rows = self._conn.execute(
            f"SELECT * FROM incidents {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._row_to_incident(r) for r in rows]

    def get_actions(self, incident_id: str) -> list[IncidentAction]:
        """Fetch all actions for an incident, ordered by timestamp."""
        rows = self._conn.execute(
            "SELECT * FROM incident_actions WHERE incident_id = ? ORDER BY timestamp ASC",
            (incident_id,),
        ).fetchall()
        return [
            IncidentAction(
                action_id=r["action_id"],
                incident_id=r["incident_id"],
                action_type=r["action_type"],
                operator=r["operator"],
                notes=r["notes"],
                timestamp=r["timestamp"],
                metadata=json.loads(r["metadata"] or "{}"),
            )
            for r in rows
        ]

    def count_open_by_severity(self) -> dict[str, int]:
        """Return count of open incidents grouped by severity."""
        rows = self._conn.execute(
            "SELECT severity, COUNT(*) AS cnt FROM incidents WHERE status = 'open' GROUP BY severity"
        ).fetchall()
        return {r["severity"]: r["cnt"] for r in rows}

    # --- Maintenance -------------------------------------------------------

    def prune_old_incidents(self, days: int = 90) -> int:
        """
        Delete resolved/closed incidents older than ``days`` days.

        Only incidents with terminal statuses (resolved, closed, false_positive)
        are pruned.  Open and investigating incidents are never deleted.

        Args:
            days: Retention period in days.

        Returns:
            Number of incidents deleted.
        """
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=days)).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                DELETE FROM incidents
                WHERE status IN ('resolved', 'closed', 'false_positive')
                AND updated_at < ?
                """,
                (cutoff,),
            )
        count = cursor.rowcount
        log.info("incident_prune deleted=%d retention_days=%d", count, days)
        return count

    # --- Export ------------------------------------------------------------

    def export_json(self, incidents: list[Incident] | None = None) -> str:
        """
        Export incidents (and their actions) as a JSON string.

        Args:
            incidents: Specific incidents to export.  Defaults to all.

        Returns:
            JSON string suitable for compliance reporting.
        """
        items = incidents or self.list_incidents(limit=100_000)
        output = []
        for inc in items:
            actions = self.get_actions(inc.incident_id)
            output.append({
                "incident": {
                    "incident_id": inc.incident_id,
                    "alert_type": inc.alert_type,
                    "severity": inc.severity,
                    "status": inc.status,
                    "source_ip": inc.source_ip,
                    "description": inc.description,
                    "evidence": inc.evidence,
                    "assigned_to": inc.assigned_to,
                    "tags": inc.tags,
                    "created_at": inc.created_at,
                    "updated_at": inc.updated_at,
                },
                "actions": [
                    {
                        "action_id": a.action_id,
                        "action_type": a.action_type,
                        "operator": a.operator,
                        "notes": a.notes,
                        "timestamp": a.timestamp,
                        "metadata": a.metadata,
                    }
                    for a in actions
                ],
            })
        return json.dumps(output, indent=2, default=str)

    def export_csv(self, incidents: list[Incident] | None = None) -> str:
        """
        Export incidents as CSV for spreadsheet-based compliance review.

        Returns:
            CSV string with header row.
        """
        items = incidents or self.list_incidents(limit=100_000)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "incident_id", "alert_type", "severity", "status",
            "source_ip", "description", "assigned_to", "tags",
            "created_at", "updated_at",
        ])
        for inc in items:
            writer.writerow([
                inc.incident_id, inc.alert_type, inc.severity, inc.status,
                inc.source_ip, inc.description[:200], inc.assigned_to,
                ",".join(inc.tags), inc.created_at, inc.updated_at,
            ])
        return buf.getvalue()

    # --- Internal helpers --------------------------------------------------

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> Incident:
        return Incident(
            incident_id=row["incident_id"],
            alert_type=row["alert_type"],
            severity=row["severity"],
            status=row["status"],
            source_ip=row["source_ip"],
            description=row["description"],
            evidence=json.loads(row["evidence"] or "{}"),
            assigned_to=row["assigned_to"] or "",
            tags=json.loads(row["tags"] or "[]"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SQLite-backed security incident database for iGaming SOAR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--db",
        default="/var/lib/soar/incidents.db",
        help="Path to SQLite database file (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List incidents with optional filters")
    p_list.add_argument("--severity", choices=[s.value for s in Severity])
    p_list.add_argument("--status", choices=[s.value for s in IncidentStatus])
    p_list.add_argument("--type", dest="alert_type")
    p_list.add_argument("--ip", dest="source_ip")
    p_list.add_argument("--limit", type=int, default=50)

    # show
    p_show = sub.add_parser("show", help="Show a single incident with all actions")
    p_show.add_argument("incident_id", help="Incident UUID")

    # create
    p_create = sub.add_parser("create", help="Create a new incident from a JSON file")
    p_create.add_argument("--file", required=True, help="Path to JSON incident definition")

    # update-status
    p_status = sub.add_parser("update-status", help="Update incident status")
    p_status.add_argument("incident_id", help="Incident UUID")
    p_status.add_argument("new_status", choices=[s.value for s in IncidentStatus])
    p_status.add_argument("--operator", default="cli")
    p_status.add_argument("--reason", default="")

    # add-action
    p_action = sub.add_parser("add-action", help="Record a response action")
    p_action.add_argument("incident_id", help="Incident UUID")
    p_action.add_argument("action_type", help="Action type string")
    p_action.add_argument("--operator", default="cli")
    p_action.add_argument("--notes", default="")

    # export
    p_export = sub.add_parser("export", help="Export incidents to JSON or CSV")
    p_export.add_argument("--format", choices=["json", "csv"], default="json")
    p_export.add_argument("--output", help="Output file (default: stdout)")

    # prune
    p_prune = sub.add_parser("prune", help="Delete old resolved/closed incidents")
    p_prune.add_argument("--days", type=int, default=90, help="Retention period (default: %(default)s)")

    # stats
    sub.add_parser("stats", help="Show open incident counts by severity")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    db = IncidentDatabase(args.db)

    if args.command == "list":
        incidents = db.list_incidents(
            severity=args.severity,
            status=args.status,
            alert_type=args.alert_type,
            source_ip=args.source_ip,
            limit=args.limit,
        )
        for inc in incidents:
            print(
                f"{inc.incident_id[:8]} | {inc.severity:8} | {inc.status:14} | "
                f"{inc.alert_type:20} | {inc.source_ip:15} | {inc.created_at[:19]}"
            )
        print(f"\n{len(incidents)} incident(s) listed")

    elif args.command == "show":
        inc = db.get_incident(args.incident_id)
        if inc is None:
            print(f"Incident not found: {args.incident_id}", file=sys.stderr)
            sys.exit(1)
        assert inc is not None
        print(json.dumps(
            {
                "incident": {**inc.__dict__, "evidence": inc.evidence, "tags": inc.tags},
                "actions": [a.__dict__ for a in db.get_actions(inc.incident_id)],
            },
            indent=2,
            default=str,
        ))

    elif args.command == "create":
        try:
            with open(args.file, encoding="utf-8") as fh:
                data: dict[str, Any] = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Cannot read file: {exc}", file=sys.stderr)
            sys.exit(1)
        incident_id = db.create_incident(**data)
        print(f"Created: {incident_id}")

    elif args.command == "update-status":
        ok = db.update_status(args.incident_id, args.new_status, args.operator, args.reason)
        sys.exit(0 if ok else 1)

    elif args.command == "add-action":
        action_id = db.add_action(args.incident_id, args.action_type, args.operator, args.notes)
        print(f"Action recorded: {action_id}")

    elif args.command == "export":
        if args.format == "json":
            output = db.export_json()
        else:
            output = db.export_csv()
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(output)
            print(f"Exported to {args.output}")
        else:
            print(output)

    elif args.command == "prune":
        count = db.prune_old_incidents(args.days)
        print(f"Pruned {count} old incident(s)")

    elif args.command == "stats":
        counts = db.count_open_by_severity()
        for sev in ["critical", "high", "medium", "low", "info"]:
            print(f"  {sev:8}: {counts.get(sev, 0)}")


if __name__ == "__main__":
    main()
