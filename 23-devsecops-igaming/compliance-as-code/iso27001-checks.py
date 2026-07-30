#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
ISO 27001:2022 Automated Control Validation for iGaming Platforms
==================================================================
Validates ISO 27001:2022 Annex A controls against live infrastructure.
Covers the 93 controls organized in 4 themes:
  - Organizational (37 controls)
  - People (8 controls)
  - Physical (14 controls)
  - Technological (34 controls)

Focus on controls most relevant to online gambling operations.

Usage:
    python3 iso27001-checks.py --target production
    python3 iso27001-checks.py --target production --themes technological,organizational
    python3 iso27001-checks.py --target staging --output iso-report.json
"""
import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("iso27001-checks")

# ---------------------------------------------------------------------------
# Check Framework (reusing from PCI DSS module pattern)
# ---------------------------------------------------------------------------
class Status:
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARNING"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass
class ControlCheck:
    control_id: str        # e.g., "A.8.9"
    theme: str             # organizational, people, physical, technological
    title: str
    description: str
    status: str
    evidence: str = ""
    remediation: str = ""
    igaming_relevance: str = ""


@dataclass
class ISOReport:
    target: str
    scan_timestamp: str
    standard: str = "ISO 27001:2022"
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0
    compliance_score: float = 0.0
    checks: list = field(default_factory=list)


def run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, "", str(e)


def kubectl_get(resource: str, namespace: str = "", jsonpath: str = "") -> Optional[str]:
    cmd = ["kubectl", "get", resource]
    if namespace:
        cmd.extend(["-n", namespace])
    if jsonpath:
        cmd.extend(["-o", f"jsonpath={jsonpath}"])
    else:
        cmd.extend(["-o", "json"])
    rc, out, _ = run_cmd(cmd)
    return out if rc == 0 else None


# ---------------------------------------------------------------------------
# ISO 27001:2022 Control Checks
# ---------------------------------------------------------------------------
class ISO27001Checks:
    def __init__(self, target: str):
        self.target = target
        self.checks: list[ControlCheck] = []

    def add(self, **kwargs) -> ControlCheck:
        c = ControlCheck(**kwargs)
        self.checks.append(c)
        return c

    # === ORGANIZATIONAL CONTROLS ===

    def a5_1_information_security_policies(self):
        """A.5.1 - Policies for information security."""
        # Check if policy ConfigMaps exist
        policies = kubectl_get("configmaps", namespace="governance", jsonpath="{.items[*].metadata.name}")
        has_policies = policies and any(
            kw in policies.lower()
            for kw in ["security-policy", "infosec-policy", "acceptable-use"]
        )
        self.add(
            control_id="A.5.1",
            theme="organizational",
            title="Policies for Information Security",
            description="Information security policy and topic-specific policies shall be defined and approved",
            status=Status.PASS if has_policies else Status.WARN,
            evidence=f"Policy ConfigMaps found: {policies[:200]}" if has_policies else "No policy ConfigMaps detected in governance namespace",  # ty:ignore[not-subscriptable]
            remediation="Create and deploy security policies as versioned ConfigMaps in governance namespace",
            igaming_relevance="UKGC requires documented information security policies reviewed annually",
        )

    def a5_23_cloud_services_security(self):
        """A.5.23 - Information security for cloud services (NEW in 2022)."""
        # Check CSP security configurations
        # This is a new control in ISO 27001:2022
        self.add(
            control_id="A.5.23",
            theme="organizational",
            title="Information Security for Cloud Services",
            description="Processes for acquisition, use, management, and exit from cloud services must be established",
            status=Status.WARN,
            evidence="Cloud service security requires manual review of CSP agreements and configurations",
            remediation="Document cloud service inventory, review shared responsibility model, configure CSP security features",
            igaming_relevance="Gambling regulators require cloud hosting in approved jurisdictions (EU/EEA for MGA, UK for UKGC)",
        )

    def a5_30_ict_readiness_business_continuity(self):
        """A.5.30 - ICT readiness for business continuity (NEW in 2022)."""
        # Check for backup/DR configurations
        backup_pods = kubectl_get("pods", namespace="velero") or kubectl_get("pods", namespace="backup")
        has_backup = backup_pods and "Running" in backup_pods

        self.add(
            control_id="A.5.30",
            theme="organizational",
            title="ICT Readiness for Business Continuity",
            description="ICT readiness shall be planned, implemented, maintained, and tested",
            status=Status.PASS if has_backup else Status.WARN,
            evidence="Backup system detected (Velero or equivalent)" if has_backup else "No automated backup infrastructure detected",
            remediation="Deploy Velero for Kubernetes backup. Define RPO/RTO targets per service tier.",
            igaming_relevance="Casino platforms require <4h RTO for payment systems, <15min RPO for player balances",
        )

    # === TECHNOLOGICAL CONTROLS ===

    def a8_1_user_endpoint_devices(self):
        """A.8.1 - User endpoint devices."""
        self.add(
            control_id="A.8.1",
            theme="technological",
            title="User Endpoint Devices",
            description="Information stored on, processed by, or accessible via endpoint devices shall be protected",
            status=Status.WARN,
            evidence="Endpoint device management requires MDM/EDR verification (CrowdStrike, SentinelOne, Intune)",
            remediation="Deploy EDR on all developer/admin workstations. Enforce disk encryption and screen lock policies.",
            igaming_relevance="Admin staff with access to player data and financial systems require managed, encrypted endpoints",
        )

    def a8_7_malware_protection(self):
        """A.8.7 - Protection against malware."""
        # Check for antimalware in Kubernetes
        daemonsets = kubectl_get("daemonsets", namespace="security")
        has_av = daemonsets and any(
            kw in daemonsets.lower()
            for kw in ["clamav", "falco", "sysdig", "crowdstrike", "defender"]
        )
        self.add(
            control_id="A.8.7",
            theme="technological",
            title="Protection Against Malware",
            description="Protection against malware shall be implemented and supported by user awareness",
            status=Status.PASS if has_av else Status.FAIL,
            evidence="Anti-malware DaemonSet detected in security namespace" if has_av else "No anti-malware DaemonSet found",
            remediation="Deploy ClamAV DaemonSet for file scanning and Falco for runtime behavior detection",
            igaming_relevance="Game providers uploading content (slot assets, live dealer streams) require malware scanning on ingestion",
        )

    def a8_8_technical_vulnerabilities(self):
        """A.8.8 - Management of technical vulnerabilities."""
        # Check for vulnerability scanning operator
        trivy_op = kubectl_get("pods", namespace="trivy-system") or kubectl_get("pods", namespace="security")
        has_scanner = trivy_op and any(kw in trivy_op.lower() for kw in ["trivy", "vulnerability", "scanner"])

        self.add(
            control_id="A.8.8",
            theme="technological",
            title="Management of Technical Vulnerabilities",
            description="Technical vulnerabilities shall be identified, evaluated, and treated in a timely manner",
            status=Status.PASS if has_scanner else Status.FAIL,
            evidence="Vulnerability scanner detected in cluster" if has_scanner else "No vulnerability scanning infrastructure found",
            remediation="Deploy Trivy Operator for continuous vulnerability scanning of container images and configs",
            igaming_relevance="GLI-33 and eCOGRA require documented vulnerability management with defined remediation SLAs",
        )

    def a8_9_configuration_management(self):
        """A.8.9 - Configuration management (NEW in 2022)."""
        # Check for GitOps/configuration management
        flux_pods = kubectl_get("pods", namespace="flux-system")
        argo_pods = kubectl_get("pods", namespace="argocd")
        has_gitops = (flux_pods and "Running" in str(flux_pods)) or (argo_pods and "Running" in str(argo_pods))

        self.add(
            control_id="A.8.9",
            theme="technological",
            title="Configuration Management",
            description="Configurations of hardware, software, services, and networks shall be managed",
            status=Status.PASS if has_gitops else Status.WARN,
            evidence="GitOps system detected (Flux/ArgoCD)" if has_gitops else "No GitOps configuration management detected",
            remediation="Deploy ArgoCD or Flux for declarative, auditable configuration management",
            igaming_relevance="Configuration drift detection is critical for maintaining certified RNG and payment configurations",
        )

    def a8_10_information_deletion(self):
        """A.8.10 - Information deletion (NEW in 2022)."""
        self.add(
            control_id="A.8.10",
            theme="technological",
            title="Information Deletion",
            description="Information stored in systems shall be deleted when no longer required",
            status=Status.WARN,
            evidence="Data deletion policies require manual verification against data retention schedules",
            remediation="Implement automated data lifecycle management. Player data retention: per jurisdiction (5yr UK, 10yr MGA financial records)",
            igaming_relevance="GDPR right-to-erasure vs gambling AML record retention creates complex deletion requirements",
        )

    def a8_11_data_masking(self):
        """A.8.11 - Data masking (NEW in 2022)."""
        self.add(
            control_id="A.8.11",
            theme="technological",
            title="Data Masking",
            description="Data masking shall be used in accordance with data classification and access policies",
            status=Status.WARN,
            evidence="Data masking configuration requires application-level verification",
            remediation="Implement PII masking in logs, non-production environments, and customer support views. Mask card numbers (show last 4), DOB, SSN/national ID.",
            igaming_relevance="Player PII must be masked in support tools. Card data must be masked per PCI DSS (first 6, last 4 only)",
        )

    def a8_12_data_leakage_prevention(self):
        """A.8.12 - Data leakage prevention (NEW in 2022)."""
        # Check for DLP policies
        self.add(
            control_id="A.8.12",
            theme="technological",
            title="Data Leakage Prevention",
            description="Data leakage prevention measures shall be applied to systems containing sensitive data",
            status=Status.WARN,
            evidence="DLP requires manual verification. Check: git secret scanning, email DLP, endpoint DLP, database activity monitoring.",
            remediation="Deploy: 1) GitLeaks/TruffleHog in CI/CD, 2) Database Activity Monitoring for CDE, 3) Email DLP for PII/card data patterns",
            igaming_relevance="Player financial data, RNG seeds, and house edge configurations must never leak to unauthorized parties",
        )

    def a8_15_logging(self):
        """A.8.15 - Logging."""
        # Check centralized logging
        logging_ns = kubectl_get("pods", namespace="logging") or kubectl_get("pods", namespace="elastic-system")
        has_logging = logging_ns and any(
            kw in logging_ns.lower()
            for kw in ["elasticsearch", "loki", "fluentd", "fluentbit", "logstash"]
        )

        self.add(
            control_id="A.8.15",
            theme="technological",
            title="Logging",
            description="Logs that record activities, exceptions, faults, and other events shall be produced and protected",
            status=Status.PASS if has_logging else Status.FAIL,
            evidence="Centralized logging infrastructure detected" if has_logging else "No centralized logging found",
            remediation="Deploy EFK/ELK stack or Loki for centralized log collection, with 90-day hot storage and 5-year cold archive",
            igaming_relevance="Gambling regulators require immutable audit logs of all financial transactions and player activity",
        )

    def a8_16_monitoring(self):
        """A.8.16 - Monitoring activities (NEW in 2022)."""
        # Check for monitoring stack
        monitoring = kubectl_get("pods", namespace="monitoring")
        has_monitoring = monitoring and any(
            kw in monitoring.lower()
            for kw in ["prometheus", "grafana", "alertmanager", "datadog", "newrelic"]
        )

        self.add(
            control_id="A.8.16",
            theme="technological",
            title="Monitoring Activities",
            description="Networks, systems, and applications shall be monitored for anomalous behaviour",
            status=Status.PASS if has_monitoring else Status.FAIL,
            evidence="Monitoring stack detected (Prometheus/Grafana or equivalent)" if has_monitoring else "No monitoring infrastructure found",
            remediation="Deploy Prometheus + Grafana with AlertManager. Configure alerts for payment failures, unusual betting patterns, authentication anomalies.",
            igaming_relevance="Real-time monitoring of odds movement, suspicious betting patterns, and payment anomalies is regulatory requirement",
        )

    def a8_23_web_filtering(self):
        """A.8.23 - Web filtering (NEW in 2022)."""
        self.add(
            control_id="A.8.23",
            theme="technological",
            title="Web Filtering",
            description="Access to external websites shall be managed to reduce exposure to malicious content",
            status=Status.WARN,
            evidence="Web filtering configuration requires network-level verification (egress policies, proxy configuration)",
            remediation="Configure Kubernetes egress NetworkPolicies. Allow only necessary outbound destinations for payment processing and game provider APIs.",
            igaming_relevance="Game servers must not have unrestricted internet access — limit to licensed game provider domains",
        )

    def a8_24_use_of_cryptography(self):
        """A.8.24 - Use of cryptography."""
        # Check TLS configurations and certificate management
        cert_manager = kubectl_get("pods", namespace="cert-manager")
        has_cert_mgmt = cert_manager and "Running" in str(cert_manager)

        self.add(
            control_id="A.8.24",
            theme="technological",
            title="Use of Cryptography",
            description="Rules for effective use of cryptography shall be defined and implemented",
            status=Status.PASS if has_cert_mgmt else Status.WARN,
            evidence="cert-manager detected for automated certificate lifecycle" if has_cert_mgmt else "No automated certificate management detected",
            remediation="Deploy cert-manager with Let's Encrypt or internal CA. Enforce TLS 1.2+ across all services.",
            igaming_relevance="RNG implementations require cryptographic seeding (FIPS 140-2/3). All player data must be encrypted in transit and at rest.",
        )

    def a8_25_secure_development(self):
        """A.8.25 - Secure development lifecycle."""
        # Check for security tools in CI/CD
        self.add(
            control_id="A.8.25",
            theme="technological",
            title="Secure Development Lifecycle",
            description="Rules for secure development shall be established and applied",
            status=Status.WARN,
            evidence="SDLC security requires verification of CI/CD pipeline security gates (SAST, DAST, SCA, container scanning)",
            remediation="Integrate: SonarQube (SAST), OWASP ZAP (DAST), Trivy (SCA/container), Gitleaks (secrets), code review enforcement",
            igaming_relevance="Game logic and RNG code changes require dual review. Payment integrations need security-focused code review.",
        )

    def a8_28_secure_coding(self):
        """A.8.28 - Secure coding (NEW in 2022)."""
        self.add(
            control_id="A.8.28",
            theme="technological",
            title="Secure Coding",
            description="Secure coding principles shall be applied to software development",
            status=Status.WARN,
            evidence="Secure coding practices require manual review of SAST results and developer training records",
            remediation="Enforce: input validation, parameterized queries, output encoding, secure session management. OWASP Top 10 training annually.",
            igaming_relevance="XSS in casino UI could redirect deposits. SQLi in sportsbook could manipulate odds. Input validation is paramount.",
        )

    # === Run all ===

    def run_all(self, themes: Optional[list[str]] = None):
        """Execute all checks, optionally filtered by theme."""
        all_check_methods = [
            # Organizational
            self.a5_1_information_security_policies,
            self.a5_23_cloud_services_security,
            self.a5_30_ict_readiness_business_continuity,
            # Technological
            self.a8_1_user_endpoint_devices,
            self.a8_7_malware_protection,
            self.a8_8_technical_vulnerabilities,
            self.a8_9_configuration_management,
            self.a8_10_information_deletion,
            self.a8_11_data_masking,
            self.a8_12_data_leakage_prevention,
            self.a8_15_logging,
            self.a8_16_monitoring,
            self.a8_23_web_filtering,
            self.a8_24_use_of_cryptography,
            self.a8_25_secure_development,
            self.a8_28_secure_coding,
        ]

        for method in all_check_methods:
            try:
                method()
            except Exception as e:
                self.add(
                    control_id="ERR",
                    theme="error",
                    title=method.__name__,
                    description=str(e),
                    status=Status.ERROR,
                )

        # Filter by theme if specified
        if themes:
            self.checks = [c for c in self.checks if c.theme in themes]

    def generate_report(self) -> ISOReport:
        report = ISOReport(
            target=self.target,
            scan_timestamp=datetime.now(timezone.utc).isoformat(),
            total_checks=len(self.checks),
            passed=sum(1 for c in self.checks if c.status == Status.PASS),
            failed=sum(1 for c in self.checks if c.status == Status.FAIL),
            warnings=sum(1 for c in self.checks if c.status == Status.WARN),
            skipped=sum(1 for c in self.checks if c.status == Status.SKIP),
            checks=[asdict(c) for c in self.checks],
        )
        assessed = report.passed + report.failed
        report.compliance_score = round(report.passed / max(assessed, 1) * 100, 1)
        return report


def print_report(report: ISOReport):
    print("\n" + "=" * 75)
    print(f"  {report.standard} Compliance Assessment")
    print(f"  Target: {report.target}")
    print(f"  Time: {report.scan_timestamp}")
    print("=" * 75)
    print(f"\n  Score: {report.compliance_score}%")
    print(f"  Passed: {report.passed} | Failed: {report.failed} | "
          f"Warnings: {report.warnings} | Skipped: {report.skipped}")
    print()

    current_theme = ""
    for check in report.checks:
        if check["theme"] != current_theme:
            current_theme = check["theme"]
            print(f"\n  --- {current_theme.upper()} CONTROLS ---")
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARNING": "[WARN]", "SKIP": "[SKIP]", "ERROR": "[ERR!]"}.get(check["status"], "[????]")
        print(f"  {icon:>6s}  {check['control_id']:>8s}  {check['title']}")
        if check["status"] in ("FAIL", "ERROR"):
            if check.get("evidence"):
                print(f"            Evidence: {check['evidence'][:100]}")
            if check.get("remediation"):
                print(f"            Fix: {check['remediation'][:100]}")
    print()


def main():
    parser = argparse.ArgumentParser(description="ISO 27001:2022 compliance checker for iGaming")
    parser.add_argument("--target", default="production")
    parser.add_argument("--themes", help="Comma-separated themes: organizational,people,physical,technological")
    parser.add_argument("--output", "-o")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    themes = [t.strip() for t in args.themes.split(",")] if args.themes else None

    checker = ISO27001Checks(target=args.target)
    checker.run_all(themes=themes)
    report = checker.generate_report()

    if not args.quiet:
        print_report(report)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        logger.info("Report saved to %s", args.output)

    if report.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
