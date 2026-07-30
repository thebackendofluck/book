#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Brazilian Betting Platform — Implementation & Launch Checklist
==============================================================
Automated compliance and readiness checker for launching a regulated
betting platform in Brazil under Lei 14.790/2023.

Runs checks against infrastructure, services, regulatory requirements,
and deployment readiness. Produces a pass/fail report.

Usage:
    python brazil_launch_checklist.py [--env staging|production] [--host HOST]
    python brazil_launch_checklist.py --report-only
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


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
    requirement: str  # Regulatory reference
    cost_brl: Optional[float] = None
    cost_usd: Optional[float] = None


@dataclass
class ChecklistReport:
    timestamp: str = ""
    environment: str = "staging"
    results: list[CheckResult] = field(default_factory=list)
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


BRL_TO_USD = 5.50  # Approximate rate March 2026


def brl_to_usd(brl: float) -> float:
    return round(brl / BRL_TO_USD, 2)


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_licensing(report: ChecklistReport) -> None:
    """Phase 1: Licensing & Legal Requirements"""

    report.add(CheckResult(
        name="SPA-MF License Application",
        category="Licensing",
        status=Status.WARN,
        detail="Verify license application submitted to Secretaria de Prêmios e Apostas",
        requirement="Lei 14.790/2023 Art. 3º",
        cost_brl=30_000_000,
        cost_usd=brl_to_usd(30_000_000),
    ))

    report.add(CheckResult(
        name="License Fee Payment (R$30M / ~$5.5M USD)",
        category="Licensing",
        status=Status.WARN,
        detail="R$30,000,000 per 5-year license, up to 3 brands (skins)",
        requirement="Portaria SPA/MF 827/2024",
        cost_brl=30_000_000,
        cost_usd=brl_to_usd(30_000_000),
    ))

    report.add(CheckResult(
        name="Financial Reserve (R$5M / ~$909K USD)",
        category="Licensing",
        status=Status.WARN,
        detail="Minimum R$5,000,000 reserve capital required",
        requirement="Portaria SPA/MF 827/2024 Art. 12",
        cost_brl=5_000_000,
        cost_usd=brl_to_usd(5_000_000),
    ))

    report.add(CheckResult(
        name="Brazilian Legal Entity (S.A. or Ltda.)",
        category="Licensing",
        status=Status.WARN,
        detail="Operator must be incorporated under Brazilian law with 20% min national capital",
        requirement="Lei 14.790/2023 Art. 5º",
    ))

    report.add(CheckResult(
        name=".bet.br Domain Registration",
        category="Licensing",
        status=Status.WARN,
        detail="Mandatory .bet.br TLD since January 2025. Register via Registro.br",
        requirement="Portaria SPA/MF 827/2024 Art. 8º",
    ))

    report.add(CheckResult(
        name="SIGAP Registration",
        category="Licensing",
        status=Status.WARN,
        detail="Register operator in SIGAP via e-CNPJ digital certificate",
        requirement="Portaria SPA/MF 722/2024",
    ))


def check_infrastructure(report: ChecklistReport, host: str) -> None:
    """Phase 2: Infrastructure Requirements"""

    report.add(CheckResult(
        name="Data Residency — Brazil",
        category="Infrastructure",
        status=Status.WARN,
        detail="All systems and data must reside in Brazil (AWS sa-east-1, Azure Brazil South, or GCP southamerica-east1)",
        requirement="Portaria SPA/MF 722/2024 Art. 15",
    ))

    report.add(CheckResult(
        name="ISO 27001 Certification",
        category="Infrastructure",
        status=Status.WARN,
        detail="Datacenter must hold ISO 27001 certification",
        requirement="Portaria SPA/MF 722/2024 Art. 16",
    ))

    report.add(CheckResult(
        name="Data Retention — 5 Years",
        category="Infrastructure",
        status=Status.WARN,
        detail="All transactional data must be retained for minimum 5 years",
        requirement="Portaria SPA/MF 722/2024 Art. 18",
    ))

    report.add(CheckResult(
        name="IDS/IPS Deployed",
        category="Infrastructure",
        status=Status.WARN,
        detail="Intrusion Detection/Prevention System mandatory",
        requirement="Portaria SPA/MF 722/2024 Art. 17",
    ))

    report.add(CheckResult(
        name="WAF (Web Application Firewall)",
        category="Infrastructure",
        status=Status.WARN,
        detail="Application-level WAF required for all public endpoints",
        requirement="Portaria SPA/MF 722/2024 Art. 17",
    ))

    report.add(CheckResult(
        name="DDoS Protection",
        category="Infrastructure",
        status=Status.WARN,
        detail="Explicit DDoS protection required",
        requirement="Portaria SPA/MF 722/2024 Art. 17",
    ))

    report.add(CheckResult(
        name="SIEM Monitoring",
        category="Infrastructure",
        status=Status.WARN,
        detail="SIEM-class monitoring for security event correlation",
        requirement="Portaria SPA/MF 722/2024 Art. 17",
    ))


def check_services(report: ChecklistReport, host: str, ports: dict) -> None:
    """Phase 3: Core Microservices"""

    services = {
        "PAM (Player Account Management)": {"port": ports.get("pam", 18010), "check": "/health"},
        "Responsible Gaming": {"port": ports.get("rg", 18020), "check": "/health"},
        "Bonus Engine": {"port": ports.get("bonus", 18030), "check": "/health"},
        "Casino Aggregation": {"port": ports.get("casino", 18040), "check": "/health"},
        "Betting Engine": {"port": ports.get("betting", 18080), "check": "/health"},
        "Wallet (PIX)": {"port": ports.get("wallet", 18081), "check": "/health"},
        "Settlement (GGR)": {"port": ports.get("settlement", 18082), "check": "/health"},
        "Odds Feed": {"port": ports.get("odds", 18083), "check": "/health"},
    }

    for name, svc in services.items():
        try:
            import urllib.request
            url = f"http://{host}:{svc['port']}{svc['check']}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                status = Status.PASS if resp.status == 200 else Status.FAIL
                detail = f"HTTP {resp.status} — {json.dumps(data)[:80]}"
        except Exception as e:
            status = Status.FAIL
            detail = f"Connection failed: {e}"

        report.add(CheckResult(
            name=f"Service: {name}",
            category="Services",
            status=status,
            detail=detail,
            requirement="Platform architecture requirement",
        ))


def check_compliance(report: ChecklistReport) -> None:
    """Phase 4: Regulatory Compliance"""

    checks = [
        ("CPF and Identity Verification", "Portaria SPA/MF 722/2024",
         "Validate identity, CPF ownership, age and required registration data before activation"),
        ("Biometric Verification (Facial Recognition)", "Portaria SPA/MF 722/2024 Art. 11",
         "Selfie-based biometric verification required for all users"),
        ("SIGAP Impediments API v2", "Portaria SPA/MF 1.231/2024, as amended",
         "Query the normalized CPF at onboarding, first login of the day, and every 15 days"),
        ("Social-Program Impediment", "Portaria SPA/MF 2.217/2025; IN SPA/MF 22/2025",
         "Block when SIGAP returns PROGRAMA_SOCIAL; do not query CadUnico/CNIS or infer the origin of a PIX transfer"),
        ("Geolocation and Territory Control", "Portaria SPA/MF 722/2024",
         "Verify that the player is physically in Brazil at the regulatory control points"),
        ("Deposit Limits (daily/weekly/monthly)", "Portaria SPA/MF 1231/2024 Art. 8",
         "Players must be able to set deposit, loss, and session time limits"),
        ("Self-Exclusion (temporary + permanent)", "Portaria SPA/MF 1231/2024 Art. 20",
         "Temporary (min 24h) and permanent self-exclusion with cooling-off period"),
        ("SIGAP Regulatory Files", "Portaria SPA/MF 722/2024; SIGAP Technical Manual",
         "Generate, sign and submit the prescribed files by their periodicity, generally with reference date no older than D-2"),
        ("GGR Calculation and Reconciliation", "Lei 13.756/2018 Art. 30, as amended",
         "Reconcile the statutory GGR base and monthly obligations; do not model SIGAP as a synchronous per-bet reporting API"),
        ("GGR Statutory Allocation (13% in 2026)", "Lei 13.756/2018 Art. 30; LC 224/2025",
         "Apply the 2026 statutory allocation and its transition; validate the complete tax stack with Brazilian counsel"),
        ("Annual Bettor Tax Evidence (ComprovaBet)", "Lei 14.790/2023 Art. 31; current RFB service rules",
         "Do not withhold tax per bet; preserve annual net-result evidence, issue ComprovaBet by the official deadline, and explain the bettor's DARF process"),
        ("Permitted Payment Rails", "Portaria SPA/MF 615/2024",
         "Use authorized account-to-account rails such as PIX or TED; reject cash, boleto, crypto and prohibited credit instruments"),
        ("Closed Payment Loop", "Portaria SPA/MF 615/2024 Art. 7",
         "Funds must flow directly between player's registered bank account and operator"),
        ("AML/COAF Reporting", "Portaria SPA/MF 1143/2024",
         "Suspicious Activity Reports (SAR) to COAF per Lei 9.613/1998"),
        ("Match Integrity (Portaria 1207)", "Portaria SPA/MF 1207/2024",
         "Report suspected match-fixing to SPA-MF within 24 hours"),
        ("RNG Certification (GLI/BMM)", "Portaria SPA/MF 722/2024 Art. 22",
         "All RNG systems must be certified by GLI, BMM, Trisigma, Quinel, or eCOGRA"),
        ("LGPD Compliance", "Lei 13.709/2018 (LGPD)",
         "Data protection: consent management, DPO, data subject rights, breach notification"),
        ("Age Verification (18+)", "Lei 14.790/2023 Art. 6",
         "Strict 18+ verification. No minors under any circumstances"),
        ("Responsible Gaming UI", "Portaria SPA/MF 1231/2024 Art. 5",
         "Visible responsible gaming tools, clock display, session duration, limit warnings"),
        ("One Account Per CPF Per Operator", "Portaria SPA/MF 722/2024 Art. 12",
         "Enforced at national level — cross-operator CPF uniqueness"),
    ]

    for name, requirement, detail in checks:
        report.add(CheckResult(
            name=name,
            category="Compliance",
            status=Status.WARN,
            detail=detail,
            requirement=requirement,
        ))


def check_testing(report: ChecklistReport) -> None:
    """Phase 5: Testing & Certification"""

    checks = [
        ("Unit Tests (>80% coverage)", "Quality", "Python: pytest, Go: go test, Scala: ScalaTest"),
        ("Integration Tests", "Quality", "Docker Compose full stack test with all 9 microservices"),
        ("Functional Tests (user journey)", "Quality", "CPF registration → biometric → PIX deposit → bet → settle → withdraw"),
        ("Load Testing (concurrent users)", "Quality", "100+ concurrent bets, 50+ PIX deposits, <500ms p95"),
        ("GLI/BMM Platform Audit", "Certification", "Full platform audit by accredited testing lab"),
        ("RNG Certification", "Certification", "Certified by GLI, BMM, Trisigma, Quinel, or eCOGRA"),
        ("Penetration Testing", "Security", "Annual pentest by qualified security firm"),
        ("SIGAP Reporting Validation", "Compliance", "Verify all SIGAP reports match expected format and data"),
    ]

    for name, category, detail in checks:
        report.add(CheckResult(
            name=name,
            category=f"Testing/{category}",
            status=Status.WARN,
            detail=detail,
            requirement="Pre-launch requirement",
        ))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(report: ChecklistReport) -> None:
    """Print formatted checklist report."""
    print()
    print("=" * 70)
    print("  BRAZILIAN BETTING PLATFORM — LAUNCH READINESS CHECKLIST")
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
            Status.PASS: "✅",
            Status.FAIL: "❌",
            Status.WARN: "⚠️ ",
            Status.SKIP: "⏭️ ",
        }[r.status]

        cost = ""
        if r.cost_brl:
            cost = f" [R${r.cost_brl:,.0f} / ${r.cost_usd:,.0f}]"

        print(f"  {icon} {r.name}{cost}")
        print(f"     {r.detail}")
        print(f"     Ref: {r.requirement}")

    # Summary
    print(f"\n  {'=' * 64}")
    print(f"  SUMMARY")
    print(f"  {'=' * 64}")
    print(f"  Total checks:  {report.total}")
    print(f"  Passed:        {report.passed} ✅")
    print(f"  Failed:        {report.failed} ❌")
    print(f"  Warnings:      {report.warnings} ⚠️")
    print(f"  Skipped:       {report.skipped} ⏭️")

    # Cost summary
    total_brl = sum(r.cost_brl for r in report.results if r.cost_brl)
    total_usd = sum(r.cost_usd for r in report.results if r.cost_usd)
    if total_brl:
        print(f"\n  Regulatory costs: R${total_brl:,.0f} (~${total_usd:,.0f} USD)")

    readiness = report.passed / report.total * 100 if report.total else 0
    print(f"\n  Launch readiness: {readiness:.0f}%")

    if report.failed > 0:
        print(f"\n  ⛔ NOT READY — {report.failed} critical checks failed")
    elif report.warnings > 5:
        print(f"\n  ⚠️  REVIEW NEEDED — {report.warnings} items need manual verification")
    else:
        print(f"\n  ✅ READY FOR LAUNCH")

    print(f"\n{'=' * 70}\n")


def export_json(report: ChecklistReport, path: str) -> None:
    """Export report as JSON."""
    data = {
        "timestamp": report.timestamp,
        "environment": report.environment,
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
                "cost_brl": r.cost_brl,
                "cost_usd": r.cost_usd,
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
    parser = argparse.ArgumentParser(description="Brazilian Betting Platform Launch Checklist")
    parser.add_argument("--env", default="staging", choices=["staging", "production"])
    parser.add_argument("--host", default="127.0.0.1", help="Host for service health checks")
    parser.add_argument("--report-only", action="store_true", help="Show checklist without running health checks")
    parser.add_argument("--json", type=str, help="Export report to JSON file")
    args = parser.parse_args()

    report = ChecklistReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        environment=args.env,
    )

    # Run all checks
    check_licensing(report)
    check_infrastructure(report, args.host)

    if not args.report_only:
        check_services(report, args.host, {})
    else:
        report.add(CheckResult(
            name="Service Health Checks",
            category="Services",
            status=Status.SKIP,
            detail="Skipped (--report-only mode)",
            requirement="",
        ))

    check_compliance(report)
    check_testing(report)

    # Output
    print_report(report)

    if args.json:
        export_json(report, args.json)


if __name__ == "__main__":
    main()
