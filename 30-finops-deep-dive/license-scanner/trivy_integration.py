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
Trivy Integration Module
========================

Integration with Aqua Security Trivy for comprehensive security scanning.
Supports vulnerability scanning, license detection, and SBOM generation.

Trivy is the industry-leading open-source scanner that finds:
- Vulnerabilities (CVEs) in OS packages and application dependencies
- Misconfigurations in Infrastructure as Code
- Sensitive information and secrets
- Software licenses for compliance

Documentation: https://trivy.dev/
GitHub: https://github.com/aquasecurity/trivy
"""

import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TrivyError(Exception):
    """Base exception for Trivy-related errors"""
    pass


class TrivyNotFoundError(TrivyError):
    """Trivy binary not found in PATH"""
    pass


class TrivyScanError(TrivyError):
    """Error during Trivy scan execution"""
    pass


class ScannerType(Enum):
    """Available Trivy scanner types"""
    VULN = "vuln"
    MISCONFIG = "misconfig"
    SECRET = "secret"
    LICENSE = "license"


class SBOMFormat(Enum):
    """Supported SBOM output formats"""
    SPDX_JSON = "spdx-json"
    CYCLONEDX_JSON = "cyclonedx-json"
    SPDX_TAG_VALUE = "spdx-tv"
    CYCLONEDX_XML = "cyclonedx-xml"


@dataclass
class TrivyScanResult:
    """Results from a Trivy scan"""
    schema_version: int
    created_at: str
    artifact_name: str
    artifact_type: str
    metadata: Dict[str, Any]
    results: List[Dict[str, Any]]
    raw_output: str = ""

    @classmethod
    def from_json(cls, json_data: Dict[str, Any], raw_output: str = "") -> "TrivyScanResult":
        """Create TrivyScanResult from JSON output"""
        return cls(
            schema_version=json_data.get("SchemaVersion", 2),
            created_at=json_data.get("CreatedAt", ""),
            artifact_name=json_data.get("ArtifactName", ""),
            artifact_type=json_data.get("ArtifactType", ""),
            metadata=json_data.get("Metadata", {}),
            results=json_data.get("Results", []),
            raw_output=raw_output
        )

    def get_licenses(self) -> List[Dict]:
        """Extract all licenses from scan results"""
        licenses = []
        for result in self.results:
            licenses.extend(result.get("Licenses", []))
        return licenses

    def get_vulnerabilities(self) -> List[Dict]:
        """Extract all vulnerabilities from scan results"""
        vulns = []
        for result in self.results:
            vulns.extend(result.get("Vulnerabilities", []))
        return vulns

    def get_secrets(self) -> List[Dict]:
        """Extract all secrets from scan results"""
        secrets = []
        for result in self.results:
            secrets.extend(result.get("Secrets", []))
        return secrets


@dataclass
class TrivyConfig:
    """Configuration for Trivy scanner"""
    cache_dir: Optional[str] = None
    timeout: int = 300  # 5 minutes
    offline: bool = False
    skip_db_update: bool = False
    severity: List[str] = field(default_factory=lambda: ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
    ignore_unfixed: bool = False
    ignore_file: Optional[str] = None
    config_file: Optional[str] = None
    debug: bool = False


class TrivyScanner:
    """
    Wrapper for Aqua Security Trivy scanner.

    Provides a Python interface for running Trivy scans on:
    - Container images
    - Git repositories
    - Local filesystems
    - Kubernetes clusters

    Example usage:
        scanner = TrivyScanner()

        # Scan a container image for licenses
        result = scanner.scan_image("nginx:latest", scanners=["license"])

        # Scan a repository with SBOM output
        result = scanner.scan_repository(
            "/path/to/repo",
            scanners=["vuln", "license"],
            format="spdx-json"
        )

        # Generate SBOM for a project
        sbom = scanner.generate_sbom("/path/to/project", format="cyclonedx-json")
    """

    def __init__(self, config: Optional[TrivyConfig] = None):
        """
        Initialize Trivy scanner.

        Args:
            config: Optional configuration for Trivy
        """
        self.config = config or TrivyConfig()
        self.trivy_path = self._find_trivy()
        self._verify_installation()

    def _find_trivy(self) -> str:
        """Find Trivy binary in PATH"""
        trivy_path = shutil.which("trivy")
        if not trivy_path:
            raise TrivyNotFoundError(
                "Trivy not found in PATH. Install it from: https://trivy.dev/\n"
                "  macOS: brew install trivy\n"
                "  Linux: curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh\n"
                "  Docker: docker pull aquasec/trivy"
            )
        return trivy_path

    def _verify_installation(self) -> None:
        """Verify Trivy is properly installed"""
        try:
            result = subprocess.run(
                [self.trivy_path, "version", "--format", "json"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                version_info = json.loads(result.stdout)
                logger.info(f"Trivy version: {version_info.get('Version', 'unknown')}")
        except Exception as e:
            logger.warning(f"Could not verify Trivy installation: {e}")

    def _build_base_args(self) -> List[str]:
        """Build base command arguments from config"""
        args = [self.trivy_path]

        if self.config.cache_dir:
            args.extend(["--cache-dir", self.config.cache_dir])

        if self.config.timeout:
            args.extend(["--timeout", f"{self.config.timeout}s"])

        if self.config.offline:
            args.append("--offline-scan")

        if self.config.skip_db_update:
            args.append("--skip-db-update")

        if self.config.debug:
            args.append("--debug")

        if self.config.config_file:
            args.extend(["--config", self.config.config_file])

        return args

    def _run_trivy(self, args: List[str]) -> TrivyScanResult:
        """Execute Trivy command and parse results"""
        full_args = self._build_base_args() + args

        logger.debug(f"Running Trivy command: {' '.join(full_args)}")

        try:
            result = subprocess.run(
                full_args,
                capture_output=True,
                text=True,
                timeout=self.config.timeout
            )

            if result.returncode != 0 and not result.stdout:
                raise TrivyScanError(
                    f"Trivy scan failed with exit code {result.returncode}: {result.stderr}"
                )

            # Parse JSON output
            try:
                json_output = json.loads(result.stdout)
                return TrivyScanResult.from_json(json_output, result.stdout)
            except json.JSONDecodeError:
                # If not JSON, return raw output
                return TrivyScanResult(
                    schema_version=2,
                    created_at="",
                    artifact_name="",
                    artifact_type="",
                    metadata={},
                    results=[],
                    raw_output=result.stdout
                )

        except subprocess.TimeoutExpired:
            raise TrivyScanError(f"Trivy scan timed out after {self.config.timeout}s")
        except Exception as e:
            raise TrivyScanError(f"Trivy scan failed: {e}")

    def scan_image(
        self,
        image: str,
        scanners: Optional[List[str]] = None,
        format: str = "json",
        severity: Optional[List[str]] = None,
        license_full: bool = True,
        **kwargs
    ) -> TrivyScanResult:
        """
        Scan a container image.

        Args:
            image: Image name (e.g., "nginx:latest", "gcr.io/project/image:tag")
            scanners: List of scanners to use (vuln, misconfig, secret, license)
            format: Output format (json, table, sarif, etc.)
            severity: List of severities to include
            license_full: Include full license text in output
            **kwargs: Additional Trivy arguments

        Returns:
            TrivyScanResult with scan findings
        """
        args = ["image"]

        if scanners:
            args.extend(["--scanners", ",".join(scanners)])

        args.extend(["--format", format])

        if severity:
            args.extend(["--severity", ",".join(severity)])

        if license_full and "license" in (scanners or []):
            args.append("--license-full")

        if self.config.ignore_unfixed:
            args.append("--ignore-unfixed")

        if self.config.ignore_file:
            args.extend(["--ignorefile", self.config.ignore_file])

        args.append(image)

        return self._run_trivy(args)

    def scan_repository(
        self,
        repo: str,
        scanners: Optional[List[str]] = None,
        format: str = "json",
        severity: Optional[List[str]] = None,
        license_full: bool = True,
        branch: Optional[str] = None,
        **kwargs
    ) -> TrivyScanResult:
        """
        Scan a Git repository.

        Args:
            repo: Repository path or URL
            scanners: List of scanners to use
            format: Output format
            severity: List of severities to include
            license_full: Include full license text
            branch: Specific branch to scan (for remote repos)
            **kwargs: Additional arguments

        Returns:
            TrivyScanResult with scan findings
        """
        args = ["repository"]

        if scanners:
            args.extend(["--scanners", ",".join(scanners)])

        args.extend(["--format", format])

        if severity:
            args.extend(["--severity", ",".join(severity)])

        if license_full and "license" in (scanners or []):
            args.append("--license-full")

        if branch:
            args.extend(["--branch", branch])

        args.append(repo)

        return self._run_trivy(args)

    def scan_filesystem(
        self,
        path: str,
        scanners: Optional[List[str]] = None,
        format: str = "json",
        severity: Optional[List[str]] = None,
        license_full: bool = True,
        **kwargs
    ) -> TrivyScanResult:
        """
        Scan a local filesystem path.

        Args:
            path: Filesystem path to scan
            scanners: List of scanners to use
            format: Output format
            severity: List of severities to include
            license_full: Include full license text
            **kwargs: Additional arguments

        Returns:
            TrivyScanResult with scan findings
        """
        args = ["filesystem"]

        if scanners:
            args.extend(["--scanners", ",".join(scanners)])

        args.extend(["--format", format])

        if severity:
            args.extend(["--severity", ",".join(severity)])

        if license_full and "license" in (scanners or []):
            args.append("--license-full")

        args.append(path)

        return self._run_trivy(args)

    def scan_kubernetes(
        self,
        context: Optional[str] = None,
        namespace: Optional[str] = None,
        scanners: Optional[List[str]] = None,
        format: str = "json",
        **kwargs
    ) -> TrivyScanResult:
        """
        Scan a Kubernetes cluster.

        Args:
            context: Kubernetes context to use
            namespace: Namespace to scan (default: all namespaces)
            scanners: List of scanners to use
            format: Output format
            **kwargs: Additional arguments

        Returns:
            TrivyScanResult with scan findings
        """
        args = ["kubernetes"]

        if scanners:
            args.extend(["--scanners", ",".join(scanners)])

        args.extend(["--format", format])

        if context:
            args.extend(["--context", context])

        if namespace:
            args.extend(["--namespace", namespace])
        else:
            args.append("--all-namespaces")

        args.append("cluster")

        return self._run_trivy(args)

    def generate_sbom(
        self,
        target: str,
        format: SBOMFormat = SBOMFormat.SPDX_JSON,
        output: Optional[str] = None,
        target_type: str = "filesystem"
    ) -> Dict[str, Any]:
        """
        Generate Software Bill of Materials (SBOM).

        Args:
            target: Target to scan (path, image, or repository)
            format: SBOM format (spdx-json, cyclonedx-json, etc.)
            output: Output file path (optional)
            target_type: Type of target (filesystem, image, repository)

        Returns:
            SBOM as dictionary
        """
        args = [target_type, "--format", format.value]

        if output:
            args.extend(["--output", output])

        args.append(target)

        result = self._run_trivy(args)

        if output:
            logger.info(f"SBOM written to: {output}")

        # Try to parse as JSON if format is JSON-based
        if "json" in format.value.lower():
            try:
                return json.loads(result.raw_output)
            except json.JSONDecodeError:
                return {"raw": result.raw_output}

        return {"raw": result.raw_output}

    def scan_with_policy(
        self,
        target: str,
        policy_path: str,
        target_type: str = "filesystem",
        format: str = "json"
    ) -> TrivyScanResult:
        """
        Scan with custom Rego policy.

        Args:
            target: Target to scan
            policy_path: Path to Rego policy files
            target_type: Type of target
            format: Output format

        Returns:
            TrivyScanResult with policy evaluation results
        """
        args = [
            target_type,
            "--scanners", "misconfig",
            "--policy", policy_path,
            "--format", format,
            target
        ]

        return self._run_trivy(args)


def check_trivy_available() -> bool:
    """Check if Trivy is available in PATH"""
    return shutil.which("trivy") is not None


def get_trivy_version() -> Optional[str]:
    """Get installed Trivy version"""
    try:
        result = subprocess.run(
            ["trivy", "version", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            version_info = json.loads(result.stdout)
            return version_info.get("Version")
    except Exception:
        pass
    return None
