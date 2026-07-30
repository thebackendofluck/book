#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 42, War Stories.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Just-In-Time Privileged Access Manager for iGaming Platforms
==============================================================

Implements JIT access control for privileged operations on gambling
platforms. Provides time-limited elevated access with automatic expiry,
audit trails, and approval workflows.

Usage:
    python jit_access_manager.py --request --role db_admin --reason "Investigate stuck withdrawals" --duration 30
    python jit_access_manager.py --approve REQ-0001
    python jit_access_manager.py --status
    python jit_access_manager.py --audit
    python jit_access_manager.py --demo
"""

import json
import logging
import argparse
import uuid
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


class AccessRole(Enum):
    DB_ADMIN = "db_admin"
    DB_READ_FINANCIAL = "db_read_financial"
    PAYMENT_ADMIN = "payment_admin"
    PLAYER_DATA_ACCESS = "player_data_access"
    RNG_SYSTEM_ACCESS = "rng_system_access"
    BONUS_OVERRIDE = "bonus_override"
    KYC_MANUAL_APPROVE = "kyc_manual_approve"
    INFRASTRUCTURE_ADMIN = "infrastructure_admin"
    LOG_FULL_ACCESS = "log_full_access"
    REGULATORY_EXPORT = "regulatory_export"


class RequestStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    ACTIVE = "active"
    EXPIRED = "expired"
    REVOKED = "revoked"


@dataclass
class RolePolicy:
    role: AccessRole
    description: str
    max_duration_minutes: int
    requires_approval: bool = True
    approval_roles: list = field(default_factory=list)  # roles that can approve
    require_mfa: bool = True
    require_reason: bool = True
    auto_approve_for: list = field(default_factory=list)  # users auto-approved
    allowed_actions: list = field(default_factory=list)
    restricted_data: list = field(default_factory=list)
    audit_level: str = "full"  # basic, standard, full
    regulatory_notification: bool = False


@dataclass
class AccessRequest:
    id: str
    requester: str
    requester_role: str
    role: AccessRole
    reason: str
    duration_minutes: int
    status: RequestStatus = RequestStatus.PENDING
    created_at: str = ""
    approved_by: Optional[str] = None
    approved_at: Optional[str] = None
    activated_at: Optional[str] = None
    expires_at: Optional[str] = None
    revoked_at: Optional[str] = None
    revoked_by: Optional[str] = None
    revoke_reason: str = ""
    mfa_verified: bool = False
    actions_performed: list = field(default_factory=list)
    ip_address: str = ""
    session_id: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]

    def is_active(self) -> bool:
        if self.status != RequestStatus.ACTIVE:
            return False
        if self.expires_at:
            return datetime.utcnow() < datetime.fromisoformat(self.expires_at)  # ty:ignore[deprecated]
        return False


@dataclass
class AuditEntry:
    timestamp: str
    request_id: str
    actor: str
    action: str
    details: str
    ip_address: str = ""
    sensitive_data_accessed: bool = False


# ---------------------------------------------------------------------------
# Role policies for iGaming
# ---------------------------------------------------------------------------

ROLE_POLICIES = {
    AccessRole.DB_ADMIN: RolePolicy(
        AccessRole.DB_ADMIN, "Full database administrator access",
        max_duration_minutes=60, requires_approval=True,
        approval_roles=["cto", "vp_engineering", "dba_lead"],
        allowed_actions=["schema_changes", "query_execution", "user_management", "backup_restore"],
        restricted_data=["player_passwords", "payment_tokens", "rng_seeds"],
        audit_level="full", regulatory_notification=True,
    ),
    AccessRole.DB_READ_FINANCIAL: RolePolicy(
        AccessRole.DB_READ_FINANCIAL, "Read-only access to financial tables",
        max_duration_minutes=120, requires_approval=True,
        approval_roles=["cfo", "head_payments", "compliance_director"],
        allowed_actions=["select_queries"],
        restricted_data=["full_card_numbers", "bank_account_details"],
        audit_level="full",
    ),
    AccessRole.PAYMENT_ADMIN: RolePolicy(
        AccessRole.PAYMENT_ADMIN, "Payment system administration",
        max_duration_minutes=30, requires_approval=True,
        approval_roles=["head_payments", "cfo"],
        allowed_actions=["manual_payout", "refund", "void_transaction", "provider_config"],
        audit_level="full", regulatory_notification=True,
    ),
    AccessRole.PLAYER_DATA_ACCESS: RolePolicy(
        AccessRole.PLAYER_DATA_ACCESS, "Access to player PII and account data",
        max_duration_minutes=60, requires_approval=True,
        approval_roles=["dpo", "compliance_director", "head_cs"],
        allowed_actions=["view_player_profile", "view_kyc_documents", "view_activity_log"],
        restricted_data=["full_ssn", "full_passport_number"],
        audit_level="full",
    ),
    AccessRole.RNG_SYSTEM_ACCESS: RolePolicy(
        AccessRole.RNG_SYSTEM_ACCESS, "RNG system configuration and monitoring",
        max_duration_minutes=30, requires_approval=True,
        approval_roles=["cto", "head_gaming", "compliance_director"],
        allowed_actions=["view_rng_config", "rng_reseed", "view_rng_logs"],
        restricted_data=["rng_seeds", "rng_algorithm_params"],
        audit_level="full", regulatory_notification=True,
    ),
    AccessRole.BONUS_OVERRIDE: RolePolicy(
        AccessRole.BONUS_OVERRIDE, "Manual bonus and promotion overrides",
        max_duration_minutes=30, requires_approval=True,
        approval_roles=["head_crm", "head_vip", "compliance_director"],
        allowed_actions=["grant_bonus", "void_bonus", "adjust_wagering", "manual_credit"],
        audit_level="full",
    ),
    AccessRole.KYC_MANUAL_APPROVE: RolePolicy(
        AccessRole.KYC_MANUAL_APPROVE, "Manual KYC verification approval",
        max_duration_minutes=120, requires_approval=True,
        approval_roles=["mlro", "head_compliance", "kyc_lead"],
        allowed_actions=["approve_kyc", "reject_kyc", "request_additional_docs"],
        audit_level="full",
    ),
    AccessRole.INFRASTRUCTURE_ADMIN: RolePolicy(
        AccessRole.INFRASTRUCTURE_ADMIN, "Production infrastructure administration",
        max_duration_minutes=60, requires_approval=True,
        approval_roles=["cto", "vp_engineering", "sre_lead"],
        allowed_actions=["kubectl_admin", "cloud_console", "network_config", "dns_changes"],
        audit_level="full",
    ),
    AccessRole.REGULATORY_EXPORT: RolePolicy(
        AccessRole.REGULATORY_EXPORT, "Export data for regulatory submissions",
        max_duration_minutes=240, requires_approval=True,
        approval_roles=["compliance_director", "ceo", "legal_counsel"],
        allowed_actions=["export_player_data", "export_transactions", "export_audit_logs"],
        audit_level="full", regulatory_notification=True,
    ),
}


# ---------------------------------------------------------------------------
# JIT Access Manager
# ---------------------------------------------------------------------------

class JITAccessManager:
    """Just-In-Time access management for gambling platforms."""

    def __init__(self):
        self.requests: dict[str, AccessRequest] = {}
        self.audit_log: list[AuditEntry] = []
        self._lock = threading.Lock()
        self._counter = 0

    def request_access(self, requester: str, requester_role: str,
                       role: AccessRole, reason: str,
                       duration_minutes: int = 30,
                       ip_address: str = "10.0.0.1") -> dict:
        """Submit a JIT access request."""
        policy = ROLE_POLICIES.get(role)
        if not policy:
            return {"error": f"Unknown role: {role.value}"}

        if duration_minutes > policy.max_duration_minutes:
            return {"error": f"Requested duration {duration_minutes}m exceeds max {policy.max_duration_minutes}m"}

        if policy.require_reason and not reason.strip():
            return {"error": "Reason is required for this access role"}

        with self._lock:
            self._counter += 1
            req_id = f"REQ-{self._counter:04d}"

        req = AccessRequest(
            id=req_id, requester=requester, requester_role=requester_role,
            role=role, reason=reason, duration_minutes=duration_minutes,
            ip_address=ip_address, session_id=uuid.uuid4().hex[:12],
        )

        # Auto-approve if requester is in auto-approve list
        if requester in policy.auto_approve_for:
            req.status = RequestStatus.APPROVED
            req.approved_by = "system (auto-approve)"
            req.approved_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
            self._activate(req)
        elif not policy.requires_approval:
            req.status = RequestStatus.APPROVED
            req.approved_by = "system (no-approval-required)"
            req.approved_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
            self._activate(req)

        self.requests[req_id] = req
        self._audit("access_requested", req, f"Requested {role.value} for {duration_minutes}m: {reason}")

        return {
            "request_id": req_id,
            "status": req.status.value,
            "role": role.value,
            "duration_minutes": duration_minutes,
            "requires_approval": policy.requires_approval and req.status == RequestStatus.PENDING,
            "approval_roles": policy.approval_roles if req.status == RequestStatus.PENDING else [],
            "expires_at": req.expires_at,
        }

    def approve(self, request_id: str, approver: str, approver_role: str) -> dict:
        """Approve a pending access request."""
        req = self.requests.get(request_id)
        if not req:
            return {"error": f"Request {request_id} not found"}
        if req.status != RequestStatus.PENDING:
            return {"error": f"Request is {req.status.value}, not pending"}

        policy = ROLE_POLICIES.get(req.role)
        if policy and approver_role not in policy.approval_roles:
            return {"error": f"Role '{approver_role}' not authorized to approve {req.role.value}"}

        req.status = RequestStatus.APPROVED
        req.approved_by = approver
        req.approved_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
        self._activate(req)
        self._audit("access_approved", req, f"Approved by {approver} ({approver_role})")

        return {"request_id": request_id, "status": "active", "expires_at": req.expires_at}

    def deny(self, request_id: str, denier: str, reason: str = "") -> dict:
        req = self.requests.get(request_id)
        if not req:
            return {"error": f"Request {request_id} not found"}
        req.status = RequestStatus.DENIED
        self._audit("access_denied", req, f"Denied by {denier}: {reason}")
        return {"request_id": request_id, "status": "denied"}

    def revoke(self, request_id: str, revoker: str, reason: str = "") -> dict:
        req = self.requests.get(request_id)
        if not req:
            return {"error": f"Request {request_id} not found"}
        if req.status != RequestStatus.ACTIVE:
            return {"error": f"Request is {req.status.value}, not active"}
        req.status = RequestStatus.REVOKED
        req.revoked_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
        req.revoked_by = revoker
        req.revoke_reason = reason
        self._audit("access_revoked", req, f"Revoked by {revoker}: {reason}")
        logger.warning("Access REVOKED for %s (%s): %s", req.requester, req.role.value, reason)
        return {"request_id": request_id, "status": "revoked"}

    def _activate(self, req: AccessRequest):
        req.status = RequestStatus.ACTIVE
        req.activated_at = datetime.utcnow().isoformat()  # ty:ignore[deprecated]
        expires = datetime.utcnow() + timedelta(minutes=req.duration_minutes)  # ty:ignore[deprecated]
        req.expires_at = expires.isoformat()
        logger.info("Access ACTIVATED for %s: %s until %s", req.requester, req.role.value, req.expires_at)

    def check_expired(self) -> list[str]:
        """Check and expire any active requests past their expiry."""
        expired = []
        for req in self.requests.values():
            if req.status == RequestStatus.ACTIVE and req.expires_at:
                if datetime.utcnow() >= datetime.fromisoformat(req.expires_at):  # ty:ignore[deprecated]
                    req.status = RequestStatus.EXPIRED
                    self._audit("access_expired", req, "Auto-expired")
                    expired.append(req.id)
                    logger.info("Access EXPIRED for %s (%s)", req.requester, req.role.value)
        return expired

    def record_action(self, request_id: str, action: str, details: str = ""):
        """Record an action performed under JIT access."""
        req = self.requests.get(request_id)
        if req and req.is_active():
            entry = {"action": action, "details": details,
                     "timestamp": datetime.utcnow().isoformat()}  # ty:ignore[deprecated]
            req.actions_performed.append(entry)
            self._audit("action_performed", req, f"{action}: {details}")

    def _audit(self, action: str, req: AccessRequest, details: str):
        self.audit_log.append(AuditEntry(
            timestamp=datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            request_id=req.id, actor=req.requester,
            action=action, details=details, ip_address=req.ip_address,
        ))

    def get_status(self) -> dict:
        self.check_expired()
        active = [r for r in self.requests.values() if r.status == RequestStatus.ACTIVE]
        pending = [r for r in self.requests.values() if r.status == RequestStatus.PENDING]
        return {
            "total_requests": len(self.requests),
            "active": len(active),
            "pending": len(pending),
            "active_sessions": [{
                "id": r.id, "requester": r.requester, "role": r.role.value,
                "reason": r.reason, "expires_at": r.expires_at,
                "actions_count": len(r.actions_performed),
            } for r in active],
            "pending_approvals": [{
                "id": r.id, "requester": r.requester, "role": r.role.value,
                "reason": r.reason, "created_at": r.created_at,
            } for r in pending],
        }

    def get_audit_log(self, limit: int = 50) -> list[dict]:
        return [asdict(e) for e in self.audit_log[-limit:]]


# ---------------------------------------------------------------------------
# Demo and CLI
# ---------------------------------------------------------------------------

def demo():
    mgr = JITAccessManager()
    print("=== JIT Access Manager Demo ===\n")

    # Request DB admin access
    r1 = mgr.request_access("john.doe", "sre_engineer", AccessRole.DB_ADMIN,
                             "Investigate stuck withdrawal TX-45892", 30)
    print(f"1. Request: {json.dumps(r1, indent=2)}\n")

    # Approve
    r2 = mgr.approve(r1["request_id"], "jane.smith", "cto")
    print(f"2. Approve: {json.dumps(r2, indent=2)}\n")

    # Record actions
    mgr.record_action(r1["request_id"], "SELECT", "Queried transactions table for TX-45892")
    mgr.record_action(r1["request_id"], "UPDATE", "Updated TX-45892 status from stuck to completed")

    # Request payment admin (denied)
    r3 = mgr.request_access("bob.hacker", "cs_agent", AccessRole.PAYMENT_ADMIN,
                             "Need to issue refund", 30)
    r4 = mgr.deny(r3["request_id"], "jane.smith", "CS agents cannot have payment admin access")
    print(f"3. Deny: {json.dumps(r4, indent=2)}\n")

    # Status
    print(f"4. Status:\n{json.dumps(mgr.get_status(), indent=2)}\n")

    # Audit log
    print(f"5. Audit Log (last 10):")
    for entry in mgr.get_audit_log(10):
        print(f"   [{entry['timestamp'][:19]}] {entry['action']:20s} {entry['actor']:15s} {entry['details'][:60]}")


def main():
    parser = argparse.ArgumentParser(description="iGaming JIT Access Manager")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--roles", action="store_true", help="List available roles")
    parser.add_argument("--request", action="store_true")
    parser.add_argument("--role", type=str)
    parser.add_argument("--reason", type=str, default="")
    parser.add_argument("--duration", type=int, default=30)
    args = parser.parse_args()

    if args.demo:
        demo()
    elif args.roles:
        print("\nAvailable JIT Access Roles:\n")
        for role, policy in ROLE_POLICIES.items():
            print(f"  {role.value:25s} Max: {policy.max_duration_minutes:4d}m  "
                  f"Approval: {policy.requires_approval}  {policy.description}")
    else:
        print("Usage: python jit_access_manager.py --demo")
        print("       python jit_access_manager.py --roles")


if __name__ == "__main__":
    main()
