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
Chapter 23 — DevSecOps Implementation Checklist
================================================
Automated readiness checker for the DevSecOps pipeline.
Validates git hooks, SAST/DAST scanners, container scanning, secret scanning,
SBOM generation, CI/CD security gates, and compliance-as-code rules.

Usage:
    python devsecops_checklist.py [--env staging|production] [--repo-root PATH]
    python devsecops_checklist.py --report-only
    python devsecops_checklist.py --json report.json
"""

import argparse
import json
import os
import shutil
import subprocess
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

def binary_available(name: str) -> bool:
    return shutil.which(name) is not None


def file_exists(path: str) -> bool:
    return os.path.isfile(path)


def run_cmd(cmd: list, timeout: int = 10) -> tuple:
    """Run a command, return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return -1, "", str(e)


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------

def check_git_hooks(report: ChecklistReport, repo_root: str) -> None:
    """Git hooks installed and configured."""

    hooks_dir = os.path.join(repo_root, ".git", "hooks")
    hooks_required = [
        ("pre-commit", "Runs SAST linting, secret scanning before each commit"),
        ("pre-push", "Runs full test suite and security scans before push"),
        ("commit-msg", "Validates commit message format for traceability"),
    ]

    for hook_name, description in hooks_required:
        hook_path = os.path.join(hooks_dir, hook_name)
        if file_exists(hook_path):
            # Check it's executable
            if os.access(hook_path, os.X_OK):
                st, det = Status.PASS, f"Hook found and executable at {hook_path}"
            else:
                st, det = Status.WARN, f"Hook exists but not executable — run: chmod +x {hook_path}"
        else:
            st = Status.FAIL
            det = f"Hook not found at {hook_path} — {description}"

        report.add(CheckResult(
            name=f"Git Hook: {hook_name}",
            category="Git Hooks",
            status=st,
            detail=det,
            requirement="Chapter 23 — Shift-left security enforcement",
        ))

    # pre-commit framework
    if binary_available("pre-commit"):
        rc, out, _ = run_cmd(["pre-commit", "--version"])
        st = Status.PASS if rc == 0 else Status.WARN
        det = out.strip() or "pre-commit installed"
    else:
        st, det = Status.WARN, "pre-commit framework not installed — pip install pre-commit"

    report.add(CheckResult(
        name="pre-commit Framework Installed",
        category="Git Hooks",
        status=st,
        detail=det,
        requirement="Chapter 23 — Hook management",
    ))

    # .pre-commit-config.yaml
    config_path = os.path.join(repo_root, ".pre-commit-config.yaml")
    if file_exists(config_path):
        st, det = Status.PASS, f"Config found at {config_path}"
    else:
        st, det = Status.WARN, "Missing .pre-commit-config.yaml in repo root"

    report.add(CheckResult(
        name=".pre-commit-config.yaml Present",
        category="Git Hooks",
        status=st,
        detail=det,
        requirement="Chapter 23 — Hook configuration",
    ))


def check_sast(report: ChecklistReport, report_only: bool) -> None:
    """Static Application Security Testing checks."""

    sast_tools = [
        ("bandit", ["bandit", "--version"], "Python SAST — detects common security issues"),
        ("semgrep", ["semgrep", "--version"], "Multi-language SAST with custom rules"),
        ("sonar-scanner", ["sonar-scanner", "--version"], "SonarQube scanner for code quality + security"),
        ("pylint", ["pylint", "--version"], "Python linter with security plugins"),
    ]

    for tool_name, cmd, description in sast_tools:
        if not report_only:
            rc, out, err = run_cmd(cmd)
            if rc == 0:
                version = out.strip().split("\n")[0][:60]
                st, det = Status.PASS, f"Installed: {version}"
            else:
                st, det = Status.WARN, f"Not found or error — {description}"
        else:
            st, det = Status.SKIP, "Skipped (--report-only mode)"

        report.add(CheckResult(
            name=f"SAST Tool: {tool_name}",
            category="SAST",
            status=st,
            detail=det,
            requirement="Chapter 23 — Static analysis pipeline",
        ))

    # SAST configuration file
    report.add(CheckResult(
        name="SAST Ruleset Configured",
        category="SAST",
        status=Status.WARN,
        detail="Custom semgrep rules for gambling-domain patterns (e.g., unencrypted PII, missing auth checks)",
        requirement="Chapter 23 — Domain-specific SAST rules",
    ))

    report.add(CheckResult(
        name="SAST Gate in CI (block on HIGH/CRITICAL)",
        category="SAST",
        status=Status.WARN,
        detail="CI pipeline must fail on any HIGH or CRITICAL SAST finding before merge",
        requirement="Chapter 23 — Security quality gate",
    ))


def check_dast(report: ChecklistReport) -> None:
    """Dynamic Application Security Testing checks."""

    report.add(CheckResult(
        name="DAST Tool: OWASP ZAP Configured",
        category="DAST",
        status=Status.WARN,
        detail="OWASP ZAP API scan configured against staging environment after each deployment",
        requirement="Chapter 23 — Dynamic security testing",
    ))

    report.add(CheckResult(
        name="DAST Tool: Nuclei Templates",
        category="DAST",
        status=Status.WARN,
        detail="Nuclei scanner with gambling-specific templates for vulnerability discovery",
        requirement="Chapter 23 — Vulnerability scanning",
    ))

    report.add(CheckResult(
        name="DAST Scan Frequency (per deploy)",
        category="DAST",
        status=Status.WARN,
        detail="DAST must run automatically after every deployment to staging/production",
        requirement="Chapter 23 — Continuous security validation",
    ))

    report.add(CheckResult(
        name="DAST Report Retention (90 days)",
        category="DAST",
        status=Status.WARN,
        detail="Store DAST reports for 90 days for audit trail and trend analysis",
        requirement="Chapter 23 — Audit trail",
    ))

    report.add(CheckResult(
        name="API Security Testing (REST + GraphQL)",
        category="DAST",
        status=Status.WARN,
        detail="All API endpoints must be tested including authenticated endpoints with JWT tokens",
        requirement="Chapter 23 — API security",
    ))


def check_container_scanning(report: ChecklistReport, report_only: bool) -> None:
    """Container image vulnerability scanning."""

    container_tools = [
        ("trivy", ["trivy", "version"], "Aqua Security Trivy — comprehensive container scanner"),
        ("grype", ["grype", "version"], "Anchore Grype — container and filesystem vulnerability scanner"),
        ("docker", ["docker", "--version"], "Docker CLI for image management"),
    ]

    for tool_name, cmd, description in container_tools:
        if not report_only:
            rc, out, _ = run_cmd(cmd)
            if rc == 0:
                version = out.strip().split("\n")[0][:60]
                st, det = Status.PASS, f"Installed: {version}"
            else:
                st, det = Status.WARN, f"Not found — {description}"
        else:
            st, det = Status.SKIP, "Skipped (--report-only mode)"

        report.add(CheckResult(
            name=f"Container Scanner: {tool_name}",
            category="Container Scanning",
            status=st,
            detail=det,
            requirement="Chapter 23 — Supply chain security",
        ))

    report.add(CheckResult(
        name="Base Image Pinned to Digest",
        category="Container Scanning",
        status=Status.WARN,
        detail="All Dockerfiles must use image@sha256:digest (not :latest or floating tags)",
        requirement="Chapter 23 — Image integrity",
    ))

    report.add(CheckResult(
        name="No CRITICAL CVEs in Production Images",
        category="Container Scanning",
        status=Status.WARN,
        detail="CI gate must block deployment if any CRITICAL CVE found in container image",
        requirement="Chapter 23 — Vulnerability policy",
    ))

    report.add(CheckResult(
        name="Container Registry Scanning Enabled",
        category="Container Scanning",
        status=Status.WARN,
        detail="Registry (ECR/GCR/Harbor) must continuously scan stored images for new CVEs",
        requirement="Chapter 23 — Ongoing vulnerability management",
    ))


def check_secret_scanning(report: ChecklistReport, report_only: bool) -> None:
    """Secret and credential scanning checks."""

    secret_tools = [
        ("gitleaks", ["gitleaks", "version"], "Git history secret scanner"),
        ("detect-secrets", ["detect-secrets", "--version"], "Yelp detect-secrets for pre-commit"),
        ("trufflehog", ["trufflehog", "--version"], "TruffleHog — deep entropy-based secret detection"),
    ]

    for tool_name, cmd, description in secret_tools:
        if not report_only:
            rc, out, _ = run_cmd(cmd)
            st = Status.PASS if rc == 0 else Status.WARN
            det = out.strip()[:60] if rc == 0 else f"Not found — {description}"
        else:
            st, det = Status.SKIP, "Skipped (--report-only mode)"

        report.add(CheckResult(
            name=f"Secret Scanner: {tool_name}",
            category="Secret Scanning",
            status=st,
            detail=det,
            requirement="Chapter 23 — Credential hygiene",
        ))

    report.add(CheckResult(
        name="Secret Scanning in CI Pipeline",
        category="Secret Scanning",
        status=Status.WARN,
        detail="Full git history scan must run on every PR — block merge if secrets found",
        requirement="Chapter 23 — Secret prevention",
    ))

    report.add(CheckResult(
        name=".gitleaks.toml Configuration",
        category="Secret Scanning",
        status=Status.WARN,
        detail="Custom gitleaks rules for gambling-domain secrets (PIX keys, API tokens, CPF patterns)",
        requirement="Chapter 23 — Domain-specific secret patterns",
    ))

    report.add(CheckResult(
        name="Secret Rotation Policy Documented",
        category="Secret Scanning",
        status=Status.WARN,
        detail="All secrets have documented rotation schedule; critical secrets rotated every 90 days",
        requirement="Chapter 23 — Secret lifecycle management",
    ))


def check_sbom(report: ChecklistReport, report_only: bool) -> None:
    """Software Bill of Materials generation checks."""

    sbom_tools = [
        ("syft", ["syft", "version"], "Anchore Syft — SBOM generation"),
        ("cyclonedx-bom", ["cyclonedx-bom", "--version"], "CycloneDX BOM generator"),
    ]

    for tool_name, cmd, description in sbom_tools:
        if not report_only:
            rc, out, _ = run_cmd(cmd)
            st = Status.PASS if rc == 0 else Status.WARN
            det = out.strip()[:60] if rc == 0 else f"Not found — {description}"
        else:
            st, det = Status.SKIP, "Skipped (--report-only mode)"

        report.add(CheckResult(
            name=f"SBOM Tool: {tool_name}",
            category="SBOM",
            status=st,
            detail=det,
            requirement="Chapter 23 — Software supply chain transparency",
        ))

    report.add(CheckResult(
        name="SBOM Generated Per Release",
        category="SBOM",
        status=Status.WARN,
        detail="CycloneDX or SPDX SBOM must be generated and published with every production release",
        requirement="Chapter 23 — Supply chain compliance",
    ))

    report.add(CheckResult(
        name="SBOM Stored in Artifact Registry",
        category="SBOM",
        status=Status.WARN,
        detail="SBOM files stored alongside container images in registry (attestation)",
        requirement="Chapter 23 — SBOM retention",
    ))

    report.add(CheckResult(
        name="License Compliance Check (GPL/AGPL)",
        category="SBOM",
        status=Status.WARN,
        detail="SBOM must flag GPL/AGPL dependencies that conflict with commercial licensing",
        requirement="Chapter 23 — License risk management",
    ))


def check_cicd_gates(report: ChecklistReport) -> None:
    """CI/CD pipeline security gates."""

    gates = [
        ("Branch Protection (main/master)", "Direct push to main blocked; require PR + review + status checks"),
        ("Required Status Checks (SAST + Tests)", "All SAST and unit test checks must pass before merge"),
        ("Signed Commits Required", "All commits must be GPG-signed for auditability"),
        ("Two-Person Rule for Production Deploy", "Production deployments require approval from second engineer"),
        ("Rollback Plan Documented", "Every deployment must have a documented rollback procedure"),
        ("Deployment Freeze Windows", "No deployments during peak hours or regulatory reporting windows"),
        ("Environment Parity (staging == production)", "Staging must mirror production infra including security controls"),
        ("Secrets via Vault/SSM (never in CI env vars)", "No plaintext secrets in CI environment variables"),
    ]

    for gate_name, description in gates:
        report.add(CheckResult(
            name=f"CI/CD Gate: {gate_name}",
            category="CI/CD Security Gates",
            status=Status.WARN,
            detail=description,
            requirement="Chapter 23 — Secure deployment pipeline",
        ))


def check_compliance_as_code(report: ChecklistReport) -> None:
    """Compliance-as-code (policy-as-code) checks."""

    rules = [
        ("OPA/Gatekeeper Policies Deployed", "Open Policy Agent rules enforce security baselines on K8s resources"),
        ("No Privileged Containers Policy", "All pods must run as non-root with read-only root filesystem"),
        ("Network Policy Enforcement", "Default-deny ingress/egress with explicit allowlist per namespace"),
        ("Resource Limits Required", "All containers must declare CPU/memory requests and limits"),
        ("Image Registry Allowlist", "Only images from approved registries allowed (no docker.io)"),
        ("PCI DSS Control Mapping", "OPA rules enforce PCI DSS 4.0 controls: encryption, logging, access control"),
        ("LGPD Data Tagging Policy", "Kubernetes labels required on all workloads handling personal data"),
        ("Audit Logging Policy", "All privileged operations must be captured in immutable audit log"),
    ]

    for rule_name, description in rules:
        report.add(CheckResult(
            name=f"Policy: {rule_name}",
            category="Compliance-as-Code",
            status=Status.WARN,
            detail=description,
            requirement="Chapter 23 — Policy-as-code enforcement",
        ))


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def print_report(report: ChecklistReport) -> None:
    print()
    print("=" * 70)
    print("  CHAPTER 23 — DEVSECOPS IMPLEMENTATION CHECKLIST")
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
    print(f"\n  DevSecOps readiness: {readiness:.0f}%")

    if report.failed > 0:
        print(f"\n  NOT READY — {report.failed} critical checks failed")
    elif report.warnings > 5:
        print(f"\n  REVIEW NEEDED — {report.warnings} items require manual verification")
    else:
        print(f"\n  READY — DevSecOps pipeline checks passed")

    print(f"\n{'=' * 70}\n")


def export_json(report: ChecklistReport, path: str) -> None:
    data = {
        "timestamp": report.timestamp,
        "environment": report.environment,
        "chapter": 23,
        "title": "DevSecOps",
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
    parser = argparse.ArgumentParser(description="Chapter 23 — DevSecOps Checklist")
    parser.add_argument("--env", default="staging", choices=["staging", "production"])
    parser.add_argument("--repo-root", default=".", help="Path to repository root for git hook checks")
    parser.add_argument("--report-only", action="store_true", help="Show checklist without running tool checks")
    parser.add_argument("--json", type=str, help="Export report to JSON file")
    args = parser.parse_args()

    repo_root = os.path.abspath(args.repo_root)

    report = ChecklistReport(
        timestamp=datetime.now(timezone.utc).isoformat(),
        environment=args.env,
    )

    check_git_hooks(report, repo_root)
    check_sast(report, args.report_only)
    check_dast(report)
    check_container_scanning(report, args.report_only)
    check_secret_scanning(report, args.report_only)
    check_sbom(report, args.report_only)
    check_cicd_gates(report)
    check_compliance_as_code(report)

    print_report(report)

    if args.json:
        export_json(report, args.json)

    sys.exit(1 if report.failed > 0 else 0)


if __name__ == "__main__":
    main()
