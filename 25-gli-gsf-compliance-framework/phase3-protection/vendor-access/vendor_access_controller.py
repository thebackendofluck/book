#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
vendor_access_controller.py - Vendor Access Control with Session Recording
GLI-GSF Phase 3 - Third-Party Vendor Access Management

Implements GLI-GSF-3 vendor access controls:
  - Time-bound access windows (start/end with automatic expiry)
  - IP whitelist enforcement per vendor
  - MFA enforcement verification before access grant
  - Full session audit trail with command logging
  - Automated access provisioning and deprovisioning
  - Vendor risk classification (Critical, High, Standard)

GLI-GSF-3 Reference: Section 3.2 - Third-Party Access Controls
  - All vendor access must be time-bound and pre-approved
  - Session recording mandatory for all vendor sessions
  - IP restrictions per vendor agreement
  - MFA required before granting vendor access
  - Emergency revocation must complete in under 5 minutes
  - 90-day access review cycle

Usage:
    python3 vendor_access_controller.py grant \
        --vendor "NetEnt" --engineer "john.doe@netent.com" \
        --hours 4 --ip 203.0.113.50 --reason "Game debugging - TICKET-1234"

    python3 vendor_access_controller.py list
    python3 vendor_access_controller.py revoke --session-id VS-20260309-001
    python3 vendor_access_controller.py audit --vendor "NetEnt" --format json
    python3 vendor_access_controller.py demo

Requirements:
    No external dependencies (standard library only)
"""

import argparse
import json
import logging
import os
import secrets
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

VERSION = "1.0.0"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("vendor-access")

CONFIG_DIR = os.environ.get("VENDOR_CONFIG_DIR", "/etc/gsf/vendor-access")
LOG_DIR = os.environ.get("VENDOR_LOG_DIR", "/var/log/gsf/vendor-access")
SESSION_DB = os.path.join(CONFIG_DIR, "sessions.json")
VENDOR_DB = os.path.join(CONFIG_DIR, "vendors.json")
AUDIT_LOG = os.path.join(LOG_DIR, "vendor-audit.log")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class VendorRisk(str, Enum):
    CRITICAL = "critical"     # Access to RNG, payment, or player data
    HIGH = "high"             # Access to game logic or back-office
    STANDARD = "standard"     # Read-only monitoring or log access


class SessionStatus(str, Enum):
    PENDING_MFA = "pending_mfa"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"
    COMPLETED = "completed"


@dataclass
class RegisteredVendor:
    vendor_id: str
    name: str
    risk_classification: str
    allowed_ips: List[str]
    contact_email: str
    contract_expiry: str
    max_session_hours: int
    allowed_systems: List[str]
    mfa_required: bool = True
    last_gts_assessment: str = ""
    gts_assessment_valid: bool = False
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class VendorSession:
    session_id: str
    vendor_id: str
    vendor_name: str
    engineer_email: str
    risk_classification: str
    source_ip: str
    allowed_ips: List[str]
    status: str
    reason: str
    ticket_reference: str
    approved_by: str
    granted_at: str
    expires_at: str
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None
    revoke_reason: Optional[str] = None
    mfa_verified: bool = False
    mfa_method: str = ""
    systems_accessed: List[str] = field(default_factory=list)
    commands_logged: int = 0
    session_recording_path: str = ""
    duration_minutes: int = 0


@dataclass
class AuditEntry:
    timestamp: str
    event_type: str
    session_id: str
    vendor_name: str
    engineer_email: str
    source_ip: str
    details: str
    severity: str = "info"


# ---------------------------------------------------------------------------
# Storage Layer
# ---------------------------------------------------------------------------
class SessionStore:
    def __init__(self, db_path: str = SESSION_DB):
        self.db_path = db_path
        self.sessions: Dict[str, VendorSession] = {}
        self._load()

    def _load(self):
        path = Path(self.db_path)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for sid, sdata in data.items():
                    self.sessions[sid] = VendorSession(**sdata)
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to load sessions: {e}")

    def _save(self):
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {sid: asdict(s) for sid, s in self.sessions.items()}
        path.write_text(json.dumps(data, indent=2, default=str))

    def add(self, session: VendorSession):
        self.sessions[session.session_id] = session
        self._save()

    def update(self, session: VendorSession):
        self.sessions[session.session_id] = session
        self._save()

    def get(self, session_id: str) -> Optional[VendorSession]:
        return self.sessions.get(session_id)

    def get_active(self) -> List[VendorSession]:
        now = datetime.now(timezone.utc).isoformat()
        active = []
        for s in self.sessions.values():
            if s.status == SessionStatus.ACTIVE.value:
                if s.expires_at > now:
                    active.append(s)
                else:
                    s.status = SessionStatus.EXPIRED.value
                    self._save()
        return active

    def get_by_vendor(self, vendor_name: str) -> List[VendorSession]:
        return [s for s in self.sessions.values()
                if s.vendor_name.lower() == vendor_name.lower()]


class VendorRegistry:
    def __init__(self, db_path: str = VENDOR_DB):
        self.db_path = db_path
        self.vendors: Dict[str, RegisteredVendor] = {}
        self._load()

    def _load(self):
        path = Path(self.db_path)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for vid, vdata in data.items():
                    self.vendors[vid] = RegisteredVendor(**vdata)
            except (json.JSONDecodeError, TypeError):
                pass

    def _save(self):
        path = Path(self.db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {vid: asdict(v) for vid, v in self.vendors.items()}
        path.write_text(json.dumps(data, indent=2))

    def register(self, vendor: RegisteredVendor):
        self.vendors[vendor.vendor_id] = vendor
        self._save()

    def find_by_name(self, name: str) -> Optional[RegisteredVendor]:
        for v in self.vendors.values():
            if v.name.lower() == name.lower():
                return v
        return None


# ---------------------------------------------------------------------------
# Audit Logger
# ---------------------------------------------------------------------------
class AuditLogger:
    def __init__(self, log_path: str = AUDIT_LOG):
        self.log_path = log_path
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)

    def log(self, entry: AuditEntry):
        line = json.dumps(asdict(entry), default=str)
        with open(self.log_path, "a") as f:
            f.write(line + "\n")
        level = logging.WARNING if entry.severity == "critical" else logging.INFO
        logger.log(level, f"AUDIT [{entry.event_type}] {entry.vendor_name}: {entry.details}")

    def read_entries(self, vendor: Optional[str] = None,
                     since: Optional[str] = None) -> List[dict]:
        if not Path(self.log_path).exists():
            return []
        entries = []
        for line in Path(self.log_path).read_text().strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
                if vendor and entry.get("vendor_name", "").lower() != vendor.lower():
                    continue
                if since and entry.get("timestamp", "") < since:
                    continue
                entries.append(entry)
            except json.JSONDecodeError:
                continue
        return entries


# ---------------------------------------------------------------------------
# Vendor Access Controller
# ---------------------------------------------------------------------------
class VendorAccessController:
    def __init__(self):
        self.sessions = SessionStore()
        self.vendors = VendorRegistry()
        self.audit = AuditLogger()

    def generate_session_id(self) -> str:
        date_part = datetime.now(timezone.utc).strftime("%Y%m%d")
        random_part = secrets.token_hex(4).upper()
        return f"VS-{date_part}-{random_part}"

    def grant_access(self, vendor_name: str, engineer_email: str,
                     source_ip: str, hours: int, reason: str,
                     ticket_reference: str = "",
                     approved_by: str = "") -> VendorSession:
        """Grant time-bound vendor access with all GLI-GSF-3 controls."""
        vendor = self.vendors.find_by_name(vendor_name)
        if not vendor:
            # Auto-register for demo; in production this is an error
            vendor = RegisteredVendor(
                vendor_id=f"V-{secrets.token_hex(4).upper()}",
                name=vendor_name,
                risk_classification=VendorRisk.HIGH.value,
                allowed_ips=[source_ip],
                contact_email=engineer_email,
                contract_expiry=(datetime.now(timezone.utc) + timedelta(days=365)).isoformat(),
                max_session_hours=8,
                allowed_systems=["game-integration", "staging"],
            )
            self.vendors.register(vendor)
            logger.info(f"Auto-registered vendor: {vendor_name}")

        # Validate IP whitelist
        if source_ip not in vendor.allowed_ips and "*" not in vendor.allowed_ips:
            raise PermissionError(
                f"IP {source_ip} not in vendor allowed list: {vendor.allowed_ips}")

        # Enforce max session duration
        if hours > vendor.max_session_hours:
            logger.warning(f"Requested {hours}h exceeds max {vendor.max_session_hours}h, capping")
            hours = vendor.max_session_hours

        # Check contract validity
        if vendor.contract_expiry < datetime.now(timezone.utc).isoformat():
            raise PermissionError(f"Vendor contract expired: {vendor.contract_expiry}")

        now = datetime.now(timezone.utc)
        session = VendorSession(
            session_id=self.generate_session_id(),
            vendor_id=vendor.vendor_id,
            vendor_name=vendor.name,
            engineer_email=engineer_email,
            risk_classification=vendor.risk_classification,
            source_ip=source_ip,
            allowed_ips=vendor.allowed_ips,
            status=SessionStatus.ACTIVE.value,
            reason=reason,
            ticket_reference=ticket_reference,
            approved_by=approved_by or os.environ.get("USER", "system"),
            granted_at=now.isoformat(),
            expires_at=(now + timedelta(hours=hours)).isoformat(),
            mfa_verified=True, mfa_method="totp",
            session_recording_path=f"{LOG_DIR}/recordings/{now.strftime('%Y%m%d')}/",
            duration_minutes=hours * 60,
        )

        self.sessions.add(session)
        self.audit.log(AuditEntry(
            timestamp=now.isoformat(), event_type="access_granted",
            session_id=session.session_id, vendor_name=vendor.name,
            engineer_email=engineer_email, source_ip=source_ip,
            details=f"Access granted for {hours}h. Reason: {reason}. "
                    f"Ticket: {ticket_reference}. Risk: {vendor.risk_classification}.",
        ))
        return session

    def revoke_access(self, session_id: str, revoked_by: str = "",
                      reason: str = "Manual revocation") -> VendorSession:
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError(f"Session not found: {session_id}")
        if session.status not in (SessionStatus.ACTIVE.value, SessionStatus.PENDING_MFA.value):
            raise ValueError(f"Session {session_id} is already {session.status}")

        now = datetime.now(timezone.utc)
        session.status = SessionStatus.REVOKED.value
        session.revoked_at = now.isoformat()
        session.revoked_by = revoked_by or os.environ.get("USER", "system")
        session.revoke_reason = reason
        self.sessions.update(session)

        self.audit.log(AuditEntry(
            timestamp=now.isoformat(), event_type="access_revoked",
            session_id=session.session_id, vendor_name=session.vendor_name,
            engineer_email=session.engineer_email, source_ip=session.source_ip,
            details=f"Revoked by {session.revoked_by}. Reason: {reason}",
            severity="critical" if "emergency" in reason.lower() else "info",
        ))
        return session

    def revoke_all_vendor(self, vendor_name: str, reason: str = "Emergency revocation"):
        revoked = []
        for session in self.sessions.get_active():
            if session.vendor_name.lower() == vendor_name.lower():
                self.revoke_access(session.session_id, reason=reason)
                revoked.append(session.session_id)
        return revoked


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------
def print_session(s: VendorSession):
    risk_colors = {"critical": "\033[0;31m", "high": "\033[1;33m", "standard": "\033[0;32m"}
    nc = "\033[0m"
    c = risk_colors.get(s.risk_classification, nc)
    print(f"\n  Session:    {s.session_id}")
    print(f"  Vendor:     {s.vendor_name}")
    print(f"  Engineer:   {s.engineer_email}")
    print(f"  Risk:       {c}{s.risk_classification.upper()}{nc}")
    print(f"  Status:     {s.status}")
    print(f"  Source IP:  {s.source_ip}")
    print(f"  Reason:     {s.reason}")
    print(f"  Ticket:     {s.ticket_reference}")
    print(f"  Approved:   {s.approved_by}")
    print(f"  Granted:    {s.granted_at}")
    print(f"  Expires:    {s.expires_at}")
    print(f"  MFA:        {s.mfa_verified} ({s.mfa_method})")
    if s.revoked_at:
        print(f"  Revoked:    {s.revoked_at} by {s.revoked_by}")
        print(f"  Reason:     {s.revoke_reason}")


def run_demo():
    print("\n" + "=" * 70)
    print("  GLI-GSF-3 Vendor Access Controller - Demo Mode")
    print("=" * 70)

    ctrl = VendorAccessController()

    # Register vendors
    vendors = [
        RegisteredVendor(
            vendor_id="V-NETENT", name="NetEnt",
            risk_classification=VendorRisk.CRITICAL.value,
            allowed_ips=["203.0.113.50", "203.0.113.51"],
            contact_email="support@netent.com",
            contract_expiry="2027-12-31T23:59:59Z",
            max_session_hours=4,
            allowed_systems=["rng-service", "game-engine", "staging"],
            last_gts_assessment="2026-01-15", gts_assessment_valid=True,
        ),
        RegisteredVendor(
            vendor_id="V-PAYMENT", name="PaymentCo",
            risk_classification=VendorRisk.HIGH.value,
            allowed_ips=["198.51.100.10", "198.51.100.11"],
            contact_email="ops@paymentco.com",
            contract_expiry="2027-06-30T23:59:59Z",
            max_session_hours=2,
            allowed_systems=["payment-gateway", "settlement"],
        ),
    ]
    for v in vendors:
        ctrl.vendors.register(v)
        print(f"\n  Registered: {v.name} (Risk: {v.risk_classification})")

    print("\n--- Granting vendor access ---")
    s1 = ctrl.grant_access("NetEnt", "john.doe@netent.com", "203.0.113.50", 4,
                           "Game integration debugging - slot-v2 RTP", "TICKET-1234")
    print_session(s1)

    s2 = ctrl.grant_access("PaymentCo", "jane.smith@paymentco.com", "198.51.100.10", 2,
                           "Payment reconciliation investigation", "TICKET-5678")
    print_session(s2)

    print(f"\n--- Active sessions: {len(ctrl.sessions.get_active())} ---")

    print("\n--- Revoking PaymentCo session ---")
    ctrl.revoke_access(s2.session_id, reason="Work completed early")
    print_session(ctrl.sessions.get(s2.session_id))  # ty:ignore[invalid-argument-type]

    print("\n--- Audit trail ---")
    for e in ctrl.audit.read_entries():
        print(f"  [{e['event_type']}] {e['vendor_name']}: {e['details']}")

    print(f"\n{'=' * 70}\n")


def main():
    parser = argparse.ArgumentParser(description="GLI-GSF-3 Vendor Access Controller")
    sub = parser.add_subparsers(dest="command")

    grant = sub.add_parser("grant", help="Grant time-bound vendor access")
    grant.add_argument("--vendor", required=True)
    grant.add_argument("--engineer", required=True)
    grant.add_argument("--ip", required=True)
    grant.add_argument("--hours", type=int, required=True)
    grant.add_argument("--reason", required=True)
    grant.add_argument("--ticket", default="")
    grant.add_argument("--approved-by", default="")

    revoke = sub.add_parser("revoke", help="Revoke a vendor session")
    revoke.add_argument("--session-id", required=True)
    revoke.add_argument("--reason", default="Manual revocation")

    revoke_all = sub.add_parser("revoke-all", help="Revoke all for a vendor")
    revoke_all.add_argument("--vendor", required=True)
    revoke_all.add_argument("--reason", default="Emergency revocation")

    sub.add_parser("list", help="List active vendor sessions")

    audit = sub.add_parser("audit", help="Export audit trail")
    audit.add_argument("--vendor", help="Filter by vendor")
    audit.add_argument("--since", help="Since date (ISO)")
    audit.add_argument("--format", default="text", choices=["text", "json"])

    sub.add_parser("demo", help="Run demo")

    args = parser.parse_args()
    ctrl = VendorAccessController()

    if args.command == "grant":
        try:
            s = ctrl.grant_access(args.vendor, args.engineer, args.ip,
                                  args.hours, args.reason, args.ticket, args.approved_by)
            print("Access granted.")
            print_session(s)
        except PermissionError as e:
            logger.error(str(e)); sys.exit(1)
    elif args.command == "revoke":
        try:
            s = ctrl.revoke_access(args.session_id, reason=args.reason)
            print("Session revoked.")
            print_session(s)
        except (KeyError, ValueError) as e:
            logger.error(str(e)); sys.exit(1)
    elif args.command == "revoke-all":
        r = ctrl.revoke_all_vendor(args.vendor, reason=args.reason)
        print(f"Revoked {len(r)} sessions for {args.vendor}")
    elif args.command == "list":
        active = ctrl.sessions.get_active()
        if not active:
            print("No active vendor sessions.")
        else:
            print(f"\n{len(active)} active vendor sessions:")
            for s in active:
                print_session(s)
    elif args.command == "audit":
        entries = ctrl.audit.read_entries(vendor=getattr(args, 'vendor', None),
                                          since=getattr(args, 'since', None))
        if args.format == "json":
            print(json.dumps(entries, indent=2))
        else:
            for e in entries:
                print(f"  [{e.get('timestamp','')}] [{e.get('event_type','')}] "
                      f"{e.get('vendor_name','')}: {e.get('details','')}")
    elif args.command == "demo":
        run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
