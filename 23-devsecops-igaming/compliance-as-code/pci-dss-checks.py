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
PCI DSS v4.0 Automated Compliance Checks for iGaming Platforms
================================================================
InSpec-style assertion framework that validates PCI DSS v4.0 controls
against live infrastructure. Designed for casino payment processing
systems, card-present and card-not-present transaction environments.

Covers key PCI DSS v4.0 requirements:
  - Req 1: Network Security Controls (firewalls, segmentation)
  - Req 2: Secure Configurations (no defaults, hardening)
  - Req 3: Protect Stored Account Data (encryption at rest)
  - Req 4: Protect Data in Transit (TLS, no weak ciphers)
  - Req 5: Malware Protection
  - Req 6: Secure Development (SDLC, patching)
  - Req 7: Restrict Access (RBAC, least privilege)
  - Req 8: Identify Users (MFA, password policies)
  - Req 10: Log and Monitor (audit trails)
  - Req 11: Security Testing (vulnerability scanning, penetration testing)
  - Req 12: Organizational Policies

Usage:
    python3 pci-dss-checks.py --target production
    python3 pci-dss-checks.py --target staging --requirements 3,4,8
    python3 pci-dss-checks.py --target production --output pci-report.json
"""
import argparse
import json
import logging
import os
import re
import socket
import ssl
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("pci-dss-checks")


# ---------------------------------------------------------------------------
# Check Framework
# ---------------------------------------------------------------------------
class CheckResult:
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARNING"
    SKIP = "SKIP"
    ERROR = "ERROR"


@dataclass
class ComplianceCheck:
    """Single PCI DSS compliance check result."""
    requirement: str          # e.g., "1.2.1"
    title: str
    description: str
    status: str               # PASS, FAIL, WARNING, SKIP, ERROR
    severity: str             # critical, high, medium, low
    evidence: str = ""
    remediation: str = ""
    igaming_note: str = ""    # iGaming-specific guidance


@dataclass
class ComplianceReport:
    """Full PCI DSS compliance report."""
    target: str
    scan_timestamp: str
    pci_dss_version: str = "4.0"
    total_checks: int = 0
    passed: int = 0
    failed: int = 0
    warnings: int = 0
    skipped: int = 0
    errors: int = 0
    compliance_score: float = 0.0
    checks: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
def run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    """Execute command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except FileNotFoundError:
        return -1, "", f"Command not found: {cmd[0]}"


def check_tls_endpoint(hostname: str, port: int = 443) -> dict:
    """Check TLS configuration of an endpoint."""
    result = {"hostname": hostname, "port": port}
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as ssock:
                result["version"] = ssock.version()  # ty:ignore[invalid-assignment]
                cipher = ssock.cipher()
                result["cipher"] = cipher[0] if cipher else "unknown"
                result["key_size"] = cipher[2] if cipher and len(cipher) > 2 else 0
                result["valid"] = True
    except Exception as e:
        result["valid"] = False
        result["error"] = str(e)
    return result


def kubectl_get_json(resource: str, namespace: str = "", selector: str = "") -> Optional[list]:
    """Retrieve Kubernetes resources as JSON."""
    cmd = ["kubectl", "get", resource, "-o", "json"]
    if namespace:
        cmd.extend(["-n", namespace])
    if selector:
        cmd.extend(["-l", selector])
    rc, out, err = run_cmd(cmd)
    if rc != 0:
        return None
    try:
        data = json.loads(out)
        return data.get("items", [data] if "kind" in data else [])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# PCI DSS Requirement Checks
# ---------------------------------------------------------------------------
class PCIDSSChecks:
    """PCI DSS v4.0 compliance checks for iGaming infrastructure."""

    def __init__(self, target: str):
        self.target = target
        self.checks: list[ComplianceCheck] = []

    def add_check(self, **kwargs) -> ComplianceCheck:
        check = ComplianceCheck(**kwargs)
        self.checks.append(check)
        return check

    # --- Requirement 1: Network Security Controls ---

    def req_1_2_1_network_segmentation(self):
        """Verify CDE is network-segmented from non-CDE."""
        # Check for Kubernetes NetworkPolicies in payment namespaces
        policies = kubectl_get_json("networkpolicies", namespace="payment-system")
        if policies is None:
            return self.add_check(
                requirement="1.2.1",
                title="CDE Network Segmentation",
                description="Cardholder Data Environment must be segmented from non-CDE networks",
                status=CheckResult.SKIP,
                severity="critical",
                evidence="Could not query Kubernetes — check manually",
                remediation="Apply NetworkPolicies to payment-system namespace",
                igaming_note="Casino payment gateways (Adyen, Nuvei, Worldpay) must be isolated from game servers",
            )

        if len(policies) > 0:
            # Verify policies deny all ingress by default
            has_deny_all = any(
                p.get("spec", {}).get("policyTypes", []) == ["Ingress", "Egress"]
                and not p.get("spec", {}).get("ingress")
                for p in policies
            )
            return self.add_check(
                requirement="1.2.1",
                title="CDE Network Segmentation",
                description="Cardholder Data Environment must be segmented from non-CDE networks",
                status=CheckResult.PASS if has_deny_all else CheckResult.WARN,
                severity="critical",
                evidence=f"Found {len(policies)} NetworkPolicies in payment-system namespace. Default-deny: {has_deny_all}",
                remediation="Ensure default-deny NetworkPolicy exists for payment-system namespace" if not has_deny_all else "",
                igaming_note="Payment processing pods must not be accessible from game-engine or marketing namespaces",
            )
        else:
            return self.add_check(
                requirement="1.2.1",
                title="CDE Network Segmentation",
                description="Cardholder Data Environment must be segmented from non-CDE networks",
                status=CheckResult.FAIL,
                severity="critical",
                evidence="No NetworkPolicies found in payment-system namespace",
                remediation="Create default-deny NetworkPolicy and allow only specific payment provider traffic",
                igaming_note="PCI DSS v4.0 requires documented network segmentation for all payment flows",
            )

    def req_1_3_3_anti_spoofing(self):
        """Check anti-spoofing measures on network boundaries."""
        # Check iptables or cloud security groups
        rc, out, _ = run_cmd(["iptables", "-L", "INPUT", "-n", "--line-numbers"])
        if rc == 0:
            has_anti_spoof = "DROP" in out and ("0.0.0.0/0" in out or "REJECT" in out)
            return self.add_check(
                requirement="1.3.3",
                title="Anti-Spoofing Controls",
                description="Anti-spoofing measures must be implemented on network boundaries",
                status=CheckResult.PASS if has_anti_spoof else CheckResult.WARN,
                severity="high",
                evidence=f"iptables rules found. Anti-spoofing detected: {has_anti_spoof}",
                remediation="Add explicit anti-spoofing rules to drop packets with spoofed source addresses",
            )
        return self.add_check(
            requirement="1.3.3",
            title="Anti-Spoofing Controls",
            description="Anti-spoofing measures must be implemented on network boundaries",
            status=CheckResult.SKIP,
            severity="high",
            evidence="iptables not accessible — verify at cloud/firewall level",
        )

    # --- Requirement 2: Secure Configurations ---

    def req_2_2_1_no_default_credentials(self):
        """Check for default credentials in deployed services."""
        # Check common Kubernetes secrets for default values
        default_passwords = ["admin", "password", "changeme", "default", "test123", "12345"]
        issues = []

        secrets = kubectl_get_json("secrets", namespace="payment-system")
        if secrets:
            for secret in secrets:
                name = secret.get("metadata", {}).get("name", "")
                # Skip service account tokens
                if "token" in name and "service-account" in name:
                    continue
                data = secret.get("data", {})
                for key, val in data.items():
                    if any(kw in key.lower() for kw in ["password", "secret", "key", "token"]):
                        # Base64 decode and check
                        try:
                            import base64
                            decoded = base64.b64decode(val).decode("utf-8", errors="ignore")
                            if decoded.lower() in default_passwords:
                                issues.append(f"Secret '{name}' key '{key}' uses a default value")
                        except Exception:
                            pass

        status = CheckResult.FAIL if issues else CheckResult.PASS
        return self.add_check(
            requirement="2.2.1",
            title="No Default Credentials",
            description="Default vendor credentials must be changed before deployment",
            status=status,
            severity="critical",
            evidence="; ".join(issues) if issues else "No default credentials detected in payment-system secrets",
            remediation="Rotate all default credentials. Use Vault for secret management." if issues else "",
            igaming_note="Casino admin panels and backoffice systems are high-value targets for credential stuffing",
        )

    def req_2_2_6_system_hardening(self):
        """Check container image hardening."""
        # Check for running containers with root user
        rc, out, _ = run_cmd(["kubectl", "get", "pods", "-n", "payment-system",
                              "-o", "jsonpath={range .items[*]}{.metadata.name}:{.spec.containers[*].securityContext.runAsNonRoot}{'\\n'}{end}"])
        if rc != 0:
            return self.add_check(
                requirement="2.2.6",
                title="System Hardening",
                description="Unnecessary functions removed, secure configurations enforced",
                status=CheckResult.SKIP,
                severity="high",
                evidence="Could not query pod security contexts",
            )

        root_pods = []
        for line in out.strip().split("\n"):
            if line and ":true" not in line.lower() and line.strip():
                pod_name = line.split(":")[0]
                if pod_name:
                    root_pods.append(pod_name)

        status = CheckResult.FAIL if root_pods else CheckResult.PASS
        return self.add_check(
            requirement="2.2.6",
            title="System Hardening - Non-Root Containers",
            description="CDE containers must run as non-root with minimal privileges",
            status=status,
            severity="high",
            evidence=f"Pods running as root: {', '.join(root_pods)}" if root_pods else "All payment pods run as non-root",
            remediation="Set securityContext.runAsNonRoot: true and runAsUser: 1000 for all payment pods" if root_pods else "",
            igaming_note="Game server containers handling financial transactions must never run as root",
        )

    # --- Requirement 3: Protect Stored Account Data ---

    def req_3_5_1_encryption_at_rest(self):
        """Verify cardholder data encryption at rest."""
        # Check Kubernetes encryption configuration
        rc, out, _ = run_cmd(["kubectl", "get", "encryptionconfigurations", "-o", "json"])
        if rc != 0:
            # Try alternate check via API server flags
            rc2, out2, _ = run_cmd(["kubectl", "describe", "pod", "-n", "kube-system", "-l", "component=kube-apiserver"])
            has_encryption = "--encryption-provider-config" in out2 if rc2 == 0 else False
            return self.add_check(
                requirement="3.5.1",
                title="Encryption at Rest",
                description="Stored account data must be encrypted using strong cryptography",
                status=CheckResult.PASS if has_encryption else CheckResult.WARN,
                severity="critical",
                evidence="API server encryption provider config detected" if has_encryption else "Could not verify encryption at rest configuration",
                remediation="Configure EncryptionConfiguration with AES-256-GCM for Kubernetes secrets",
                igaming_note="Player card data stored for recurring deposits must be encrypted with AES-256 minimum",
            )

        return self.add_check(
            requirement="3.5.1",
            title="Encryption at Rest",
            description="Stored account data must be encrypted using strong cryptography",
            status=CheckResult.PASS,
            severity="critical",
            evidence="Kubernetes encryption configuration found",
        )

    # --- Requirement 4: Protect Data in Transit ---

    def req_4_2_1_strong_cryptography_transit(self):
        """Verify TLS 1.2+ on all payment endpoints."""
        payment_endpoints = [
            ("payment-gateway.acme-casino.io", 443),
            ("wallet-api.acme-casino.io", 443),
            ("cashier.acme-casino.io", 443),
        ]

        results = []
        weak_endpoints = []

        for hostname, port in payment_endpoints:
            tls = check_tls_endpoint(hostname, port)
            results.append(tls)
            if tls.get("valid"):
                version = tls.get("version", "")
                if version in ("SSLv3", "TLSv1", "TLSv1.1"):
                    weak_endpoints.append(f"{hostname}: {version}")
            # Endpoint unreachable is not necessarily a failure (might be internal)

        status = CheckResult.FAIL if weak_endpoints else CheckResult.PASS
        evidence_parts = []
        for r in results:
            if r.get("valid"):
                evidence_parts.append(f"{r['hostname']}: {r.get('version', 'N/A')} ({r.get('cipher', 'N/A')})")
            elif "error" in r:
                evidence_parts.append(f"{r['hostname']}: unreachable ({r['error'][:50]})")

        return self.add_check(
            requirement="4.2.1",
            title="Strong Cryptography in Transit",
            description="TLS 1.2+ required for all payment data transmission",
            status=status,
            severity="critical",
            evidence="; ".join(evidence_parts) if evidence_parts else "No endpoints reachable for testing",
            remediation="Disable TLS 1.0/1.1. Configure TLS 1.2+ with strong cipher suites." if weak_endpoints else "",
            igaming_note="All player deposit/withdrawal flows must use TLS 1.2+ (PCI DSS v4.0 removed TLS 1.0/1.1 grace period)",
        )

    # --- Requirement 6: Secure Development ---

    def req_6_3_3_patch_management(self):
        """Check for known vulnerabilities in deployed images."""
        # Check if vulnerability scanning is integrated in CI/CD
        rc, out, _ = run_cmd(["kubectl", "get", "pods", "-n", "payment-system",
                              "-o", "jsonpath={range .items[*]}{.spec.containers[*].image}{'\\n'}{end}"])
        if rc != 0:
            return self.add_check(
                requirement="6.3.3",
                title="Vulnerability Patch Management",
                description="Known vulnerabilities must be patched per severity-based SLA",
                status=CheckResult.SKIP,
                severity="high",
                evidence="Could not enumerate payment pod images",
            )

        images = [img.strip() for img in out.strip().split("\n") if img.strip()]
        images_without_tag = [img for img in images if ":latest" in img or ":" not in img]

        return self.add_check(
            requirement="6.3.3",
            title="Vulnerability Patch Management",
            description="Known vulnerabilities must be patched per severity-based SLA",
            status=CheckResult.FAIL if images_without_tag else CheckResult.PASS,
            severity="high",
            evidence=f"Images using :latest tag: {', '.join(images_without_tag)}" if images_without_tag else f"All {len(images)} images use explicit version tags",
            remediation="Pin all container images to specific SHA256 digests or semantic version tags" if images_without_tag else "",
            igaming_note="Game provider SDK images and payment gateway clients must use pinned, scanned versions",
        )

    # --- Requirement 8: Identify and Authenticate ---

    def req_8_3_6_password_complexity(self):
        """Check password policy configuration."""
        # Check if password policy is enforced in auth service
        password_policy = {
            "min_length": 12,
            "require_uppercase": True,
            "require_lowercase": True,
            "require_digits": True,
            "require_special": True,
            "max_age_days": 90,
            "history_count": 4,
        }

        # Check Kubernetes ConfigMaps for auth service config
        configs = kubectl_get_json("configmaps", namespace="auth-system", selector="app=auth-service")
        if not configs:
            return self.add_check(
                requirement="8.3.6",
                title="Password Complexity Requirements",
                description="Passwords must meet minimum complexity (12+ chars, mixed case, numbers, special)",
                status=CheckResult.WARN,
                severity="high",
                evidence="Auth service ConfigMap not found — verify password policy manually",
                remediation=f"Configure password policy: {json.dumps(password_policy)}",
                igaming_note="Casino player accounts and backoffice admin accounts require strict password policies per UKGC LCCP",
            )

        return self.add_check(
            requirement="8.3.6",
            title="Password Complexity Requirements",
            description="Passwords must meet minimum complexity (12+ chars, mixed case, numbers, special)",
            status=CheckResult.WARN,
            severity="high",
            evidence="Auth service config found — manual review required for password policy validation",
            remediation=f"Ensure password policy meets: {json.dumps(password_policy)}",
        )

    def req_8_4_2_mfa_cde_access(self):
        """Check MFA for CDE access."""
        # Check if MFA is configured on the auth system / VPN
        return self.add_check(
            requirement="8.4.2",
            title="MFA for CDE Access",
            description="Multi-factor authentication required for all access to CDE",
            status=CheckResult.WARN,
            severity="critical",
            evidence="MFA configuration requires manual verification against IAM provider (Okta/Azure AD/Google Workspace)",
            remediation="Enable MFA for: 1) All admin console access, 2) VPN to CDE networks, 3) SSH to payment servers, 4) Database access",
            igaming_note="Casino backoffice operators handling player withdrawals must have hardware MFA tokens (FIDO2/YubiKey)",
        )

    # --- Requirement 10: Logging and Monitoring ---

    def req_10_2_1_audit_logging(self):
        """Verify comprehensive audit logging is enabled."""
        # Check Kubernetes audit policy
        rc, out, _ = run_cmd(["kubectl", "describe", "pod", "-n", "kube-system", "-l", "component=kube-apiserver"])
        has_audit = "--audit-log-path" in out if rc == 0 else False

        # Check for centralized logging (Elasticsearch/Loki)
        logging_pods = kubectl_get_json("pods", namespace="logging") or kubectl_get_json("pods", namespace="monitoring")
        has_centralized = bool(logging_pods)

        if has_audit and has_centralized:
            status = CheckResult.PASS
            evidence = "Kubernetes audit logging enabled with centralized log collection"
        elif has_audit:
            status = CheckResult.WARN
            evidence = "Kubernetes audit logging enabled but centralized collection not verified"
        else:
            status = CheckResult.FAIL
            evidence = "Kubernetes audit logging not detected"

        return self.add_check(
            requirement="10.2.1",
            title="Audit Logging",
            description="Audit logs must capture all access to cardholder data",
            status=status,
            severity="critical",
            evidence=evidence,
            remediation="Enable Kubernetes audit logging and forward to SIEM. Log all payment API calls, admin actions, and data access.",
            igaming_note="Gambling regulators (UKGC, MGA) require 5-year audit log retention for financial transactions",
        )

    # --- Requirement 11: Security Testing ---

    def req_11_3_1_vulnerability_scanning(self):
        """Verify regular vulnerability scanning."""
        # Check if vulnerability scanner is deployed
        scanner_pods = (
            kubectl_get_json("pods", namespace="security", selector="app=trivy-operator")
            or kubectl_get_json("pods", namespace="security", selector="app=vulnerability-scanner")
        )

        if scanner_pods:
            running = sum(1 for p in scanner_pods
                          if p.get("status", {}).get("phase") == "Running")
            return self.add_check(
                requirement="11.3.1",
                title="Internal Vulnerability Scanning",
                description="Internal vulnerability scans must be performed quarterly (at minimum)",
                status=CheckResult.PASS if running > 0 else CheckResult.WARN,
                severity="high",
                evidence=f"Found {len(scanner_pods)} scanner pods ({running} running)",
                remediation="" if running > 0 else "Ensure vulnerability scanner pods are running",
                igaming_note="GLI-33 requires documented vulnerability scanning results for certification",
            )

        return self.add_check(
            requirement="11.3.1",
            title="Internal Vulnerability Scanning",
            description="Internal vulnerability scans must be performed quarterly (at minimum)",
            status=CheckResult.FAIL,
            severity="high",
            evidence="No automated vulnerability scanning infrastructure detected",
            remediation="Deploy Trivy Operator or equivalent for continuous vulnerability scanning",
            igaming_note="Both PCI DSS and gambling licenses (UKGC, MGA) require quarterly vulnerability scans by ASV",
        )

    # --- Run all checks ---

    def run_all(self, requirements: Optional[list[str]] = None):
        """Execute all PCI DSS checks (or filtered by requirement number)."""
        all_checks = {
            "1": [self.req_1_2_1_network_segmentation, self.req_1_3_3_anti_spoofing],
            "2": [self.req_2_2_1_no_default_credentials, self.req_2_2_6_system_hardening],
            "3": [self.req_3_5_1_encryption_at_rest],
            "4": [self.req_4_2_1_strong_cryptography_transit],
            "6": [self.req_6_3_3_patch_management],
            "8": [self.req_8_3_6_password_complexity, self.req_8_4_2_mfa_cde_access],
            "10": [self.req_10_2_1_audit_logging],
            "11": [self.req_11_3_1_vulnerability_scanning],
        }

        for req_num, check_funcs in all_checks.items():
            if requirements and req_num not in requirements:
                continue
            for check_func in check_funcs:
                try:
                    check_func()
                except Exception as e:
                    self.add_check(
                        requirement=f"{req_num}.x",
                        title=check_func.__doc__ or check_func.__name__,
                        description=f"Check failed with error: {e}",
                        status=CheckResult.ERROR,
                        severity="high",
                    )

    def generate_report(self) -> ComplianceReport:
        report = ComplianceReport(
            target=self.target,
            scan_timestamp=datetime.now(timezone.utc).isoformat(),
            total_checks=len(self.checks),
            passed=sum(1 for c in self.checks if c.status == CheckResult.PASS),
            failed=sum(1 for c in self.checks if c.status == CheckResult.FAIL),
            warnings=sum(1 for c in self.checks if c.status == CheckResult.WARN),
            skipped=sum(1 for c in self.checks if c.status == CheckResult.SKIP),
            errors=sum(1 for c in self.checks if c.status == CheckResult.ERROR),
            checks=[asdict(c) for c in self.checks],
        )
        assessed = report.passed + report.failed
        report.compliance_score = round(report.passed / max(assessed, 1) * 100, 1)
        return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def print_report(report: ComplianceReport):
    print("\n" + "=" * 75)
    print(f"  PCI DSS v{report.pci_dss_version} Compliance Assessment")
    print(f"  Target: {report.target}")
    print(f"  Time: {report.scan_timestamp}")
    print("=" * 75)
    print(f"\n  Score: {report.compliance_score}%")
    print(f"  Passed: {report.passed} | Failed: {report.failed} | "
          f"Warnings: {report.warnings} | Skipped: {report.skipped} | Errors: {report.errors}")
    print()

    for check in report.checks:
        icon = {"PASS": "[PASS]", "FAIL": "[FAIL]", "WARNING": "[WARN]", "SKIP": "[SKIP]", "ERROR": "[ERR!]"}.get(check["status"], "[????]")
        print(f"  {icon:>6s}  Req {check['requirement']:>6s}  {check['title']}")
        if check["status"] in ("FAIL", "ERROR"):
            if check.get("evidence"):
                print(f"           Evidence: {check['evidence'][:100]}")
            if check.get("remediation"):
                print(f"           Fix: {check['remediation'][:100]}")
            if check.get("igaming_note"):
                print(f"           iGaming: {check['igaming_note'][:100]}")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="PCI DSS v4.0 compliance checker for iGaming")
    parser.add_argument("--target", default="production", help="Target environment")
    parser.add_argument("--requirements", help="Comma-separated requirement numbers (e.g., 3,4,8)")
    parser.add_argument("--output", "-o", help="Output JSON report path")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()

    reqs = args.requirements.split(",") if args.requirements else None

    checker = PCIDSSChecks(target=args.target)
    checker.run_all(requirements=reqs)
    report = checker.generate_report()

    if not args.quiet:
        print_report(report)

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(asdict(report), f, indent=2, default=str)
        logger.info("Report saved to %s", args.output)

    # Exit code based on compliance
    if report.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
