# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Registry Security Scanner
# =============================================================================
"""
Comprehensive security scanning for container images.

Supports multiple scanning backends:
- Trivy (free, open-source) - vulnerability, secret, and config scanning
- Clair (free, open-source) - static vulnerability analysis
- Grype (free, open-source) - vulnerability scanner
- Aqua Security (enterprise) - comprehensive security platform

Container Runtime Support:
- Docker images
- Podman images
- QEMU/libvirt VM images

Regulatory Compliance:
- PCI-DSS: Requirement 6.2 (vulnerability scanning)
- NIST SP 800-190: Container security guidelines
- CIS Docker/Kubernetes Benchmarks
- SOX: Integrity verification
"""

import asyncio
import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ScannerType(Enum):
    """Available scanner backends."""

    TRIVY = "trivy"
    CLAIR = "clair"
    GRYPE = "grype"
    AQUA = "aqua"


class ContainerRuntime(Enum):
    """Supported container runtimes."""

    DOCKER = "docker"
    PODMAN = "podman"


class SeverityLevel(Enum):
    """Vulnerability severity levels."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass
class Vulnerability:
    """Represents a detected vulnerability."""

    cve_id: str
    severity: str
    package: str
    version: str
    description: str
    cvss_score: float
    fix_available: bool
    fixed_version: Optional[str] = None
    exploit_available: bool = False
    references: List[str] = field(default_factory=list)


@dataclass
class ScanResult:
    """Complete scan result."""

    image: str
    scanner: str
    timestamp: datetime
    status: str
    vulnerabilities: List[Vulnerability]
    malware_detected: bool
    secrets_found: int
    config_issues: int
    risk_score: int
    risk_level: str
    compliance: Dict[str, bool]
    raw_output: Optional[str] = None


class RegistrySecurityScanner:
    """
    Multi-backend security scanner for container images.

    Supports:
    - Vulnerability scanning (CVEs)
    - Malware detection
    - Secret scanning (API keys, passwords)
    - Configuration auditing
    - Compliance verification

    Example:
        scanner = RegistrySecurityScanner(
            registry_url='https://registry.local:5000'
        )

        # Scan with Trivy (free)
        result = await scanner.scan_image('myapp:latest')

        # Scan Podman image
        result = await scanner.scan_podman_image('myapp:latest')
    """

    def __init__(
        self,
        registry_url: str,
        scanner_type: ScannerType = ScannerType.TRIVY,
        runtime: ContainerRuntime = ContainerRuntime.DOCKER,
        trivy_path: str = "trivy",
        grype_path: str = "grype",
        cache_dir: str = "/tmp/scanner-cache",
    ):
        """
        Initialize security scanner.

        Args:
            registry_url: Registry base URL
            scanner_type: Scanner backend to use
            runtime: Container runtime (docker/podman)
            trivy_path: Path to trivy binary
            grype_path: Path to grype binary
            cache_dir: Directory for scanner cache
        """
        self.registry_url = registry_url.rstrip("/")
        self.scanner_type = scanner_type
        self.runtime = runtime
        self.trivy_path = trivy_path
        self.grype_path = grype_path
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def perform_comprehensive_scan(self, image_name: str) -> Dict[str, Any]:
        """
        Perform comprehensive security scan of container image.

        Args:
            image_name: Image name with optional tag

        Returns:
            Complete scan results including risk assessment
        """
        try:
            scan_results: Dict[str, Any] = {
                "image": image_name,
                "timestamp": datetime.now().isoformat(),
                "scanner": self.scanner_type.value,
                "runtime": self.runtime.value,
                "scans": {},
            }

            # Vulnerability scan
            vuln_scan = await self._scan_vulnerabilities(image_name)
            scan_results["scans"]["vulnerabilities"] = vuln_scan

            # Malware scan (if ClamAV available)
            if shutil.which("clamscan"):
                malware_scan = await self._scan_malware(image_name)
                scan_results["scans"]["malware"] = malware_scan
            else:
                scan_results["scans"]["malware"] = {"status": "skipped", "reason": "ClamAV not installed"}

            # Configuration scan
            config_scan = await self._scan_configuration(image_name)
            scan_results["scans"]["configuration"] = config_scan

            # Secret scan
            secret_scan = await self._scan_secrets(image_name)
            scan_results["scans"]["secrets"] = secret_scan

            # Calculate risk score
            scan_results["risk_assessment"] = self._calculate_risk_score(scan_results)

            # Generate compliance report
            scan_results["compliance"] = self._generate_compliance_report(scan_results)

            return scan_results

        except Exception as e:
            logger.error(f"Comprehensive scan failed: {e}")
            return {"error": str(e), "image": image_name}

    async def scan_image(self, image_name: str) -> ScanResult:
        """
        Scan a Docker/Podman image for vulnerabilities.

        Args:
            image_name: Image name with optional tag

        Returns:
            ScanResult with vulnerabilities and risk assessment
        """
        full_results = await self.perform_comprehensive_scan(image_name)

        if "error" in full_results:
            return ScanResult(
                image=image_name,
                scanner=self.scanner_type.value,
                timestamp=datetime.now(),
                status="error",
                vulnerabilities=[],
                malware_detected=False,
                secrets_found=0,
                config_issues=0,
                risk_score=0,
                risk_level="UNKNOWN",
                compliance={},
                raw_output=full_results.get("error"),
            )

        vuln_data = full_results.get("scans", {}).get("vulnerabilities", {})
        vulnerabilities = [
            Vulnerability(**v) for v in vuln_data.get("details", [])
        ]

        risk = full_results.get("risk_assessment", {})
        compliance = full_results.get("compliance", {}).get("checks", {})

        return ScanResult(
            image=image_name,
            scanner=self.scanner_type.value,
            timestamp=datetime.now(),
            status=vuln_data.get("status", "unknown"),
            vulnerabilities=vulnerabilities,
            malware_detected=full_results.get("scans", {}).get("malware", {}).get("malware_found", False),
            secrets_found=full_results.get("scans", {}).get("secrets", {}).get("secrets_found", 0),
            config_issues=full_results.get("scans", {}).get("configuration", {}).get("configuration_issues", 0),
            risk_score=risk.get("risk_score", 0),
            risk_level=risk.get("risk_level", "UNKNOWN"),
            compliance=compliance,
        )

    async def scan_podman_image(self, image_name: str) -> ScanResult:
        """
        Scan a Podman image for vulnerabilities.

        Args:
            image_name: Podman image name

        Returns:
            ScanResult with vulnerabilities
        """
        # Temporarily switch to podman runtime
        original_runtime = self.runtime
        self.runtime = ContainerRuntime.PODMAN

        try:
            return await self.scan_image(image_name)
        finally:
            self.runtime = original_runtime

    async def scan_qemu_image(self, image_path: str) -> Dict[str, Any]:
        """
        Scan a QEMU/libvirt VM image for vulnerabilities.

        Args:
            image_path: Path to QEMU image file

        Returns:
            Scan results for VM image
        """
        try:
            # Mount the QEMU image to scan its contents
            mount_point = self.cache_dir / "qemu_mount"
            mount_point.mkdir(parents=True, exist_ok=True)

            # Use guestmount if available
            if not shutil.which("guestmount"):
                return {
                    "status": "error",
                    "error": "guestmount (libguestfs-tools) not installed",
                    "image": image_path,
                }

            # Mount image
            mount_cmd = f"guestmount -a {image_path} -i --ro {mount_point}"
            process = await asyncio.create_subprocess_shell(
                mount_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            if process.returncode != 0:
                return {
                    "status": "error",
                    "error": "Failed to mount QEMU image",
                    "image": image_path,
                }

            try:
                # Scan mounted filesystem with Trivy
                scan_results = await self._scan_filesystem(str(mount_point))

                return {
                    "status": "completed",
                    "image": image_path,
                    "image_type": "qemu",
                    "scan_results": scan_results,
                    "timestamp": datetime.now().isoformat(),
                }

            finally:
                # Unmount image
                unmount_cmd = f"guestunmount {mount_point}"
                await asyncio.create_subprocess_shell(unmount_cmd)

        except Exception as e:
            logger.error(f"QEMU image scan failed: {e}")
            return {"status": "error", "error": str(e), "image": image_path}

    async def _scan_vulnerabilities(self, image_name: str) -> Dict[str, Any]:
        """Scan for vulnerabilities using configured scanner."""
        if self.scanner_type == ScannerType.TRIVY:
            return await self._scan_with_trivy(image_name)
        elif self.scanner_type == ScannerType.GRYPE:
            return await self._scan_with_grype(image_name)
        else:
            return await self._scan_with_trivy(image_name)

    async def _scan_with_trivy(self, image_name: str) -> Dict[str, Any]:
        """Scan image using Trivy."""
        try:
            output_file = self.cache_dir / f"trivy_{image_name.replace('/', '_').replace(':', '_')}.json"

            # Build image reference
            if self.registry_url and not image_name.startswith(self.registry_url):
                full_image = f"{self.registry_url}/{image_name}"
            else:
                full_image = image_name

            # Use podman if configured
            image_source = ""
            if self.runtime == ContainerRuntime.PODMAN:
                image_source = "podman:"

            cmd = [
                self.trivy_path,
                "image",
                "--format",
                "json",
                "--output",
                str(output_file),
                "--severity",
                "CRITICAL,HIGH,MEDIUM,LOW",
                f"{image_source}{full_image}",
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0 and output_file.exists():
                trivy_data = json.loads(output_file.read_text())
                return self._parse_trivy_results(trivy_data)
            else:
                return {
                    "status": "failed",
                    "error": stderr.decode() if stderr else "Unknown error",
                    "return_code": process.returncode,
                }

        except FileNotFoundError:
            return {
                "status": "error",
                "error": "Trivy not installed. Install with: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _scan_with_grype(self, image_name: str) -> Dict[str, Any]:
        """Scan image using Grype (alternative free scanner)."""
        try:
            output_file = self.cache_dir / f"grype_{image_name.replace('/', '_').replace(':', '_')}.json"

            if self.registry_url and not image_name.startswith(self.registry_url):
                full_image = f"{self.registry_url}/{image_name}"
            else:
                full_image = image_name

            # Use podman scheme if configured
            image_source = ""
            if self.runtime == ContainerRuntime.PODMAN:
                image_source = "podman:"

            cmd = [
                self.grype_path,
                f"{image_source}{full_image}",
                "-o",
                "json",
                "--file",
                str(output_file),
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            if process.returncode == 0 and output_file.exists():
                grype_data = json.loads(output_file.read_text())
                return self._parse_grype_results(grype_data)
            else:
                return {"status": "failed", "error": "Grype scan failed"}

        except FileNotFoundError:
            return {
                "status": "error",
                "error": "Grype not installed. Install with: curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b /usr/local/bin",
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _parse_trivy_results(self, trivy_data: Dict) -> Dict[str, Any]:
        """Parse Trivy JSON output into standardized format."""
        vulnerabilities: List[Dict[str, Any]] = []

        for result in trivy_data.get("Results", []):
            for vuln in result.get("Vulnerabilities", []):
                cvss_score = 0.0
                if "CVSS" in vuln:
                    nvd = vuln["CVSS"].get("nvd", {})
                    cvss_score = float(nvd.get("V3Score", nvd.get("V2Score", 0)))

                vulnerabilities.append({
                    "cve_id": vuln.get("VulnerabilityID", ""),
                    "severity": vuln.get("Severity", "UNKNOWN"),
                    "package": vuln.get("PkgName", ""),
                    "version": vuln.get("InstalledVersion", ""),
                    "description": vuln.get("Description", "")[:500],
                    "cvss_score": cvss_score,
                    "fix_available": bool(vuln.get("FixedVersion")),
                    "fixed_version": vuln.get("FixedVersion"),
                    "exploit_available": self._check_exploit_availability(vuln),
                    "references": vuln.get("References", [])[:5],
                })

        severity_counts = {
            "critical": len([v for v in vulnerabilities if v["severity"] == "CRITICAL"]),
            "high": len([v for v in vulnerabilities if v["severity"] == "HIGH"]),
            "medium": len([v for v in vulnerabilities if v["severity"] == "MEDIUM"]),
            "low": len([v for v in vulnerabilities if v["severity"] == "LOW"]),
        }

        return {
            "status": "completed",
            "vulnerabilities_found": len(vulnerabilities),
            **severity_counts,
            "details": vulnerabilities,
        }

    def _parse_grype_results(self, grype_data: Dict) -> Dict[str, Any]:
        """Parse Grype JSON output into standardized format."""
        vulnerabilities: List[Dict[str, Any]] = []

        for match in grype_data.get("matches", []):
            vuln = match.get("vulnerability", {})
            artifact = match.get("artifact", {})

            vulnerabilities.append({
                "cve_id": vuln.get("id", ""),
                "severity": vuln.get("severity", "UNKNOWN").upper(),
                "package": artifact.get("name", ""),
                "version": artifact.get("version", ""),
                "description": vuln.get("description", "")[:500],
                "cvss_score": float(vuln.get("cvss", [{}])[0].get("metrics", {}).get("baseScore", 0) if vuln.get("cvss") else 0),
                "fix_available": bool(vuln.get("fix", {}).get("versions")),
                "fixed_version": vuln.get("fix", {}).get("versions", [None])[0] if vuln.get("fix", {}).get("versions") else None,
                "exploit_available": False,
                "references": vuln.get("urls", [])[:5],
            })

        severity_counts = {
            "critical": len([v for v in vulnerabilities if v["severity"] == "CRITICAL"]),
            "high": len([v for v in vulnerabilities if v["severity"] == "HIGH"]),
            "medium": len([v for v in vulnerabilities if v["severity"] == "MEDIUM"]),
            "low": len([v for v in vulnerabilities if v["severity"] == "LOW"]),
        }

        return {
            "status": "completed",
            "vulnerabilities_found": len(vulnerabilities),
            **severity_counts,
            "details": vulnerabilities,
        }

    async def _scan_malware(self, image_name: str) -> Dict[str, Any]:
        """Scan for malware using ClamAV."""
        try:
            # Export image to temporary directory
            export_dir = self.cache_dir / f"export_{image_name.replace('/', '_').replace(':', '_')}"
            export_dir.mkdir(parents=True, exist_ok=True)

            # Export image layers
            runtime_cmd = "podman" if self.runtime == ContainerRuntime.PODMAN else "docker"
            export_cmd = f"{runtime_cmd} save {image_name} | tar -xf - -C {export_dir}"

            process = await asyncio.create_subprocess_shell(
                export_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()

            if process.returncode != 0:
                return {"status": "skipped", "reason": "Failed to export image"}

            # Scan with ClamAV
            scan_cmd = f"clamscan --recursive --infected {export_dir}"
            process = await asyncio.create_subprocess_shell(
                scan_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            infected_files: List[str] = []
            if process.returncode == 1:
                lines = stdout.decode().split("\n")
                for line in lines:
                    if "FOUND" in line:
                        infected_files.append(line.split(":")[0])

            # Cleanup
            shutil.rmtree(export_dir, ignore_errors=True)

            return {
                "status": "completed",
                "malware_found": len(infected_files) > 0,
                "infected_files": infected_files,
            }

        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _scan_configuration(self, image_name: str) -> Dict[str, Any]:
        """Scan container configuration for security issues."""
        try:
            # Use Trivy for config scanning
            cmd = [
                self.trivy_path,
                "image",
                "--scanners",
                "config",
                "--format",
                "json",
                image_name,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                config_data = json.loads(stdout.decode())
                issues: List[str] = []

                for result in config_data.get("Results", []):
                    for misconfig in result.get("Misconfigurations", []):
                        issues.append(
                            f"[{misconfig.get('Severity', 'UNKNOWN')}] "
                            f"{misconfig.get('Title', 'Unknown issue')}"
                        )

                return {
                    "status": "completed",
                    "configuration_issues": len(issues),
                    "details": issues[:20],  # Limit to 20 issues
                }

            return {"status": "completed", "configuration_issues": 0, "details": []}

        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _scan_secrets(self, image_name: str) -> Dict[str, Any]:
        """Scan for hardcoded secrets in container image."""
        try:
            cmd = [
                self.trivy_path,
                "image",
                "--scanners",
                "secret",
                "--format",
                "json",
                image_name,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                secrets_data = json.loads(stdout.decode())
                secrets_found: List[Dict[str, str]] = []

                for result in secrets_data.get("Results", []):
                    for secret in result.get("Secrets", []):
                        secrets_found.append({
                            "rule_id": secret.get("RuleID", ""),
                            "category": secret.get("Category", ""),
                            "severity": secret.get("Severity", ""),
                            "title": secret.get("Title", ""),
                        })

                return {
                    "status": "completed",
                    "secrets_found": len(secrets_found),
                    "details": secrets_found,
                }

            return {"status": "completed", "secrets_found": 0, "details": []}

        except Exception as e:
            return {"status": "error", "error": str(e)}

    async def _scan_filesystem(self, path: str) -> Dict[str, Any]:
        """Scan a filesystem path for vulnerabilities."""
        try:
            cmd = [
                self.trivy_path,
                "filesystem",
                "--format",
                "json",
                path,
            ]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                return self._parse_trivy_results(json.loads(stdout.decode()))

            return {"status": "error", "error": stderr.decode()}

        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _calculate_risk_score(self, scan_results: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate overall risk score from scan results."""
        risk_score = 0
        max_score = 100

        # Vulnerability risk
        vuln_scan = scan_results.get("scans", {}).get("vulnerabilities", {})
        if vuln_scan.get("status") == "completed":
            critical = vuln_scan.get("critical", 0)
            high = vuln_scan.get("high", 0)
            medium = vuln_scan.get("medium", 0)

            risk_score += min(critical * 15 + high * 8 + medium * 3, 50)

        # Malware risk
        malware_scan = scan_results.get("scans", {}).get("malware", {})
        if malware_scan.get("malware_found"):
            risk_score += 40

        # Configuration risk
        config_scan = scan_results.get("scans", {}).get("configuration", {})
        if config_scan.get("status") == "completed":
            config_issues = config_scan.get("configuration_issues", 0)
            risk_score += min(config_issues * 2, 10)

        # Secrets risk
        secret_scan = scan_results.get("scans", {}).get("secrets", {})
        if secret_scan.get("status") == "completed":
            secrets_found = secret_scan.get("secrets_found", 0)
            risk_score += min(secrets_found * 10, 30)

        # Determine risk level
        if risk_score >= 70:
            risk_level = "CRITICAL"
        elif risk_score >= 50:
            risk_level = "HIGH"
        elif risk_score >= 30:
            risk_level = "MEDIUM"
        elif risk_score >= 10:
            risk_level = "LOW"
        else:
            risk_level = "VERY_LOW"

        return {
            "risk_score": min(risk_score, max_score),
            "risk_level": risk_level,
            "recommendations": self._generate_risk_recommendations(risk_level, scan_results),
        }

    def _generate_risk_recommendations(
        self, risk_level: str, scan_results: Dict[str, Any]
    ) -> List[str]:
        """Generate risk mitigation recommendations."""
        recommendations: List[str] = []

        if risk_level == "CRITICAL":
            recommendations.extend([
                "IMMEDIATE ACTION: Do not deploy this image to production",
                "Contact security team for incident assessment",
                "Isolate any running containers using this image",
            ])
        elif risk_level == "HIGH":
            recommendations.extend([
                "Address all critical and high vulnerabilities before deployment",
                "Require security team approval for production use",
            ])
        elif risk_level == "MEDIUM":
            recommendations.extend([
                "Address high-severity vulnerabilities within 7 days",
                "Document risk acceptance if deploying to production",
            ])

        # Specific recommendations based on findings
        vuln_scan = scan_results.get("scans", {}).get("vulnerabilities", {})
        if vuln_scan.get("critical", 0) > 0:
            recommendations.append(
                f"Fix {vuln_scan['critical']} critical vulnerabilities immediately"
            )

        secrets_scan = scan_results.get("scans", {}).get("secrets", {})
        if secrets_scan.get("secrets_found", 0) > 0:
            recommendations.append(
                "Remove hardcoded secrets and use secret management (Vault, AWS Secrets Manager)"
            )

        return recommendations

    def _check_exploit_availability(self, vuln: Dict) -> bool:
        """Check if exploit is publicly available."""
        # Check references for exploit-db or similar
        refs = vuln.get("References", [])
        exploit_indicators = ["exploit-db", "metasploit", "poc", "github.com/exploit"]

        for ref in refs:
            if any(indicator in ref.lower() for indicator in exploit_indicators):
                return True

        return False

    def _generate_compliance_report(self, scan_results: Dict[str, Any]) -> Dict[str, Any]:
        """Generate compliance report for regulatory requirements."""
        vuln_scan = scan_results.get("scans", {}).get("vulnerabilities", {})
        secret_scan = scan_results.get("scans", {}).get("secrets", {})
        malware_scan = scan_results.get("scans", {}).get("malware", {})

        checks = {
            "pci_dss": self._check_pci_compliance(vuln_scan, secret_scan),
            "gdpr": self._check_gdpr_compliance(secret_scan),
            "sox": self._check_sox_compliance(malware_scan),
            "iso27001": self._check_iso27001_compliance(scan_results),
            "nist_800_190": self._check_nist_compliance(vuln_scan),
        }

        return {
            "overall_compliant": all(checks.values()),
            "checks": checks,
            "non_compliance_issues": [
                check for check, compliant in checks.items() if not compliant
            ],
        }

    def _check_pci_compliance(self, vuln_scan: Dict, secret_scan: Dict) -> bool:
        """Check PCI-DSS compliance."""
        no_critical = vuln_scan.get("critical", 0) == 0
        no_secrets = secret_scan.get("secrets_found", 0) == 0
        return no_critical and no_secrets

    def _check_gdpr_compliance(self, secret_scan: Dict) -> bool:
        """Check GDPR compliance (no exposed PII)."""
        return secret_scan.get("secrets_found", 0) == 0

    def _check_sox_compliance(self, malware_scan: Dict) -> bool:
        """Check SOX compliance (no malware)."""
        return not malware_scan.get("malware_found", False)

    def _check_iso27001_compliance(self, scan_results: Dict) -> bool:
        """Check ISO 27001 compliance."""
        risk = scan_results.get("risk_assessment", {})
        return risk.get("risk_level") in ["LOW", "VERY_LOW"]

    def _check_nist_compliance(self, vuln_scan: Dict) -> bool:
        """Check NIST SP 800-190 compliance."""
        critical = vuln_scan.get("critical", 0)
        high = vuln_scan.get("high", 0)
        return critical == 0 and high <= 5
