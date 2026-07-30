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
cis-controls-mapper.py - CIS Controls v8.1 to GLI-GSF-1 Appendix A Mapper

GLI-GSF-1 Appendix A maps directly to CIS Controls v8.1. This tool provides
the complete mapping, generates compliance gap analysis reports, and tracks
implementation status for each control.

CIS Controls v8.1 has 18 control families with 153 safeguards organized
into three Implementation Groups (IGs):
  - IG1: Essential cyber hygiene (56 safeguards)
  - IG2: Enterprise-level (74 additional safeguards, 130 total)
  - IG3: Advanced/complex environments (23 additional, 153 total)

For online gaming operators (GIG3), all three IGs are typically required.

Usage:
    python3 cis-controls-mapper.py --report
    python3 cis-controls-mapper.py --gap-analysis --output gap-report.md
    python3 cis-controls-mapper.py --export csv > controls.csv
    python3 cis-controls-mapper.py --control 1 --details

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
from typing import Dict, List, Optional

VERSION = "1.0.0"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cis-mapper")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
class ImplementationStatus(str):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    IMPLEMENTED = "Implemented"
    NOT_APPLICABLE = "N/A"


@dataclass
class CISSafeguard:
    """A single CIS Controls v8.1 safeguard."""
    control_id: str          # e.g., "1.1"
    control_family: str      # e.g., "Inventory and Control of Enterprise Assets"
    safeguard: str           # Safeguard title
    description: str         # What the safeguard requires
    ig_level: int            # 1, 2, or 3
    asset_type: str          # Devices, Users, Applications, Network, Data
    security_function: str   # Identify, Protect, Detect, Respond, Recover
    gsf_reference: str       # GLI-GSF-1 Appendix A section reference
    ogis_relevance: List[str]  # Which OGIS domains this supports
    igaming_context: str     # How this applies to online gaming specifically
    implementation_tools: List[str]  # Tools/technologies for implementation
    status: str = ImplementationStatus.NOT_STARTED
    evidence_location: str = ""
    notes: str = ""


# ---------------------------------------------------------------------------
# CIS Controls v8.1 to GLI-GSF-1 Mapping Database
# ---------------------------------------------------------------------------
# This covers the most critical controls for online gaming operators.
# Full mapping should be customized per operator's GPE scope.

CIS_CONTROLS_MAP: List[CISSafeguard] = [
    # =========================================================================
    # Control 1: Inventory and Control of Enterprise Assets
    # =========================================================================
    CISSafeguard(
        control_id="1.1",
        control_family="Inventory and Control of Enterprise Assets",
        safeguard="Establish and Maintain Detailed Enterprise Asset Inventory",
        description=(
            "Establish and maintain an accurate, detailed, and up-to-date "
            "inventory of all enterprise assets with the potential to store "
            "or process data, to include: end-user devices, network devices, "
            "non-computing/IoT devices, and servers."
        ),
        ig_level=1,
        asset_type="Devices",
        security_function="Identify",
        gsf_reference="GLI-GSF-1 Section 1.3 (CSC Inventory)",
        ogis_relevance=["OGIS-1", "OGIS-3"],
        igaming_context=(
            "Maps directly to CSC inventory within the GPE. Must include "
            "game servers, RNG systems, payment gateways, player databases, "
            "bonus engines, and all supporting infrastructure."
        ),
        implementation_tools=["csc-inventory.py", "AWS Config", "ServiceNow CMDB"],
    ),
    CISSafeguard(
        control_id="1.2",
        control_family="Inventory and Control of Enterprise Assets",
        safeguard="Address Unauthorized Assets",
        description=(
            "Ensure that a process exists to address unauthorized assets "
            "on a weekly basis. Remove from the network, deny access, "
            "or quarantine."
        ),
        ig_level=1,
        asset_type="Devices",
        security_function="Respond",
        gsf_reference="GLI-GSF-1 Section 1.3",
        ogis_relevance=["OGIS-3"],
        igaming_context=(
            "Unauthorized devices in the GPE represent a direct compliance "
            "risk. Rogue game servers or unregistered payment endpoints "
            "must be identified and removed."
        ),
        implementation_tools=["Nmap", "AWS Config Rules", "NAC solutions"],
    ),

    # =========================================================================
    # Control 2: Inventory and Control of Software Assets
    # =========================================================================
    CISSafeguard(
        control_id="2.1",
        control_family="Inventory and Control of Software Assets",
        safeguard="Establish and Maintain a Software Inventory",
        description=(
            "Establish and maintain a detailed inventory of all licensed "
            "software installed on enterprise assets."
        ),
        ig_level=1,
        asset_type="Applications",
        security_function="Identify",
        gsf_reference="GLI-GSF-1 Section 2.3.4",
        ogis_relevance=["OGIS-1", "OGIS-3"],
        igaming_context=(
            "Critical for OGIS-1 signature verification. Every Critical "
            "Control Program (RNG, game logic, payout calculation) must be "
            "inventoried with version and cryptographic hash."
        ),
        implementation_tools=["SBOM tools", "Snyk", "Syft/Grype"],
    ),
    CISSafeguard(
        control_id="2.3",
        control_family="Inventory and Control of Software Assets",
        safeguard="Address Unauthorized Software",
        description=(
            "Ensure that unauthorized software is either removed or the "
            "inventory is updated on a monthly basis."
        ),
        ig_level=1,
        asset_type="Applications",
        security_function="Respond",
        gsf_reference="GLI-GSF-1 Section 2.3.4",
        ogis_relevance=["OGIS-3"],
        igaming_context=(
            "Application whitelisting is explicitly required by OGIS-3. "
            "Unauthorized code on game servers is a critical finding."
        ),
        implementation_tools=["AppLocker", "SELinux", "AWS SSM"],
    ),
    CISSafeguard(
        control_id="2.5",
        control_family="Inventory and Control of Software Assets",
        safeguard="Allowlist Authorized Software",
        description=(
            "Use technical controls to ensure only authorized software "
            "can execute on enterprise assets."
        ),
        ig_level=2,
        asset_type="Applications",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.4, OGIS-3",
        ogis_relevance=["OGIS-3"],
        igaming_context=(
            "OGIS-3 mandates application whitelisting and code signing. "
            "Only verified game binaries and platform components should "
            "be executable within the GPE."
        ),
        implementation_tools=["Code signing", "Container image signing", "Cosign"],
    ),

    # =========================================================================
    # Control 3: Data Protection
    # =========================================================================
    CISSafeguard(
        control_id="3.1",
        control_family="Data Protection",
        safeguard="Establish and Maintain a Data Management Process",
        description=(
            "Establish and maintain a data management process. Address data "
            "sensitivity, data owner, handling, retention, and disposal."
        ),
        ig_level=1,
        asset_type="Data",
        security_function="Identify",
        gsf_reference="GLI-GSF-1 Section 2.3.10 (Record Retention)",
        ogis_relevance=["OGIS-1", "OGIS-2"],
        igaming_context=(
            "GLI-GSF requires 5-year retention for all audit logs, incident "
            "reports, and assessment results. Player data classification "
            "must address PII, KYC documents, financial records, and "
            "gameplay data per GDPR/ePrivacy requirements."
        ),
        implementation_tools=["Data classification tools", "DLP", "MinIO lifecycle"],
    ),
    CISSafeguard(
        control_id="3.6",
        control_family="Data Protection",
        safeguard="Encrypt Data on End-User Devices",
        description="Encrypt data on end-user devices containing sensitive data.",
        ig_level=1,
        asset_type="Devices",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.6",
        ogis_relevance=["OGIS-4"],
        igaming_context=(
            "Mobile gaming apps must encrypt locally stored data. OGIS-4 "
            "requires tamper detection and anti-debugging. Any cached "
            "player data on mobile devices must be encrypted."
        ),
        implementation_tools=["Android Keystore", "iOS Keychain", "SQLCipher"],
    ),
    CISSafeguard(
        control_id="3.11",
        control_family="Data Protection",
        safeguard="Encrypt Sensitive Data at Rest",
        description="Encrypt sensitive data at rest on servers, databases, and removable media.",
        ig_level=1,
        asset_type="Data",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.6",
        ogis_relevance=["OGIS-2", "OGIS-3"],
        igaming_context=(
            "All player databases, payment records, and game state data "
            "must be encrypted at rest. RDS encryption, EBS encryption, "
            "and application-level field encryption for PII."
        ),
        implementation_tools=["AWS KMS", "HashiCorp Vault", "Transparent Data Encryption"],
    ),

    # =========================================================================
    # Control 4: Secure Configuration
    # =========================================================================
    CISSafeguard(
        control_id="4.1",
        control_family="Secure Configuration of Enterprise Assets and Software",
        safeguard="Establish and Maintain a Secure Configuration Process",
        description=(
            "Establish and maintain a secure configuration process for "
            "enterprise assets and software."
        ),
        ig_level=1,
        asset_type="Applications",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.4",
        ogis_relevance=["OGIS-3"],
        igaming_context=(
            "CIS Benchmarks should be applied to all GPE systems. "
            "Game servers, databases, and web servers must follow "
            "hardened configurations. Infrastructure as Code enables "
            "consistent secure configuration."
        ),
        implementation_tools=["CIS-CAT Pro", "Ansible", "Terraform", "AWS Config"],
    ),

    # =========================================================================
    # Control 5: Account Management
    # =========================================================================
    CISSafeguard(
        control_id="5.1",
        control_family="Account Management",
        safeguard="Establish and Maintain an Inventory of Accounts",
        description="Establish and maintain an inventory of all accounts managed in the enterprise.",
        ig_level=1,
        asset_type="Users",
        security_function="Identify",
        gsf_reference="GLI-GSF-1 Section 2.3.5, OGIS-2",
        ogis_relevance=["OGIS-2"],
        igaming_context=(
            "OGIS-2 requires complete visibility of all accounts with "
            "GPE access. Includes operator staff, vendor accounts, "
            "service accounts, and API keys. Orphaned account detection "
            "must be automated."
        ),
        implementation_tools=["IAM solutions", "Okta", "AWS IAM Access Analyzer"],
    ),
    CISSafeguard(
        control_id="5.4",
        control_family="Account Management",
        safeguard="Restrict Administrator Privileges to Dedicated Administrator Accounts",
        description="Restrict administrator privileges to dedicated administrator accounts.",
        ig_level=1,
        asset_type="Users",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.5, OGIS-2",
        ogis_relevance=["OGIS-2"],
        igaming_context=(
            "Segregation of duties is critical in gaming. An operator who "
            "manages player accounts should not have access to payment "
            "processing or game configuration. The RBAC matrix must "
            "enforce this separation."
        ),
        implementation_tools=["rbac_generator.py", "AWS Organizations SCPs", "Okta groups"],
    ),

    # =========================================================================
    # Control 6: Access Control Management
    # =========================================================================
    CISSafeguard(
        control_id="6.3",
        control_family="Access Control Management",
        safeguard="Require MFA for Externally-Exposed Applications",
        description="Require MFA for externally-exposed enterprise or third-party applications.",
        ig_level=1,
        asset_type="Users",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.5, OGIS-2",
        ogis_relevance=["OGIS-2"],
        igaming_context=(
            "OGIS-2 mandates 100% MFA coverage on administrative accounts. "
            "Hardware tokens for critical roles, software tokens for "
            "standard privileged users. No exceptions."
        ),
        implementation_tools=["mfa-audit.sh", "YubiKey", "Okta Verify", "Google Authenticator"],
    ),
    CISSafeguard(
        control_id="6.4",
        control_family="Access Control Management",
        safeguard="Require MFA for Remote Network Access",
        description="Require MFA for remote network access.",
        ig_level=1,
        asset_type="Users",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.5, OGIS-2",
        ogis_relevance=["OGIS-2"],
        igaming_context=(
            "All remote access to the GPE must use MFA. This includes "
            "VPN connections, bastion host access, and vendor remote "
            "sessions. GLI-GSF-3 adds session recording requirements "
            "for vendor access."
        ),
        implementation_tools=["VPN with MFA", "AWS SSM Session Manager", "Teleport"],
    ),
    CISSafeguard(
        control_id="6.5",
        control_family="Access Control Management",
        safeguard="Require MFA for Administrative Access",
        description="Require MFA for all administrative access accounts.",
        ig_level=1,
        asset_type="Users",
        security_function="Protect",
        gsf_reference="OGIS-2",
        ogis_relevance=["OGIS-2"],
        igaming_context=(
            "Every admin account accessing back-office, payment systems, "
            "game configuration, or infrastructure must have MFA. "
            "The ISF will test by attempting access without MFA."
        ),
        implementation_tools=["mfa-audit.sh", "AWS IAM MFA enforcement", "Conditional access policies"],
    ),

    # =========================================================================
    # Control 8: Audit Log Management
    # =========================================================================
    CISSafeguard(
        control_id="8.1",
        control_family="Audit Log Management",
        safeguard="Establish and Maintain an Audit Log Management Process",
        description="Establish and maintain an audit log management process.",
        ig_level=1,
        asset_type="Network",
        security_function="Detect",
        gsf_reference="GLI-GSF-1 Section 2.3.10, OGIS-1",
        ogis_relevance=["OGIS-1", "OGIS-3"],
        igaming_context=(
            "GLI-GSF requires 5-year retention of all audit logs with "
            "NTP-synchronized timestamps. OGIS-1 specifically requires "
            "signature verification logs in CSV, JSON, and XML formats "
            "accessible to regulators at any time."
        ),
        implementation_tools=["ELK Stack", "Wazuh", "CloudWatch Logs", "MinIO + lifecycle"],
    ),
    CISSafeguard(
        control_id="8.2",
        control_family="Audit Log Management",
        safeguard="Collect Audit Logs",
        description="Collect audit logs from enterprise assets.",
        ig_level=1,
        asset_type="Network",
        security_function="Detect",
        gsf_reference="GLI-GSF-1 Section 2.3.10",
        ogis_relevance=["OGIS-1", "OGIS-3"],
        igaming_context=(
            "Centralized log collection from all CSCs. Game session logs, "
            "payment transaction logs, admin action logs, and security "
            "event logs must all feed into the SIEM."
        ),
        implementation_tools=["Wazuh agents", "Filebeat", "Fluentd", "CloudWatch agent"],
    ),
    CISSafeguard(
        control_id="8.11",
        control_family="Audit Log Management",
        safeguard="Conduct Audit Log Reviews",
        description="Conduct reviews of audit logs to detect anomalies or abnormal events.",
        ig_level=2,
        asset_type="Network",
        security_function="Detect",
        gsf_reference="GLI-GSF-1 Section 2.3.10, OGIS-3",
        ogis_relevance=["OGIS-3"],
        igaming_context=(
            "OGIS-3 requires SIEM with sub-15-minute detection KPI. "
            "Automated log analysis must flag unusual gameplay patterns, "
            "payment anomalies, and unauthorized access attempts."
        ),
        implementation_tools=["Wazuh rules", "SIEM correlation", "ML anomaly detection"],
    ),

    # =========================================================================
    # Control 10: Malware Defenses
    # =========================================================================
    CISSafeguard(
        control_id="10.1",
        control_family="Malware Defenses",
        safeguard="Deploy and Maintain Anti-Malware Software",
        description="Deploy and maintain anti-malware software on all enterprise assets.",
        ig_level=1,
        asset_type="Devices",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.4",
        ogis_relevance=["OGIS-3"],
        igaming_context=(
            "All GPE servers must have endpoint protection. Container "
            "runtime security for containerized game services. "
            "Anti-malware scanning on file uploads (KYC documents)."
        ),
        implementation_tools=["ClamAV", "Falcon", "Wazuh FIM", "Trivy"],
    ),

    # =========================================================================
    # Control 13: Network Monitoring and Defense
    # =========================================================================
    CISSafeguard(
        control_id="13.1",
        control_family="Network Monitoring and Defense",
        safeguard="Centralize Security Event Alerting",
        description="Centralize security event alerting across enterprise assets.",
        ig_level=1,
        asset_type="Network",
        security_function="Detect",
        gsf_reference="OGIS-3",
        ogis_relevance=["OGIS-3"],
        igaming_context=(
            "OGIS-3 mandates SIEM with real-time alerting, behavioral "
            "analytics, and 24/7 SOC coverage. Mean time to detect "
            "must be under 15 minutes."
        ),
        implementation_tools=["Wazuh", "Splunk", "ELK + ElastAlert", "PagerDuty"],
    ),
    CISSafeguard(
        control_id="13.6",
        control_family="Network Monitoring and Defense",
        safeguard="Collect Network Traffic Flow Logs",
        description="Collect network traffic flow logs and/or network traffic.",
        ig_level=2,
        asset_type="Network",
        security_function="Detect",
        gsf_reference="GLI-GSF-1 Section 2.3.8, OGIS-5",
        ogis_relevance=["OGIS-5"],
        igaming_context=(
            "Network flow analysis is critical for DDoS detection (OGIS-5). "
            "VPC flow logs, WAF logs, and CDN analytics must be collected "
            "and analyzed for volumetric attack indicators."
        ),
        implementation_tools=["VPC Flow Logs", "AWS WAF logs", "Cloudflare analytics"],
    ),

    # =========================================================================
    # Control 14: Security Awareness and Skills Training
    # =========================================================================
    CISSafeguard(
        control_id="14.1",
        control_family="Security Awareness and Skills Training",
        safeguard="Establish and Maintain a Security Awareness Program",
        description="Establish and maintain a security awareness program.",
        ig_level=1,
        asset_type="N/A",
        security_function="Protect",
        gsf_reference="GLI-GSF-1 Section 2.3.3",
        ogis_relevance=[],
        igaming_context=(
            "All GPE personnel must complete security awareness training. "
            "Gaming-specific modules should cover social engineering "
            "targeting operator staff, responsible gaming obligations, "
            "and data handling for player PII/KYC."
        ),
        implementation_tools=["KnowBe4", "Security awareness LMS", "Phishing simulations"],
    ),

    # =========================================================================
    # Control 16: Application Software Security
    # =========================================================================
    CISSafeguard(
        control_id="16.1",
        control_family="Application Software Security",
        safeguard="Establish and Maintain a Secure Application Development Process",
        description="Establish and maintain a secure application development process.",
        ig_level=2,
        asset_type="Applications",
        security_function="Protect",
        gsf_reference="OGIS-3, OGIS-4",
        ogis_relevance=["OGIS-3", "OGIS-4"],
        igaming_context=(
            "OGIS-3 mandates secure coding practices for all game logic "
            "and platform code. Server-side validation, input sanitization, "
            "and code signing are explicit requirements. See Chapter 11 "
            "for DevSecOps pipeline implementation."
        ),
        implementation_tools=["SAST (Semgrep)", "DAST (ZAP)", "SCA (Snyk)", "Code signing"],
    ),
    CISSafeguard(
        control_id="16.4",
        control_family="Application Software Security",
        safeguard="Establish and Manage an Inventory of Third-Party Software Components",
        description="Establish and manage an inventory of third-party software components.",
        ig_level=2,
        asset_type="Applications",
        security_function="Identify",
        gsf_reference="GLI-GSF-3 (Vendor Risk)",
        ogis_relevance=["OGIS-1"],
        igaming_context=(
            "Every third-party game provider SDK, payment library, and "
            "integration component must be inventoried. GLI-GSF-3 requires "
            "vendor supply chain risk management with SBOMs."
        ),
        implementation_tools=["Syft", "Grype", "OWASP Dependency-Track", "Snyk"],
    ),

    # =========================================================================
    # Control 17: Incident Response Management
    # =========================================================================
    CISSafeguard(
        control_id="17.1",
        control_family="Incident Response Management",
        safeguard="Designate Personnel to Manage Incident Handling",
        description="Designate one key person and at least one backup to manage incident handling.",
        ig_level=1,
        asset_type="N/A",
        security_function="Respond",
        gsf_reference="GLI-GSF-1 Section 2.3.9",
        ogis_relevance=[],
        igaming_context=(
            "The GIS Officer is the designated incident handler per "
            "GLI-GSF-1. Backup must be identified. For gaming-specific "
            "incidents (RNG compromise, payout errors), the incident "
            "response team must include game operations specialists."
        ),
        implementation_tools=["PagerDuty", "Incident runbooks", "On-call rotation"],
    ),
    CISSafeguard(
        control_id="17.4",
        control_family="Incident Response Management",
        safeguard="Establish and Maintain an Incident Response Process",
        description="Establish and maintain an incident response process.",
        ig_level=1,
        asset_type="N/A",
        security_function="Respond",
        gsf_reference="GLI-GSF-1 Section 2.3.9",
        ogis_relevance=[],
        igaming_context=(
            "GLI-GSF-1 defines specific incident thresholds: any outage "
            "exceeding 15 minutes is a GIS incident. Regulatory notification "
            "within 30 days for significant incidents. Root cause analysis "
            "required for all Critical and High severity incidents."
        ),
        implementation_tools=["Incident response playbooks", "Post-mortem templates"],
    ),
]


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
def generate_mapping_report(controls: List[CISSafeguard]) -> str:
    """Generate full mapping report in Markdown."""
    lines = [
        "# CIS Controls v8.1 to GLI-GSF-1 Appendix A Mapping",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Framework** | CIS Controls v8.1 |",
        f"| **GLI-GSF Reference** | GLI-GSF-1 Appendix A |",
        f"| **Date** | {datetime.now(timezone.utc).strftime('%Y-%m-%d')} |",
        f"| **Total Safeguards Mapped** | {len(controls)} |",
        f"| **Version** | {VERSION} |",
        "",
        "## Mapping Summary",
        "",
        "| CIS Control ID | Safeguard | IG | GLI-GSF Reference | OGIS Domains | Status |",
        "|---------------|-----------|----|--------------------|-------------|--------|",
    ]

    for ctrl in controls:
        ogis = ", ".join(ctrl.ogis_relevance) if ctrl.ogis_relevance else "-"
        lines.append(
            f"| {ctrl.control_id} | {ctrl.safeguard} | IG{ctrl.ig_level} "
            f"| {ctrl.gsf_reference} | {ogis} | {ctrl.status} |"
        )

    # Group by control family for detailed view
    families: Dict[str, List[CISSafeguard]] = {}
    for ctrl in controls:
        families.setdefault(ctrl.control_family, []).append(ctrl)

    lines.extend(["", "## Detailed Mapping by Control Family", ""])

    for family_name, family_controls in families.items():
        lines.extend([
            f"### {family_controls[0].control_id.split('.')[0]}. {family_name}",
            "",
        ])
        for ctrl in family_controls:
            ogis = ", ".join(ctrl.ogis_relevance) if ctrl.ogis_relevance else "N/A"
            tools = ", ".join(ctrl.implementation_tools) if ctrl.implementation_tools else "N/A"
            lines.extend([
                f"#### {ctrl.control_id}: {ctrl.safeguard}",
                "",
                f"| Field | Value |",
                f"|-------|-------|",
                f"| **IG Level** | IG{ctrl.ig_level} |",
                f"| **Asset Type** | {ctrl.asset_type} |",
                f"| **Security Function** | {ctrl.security_function} |",
                f"| **GLI-GSF Reference** | {ctrl.gsf_reference} |",
                f"| **OGIS Domains** | {ogis} |",
                f"| **Status** | {ctrl.status} |",
                "",
                f"**Description:** {ctrl.description}",
                "",
                f"**iGaming Context:** {ctrl.igaming_context}",
                "",
                f"**Implementation Tools:** {tools}",
                "",
            ])

    lines.extend([
        "## Implementation Group Coverage",
        "",
        "| IG Level | Mapped Controls | Description |",
        "|----------|----------------|-------------|",
        f"| IG1 | {sum(1 for c in controls if c.ig_level == 1)} | Essential cyber hygiene |",
        f"| IG2 | {sum(1 for c in controls if c.ig_level == 2)} | Enterprise-level |",
        f"| IG3 | {sum(1 for c in controls if c.ig_level == 3)} | Advanced/complex |",
        "",
        "> **Note:** Online gaming operators at GIG3 should implement all three IGs.",
        "",
        "---",
        f"*Generated by cis-controls-mapper.py v{VERSION}*",
    ])

    return "\n".join(lines)


def generate_gap_analysis(controls: List[CISSafeguard]) -> str:
    """Generate gap analysis report showing implementation status."""
    not_started = [c for c in controls if c.status == ImplementationStatus.NOT_STARTED]
    in_progress = [c for c in controls if c.status == ImplementationStatus.IN_PROGRESS]
    implemented = [c for c in controls if c.status == ImplementationStatus.IMPLEMENTED]

    total = len(controls)
    pct_complete = (len(implemented) / total * 100) if total else 0

    lines = [
        "# CIS Controls v8.1 Gap Analysis Report",
        "",
        f"**Date:** {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        f"**Compliance:** {pct_complete:.0f}% ({len(implemented)}/{total} controls implemented)",
        "",
        "## Summary",
        "",
        f"| Status | Count | Percentage |",
        f"|--------|-------|------------|",
        f"| Implemented | {len(implemented)} | {len(implemented)/total*100:.0f}% |",
        f"| In Progress | {len(in_progress)} | {len(in_progress)/total*100:.0f}% |",
        f"| Not Started | {len(not_started)} | {len(not_started)/total*100:.0f}% |",
        "",
    ]

    if not_started:
        lines.extend([
            "## Controls Not Yet Started (Action Required)",
            "",
            "| Control | Safeguard | IG | GLI-GSF Ref | Priority |",
            "|---------|-----------|----|-----------| ---------|",
        ])
        for ctrl in not_started:
            priority = "HIGH" if ctrl.ig_level == 1 else ("MEDIUM" if ctrl.ig_level == 2 else "LOW")
            lines.append(
                f"| {ctrl.control_id} | {ctrl.safeguard} | IG{ctrl.ig_level} "
                f"| {ctrl.gsf_reference} | {priority} |"
            )

    lines.extend([
        "",
        "---",
        f"*Generated by cis-controls-mapper.py v{VERSION}*",
    ])

    return "\n".join(lines)


def export_csv(controls: List[CISSafeguard]) -> str:
    """Export mapping as CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Control ID", "Family", "Safeguard", "IG Level", "Asset Type",
        "Security Function", "GLI-GSF Reference", "OGIS Domains",
        "iGaming Context", "Status", "Tools",
    ])
    for ctrl in controls:
        writer.writerow([
            ctrl.control_id, ctrl.control_family, ctrl.safeguard,
            ctrl.ig_level, ctrl.asset_type, ctrl.security_function,
            ctrl.gsf_reference, "; ".join(ctrl.ogis_relevance),
            ctrl.igaming_context, ctrl.status,
            "; ".join(ctrl.implementation_tools),
        ])
    return output.getvalue()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="CIS Controls v8.1 to GLI-GSF-1 Appendix A Mapper",
    )
    parser.add_argument("--report", action="store_true", help="Generate full mapping report")
    parser.add_argument("--gap-analysis", action="store_true", help="Generate gap analysis")
    parser.add_argument("--export", choices=["csv", "json"], help="Export format")
    parser.add_argument("--control", help="Show details for specific control (e.g., 1.1)")
    parser.add_argument("--details", action="store_true", help="Show detailed info")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--version", action="version", version=f"v{VERSION}")

    args = parser.parse_args()

    controls = CIS_CONTROLS_MAP

    if args.control:
        matches = [c for c in controls if c.control_id == args.control]
        if matches:
            for m in matches:
                print(json.dumps(asdict(m), indent=2))
        else:
            logger.error(f"Control {args.control} not found")
        return

    if args.export == "csv":
        result = export_csv(controls)
    elif args.export == "json":
        result = json.dumps([asdict(c) for c in controls], indent=2)
    elif args.gap_analysis:
        result = generate_gap_analysis(controls)
    else:
        result = generate_mapping_report(controls)

    if args.output:
        Path(args.output).write_text(result)  # ty:ignore[unresolved-reference]
        logger.info(f"Report saved to {args.output}")
    else:
        print(result)


if __name__ == "__main__":
    main()
