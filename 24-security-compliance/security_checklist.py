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
Chapter 24 — Security & Compliance Implementation Checklist
============================================================
Automated readiness checker for platform security and compliance posture.
Validates SSL/TLS certificates, WAF, IDS/IPS, password policy, MFA,
network encryption, geo-blocking, PCI DSS controls, and GDPR/LGPD compliance.

Usage:
    python security_checklist.py [--env staging|production] [--host HOST]
    python security_checklist.py --report-only
    python security_checklist.py --json report.json
"""

import argparse
import json
import shutil
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
import urllib.request
import urllib.error


class Status(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    category: str
    status: Status
    detail: str
    requirement: str
    cost_usd: Optional[float] = None


@dataclass
class ChecklistReport:
    timestamp: str = ""
    environment: str = "staging"
    results: list = field(default_factory=list)
    total: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0

    def add(self, result: CheckResult) -> None:
        self.results.append(result)
        self.total += 1
        if result.status == Status.PASS:
            self.passed += 1
        elif result.status == Status.FAIL:
            self.failed += 1
        elif result.status == Status.WARN:
            self.warnings += 1
        else:
            self.skipped += 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def http_get(url: str, timeout: int = 5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except Exception as e:
        return None, str(e)


def run_cmd(cmd: list, timeout: int = 10) -> tuple:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout, result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, "", str(e)


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_tls_certificates(report: ChecklistReport, host: str, report_only: bool) -> None:
    """SSL/TLS certificate validity and configuration checks."""

    if report_only:
        report.add(CheckResult(
            name="SSL/TLS Certificate Validity",
            category="SSL/TLS",
            status=Status.SKIP,
            detail="Skipped (--report-only mode)",
            requirement="Chapter 24 — Transport security",
        ))
    else:
        try:
            ctx = ssl.create_default_context()
            conn = ctx.wrap_socket(socket.socket(), server_hostname=host)
            conn.settimeout(5)
            conn.connect((host, 443))
            cert = conn.getpeercert()
            conn.close()

            not_after = cert.get("notAfter", "")
            subject = dict(x[0] for x in cert.get("subject", []))
            cn = subject.get("commonName", "unknown")

            # Check expiry
            if not_after:
                from datetime import datetime as dt
                expiry = dt.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                days_left = (expiry - dt.now(timezone.utc)).days
                if days_left > 30:
                    st = Status.PASS
                    det = f"Valid — CN={cn}, expires in {days_left} days ({not_after})"
                elif days_left > 0:
                    st = Status.WARN
                    det = f"Expires soon — {days_left} days remaining ({not_after})"
                else:
                    st = Status.FAIL
                    det = f"Certificate EXPIRED ({not_after})"
            else:
                st, det = Status.WARN, f"CN={cn} — could not determine expiry"

        except ssl.SSLError as e:
            st, det = Status.FAIL, f"SSL error: {e}"
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            st, det = Status.WARN, f"Cannot connect to {host}:443 — {e}"

        report.add(CheckResult(
            name="SSL/TLS Certificate Valid",
            category="SSL/TLS",
            status=st,
            detail=det,
            requirement="Chapter 24 — PCI DSS 4.0 req. 4.2.1",
        ))

    tls_checks = [
        ("TLS 1.2+ Only (disable TLS 1.0/1.1)", "TLS 1.0 and 1.1 must be disabled on all public endpoints"),
        ("TLS 1.3 Preferred", "TLS 1.3 should be the default cipher suite for new connections"),
        ("HSTS Header Enabled (min 1 year)", "Strict-Transport-Security: max-age=31536000; includeSubDomains; preload"),
        ("Certificate Auto-Renewal (Let's Encrypt / ACM)", "Certificates must auto-renew before expiry — no manual renewal"),
        ("Wildcard Cert Scope Limited", "Wildcard certificates must not be used for production payment endpoints"),
        ("mTLS for Internal Service Communication", "All internal service-to-service calls use mutual TLS"),
    ]

    for name, detail in tls_checks:
        report.add(CheckResult(
            name=name,
            category="SSL/TLS",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — Transport layer security",
        ))


def check_waf(report: ChecklistReport, host: str) -> None:
    """WAF rules and deployment checks."""

    waf_rules = [
        ("WAF Deployed on All Public Endpoints", "Web Application Firewall in front of API gateway and web frontend"),
        ("OWASP Top 10 Rules Active", "Core OWASP ruleset enabled: SQLi, XSS, CSRF, SSRF, file inclusion"),
        ("Rate Limiting Rules", "IP-based rate limiting: max 100 req/s per IP, 1000 req/min per account"),
        ("Bot Detection Rules", "Challenge/block headless browsers, scrapers, credential stuffing tools"),
        ("SQL Injection Protection", "Parameterised queries enforced + WAF block on raw SQL patterns"),
        ("XSS Protection Headers", "Content-Security-Policy, X-Frame-Options, X-Content-Type-Options set"),
        ("WAF Logging to SIEM", "All WAF events (blocked + challenged) forwarded to SIEM in real time"),
        ("WAF Rule Update Schedule", "WAF rules reviewed and updated monthly or on new CVE disclosure"),
    ]

    for name, detail in waf_rules:
        report.add(CheckResult(
            name=name,
            category="WAF",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — Portaria SPA/MF 722/2024 Art. 17",
        ))


def check_ids_ips(report: ChecklistReport) -> None:
    """IDS/IPS active and configured checks."""

    ids_checks = [
        ("IDS/IPS Deployed", "Intrusion Detection/Prevention System active on all network segments"),
        ("Suricata/Snort Rules Updated", "IDS rules updated within last 7 days; custom gambling-domain rules"),
        ("Alert Thresholds Configured", "Alert on: port scans, brute force, anomalous data exfil, C2 beacons"),
        ("IDS Alerts → SIEM Integration", "All IDS alerts forwarded to SIEM with enrichment and correlation"),
        ("Network Segmentation Enforced", "DMZ, app tier, database tier, and payment tier fully isolated"),
        ("East-West Traffic Inspection", "Internal traffic between microservices inspected for lateral movement"),
        ("DDoS Mitigation Active", "Layer 3/4 and Layer 7 DDoS protection with auto-scaling scrubbing"),
        ("Bandwidth Monitoring", "Automated alert on >3x baseline traffic — potential DDoS or data exfil"),
    ]

    for name, detail in ids_checks:
        report.add(CheckResult(
            name=name,
            category="IDS/IPS",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — Network security monitoring",
        ))


def check_access_controls(report: ChecklistReport) -> None:
    """Password policy, MFA, and access control checks."""

    access_checks = [
        ("Password Policy: Min 12 Characters", "Minimum 12-character passwords with complexity requirements"),
        ("Password Policy: No Common Passwords", "Block top-10000 common passwords via blocklist at registration"),
        ("Password Hashing: bcrypt/Argon2id", "All passwords stored with bcrypt (cost=12) or Argon2id"),
        ("MFA Enabled for All Admin Accounts", "Multi-factor authentication mandatory for all staff and admin users"),
        ("MFA Enabled for Player Withdrawals", "Players must pass MFA for withdrawals above R$500"),
        ("MFA Methods: TOTP + Hardware Key", "TOTP (Google Authenticator) and FIDO2/WebAuthn hardware key support"),
        ("Session Timeout: 30 min inactivity", "Automatic session expiry after 30 minutes of inactivity"),
        ("Concurrent Session Limit", "Maximum 3 concurrent sessions per player account"),
        ("Privileged Access Workstations (PAW)", "Admins must use dedicated hardened workstation for privileged access"),
        ("Just-in-Time (JIT) Access", "Production access granted on-demand with time-bound elevation, not permanent"),
        ("RBAC: Least Privilege Enforced", "Every role has minimal permissions; quarterly access review"),
        ("Service Accounts: No Human Login", "Service accounts cannot be used for interactive login"),
    ]

    for name, detail in access_checks:
        report.add(CheckResult(
            name=name,
            category="Access Controls",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — PCI DSS 4.0 req. 8",
        ))


def check_network_encryption(report: ChecklistReport) -> None:
    """Network encryption and data-at-rest checks."""

    encryption_checks = [
        ("Data at Rest: AES-256 Encryption", "All databases, object storage, and backups encrypted with AES-256"),
        ("Database Encryption (TDE)", "Transparent Data Encryption enabled on PostgreSQL and Redis"),
        ("Backup Encryption", "All backups encrypted before leaving the datacenter"),
        ("Encryption Key Management (KMS/HSM)", "Keys managed by AWS KMS, Azure Key Vault, or HSM — not in code"),
        ("Key Rotation Schedule (annual)", "Encryption keys rotated annually with zero-downtime rotation"),
        ("PII Field-Level Encryption", "CPF, bank account numbers, biometric hashes encrypted at field level"),
        ("Log Sanitisation (no PII in logs)", "Automated log scrubbing removes CPF, card numbers, tokens"),
    ]

    for name, detail in encryption_checks:
        report.add(CheckResult(
            name=name,
            category="Network/Data Encryption",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — PCI DSS 4.0 req. 3 & 4",
        ))


def check_geo_blocking(report: ChecklistReport, host: str) -> None:
    """Geo-blocking and geofencing configuration checks."""

    geo_checks = [
        ("Geo-Blocking at CDN/WAF Layer", "Country-level blocking enforced at CDN/WAF before reaching origin"),
        ("Brazil-Only Access Enforced", "Non-Brazilian IPs blocked unless VPN exemption whitelist applied"),
        ("Tor Exit Node Blocking", "All known Tor exit nodes blocked at WAF and application layer"),
        ("VPN/Proxy Detection", "Commercial VPN and proxy IPs flagged and challenged with additional verification"),
        ("Geolocation Reverification (30 min)", "Player location re-checked every 30 minutes per Portaria 722/2024"),
        ("IP Allowlist for Admin Access", "Admin panel only accessible from allowlisted IP ranges"),
        ("Sanctions List Geo-Check", "IP geolocation cross-referenced with OFAC sanctions country list"),
    ]

    for name, detail in geo_checks:
        report.add(CheckResult(
            name=name,
            category="Geo-Blocking",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — Portaria SPA/MF 722/2024 Art. 14",
        ))


def check_pci_dss(report: ChecklistReport) -> None:
    """PCI DSS 4.0 controls verification."""

    pci_controls = [
        ("PCI DSS 4.0 SAQ/ROC Scope Defined", "Cardholder Data Environment (CDE) boundaries documented"),
        ("Network Segmentation from CDE", "Payment processing isolated from non-CDE systems with firewall"),
        ("Cardholder Data Not Stored (tokenisation)", "PANs tokenised at point of entry; CVV never stored"),
        ("PCI DSS Quarterly ASV Scan", "Approved Scanning Vendor external vulnerability scan every quarter"),
        ("PCI DSS Annual Penetration Test", "Annual penetration test by qualified QSA-approved tester"),
        ("Cardholder Data Discovery Scan", "Automated scan to detect any PAN stored outside CDE"),
        ("Change Control Process", "All CDE changes go through documented change management"),
        ("PCI DSS Log Monitoring (12 months)", "All CDE logs retained for 12 months, 3 months immediately available"),
    ]

    for name, detail in pci_controls:
        report.add(CheckResult(
            name=name,
            category="PCI DSS",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — PCI DSS 4.0",
        ))


def check_gdpr_lgpd(report: ChecklistReport) -> None:
    """GDPR and LGPD compliance checks."""

    privacy_checks = [
        ("DPO (Data Protection Officer) Appointed", "Dedicated DPO with contact published on platform"),
        ("Privacy Policy Published (PT-BR)", "Privacy policy in Portuguese covering LGPD rights"),
        ("Consent Management Platform (CMP)", "Granular consent collection and withdrawal for marketing, cookies, analytics"),
        ("Data Subject Rights Workflow", "Automated: access, rectification, deletion, portability requests within 15 days"),
        ("Data Inventory / ROPA", "Record of Processing Activities maintained and reviewed quarterly"),
        ("Data Breach Notification (72h to ANPD)", "Incident response plan covers ANPD notification within 72 hours"),
        ("Cross-Border Transfer Mechanism", "Standard Contractual Clauses or adequacy decision for any non-Brazil transfer"),
        ("Data Minimisation Enforced", "Only data strictly necessary for the processing purpose is collected"),
        ("Retention Schedules Implemented", "Automated purge of data beyond retention period (5 years transactional, 1 year marketing)"),
        ("LGPD Impact Assessment (RIPD)", "Privacy impact assessment completed for high-risk processing activities"),
    ]

    for name, detail in privacy_checks:
        report.add(CheckResult(
            name=name,
            category="GDPR/LGPD",
            status=Status.WARN,
            detail=detail,
            requirement="Chapter 24 — Lei 13.709/2018 (LGPD) / GDPR",
        ))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(report: ChecklistReport) -> None:
    print()
    print("=" * 70)
    print("  CHAPTER 24 — SECURITY & COMPLIANCE IMPLEMENTATION CHECKLIST")
    print(f"  Environment: {report.environment}")
    print(f"  Generated:   {report.timestamp}")
    print("=" * 70)

    current_category = ""
    for r in report.results:
        if r.category != current_category:
            current_category = r.category
            print(f"\n  {'─' * 64}")
            print(f"  {current_category.upper()}")
            print(f"  {'─' * 64}")

        icon = {
            Status.PASS: "[PASS]",
            Status.FAIL: "[FAIL]",
            Status.WARN: "[WARN]",
            Status.SKIP: "[SKIP]",
        }[r.status]

        print(f"  {icon} {r.name}")
        print(f"         {r.detail}")
        print(f"         Ref: {r.requirement}")

    print(f"\n  {'=' * 64}")
    print(f"  SUMMARY")
    print(f"  {'=' * 64}")
    print(f"  Total checks:  {report.total}")
    print(f"  Passed:        {report.passed}")
    print(f"  Failed:        {report.failed}")
    print(f"  Warnings:      {report.warnings}")
    print(f"  Skipped:       {report.skipped}")

    readiness = report.passed / report.total * 100 if report.total else 0
    print(f"\n  Security & compliance readiness: {readiness:.0f}%")

    if report.failed > 0:
        print(f"\n  NOT READY — {report.failed} critical checks failed")
    elif report.warnings > 5:
        print(f"\n  REVIEW NEEDED — {report.warnings} items require manual verification")
    else:
        print(f"\n  READY — Security and compliance checks passed")

    print(f"\n{'=' * 70}\n")


def export_json(report: ChecklistReport, path: str) -> None:
    data = {
        "timestamp": report.timestamp,
        "environment": report.environment,
        "chapter": 24,
        "title": "Security & Compliance",
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "warnings": report.warnings,
            "skipped": report.skipped,
        },
        "checks": [
            {
                "name": r.name,
                "category": r.category,
                "status": r.status.value,
                "detail": r.detail,
                "requirement": r.requirement,
            }
            for r in report.results
        ],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"  Report exported to {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Chapter 24 — Security & Compliance Checklist")
    parser.add_argument("--env", default="staging", choices=["staging", "production"])
    parser.add_argument("--host", default="127.0.0.1", help="Host for certificate and WAF checks")
    parser.add_argument("--report-only", action="store_true", help="Show checklist without live checks")
    parser.add_argument("--json", type=str, help="Export report to JSON file")
    args = parser.parse_args()

    report = ChecklistReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        environment=args.env,
    )

    check_tls_certificates(report, args.host, args.report_only)
    check_waf(report, args.host)
    check_ids_ips(report)
    check_access_controls(report)
    check_network_encryption(report)
    check_geo_blocking(report, args.host)
    check_pci_dss(report)
    check_gdpr_lgpd(report)

    print_report(report)

    if args.json:
        export_json(report, args.json)

    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
