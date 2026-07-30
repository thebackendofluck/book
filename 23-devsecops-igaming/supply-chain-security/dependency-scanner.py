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
Multi-Scanner Dependency Aggregator for iGaming Platforms
=========================================================
Aggregates vulnerability findings from Trivy, Grype, and Snyk into a unified
view with contextual severity scoring tailored for iGaming environments.

Payment-path components, RNG services, and KYC modules receive elevated
severity scores. Findings are deduplicated by CVE and enriched with
iGaming-specific context before output.

Usage:
    python3 dependency-scanner.py --image acme-casino/payment-service:v2.1.0
    python3 dependency-scanner.py --image acme-casino/game-engine:latest --output report.json
    python3 dependency-scanner.py --sbom /path/to/sbom.json --scanners trivy,grype
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("dependency-scanner")

# iGaming contextual severity multipliers
# Components in critical paths get their CVSS scores multiplied
IGAMING_CONTEXT_MULTIPLIERS = {
    # Payment and financial components
    "payment":              1.5,
    "wallet":               1.5,
    "transaction":          1.4,
    "billing":              1.3,
    "payout":               1.5,
    "stripe":               1.4,
    "adyen":                1.4,
    # Game integrity components
    "rng":                  1.6,
    "random":               1.5,
    "game-engine":          1.4,
    "game_engine":          1.4,
    "slot":                 1.3,
    "live-dealer":          1.3,
    # Identity and compliance
    "kyc":                  1.4,
    "identity":             1.3,
    "verification":         1.3,
    "aml":                  1.4,
    "responsible-gaming":   1.3,
    "self-exclusion":       1.3,
    # Anti-fraud
    "anti-fraud":           1.5,
    "fraud":                1.4,
    "bonus-engine":         1.3,
    # Regulatory
    "regulatory":           1.3,
    "reporting":            1.2,
    "audit":                1.2,
    # Cryptographic libraries (always critical in gambling)
    "openssl":              1.4,
    "libcrypto":            1.4,
    "bcrypt":               1.3,
    "jose":                 1.3,
    "jwt":                  1.3,
}

# SLA thresholds (hours) by effective severity
SLA_HOURS = {
    "CRITICAL": 4,
    "HIGH":     24,
    "MEDIUM":   168,    # 7 days
    "LOW":      720,    # 30 days
}


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NEGLIGIBLE = "NEGLIGIBLE"
    UNKNOWN = "UNKNOWN"


@dataclass
class VulnFinding:
    """Unified vulnerability finding across scanners."""
    cve_id: str
    title: str
    severity: str
    cvss_score: float
    effective_score: float       # After iGaming context multiplier
    effective_severity: str      # Recalculated based on effective_score
    package_name: str
    package_version: str
    fixed_version: Optional[str]
    scanners: list = field(default_factory=list)
    igaming_context: str = ""
    sla_hours: int = 0
    data_source: str = ""
    description: str = ""
    exploit_available: bool = False


# ---------------------------------------------------------------------------
# Scanner Adapters
# ---------------------------------------------------------------------------
class TrivyScanner:
    """Adapter for Aqua Security Trivy."""

    name = "trivy"

    @staticmethod
    def scan(target: str, is_sbom: bool = False) -> list[dict]:
        logger.info("Running Trivy scan on %s", target)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            cmd = ["trivy"]
            if is_sbom:
                cmd += ["sbom", target]
            else:
                cmd += ["image", "--severity", "CRITICAL,HIGH,MEDIUM,LOW", target]
            cmd += ["-f", "json", "-o", tmp_path, "--quiet"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.warning("Trivy scan returned code %d: %s", result.returncode, result.stderr)

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                logger.error("Trivy produced no output")
                return []

            with open(tmp_path) as f:
                data = json.load(f)

            findings = []
            for result_block in data.get("Results", []):
                for vuln in result_block.get("Vulnerabilities", []):
                    findings.append({
                        "cve_id": vuln.get("VulnerabilityID", ""),
                        "title": vuln.get("Title", vuln.get("VulnerabilityID", "")),
                        "severity": vuln.get("Severity", "UNKNOWN").upper(),
                        "cvss_score": _extract_cvss(vuln),
                        "package_name": vuln.get("PkgName", ""),
                        "package_version": vuln.get("InstalledVersion", ""),
                        "fixed_version": vuln.get("FixedVersion"),
                        "description": vuln.get("Description", ""),
                        "scanner": "trivy",
                    })
            logger.info("Trivy found %d vulnerabilities", len(findings))
            return findings
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class GrypeScanner:
    """Adapter for Anchore Grype."""

    name = "grype"

    @staticmethod
    def scan(target: str, is_sbom: bool = False) -> list[dict]:
        logger.info("Running Grype scan on %s", target)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if is_sbom:
                cmd = ["grype", f"sbom:{target}"]
            else:
                cmd = ["grype", target]
            cmd += ["-o", "json", "--file", tmp_path, "--quiet"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                logger.warning("Grype scan returned code %d: %s", result.returncode, result.stderr)

            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                logger.error("Grype produced no output")
                return []

            with open(tmp_path) as f:
                data = json.load(f)

            findings = []
            for match in data.get("matches", []):
                vuln = match.get("vulnerability", {})
                artifact = match.get("artifact", {})
                findings.append({
                    "cve_id": vuln.get("id", ""),
                    "title": vuln.get("description", vuln.get("id", ""))[:200],
                    "severity": vuln.get("severity", "UNKNOWN").upper(),
                    "cvss_score": _extract_grype_cvss(vuln),
                    "package_name": artifact.get("name", ""),
                    "package_version": artifact.get("version", ""),
                    "fixed_version": _extract_grype_fix(vuln),
                    "description": vuln.get("description", ""),
                    "scanner": "grype",
                })
            logger.info("Grype found %d vulnerabilities", len(findings))
            return findings
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


class SnykScanner:
    """Adapter for Snyk container scanning."""

    name = "snyk"

    @staticmethod
    def scan(target: str, is_sbom: bool = False) -> list[dict]:
        logger.info("Running Snyk scan on %s", target)
        try:
            if is_sbom:
                cmd = ["snyk", "test", "--json", f"--file={target}"]
            else:
                cmd = ["snyk", "container", "test", target, "--json"]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            # Snyk returns exit code 1 when vulns are found
            if result.returncode > 1:
                logger.warning("Snyk error: %s", result.stderr)
                return []

            if not result.stdout.strip():
                logger.error("Snyk produced no output")
                return []

            data = json.loads(result.stdout)
            findings = []
            for vuln in data.get("vulnerabilities", []):
                findings.append({
                    "cve_id": vuln.get("identifiers", {}).get("CVE", [""])[0] if vuln.get("identifiers", {}).get("CVE") else vuln.get("id", ""),
                    "title": vuln.get("title", ""),
                    "severity": vuln.get("severity", "unknown").upper(),
                    "cvss_score": vuln.get("cvssScore", 0.0),
                    "package_name": vuln.get("packageName", ""),
                    "package_version": vuln.get("version", ""),
                    "fixed_version": vuln.get("fixedIn", [None])[0] if vuln.get("fixedIn") else None,
                    "description": vuln.get("description", "")[:500],
                    "scanner": "snyk",
                    "exploit_available": vuln.get("exploit", "Not Defined") != "Not Defined",
                })
            logger.info("Snyk found %d vulnerabilities", len(findings))
            return findings
        except (json.JSONDecodeError, FileNotFoundError) as e:
            logger.error("Snyk scan failed: %s", e)
            return []


# ---------------------------------------------------------------------------
# CVSS extraction helpers
# ---------------------------------------------------------------------------
def _extract_cvss(vuln: dict) -> float:
    """Extract CVSS score from Trivy vulnerability data."""
    cvss = vuln.get("CVSS", {})
    for source in ["nvd", "redhat", "ghsa"]:
        if source in cvss and "V3Score" in cvss[source]:
            return float(cvss[source]["V3Score"])
    # Fallback to severity mapping
    return _severity_to_cvss(vuln.get("Severity", "UNKNOWN"))


def _extract_grype_cvss(vuln: dict) -> float:
    """Extract CVSS score from Grype vulnerability data."""
    for cvss_entry in vuln.get("cvss", []):
        if "metrics" in cvss_entry and "baseScore" in cvss_entry["metrics"]:
            return float(cvss_entry["metrics"]["baseScore"])
    return _severity_to_cvss(vuln.get("severity", "UNKNOWN"))


def _extract_grype_fix(vuln: dict) -> Optional[str]:
    """Extract fixed version from Grype vulnerability data."""
    fix = vuln.get("fix", {})
    versions = fix.get("versions", [])
    return versions[0] if versions else None


def _severity_to_cvss(severity: str) -> float:
    """Map severity string to approximate CVSS score."""
    return {
        "CRITICAL": 9.5,
        "HIGH": 7.5,
        "MEDIUM": 5.0,
        "LOW": 2.5,
        "NEGLIGIBLE": 0.5,
    }.get(severity.upper(), 0.0)


# ---------------------------------------------------------------------------
# Contextual Scoring Engine
# ---------------------------------------------------------------------------
def apply_igaming_context(finding: dict, image_name: str) -> VulnFinding:
    """Apply iGaming-specific contextual scoring to a finding."""
    base_score = finding.get("cvss_score", 0.0)
    max_multiplier = 1.0
    context_reasons = []

    # Check image/service name against context multipliers
    image_lower = image_name.lower()
    for keyword, multiplier in IGAMING_CONTEXT_MULTIPLIERS.items():
        if keyword in image_lower:
            if multiplier > max_multiplier:
                max_multiplier = multiplier
                context_reasons.append(f"service={keyword}(x{multiplier})")

    # Check package name against context multipliers
    pkg_lower = finding.get("package_name", "").lower()
    for keyword, multiplier in IGAMING_CONTEXT_MULTIPLIERS.items():
        if keyword in pkg_lower:
            if multiplier > max_multiplier:
                max_multiplier = multiplier
                context_reasons.append(f"package={keyword}(x{multiplier})")

    # Exploit availability adds 20% to score
    if finding.get("exploit_available"):
        max_multiplier *= 1.2
        context_reasons.append("exploit_available(x1.2)")

    effective_score = min(base_score * max_multiplier, 10.0)
    effective_severity = _score_to_severity(effective_score)

    return VulnFinding(
        cve_id=finding["cve_id"],
        title=finding.get("title", ""),
        severity=finding.get("severity", "UNKNOWN"),
        cvss_score=base_score,
        effective_score=round(effective_score, 1),
        effective_severity=effective_severity,
        package_name=finding.get("package_name", ""),
        package_version=finding.get("package_version", ""),
        fixed_version=finding.get("fixed_version"),
        scanners=[finding.get("scanner", "unknown")],
        igaming_context="; ".join(context_reasons) if context_reasons else "standard",
        sla_hours=SLA_HOURS.get(effective_severity, 720),
        description=finding.get("description", ""),
        exploit_available=finding.get("exploit_available", False),
    )


def _score_to_severity(score: float) -> str:
    if score >= 9.0:
        return "CRITICAL"
    elif score >= 7.0:
        return "HIGH"
    elif score >= 4.0:
        return "MEDIUM"
    elif score >= 0.1:
        return "LOW"
    return "NEGLIGIBLE"


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------
def deduplicate_findings(findings: list[VulnFinding]) -> list[VulnFinding]:
    """Merge findings for the same CVE+package, tracking which scanners found it."""
    seen: dict[str, VulnFinding] = {}

    for finding in findings:
        key = f"{finding.cve_id}:{finding.package_name}:{finding.package_version}"
        if key in seen:
            existing = seen[key]
            # Merge scanner list
            for scanner in finding.scanners:
                if scanner not in existing.scanners:
                    existing.scanners.append(scanner)
            # Use highest effective score
            if finding.effective_score > existing.effective_score:
                existing.effective_score = finding.effective_score
                existing.effective_severity = finding.effective_severity
                existing.sla_hours = finding.sla_hours
            # Preserve exploit flag
            if finding.exploit_available:
                existing.exploit_available = True
        else:
            seen[key] = finding

    deduped = sorted(seen.values(), key=lambda f: f.effective_score, reverse=True)
    logger.info(
        "Deduplicated %d raw findings into %d unique findings",
        len(findings), len(deduped),
    )
    return deduped


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------
def generate_report(
    findings: list[VulnFinding],
    image: str,
    scanners_used: list[str],
    output_path: Optional[str] = None,
) -> dict:
    """Generate aggregated vulnerability report."""
    severity_counts = {s.value: 0 for s in Severity}
    for f in findings:
        sev = f.effective_severity
        if sev in severity_counts:
            severity_counts[sev] += 1

    report = {
        "metadata": {
            "scan_target": image,
            "scan_timestamp": datetime.now(timezone.utc).isoformat(),
            "scanners_used": scanners_used,
            "total_findings": len(findings),
            "severity_distribution": severity_counts,
            "igaming_context_applied": True,
        },
        "sla_summary": {
            sev: {
                "count": severity_counts.get(sev, 0),
                "sla_hours": hours,
                "sla_description": f"Must remediate within {hours} hours",
            }
            for sev, hours in SLA_HOURS.items()
        },
        "findings": [asdict(f) for f in findings],
    }

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info("Report written to %s", output_path)

    return report


def print_summary(report: dict) -> None:
    """Print human-readable summary to stdout."""
    meta = report["metadata"]
    print("\n" + "=" * 70)
    print(f"  Dependency Scan Report — {meta['scan_target']}")
    print(f"  Scanners: {', '.join(meta['scanners_used'])}")
    print(f"  Timestamp: {meta['scan_timestamp']}")
    print("=" * 70)
    print(f"\n  Total findings: {meta['total_findings']}")
    print("  Severity distribution (with iGaming context):")
    for sev, count in meta["severity_distribution"].items():
        if count > 0:
            sla = SLA_HOURS.get(sev, "N/A")
            print(f"    {sev:12s}: {count:4d}  (SLA: {sla}h)")
    print()

    # Top 10 critical findings
    critical = [f for f in report["findings"] if f["effective_severity"] == "CRITICAL"]
    if critical:
        print("  Top Critical Findings:")
        print("  " + "-" * 66)
        for f in critical[:10]:
            fix = f["fixed_version"] or "no fix"
            ctx = f["igaming_context"]
            print(f"  {f['cve_id']:20s} | {f['package_name']:25s} | {f['effective_score']:4.1f} | {fix}")
            if ctx != "standard":
                print(f"  {'':20s}   Context: {ctx}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
SCANNER_CLASSES = {
    "trivy": TrivyScanner,
    "grype": GrypeScanner,
    "snyk": SnykScanner,
}


def main():
    parser = argparse.ArgumentParser(
        description="Multi-scanner dependency vulnerability aggregator for iGaming platforms"
    )
    parser.add_argument("--image", help="Container image to scan")
    parser.add_argument("--sbom", help="SBOM file to scan (instead of image)")
    parser.add_argument(
        "--scanners",
        default="trivy,grype,snyk",
        help="Comma-separated list of scanners to use (default: trivy,grype,snyk)",
    )
    parser.add_argument("--output", "-o", help="Output JSON report path")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress summary output")
    args = parser.parse_args()

    if not args.image and not args.sbom:
        parser.error("Either --image or --sbom is required")

    target = args.sbom or args.image
    is_sbom = bool(args.sbom)
    scanner_names = [s.strip() for s in args.scanners.split(",")]

    # Run scanners
    all_raw_findings = []
    scanners_used = []

    for name in scanner_names:
        scanner_cls = SCANNER_CLASSES.get(name)
        if not scanner_cls:
            logger.warning("Unknown scanner: %s (available: %s)", name, ", ".join(SCANNER_CLASSES))
            continue

        try:
            raw = scanner_cls.scan(target, is_sbom=is_sbom)
            all_raw_findings.extend(raw)
            if raw:
                scanners_used.append(name)
        except FileNotFoundError:
            logger.warning("Scanner '%s' not found in PATH — skipping", name)
        except Exception as e:
            logger.error("Scanner '%s' failed: %s", name, e)

    if not all_raw_findings:
        logger.warning("No findings from any scanner")

    # Apply iGaming contextual scoring
    image_name = args.image or args.sbom or ""
    scored = [apply_igaming_context(f, image_name) for f in all_raw_findings]

    # Deduplicate
    deduped = deduplicate_findings(scored)

    # Generate report
    output_path = args.output or f"/opt/acme-casino/scans/scan-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
    report = generate_report(deduped, image_name, scanners_used, output_path)

    if not args.quiet:
        print_summary(report)

    # Exit with non-zero if critical findings exist
    crit_count = report["metadata"]["severity_distribution"].get("CRITICAL", 0)
    if crit_count > 0:
        logger.warning("Exiting with code 1: %d critical findings", crit_count)
        sys.exit(1)


if __name__ == "__main__":
    main()
