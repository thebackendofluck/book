#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 30, FinOps Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
License Scanner Framework for iGaming CI/CD Pipelines
======================================================

This module provides comprehensive software license scanning using Aqua Security Trivy
and custom policy enforcement for iGaming operations compliance.

Features:
- Multi-format SBOM generation (SPDX, CycloneDX)
- License policy enforcement with customizable rules
- GitOps integration for automated compliance
- Integration with Aqua Security Trivy for vulnerability + license scanning
- Compliance reporting with audit trails

Usage:
    python scanner.py --repo /path/to/repo --output sbom.json --format spdx-json
    python scanner.py --image nginx:latest --policy strict --fail-on-violation

Author: iGaming Technical Book - Chapter 20
License: Apache 2.0
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from trivy_integration import TrivyScanner, TrivyScanResult
from license_policies import LicensePolicy, PolicyEngine, RiskLevel
from sbom_generator import SBOMGenerator, SBOMFormat
from compliance_reporter import ComplianceReporter, ReportFormat

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ScanTarget(Enum):
    """Supported scan target types"""
    REPOSITORY = "repository"
    CONTAINER_IMAGE = "image"
    FILESYSTEM = "filesystem"
    KUBERNETES = "kubernetes"


@dataclass
class ScanConfiguration:
    """Configuration for license scanning"""
    target: str
    target_type: ScanTarget = ScanTarget.REPOSITORY
    output_format: SBOMFormat = SBOMFormat.SPDX_JSON
    output_path: Optional[str] = None
    policy_file: Optional[str] = None
    allowed_licenses: List[str] = field(default_factory=list)
    denied_licenses: List[str] = field(default_factory=list)
    fail_on_violation: bool = True
    include_dev_dependencies: bool = False
    scan_vulnerabilities: bool = True
    severity_threshold: str = "HIGH"
    generate_report: bool = True
    report_format: ReportFormat = ReportFormat.HTML


@dataclass
class LicenseInfo:
    """Information about a detected license"""
    name: str
    spdx_id: str
    package_name: str
    package_version: str
    package_type: str  # npm, pip, maven, etc.
    file_path: Optional[str] = None
    confidence: float = 1.0
    risk_level: RiskLevel = RiskLevel.LOW
    copyleft: bool = False
    commercial_use: bool = True
    patent_grant: bool = False


@dataclass
class ScanResult:
    """Results from a license scan"""
    scan_id: str
    timestamp: datetime
    target: str
    target_type: ScanTarget
    licenses: List[LicenseInfo]
    violations: List[Dict]
    warnings: List[Dict]
    sbom: Dict
    vulnerabilities: Optional[List[Dict]] = None
    compliance_score: float = 100.0
    scan_duration_seconds: float = 0.0

    def has_violations(self) -> bool:
        """Check if scan has any policy violations"""
        return len(self.violations) > 0

    def get_unique_licenses(self) -> Set[str]:
        """Get set of unique license identifiers"""
        return {lic.spdx_id for lic in self.licenses}

    def get_risk_summary(self) -> Dict[str, int]:
        """Get summary of licenses by risk level"""
        summary = {level.value: 0 for level in RiskLevel}
        for lic in self.licenses:
            summary[lic.risk_level.value] += 1
        return summary


class LicenseScanner:
    """
    Main license scanner class integrating Trivy and policy enforcement.

    This scanner is designed for iGaming operations where license compliance
    is critical for regulatory requirements and vendor agreements.
    """

    # Default allowed licenses for iGaming (permissive licenses)
    DEFAULT_ALLOWED_LICENSES = [
        "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause",
        "ISC", "CC0-1.0", "Unlicense", "0BSD", "BlueOak-1.0.0",
        "Python-2.0", "PSF-2.0", "Zlib", "WTFPL"
    ]

    # Licenses requiring careful review
    REVIEW_REQUIRED_LICENSES = [
        "LGPL-2.0-only", "LGPL-2.1-only", "LGPL-3.0-only",
        "MPL-2.0", "EPL-1.0", "EPL-2.0", "CDDL-1.0"
    ]

    # Licenses typically denied for commercial iGaming use
    DEFAULT_DENIED_LICENSES = [
        "GPL-2.0-only", "GPL-3.0-only", "AGPL-3.0-only",
        "SSPL-1.0", "BSL-1.1", "Commons-Clause",
        "Elastic-2.0", "BUSL-1.1"
    ]

    def __init__(self, config: ScanConfiguration):
        """
        Initialize the license scanner.

        Args:
            config: Scan configuration options
        """
        self.config = config
        self.trivy = TrivyScanner()
        self.policy_engine = PolicyEngine(
            allowed=config.allowed_licenses or self.DEFAULT_ALLOWED_LICENSES,
            denied=config.denied_licenses or self.DEFAULT_DENIED_LICENSES,
            review_required=self.REVIEW_REQUIRED_LICENSES
        )
        self.sbom_generator = SBOMGenerator()
        self.reporter = ComplianceReporter()

        # Load custom policy if provided
        if config.policy_file:
            self._load_custom_policy(config.policy_file)

    def _load_custom_policy(self, policy_file: str) -> None:
        """Load custom license policy from file"""
        try:
            with open(policy_file, 'r') as f:
                policy_data = json.load(f)
            self.policy_engine.load_policy(policy_data)
            logger.info(f"Loaded custom policy from {policy_file}")
        except Exception as e:
            logger.error(f"Failed to load policy file: {e}")
            raise

    def scan(self) -> ScanResult:
        """
        Execute license scan on the configured target.

        Returns:
            ScanResult containing all scan findings
        """
        start_time = datetime.now()
        scan_id = f"scan_{start_time.strftime('%Y%m%d_%H%M%S')}"

        logger.info(f"Starting license scan: {scan_id}")
        logger.info(f"Target: {self.config.target} ({self.config.target_type.value})")

        try:
            # Run Trivy scan
            trivy_result = self._run_trivy_scan()

            # Extract license information
            licenses = self._extract_licenses(trivy_result)

            # Apply policy rules
            violations, warnings = self._evaluate_policies(licenses)

            # Generate SBOM
            sbom = self.sbom_generator.generate(
                licenses=licenses,
                format=self.config.output_format,
                metadata={
                    "scan_id": scan_id,
                    "target": self.config.target,
                    "timestamp": start_time.isoformat()
                }
            )

            # Calculate compliance score
            compliance_score = self._calculate_compliance_score(
                total_packages=len(licenses),
                violations=len(violations),
                warnings=len(warnings)
            )

            # Extract vulnerabilities if enabled
            vulnerabilities = None
            if self.config.scan_vulnerabilities:
                vulnerabilities = self._extract_vulnerabilities(trivy_result)

            end_time = datetime.now()
            duration = (end_time - start_time).total_seconds()

            result = ScanResult(
                scan_id=scan_id,
                timestamp=start_time,
                target=self.config.target,
                target_type=self.config.target_type,
                licenses=licenses,
                violations=violations,
                warnings=warnings,
                sbom=sbom,
                vulnerabilities=vulnerabilities,
                compliance_score=compliance_score,
                scan_duration_seconds=duration
            )

            # Generate report if enabled
            if self.config.generate_report:
                self._generate_report(result)

            # Save SBOM if output path specified
            if self.config.output_path:
                self._save_sbom(result.sbom, self.config.output_path)

            logger.info(f"Scan completed in {duration:.2f}s")
            logger.info(f"Found {len(licenses)} packages, "
                       f"{len(violations)} violations, "
                       f"{len(warnings)} warnings")
            logger.info(f"Compliance score: {compliance_score:.1f}%")

            return result

        except Exception as e:
            logger.error(f"Scan failed: {e}")
            raise

    def _run_trivy_scan(self) -> TrivyScanResult:
        """Run Trivy scan based on target type"""
        scan_args: Dict[str, Any] = {
            "scanners": ["license"],
            "format": "json",
            "license_full": True
        }

        if self.config.scan_vulnerabilities:
            scan_args["scanners"].append("vuln")
            scan_args["severity"] = self.config.severity_threshold

        if self.config.target_type == ScanTarget.REPOSITORY:
            return self.trivy.scan_repository(
                self.config.target,
                **scan_args
            )
        elif self.config.target_type == ScanTarget.CONTAINER_IMAGE:
            return self.trivy.scan_image(
                self.config.target,
                **scan_args
            )
        elif self.config.target_type == ScanTarget.FILESYSTEM:
            return self.trivy.scan_filesystem(
                self.config.target,
                **scan_args
            )
        else:
            raise ValueError(f"Unsupported target type: {self.config.target_type}")

    def _extract_licenses(self, trivy_result: TrivyScanResult) -> List[LicenseInfo]:
        """Extract license information from Trivy results"""
        licenses = []

        for result in trivy_result.results:
            if "Licenses" not in result:
                continue

            for license_entry in result.get("Licenses", []):
                # Determine risk level
                risk_level = self.policy_engine.assess_risk(
                    license_entry.get("Name", "Unknown")
                )

                license_info = LicenseInfo(
                    name=license_entry.get("Name", "Unknown"),
                    spdx_id=license_entry.get("Name", "Unknown"),
                    package_name=license_entry.get("PkgName", "Unknown"),
                    package_version=license_entry.get("PkgVersion", "Unknown"),
                    package_type=result.get("Type", "Unknown"),
                    file_path=license_entry.get("FilePath"),
                    confidence=license_entry.get("Confidence", 1.0),
                    risk_level=risk_level,
                    copyleft=self._is_copyleft(license_entry.get("Name", "")),
                    commercial_use=self._allows_commercial(license_entry.get("Name", "")),
                    patent_grant=self._has_patent_grant(license_entry.get("Name", ""))
                )
                licenses.append(license_info)

        return licenses

    def _evaluate_policies(self, licenses: List[LicenseInfo]) -> Tuple[List[Dict], List[Dict]]:
        """Evaluate licenses against policy rules"""
        violations = []
        warnings = []

        for lic in licenses:
            result = self.policy_engine.evaluate(lic)

            if result.is_violation:
                violations.append({
                    "license": lic.spdx_id,
                    "package": f"{lic.package_name}@{lic.package_version}",
                    "reason": result.reason,
                    "severity": "HIGH",
                    "recommendation": result.recommendation
                })
            elif result.requires_review:
                warnings.append({
                    "license": lic.spdx_id,
                    "package": f"{lic.package_name}@{lic.package_version}",
                    "reason": result.reason,
                    "severity": "MEDIUM",
                    "recommendation": result.recommendation
                })

        return violations, warnings

    def _calculate_compliance_score(self, total_packages: int,
                                   violations: int, warnings: int) -> float:
        """Calculate overall compliance score (0-100)"""
        if total_packages == 0:
            return 100.0

        # Violations have high impact, warnings have lower impact
        violation_weight = 10.0
        warning_weight = 2.0

        penalty = (violations * violation_weight + warnings * warning_weight) / total_packages
        score = max(0.0, 100.0 - (penalty * 10))

        return round(score, 2)

    def _extract_vulnerabilities(self, trivy_result: TrivyScanResult) -> List[Dict]:
        """Extract vulnerability information from Trivy results"""
        vulnerabilities = []

        for result in trivy_result.results:
            for vuln in result.get("Vulnerabilities", []):
                vulnerabilities.append({
                    "id": vuln.get("VulnerabilityID"),
                    "package": vuln.get("PkgName"),
                    "version": vuln.get("InstalledVersion"),
                    "severity": vuln.get("Severity"),
                    "title": vuln.get("Title"),
                    "fixed_version": vuln.get("FixedVersion")
                })

        return vulnerabilities

    def _is_copyleft(self, license_id: str) -> bool:
        """Check if license is copyleft"""
        copyleft_licenses = ["GPL", "LGPL", "AGPL", "MPL", "EPL", "CDDL", "SSPL"]
        return any(cl in license_id.upper() for cl in copyleft_licenses)

    def _allows_commercial(self, license_id: str) -> bool:
        """Check if license allows commercial use"""
        # Most open source licenses allow commercial use
        # Only specific restrictive licenses don't
        non_commercial = ["CC-BY-NC", "NonCommercial", "SSPL", "Commons-Clause"]
        return not any(nc in license_id for nc in non_commercial)

    def _has_patent_grant(self, license_id: str) -> bool:
        """Check if license includes patent grant"""
        patent_licenses = ["Apache-2.0", "GPL-3.0", "LGPL-3.0", "MPL-2.0", "EPL"]
        return any(pl in license_id for pl in patent_licenses)

    def _generate_report(self, result: ScanResult) -> None:
        """Generate compliance report"""
        report_path = self.reporter.generate(
            result=result,
            format=self.config.report_format
        )
        logger.info(f"Report generated: {report_path}")

    def _save_sbom(self, sbom: Dict, output_path: str) -> None:
        """Save SBOM to file"""
        with open(output_path, 'w') as f:
            json.dump(sbom, f, indent=2)
        logger.info(f"SBOM saved to: {output_path}")


def main():
    """Main entry point for CLI usage"""
    parser = argparse.ArgumentParser(
        description="License Scanner for iGaming CI/CD Pipelines",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan a repository
  python scanner.py --repo /path/to/repo --output sbom.json

  # Scan a container image
  python scanner.py --image nginx:latest --fail-on-violation

  # Scan with custom policy
  python scanner.py --repo . --policy config/policy_rules.yaml

  # Generate HTML report
  python scanner.py --repo . --report html --output-dir ./reports
        """
    )

    # Target options
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--repo", help="Repository path or URL to scan")
    target_group.add_argument("--image", help="Container image to scan")
    target_group.add_argument("--filesystem", help="Filesystem path to scan")

    # Output options
    parser.add_argument("--output", "-o", help="Output file path for SBOM")
    parser.add_argument("--format", "-f",
                       choices=["spdx-json", "cyclonedx-json", "spdx-tv", "cyclonedx-xml"],
                       default="spdx-json",
                       help="SBOM output format (default: spdx-json)")

    # Policy options
    parser.add_argument("--policy", help="Custom policy file path")
    parser.add_argument("--allowed", nargs="+", help="Additional allowed licenses")
    parser.add_argument("--denied", nargs="+", help="Additional denied licenses")
    parser.add_argument("--fail-on-violation", action="store_true",
                       help="Exit with error if violations found")

    # Scan options
    parser.add_argument("--include-dev", action="store_true",
                       help="Include development dependencies")
    parser.add_argument("--scan-vulns", action="store_true", default=True,
                       help="Also scan for vulnerabilities")
    parser.add_argument("--severity",
                       choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
                       default="HIGH",
                       help="Minimum vulnerability severity to report")

    # Report options
    parser.add_argument("--report", choices=["html", "json", "markdown", "sarif"],
                       default="html",
                       help="Report format (default: html)")
    parser.add_argument("--no-report", action="store_true",
                       help="Skip report generation")

    # Verbosity
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Enable verbose output")
    parser.add_argument("--quiet", "-q", action="store_true",
                       help="Suppress all output except errors")

    args = parser.parse_args()

    # Configure logging level
    if args.quiet:
        logging.getLogger().setLevel(logging.ERROR)
    elif args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Determine target type
    if args.repo:
        target = args.repo
        target_type = ScanTarget.REPOSITORY
    elif args.image:
        target = args.image
        target_type = ScanTarget.CONTAINER_IMAGE
    else:
        target = args.filesystem
        target_type = ScanTarget.FILESYSTEM

    # Map format string to enum
    format_map = {
        "spdx-json": SBOMFormat.SPDX_JSON,
        "cyclonedx-json": SBOMFormat.CYCLONEDX_JSON,
        "spdx-tv": SBOMFormat.SPDX_TAG_VALUE,
        "cyclonedx-xml": SBOMFormat.CYCLONEDX_XML
    }

    report_format_map = {
        "html": ReportFormat.HTML,
        "json": ReportFormat.JSON,
        "markdown": ReportFormat.MARKDOWN,
        "sarif": ReportFormat.SARIF
    }

    # Create configuration
    config = ScanConfiguration(
        target=target,
        target_type=target_type,
        output_format=format_map.get(args.format, SBOMFormat.SPDX_JSON),
        output_path=args.output,
        policy_file=args.policy,
        allowed_licenses=args.allowed or [],
        denied_licenses=args.denied or [],
        fail_on_violation=args.fail_on_violation,
        include_dev_dependencies=args.include_dev,
        scan_vulnerabilities=args.scan_vulns,
        severity_threshold=args.severity,
        generate_report=not args.no_report,
        report_format=report_format_map.get(args.report, ReportFormat.HTML)
    )

    # Run scan
    scanner = LicenseScanner(config)
    result = scanner.scan()

    # Print summary
    if not args.quiet:
        print("\n" + "="*60)
        print("LICENSE SCAN SUMMARY")
        print("="*60)
        print(f"Target: {result.target}")
        print(f"Scan ID: {result.scan_id}")
        print(f"Duration: {result.scan_duration_seconds:.2f}s")
        print(f"Total packages: {len(result.licenses)}")
        print(f"Unique licenses: {len(result.get_unique_licenses())}")
        print(f"Violations: {len(result.violations)}")
        print(f"Warnings: {len(result.warnings)}")
        print(f"Compliance Score: {result.compliance_score:.1f}%")

        if result.violations:
            print("\nVIOLATIONS:")
            for v in result.violations:
                print(f"  - [{v['severity']}] {v['package']}: {v['license']}")
                print(f"    Reason: {v['reason']}")

        if result.warnings:
            print("\nWARNINGS:")
            for w in result.warnings:
                print(f"  - [{w['severity']}] {w['package']}: {w['license']}")

        print("="*60)

    # Exit with error if violations found and fail-on-violation is set
    if args.fail_on_violation and result.has_violations():
        logger.error("License violations detected - failing build")
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
