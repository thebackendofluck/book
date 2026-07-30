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
rbac_generator.py - RBAC Matrix Generator with Segregation-of-Duties Validation

OGIS-2 requires:
  - Role definitions documented and approved
  - Quarterly access reviews with manager sign-off
  - Automated orphaned account detection
  - Segregation-of-duties (SoD) matrix with automated conflict detection

This tool generates an RBAC matrix for iGaming platforms, validates
segregation-of-duties policies, detects conflicts, and produces
evidence reports for GLI-GSF assessments.

iGaming SoD principles:
  - Game configuration and game payouts must be segregated
  - Payment approval and payment processing must be segregated
  - Player account management and bonus granting must be segregated
  - Security administration and system administration must be segregated
  - RNG management and game result verification must be segregated

Usage:
    python3 rbac_generator.py --generate          # Generate RBAC matrix
    python3 rbac_generator.py --validate           # Validate SoD conflicts
    python3 rbac_generator.py --audit              # Run quarterly audit
    python3 rbac_generator.py --export csv > rbac.csv

Requirements:
    No external dependencies (standard library only)
"""

import argparse
import csv
import io
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

VERSION = "1.0.0"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rbac-generator")


# ---------------------------------------------------------------------------
# iGaming Permission Model
# ---------------------------------------------------------------------------
class Permission:
    """Permissions relevant to iGaming platforms."""
    # Game Operations
    GAME_CONFIG = "game:config"
    GAME_DEPLOY = "game:deploy"
    GAME_PAYOUT_VIEW = "game:payout:view"
    GAME_PAYOUT_MODIFY = "game:payout:modify"
    GAME_RNG_MANAGE = "game:rng:manage"
    GAME_RESULT_VERIFY = "game:result:verify"

    # Player Management
    PLAYER_VIEW = "player:view"
    PLAYER_EDIT = "player:edit"
    PLAYER_KYC_REVIEW = "player:kyc:review"
    PLAYER_SUSPEND = "player:suspend"
    PLAYER_CLOSE = "player:close"

    # Financial
    PAYMENT_VIEW = "payment:view"
    PAYMENT_APPROVE = "payment:approve"
    PAYMENT_PROCESS = "payment:process"
    PAYMENT_REFUND = "payment:refund"
    PAYMENT_CONFIG = "payment:config"

    # Bonus/Promotions
    BONUS_CREATE = "bonus:create"
    BONUS_APPROVE = "bonus:approve"
    BONUS_GRANT = "bonus:grant"
    BONUS_CONFIG = "bonus:config"

    # Compliance/AML
    AML_VIEW = "aml:view"
    AML_INVESTIGATE = "aml:investigate"
    AML_SAR_SUBMIT = "aml:sar:submit"
    AML_CONFIG = "aml:config"

    # Administration
    ADMIN_USER_MANAGE = "admin:user:manage"
    ADMIN_ROLE_MANAGE = "admin:role:manage"
    ADMIN_AUDIT_VIEW = "admin:audit:view"
    ADMIN_CONFIG = "admin:config"

    # Security
    SECURITY_ADMIN = "security:admin"
    SECURITY_SIEM_VIEW = "security:siem:view"
    SECURITY_INCIDENT = "security:incident"
    SECURITY_VULN_MANAGE = "security:vuln:manage"

    # Infrastructure
    INFRA_ADMIN = "infra:admin"
    INFRA_DEPLOY = "infra:deploy"
    INFRA_MONITOR = "infra:monitor"
    INFRA_DB_ADMIN = "infra:db:admin"

    # Reporting
    REPORT_FINANCIAL = "report:financial"
    REPORT_PLAYER = "report:player"
    REPORT_COMPLIANCE = "report:compliance"
    REPORT_OPERATIONAL = "report:operational"


# ---------------------------------------------------------------------------
# Role Definitions
# ---------------------------------------------------------------------------
@dataclass
class Role:
    """An RBAC role with permissions."""
    name: str
    description: str
    permissions: Set[str]
    criticality: str  # critical, high, medium, low
    mfa_requirement: str  # hardware, software, any
    session_timeout_min: int = 15
    max_concurrent_sessions: int = 1
    quarterly_review_required: bool = True


# Standard iGaming platform roles
IGAMING_ROLES: Dict[str, Role] = {
    "platform-admin": Role(
        name="Platform Administrator",
        description="Full platform administration (restricted SoD applies)",
        permissions={
            Permission.ADMIN_USER_MANAGE, Permission.ADMIN_ROLE_MANAGE,
            Permission.ADMIN_CONFIG, Permission.ADMIN_AUDIT_VIEW,
            Permission.INFRA_MONITOR, Permission.REPORT_OPERATIONAL,
        },
        criticality="critical",
        mfa_requirement="hardware",
        session_timeout_min=15,
    ),
    "game-manager": Role(
        name="Game Manager",
        description="Manages game configuration and deployment",
        permissions={
            Permission.GAME_CONFIG, Permission.GAME_DEPLOY,
            Permission.GAME_PAYOUT_VIEW, Permission.REPORT_OPERATIONAL,
        },
        criticality="high",
        mfa_requirement="hardware",
    ),
    "game-analyst": Role(
        name="Game Analyst",
        description="Views game performance and verifies results",
        permissions={
            Permission.GAME_PAYOUT_VIEW, Permission.GAME_RESULT_VERIFY,
            Permission.REPORT_OPERATIONAL,
        },
        criticality="medium",
        mfa_requirement="software",
    ),
    "rng-admin": Role(
        name="RNG Administrator",
        description="Manages RNG systems (OGIS-1 critical role)",
        permissions={
            Permission.GAME_RNG_MANAGE, Permission.ADMIN_AUDIT_VIEW,
        },
        criticality="critical",
        mfa_requirement="hardware",
    ),
    "payment-approver": Role(
        name="Payment Approver",
        description="Approves payment transactions",
        permissions={
            Permission.PAYMENT_VIEW, Permission.PAYMENT_APPROVE,
            Permission.REPORT_FINANCIAL,
        },
        criticality="critical",
        mfa_requirement="hardware",
    ),
    "payment-processor": Role(
        name="Payment Processor",
        description="Processes approved payment transactions",
        permissions={
            Permission.PAYMENT_VIEW, Permission.PAYMENT_PROCESS,
            Permission.PAYMENT_REFUND,
        },
        criticality="critical",
        mfa_requirement="hardware",
    ),
    "payment-config": Role(
        name="Payment Configuration Manager",
        description="Configures payment methods and providers",
        permissions={
            Permission.PAYMENT_CONFIG, Permission.PAYMENT_VIEW,
        },
        criticality="high",
        mfa_requirement="hardware",
    ),
    "player-support": Role(
        name="Player Support Agent",
        description="First-line player support",
        permissions={
            Permission.PLAYER_VIEW, Permission.PLAYER_EDIT,
        },
        criticality="medium",
        mfa_requirement="software",
    ),
    "player-manager": Role(
        name="Player Account Manager",
        description="Advanced player management including suspension",
        permissions={
            Permission.PLAYER_VIEW, Permission.PLAYER_EDIT,
            Permission.PLAYER_SUSPEND, Permission.PLAYER_KYC_REVIEW,
        },
        criticality="high",
        mfa_requirement="software",
    ),
    "bonus-manager": Role(
        name="Bonus/Promotions Manager",
        description="Creates and manages bonus campaigns",
        permissions={
            Permission.BONUS_CREATE, Permission.BONUS_CONFIG,
            Permission.REPORT_PLAYER,
        },
        criticality="high",
        mfa_requirement="software",
    ),
    "bonus-approver": Role(
        name="Bonus Approver",
        description="Approves bonus campaigns and grants",
        permissions={
            Permission.BONUS_APPROVE, Permission.BONUS_GRANT,
            Permission.REPORT_PLAYER,
        },
        criticality="high",
        mfa_requirement="software",
    ),
    "compliance-officer": Role(
        name="Compliance Officer",
        description="AML compliance and regulatory reporting",
        permissions={
            Permission.AML_VIEW, Permission.AML_INVESTIGATE,
            Permission.AML_SAR_SUBMIT, Permission.AML_CONFIG,
            Permission.REPORT_COMPLIANCE, Permission.REPORT_FINANCIAL,
            Permission.ADMIN_AUDIT_VIEW,
        },
        criticality="critical",
        mfa_requirement="hardware",
    ),
    "gis-officer": Role(
        name="GIS Officer",
        description="Gaming Information Security Officer (GLI-GSF-1)",
        permissions={
            Permission.SECURITY_ADMIN, Permission.SECURITY_SIEM_VIEW,
            Permission.SECURITY_INCIDENT, Permission.SECURITY_VULN_MANAGE,
            Permission.ADMIN_AUDIT_VIEW, Permission.REPORT_COMPLIANCE,
        },
        criticality="critical",
        mfa_requirement="hardware",
    ),
    "soc-analyst": Role(
        name="SOC Analyst",
        description="Security Operations Center analyst",
        permissions={
            Permission.SECURITY_SIEM_VIEW, Permission.SECURITY_INCIDENT,
            Permission.ADMIN_AUDIT_VIEW,
        },
        criticality="high",
        mfa_requirement="software",
    ),
    "infra-engineer": Role(
        name="Infrastructure Engineer",
        description="Platform infrastructure management",
        permissions={
            Permission.INFRA_ADMIN, Permission.INFRA_DEPLOY,
            Permission.INFRA_MONITOR,
        },
        criticality="high",
        mfa_requirement="hardware",
    ),
    "dba": Role(
        name="Database Administrator",
        description="Database administration and maintenance",
        permissions={
            Permission.INFRA_DB_ADMIN, Permission.INFRA_MONITOR,
        },
        criticality="critical",
        mfa_requirement="hardware",
    ),
    "auditor": Role(
        name="Internal Auditor",
        description="Read-only access for audit purposes",
        permissions={
            Permission.ADMIN_AUDIT_VIEW, Permission.REPORT_FINANCIAL,
            Permission.REPORT_PLAYER, Permission.REPORT_COMPLIANCE,
            Permission.REPORT_OPERATIONAL,
        },
        criticality="medium",
        mfa_requirement="software",
    ),
    "isf-assessor": Role(
        name="ISF Assessor (External)",
        description="Independent Security Firm assessor (GLI-GSF-2)",
        permissions={
            Permission.ADMIN_AUDIT_VIEW, Permission.REPORT_COMPLIANCE,
            Permission.SECURITY_SIEM_VIEW,
        },
        criticality="high",
        mfa_requirement="hardware",
        quarterly_review_required=False,  # Time-limited access
    ),
}


# ---------------------------------------------------------------------------
# Segregation of Duties (SoD) Rules
# ---------------------------------------------------------------------------
@dataclass
class SoDRule:
    """A segregation-of-duties rule."""
    rule_id: str
    description: str
    conflicting_permissions: Tuple[str, str]
    severity: str  # critical, high, medium
    regulatory_reference: str


# SoD rules for iGaming platforms
SOD_RULES: List[SoDRule] = [
    SoDRule(
        rule_id="SOD-001",
        description="Game configuration and payout modification must be segregated",
        conflicting_permissions=(Permission.GAME_CONFIG, Permission.GAME_PAYOUT_MODIFY),
        severity="critical",
        regulatory_reference="OGIS-3 (Server-Side Integrity)",
    ),
    SoDRule(
        rule_id="SOD-002",
        description="Payment approval and payment processing must be segregated",
        conflicting_permissions=(Permission.PAYMENT_APPROVE, Permission.PAYMENT_PROCESS),
        severity="critical",
        regulatory_reference="OGIS-2 (Back Office Security)",
    ),
    SoDRule(
        rule_id="SOD-003",
        description="Player account management and bonus granting must be segregated",
        conflicting_permissions=(Permission.PLAYER_EDIT, Permission.BONUS_GRANT),
        severity="high",
        regulatory_reference="OGIS-2 (Back Office Security)",
    ),
    SoDRule(
        rule_id="SOD-004",
        description="Security administration and system administration must be segregated",
        conflicting_permissions=(Permission.SECURITY_ADMIN, Permission.INFRA_ADMIN),
        severity="critical",
        regulatory_reference="GLI-GSF-1 Section 2.3.5",
    ),
    SoDRule(
        rule_id="SOD-005",
        description="RNG management and game result verification must be segregated",
        conflicting_permissions=(Permission.GAME_RNG_MANAGE, Permission.GAME_RESULT_VERIFY),
        severity="critical",
        regulatory_reference="OGIS-1 (Critical Control Program Verification)",
    ),
    SoDRule(
        rule_id="SOD-006",
        description="User management and role management require dual approval",
        conflicting_permissions=(Permission.ADMIN_USER_MANAGE, Permission.ADMIN_ROLE_MANAGE),
        severity="high",
        regulatory_reference="OGIS-2 (Back Office Security)",
    ),
    SoDRule(
        rule_id="SOD-007",
        description="Bonus creation and bonus approval must be segregated",
        conflicting_permissions=(Permission.BONUS_CREATE, Permission.BONUS_APPROVE),
        severity="high",
        regulatory_reference="OGIS-2 (Back Office Security)",
    ),
    SoDRule(
        rule_id="SOD-008",
        description="Payment configuration and payment processing must be segregated",
        conflicting_permissions=(Permission.PAYMENT_CONFIG, Permission.PAYMENT_PROCESS),
        severity="critical",
        regulatory_reference="OGIS-2 (Back Office Security)",
    ),
    SoDRule(
        rule_id="SOD-009",
        description="AML investigation and SAR submission should be reviewed",
        conflicting_permissions=(Permission.AML_INVESTIGATE, Permission.AML_SAR_SUBMIT),
        severity="medium",
        regulatory_reference="GLI-GSF-1 Section 2.3.9",
    ),
    SoDRule(
        rule_id="SOD-010",
        description="Database admin and security admin must be segregated",
        conflicting_permissions=(Permission.INFRA_DB_ADMIN, Permission.SECURITY_ADMIN),
        severity="high",
        regulatory_reference="GLI-GSF-1 Section 2.3.5",
    ),
]


# ---------------------------------------------------------------------------
# SoD Validation Engine
# ---------------------------------------------------------------------------
@dataclass
class SoDViolation:
    """A detected SoD violation."""
    rule: SoDRule
    role_name: str
    has_both_permissions: bool


def validate_sod(roles: Dict[str, Role]) -> List[SoDViolation]:
    """Validate all roles against SoD rules."""
    violations = []

    for role_key, role in roles.items():
        for rule in SOD_RULES:
            perm_a, perm_b = rule.conflicting_permissions
            if perm_a in role.permissions and perm_b in role.permissions:
                violations.append(SoDViolation(
                    rule=rule,
                    role_name=f"{role_key} ({role.name})",
                    has_both_permissions=True,
                ))

    return violations


# ---------------------------------------------------------------------------
# User-Role Assignment Validation
# ---------------------------------------------------------------------------
@dataclass
class UserAssignment:
    """A user's role assignments."""
    username: str
    email: str
    roles: List[str]
    department: str
    manager: str
    last_review_date: str
    status: str = "active"


def validate_user_sod(
    users: List[UserAssignment], roles: Dict[str, Role]
) -> List[dict]:
    """Check for SoD conflicts across a user's combined role permissions."""
    conflicts = []

    for user in users:
        # Collect all permissions across all assigned roles
        all_permissions: Set[str] = set()
        for role_key in user.roles:
            if role_key in roles:
                all_permissions.update(roles[role_key].permissions)

        # Check against SoD rules
        for rule in SOD_RULES:
            perm_a, perm_b = rule.conflicting_permissions
            if perm_a in all_permissions and perm_b in all_permissions:
                # Find which roles contribute the conflict
                roles_with_a = [
                    r for r in user.roles
                    if r in roles and perm_a in roles[r].permissions
                ]
                roles_with_b = [
                    r for r in user.roles
                    if r in roles and perm_b in roles[r].permissions
                ]

                conflicts.append({
                    "user": user.username,
                    "email": user.email,
                    "rule_id": rule.rule_id,
                    "description": rule.description,
                    "severity": rule.severity,
                    "permission_a": perm_a,
                    "permission_b": perm_b,
                    "roles_granting_a": roles_with_a,
                    "roles_granting_b": roles_with_b,
                })

    return conflicts


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
def generate_matrix_report(roles: Dict[str, Role]) -> str:
    """Generate RBAC matrix as Markdown."""
    # Collect all permissions
    all_perms = sorted(set(
        p for role in roles.values() for p in role.permissions
    ))

    lines = [
        "# RBAC Matrix - iGaming Platform",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Date** | {datetime.now(timezone.utc).strftime('%Y-%m-%d')} |",
        f"| **GLI-GSF Reference** | OGIS-2 |",
        f"| **Total Roles** | {len(roles)} |",
        f"| **Total Permissions** | {len(all_perms)} |",
        f"| **SoD Rules** | {len(SOD_RULES)} |",
        "",
        "## Role Definitions",
        "",
    ]

    for role_key, role in roles.items():
        lines.extend([
            f"### {role.name} (`{role_key}`)",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| **Criticality** | {role.criticality} |",
            f"| **MFA Requirement** | {role.mfa_requirement} token |",
            f"| **Session Timeout** | {role.session_timeout_min} min |",
            f"| **Max Sessions** | {role.max_concurrent_sessions} |",
            f"| **Quarterly Review** | {'Yes' if role.quarterly_review_required else 'No'} |",
            "",
            f"**Permissions:** {', '.join(sorted(role.permissions))}",
            "",
        ])

    # Permission matrix table
    lines.extend([
        "## Permission Matrix",
        "",
    ])

    # Header
    role_keys = list(roles.keys())
    header = "| Permission |" + "|".join(f" {k[:10]} " for k in role_keys) + "|"
    separator = "|" + "|".join("-" * max(len(k[:10]) + 2, 3) for k in ["Permission"] + role_keys) + "|"
    lines.append(header)
    lines.append(separator)

    for perm in all_perms:
        row = f"| `{perm}` |"
        for rk in role_keys:
            if perm in roles[rk].permissions:
                row += " X |"
            else:
                row += "   |"
        lines.append(row)

    # SoD rules
    lines.extend([
        "",
        "## Segregation of Duties Rules",
        "",
        "| Rule ID | Description | Conflicting Permissions | Severity | Reference |",
        "|---------|-------------|------------------------|----------|-----------|",
    ])

    for rule in SOD_RULES:
        lines.append(
            f"| {rule.rule_id} | {rule.description} | "
            f"`{rule.conflicting_permissions[0]}` vs "
            f"`{rule.conflicting_permissions[1]}` | "
            f"**{rule.severity}** | {rule.regulatory_reference} |"
        )

    # SoD validation results
    violations = validate_sod(roles)
    lines.extend([
        "",
        "## SoD Validation Results",
        "",
    ])

    if violations:
        lines.append(f"**{len(violations)} violation(s) detected:**")
        lines.append("")
        for v in violations:
            lines.append(
                f"- **{v.rule.rule_id}**: Role `{v.role_name}` violates "
                f"\"{v.rule.description}\" [{v.rule.severity}]"
            )
    else:
        lines.append("No SoD violations detected in role definitions.")

    lines.extend([
        "",
        "---",
        f"*Generated by rbac_generator.py v{VERSION}*",
    ])

    return "\n".join(lines)


def generate_audit_template() -> str:
    """Generate quarterly access review template."""
    lines = [
        "# Quarterly Access Review Template",
        "",
        f"**Review Period:** {datetime.now(timezone.utc).strftime('%Y-Q%q').replace('%q', str((datetime.now().month - 1) // 3 + 1))}",
        f"**GLI-GSF Reference:** OGIS-2 (Quarterly Access Reviews)",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        "",
        "## Instructions",
        "",
        "1. Review each user's role assignments below",
        "2. Verify each assignment is still required (business justification)",
        "3. Check for orphaned accounts (employees who have left)",
        "4. Validate SoD compliance across multi-role users",
        "5. Manager must sign off on each user's access",
        "6. Submit completed review to GIS Officer",
        "",
        "## Review Checklist",
        "",
        "- [ ] All active users reviewed",
        "- [ ] Orphaned accounts identified and disabled",
        "- [ ] SoD conflicts resolved or documented with risk acceptance",
        "- [ ] Manager sign-off obtained for all users",
        "- [ ] Service accounts reviewed (no interactive login)",
        "- [ ] Vendor accounts reviewed (time-limited, GLI-GSF-3)",
        "- [ ] MFA status verified for all accounts (100% coverage)",
        "- [ ] Review evidence stored in retention system (5-year)",
        "",
        "## User Access Review",
        "",
        "| # | Username | Email | Roles | Department | Manager | Action | Sign-off |",
        "|---|----------|-------|-------|------------|---------|--------|----------|",
        "| 1 | | | | | | Retain / Modify / Revoke | |",
        "| 2 | | | | | | Retain / Modify / Revoke | |",
        "| 3 | | | | | | Retain / Modify / Revoke | |",
        "",
        "## Review Sign-off",
        "",
        "| Role | Name | Signature | Date |",
        "|------|------|-----------|------|",
        "| Reviewer | | | |",
        "| GIS Officer | | | |",
        "| Compliance Officer | | | |",
        "",
        "---",
        f"*Generated by rbac_generator.py v{VERSION}*",
    ]
    return "\n".join(lines)


def export_csv(roles: Dict[str, Role]) -> str:
    """Export RBAC matrix as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Role Key", "Role Name", "Permission", "Criticality",
        "MFA Requirement", "Session Timeout (min)", "Quarterly Review",
    ])
    for role_key, role in roles.items():
        for perm in sorted(role.permissions):
            writer.writerow([
                role_key, role.name, perm, role.criticality,
                role.mfa_requirement, role.session_timeout_min,
                role.quarterly_review_required,
            ])
    return output.getvalue()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="RBAC Matrix Generator with SoD Validation (OGIS-2)",
    )
    parser.add_argument("--generate", action="store_true", help="Generate RBAC matrix report")
    parser.add_argument("--validate", action="store_true", help="Validate SoD rules")
    parser.add_argument("--audit", action="store_true", help="Generate quarterly review template")
    parser.add_argument("--export", choices=["csv", "json"], help="Export format")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--version", action="version", version=f"v{VERSION}")

    args = parser.parse_args()

    roles = IGAMING_ROLES

    if args.validate:
        violations = validate_sod(roles)
        if violations:
            logger.warning(f"Found {len(violations)} SoD violations:")
            for v in violations:
                logger.warning(
                    f"  {v.rule.rule_id}: {v.role_name} - {v.rule.description}"
                )
            sys.exit(1)
        else:
            logger.info("No SoD violations detected.")
            sys.exit(0)

    if args.audit:
        result = generate_audit_template()
    elif args.export == "csv":
        result = export_csv(roles)
    elif args.export == "json":
        result = json.dumps(
            {k: {**asdict(v), "permissions": sorted(v.permissions)} for k, v in roles.items()},
            indent=2,
        )
    else:
        result = generate_matrix_report(roles)

    if args.output:
        from pathlib import Path
        Path(args.output).write_text(result)
        logger.info(f"Report saved to {args.output}")
    else:
        print(result)


if __name__ == "__main__":
    main()
