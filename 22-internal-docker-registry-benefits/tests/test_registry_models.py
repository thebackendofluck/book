# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""Behavioral tests for Chapter 22 — Registry Management models and enums."""

import importlib.util
import sys
import os
from datetime import datetime
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import cast

# ---------------------------------------------------------------------------
# Local module loading
# ---------------------------------------------------------------------------
# chapter-22/registry-management ships generic module names -- scanner,
# security, maintenance -- that collide with sibling chapters (chapter-10
# supplier-control-plane has a `maintenance.py`, chapter-11/24/33/42
# have their own `security.py`/`scanner.py` too). Pre-install the local
# copies via importlib.util.spec_from_file_location so `from scanner
# import ...` resolves to chapter-22's version regardless of pytest's
# global `sys.modules` state.
_REG_DIR = Path(__file__).resolve().parent.parent / "registry-management"
if str(_REG_DIR) not in sys.path:
    sys.path.insert(0, str(_REG_DIR))


def _load_local_module(module_name: str, file_name: str) -> None:
    sys.modules.pop(module_name, None)
    spec = importlib.util.spec_from_file_location(
        module_name, _REG_DIR / file_name,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(cast(ModuleSpec, spec))
    sys.modules[module_name] = module
    spec.loader.exec_module(module)


_load_local_module("scanner", "scanner.py")
_load_local_module("security", "security.py")
_load_local_module("maintenance", "maintenance.py")

from scanner import (
    ContainerRuntime,
    ScannerType,
    SeverityLevel,
    Vulnerability,
    ScanResult,
)
from security import AccessPolicy, AuthConfig
from maintenance import MaintenanceCheck, CleanupResult


class TestScannerEnums:
    """Validate scanner enum values cover expected backends."""

    def test_scanner_types_include_free_tools(self):
        free_scanners = {ScannerType.TRIVY, ScannerType.GRYPE, ScannerType.CLAIR}
        assert len(free_scanners) == 3

    def test_severity_levels_ordered(self):
        levels = [s.value for s in SeverityLevel]
        assert "CRITICAL" in levels
        assert "HIGH" in levels
        assert "LOW" in levels

    def test_container_runtimes(self):
        assert ContainerRuntime.DOCKER.value == "docker"
        assert ContainerRuntime.PODMAN.value == "podman"


class TestVulnerability:
    """Validate Vulnerability dataclass construction and defaults."""

    def test_vulnerability_with_fix(self):
        vuln = Vulnerability(
            cve_id="CVE-2024-1234",
            severity="HIGH",
            package="openssl",
            version="1.1.1k",
            description="Buffer overflow in TLS handshake",
            cvss_score=8.1,
            fix_available=True,
            fixed_version="1.1.1l",
        )
        assert vuln.cve_id == "CVE-2024-1234"
        assert vuln.fix_available is True
        assert vuln.fixed_version == "1.1.1l"
        assert vuln.exploit_available is False  # default

    def test_vulnerability_defaults(self):
        vuln = Vulnerability(
            cve_id="CVE-2024-0001",
            severity="LOW",
            package="zlib",
            version="1.2.11",
            description="Minor issue",
            cvss_score=2.0,
            fix_available=False,
        )
        assert vuln.fixed_version is None
        assert vuln.references == []


class TestScanResult:
    """Validate ScanResult aggregates vulnerability data correctly."""

    def test_scan_result_with_vulnerabilities(self):
        vulns = [
            Vulnerability("CVE-1", "HIGH", "pkg1", "1.0", "desc", 7.5, True),
            Vulnerability("CVE-2", "LOW", "pkg2", "2.0", "desc", 2.0, False),
        ]
        result = ScanResult(
            image="myapp:latest",
            scanner="trivy",
            timestamp=datetime.now(),
            status="completed",
            vulnerabilities=vulns,
            malware_detected=False,
            secrets_found=0,
            config_issues=1,
            risk_score=65,
            risk_level="medium",
            compliance={"pci_dss": True, "cis_benchmark": False},
        )
        assert len(result.vulnerabilities) == 2
        assert result.risk_score == 65
        assert result.compliance["pci_dss"] is True


class TestAccessPolicy:
    """Validate AccessPolicy construction."""

    def test_policy_with_expiry(self):
        policy = AccessPolicy(
            name="ci-readonly",
            policy_type="repository",
            repository_pattern="prod/*",
            actions=["pull"],
            expires_at=datetime(2026, 12, 31),
        )
        assert policy.actions == ["pull"]
        assert policy.expires_at is not None

    def test_policy_defaults(self):
        policy = AccessPolicy(
            name="admin",
            policy_type="registry",
            repository_pattern="**",
            actions=["pull", "push", "delete"],
        )
        assert policy.conditions == {}
        assert policy.expires_at is None


class TestMaintenanceModels:
    """Validate maintenance dataclasses."""

    def test_cleanup_result(self):
        result = CleanupResult(
            status="success",
            cleaned_images=15,
            freed_space_bytes=1_073_741_824,
            images_list=["old-app:v1", "old-app:v2"],
            errors=[],
        )
        assert result.freed_space_bytes > 0
        assert result.cleaned_images == 15
        assert len(result.errors) == 0
