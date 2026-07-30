#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Certificate Pinning Validator for iGaming Mobile Apps
GLI-GSF Phase 2 - Mobile Security Controls

Validates that mobile gambling applications correctly implement
certificate pinning per GLI-GSF-4 mobile security requirements.

Features:
- APK/IPA static analysis for pinning configurations
- Network Security Config (Android) validation
- Info.plist ATS settings (iOS) validation
- Runtime pinning bypass detection
- HPKP header analysis for web-based mobile clients
- Report generation for GLI auditors

Requirements:
    pip install requests cryptography pyOpenSSL androguard biplist
"""

import argparse
import hashlib
import json
import logging
import os
import re
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("pinning-check")


class Severity(Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class Finding:
    title: str
    severity: Severity
    description: str
    remediation: str
    gli_reference: str = ""
    evidence: str = ""


@dataclass
class PinningReport:
    app_name: str
    app_version: str = "unknown"
    platform: str = "unknown"
    scan_date: str = field(default_factory=lambda: datetime.utcnow().isoformat())  # ty:ignore[deprecated]
    findings: list = field(default_factory=list)
    endpoints_checked: list = field(default_factory=list)
    pinning_configs_found: int = 0
    overall_pass: bool = False

    def add_finding(self, finding: Finding):
        self.findings.append(finding)

    def has_critical(self) -> bool:
        return any(f.severity == Severity.CRITICAL for f in self.findings)


# ---------------------------------------------------------------------------
# Android APK Analysis
# ---------------------------------------------------------------------------
class AndroidPinningAnalyzer:
    """Analyze Android APK for certificate pinning implementation."""

    # Patterns indicating pinning in code
    PINNING_PATTERNS = [
        (r"CertificatePinner", "OkHttp CertificatePinner"),
        (r"X509TrustManager", "Custom TrustManager"),
        (r"network_security_config", "Network Security Config reference"),
        (r"certificateTransparency", "Certificate Transparency"),
        (r"sha256/[A-Za-z0-9+/=]{43,44}", "SHA-256 pin hash"),
        (r"TrustManagerFactory", "TrustManagerFactory usage"),
        (r"\.pin\(", "Pin method call"),
    ]

    # Anti-patterns (pinning bypass indicators)
    BYPASS_PATTERNS = [
        (r"TrustAllCerts", "Trust-all certificates pattern"),
        (r"AllowAllHostnameVerifier", "Hostname verification disabled"),
        (r"ALLOW_ALL_HOSTNAME_VERIFIER", "Permissive hostname verifier"),
        (r"setHostnameVerifier\s*\(\s*null", "Null hostname verifier"),
        (r"trustAllCerts|trustAll", "Trust-all shortcut"),
        (r"X509Certificate\[\]\s*\{\s*\}", "Empty trust anchor"),
        (r"checkServerTrusted.*\{\s*\}", "Empty server trust check"),
        (r"SSLContext\.getInstance\(\"TLS\"\)", "Generic TLS without pinning"),
    ]

    def __init__(self, apk_path: str):
        self.apk_path = apk_path
        self.report = PinningReport(app_name=os.path.basename(apk_path), platform="Android")
        self.temp_dir = None

    def analyze(self) -> PinningReport:
        logger.info(f"Analyzing Android APK: {self.apk_path}")

        if not os.path.exists(self.apk_path):
            self.report.add_finding(Finding(
                title="APK file not found",
                severity=Severity.CRITICAL,
                description=f"Cannot find APK at {self.apk_path}",
                remediation="Provide valid APK path"
            ))
            return self.report

        self.temp_dir = tempfile.mkdtemp(prefix="pinning_check_")

        try:
            self._extract_apk()
            self._check_network_security_config()
            self._check_manifest_cleartext()
            self._scan_dex_for_pinning()
            self._check_okhttp_pins()
            self._check_webview_config()
            self._evaluate_overall()
        except Exception as e:
            logger.error(f"Analysis error: {e}")
            self.report.add_finding(Finding(
                title="Analysis Error",
                severity=Severity.INFO,
                description=str(e),
                remediation="Review APK manually"
            ))
        finally:
            if self.temp_dir:
                subprocess.run(["rm", "-rf", self.temp_dir], capture_output=True)

        return self.report

    def _extract_apk(self):
        """Extract APK contents."""
        try:
            with zipfile.ZipFile(self.apk_path, 'r') as z:
                z.extractall(self.temp_dir)
            logger.info("APK extracted successfully")
        except zipfile.BadZipFile:
            raise ValueError("Invalid APK file (not a valid ZIP)")

    def _check_network_security_config(self):
        """Check Android Network Security Configuration (Android 7+)."""
        nsc_path = os.path.join(self.temp_dir, "res", "xml", "network_security_config.xml")  # ty:ignore[no-matching-overload]

        if not os.path.exists(nsc_path):
            self.report.add_finding(Finding(
                title="No Network Security Config found",
                severity=Severity.HIGH,
                description=(
                    "Android Network Security Configuration file not found. "
                    "This is the recommended way to implement certificate pinning on Android 7+."
                ),
                remediation=(
                    "Create res/xml/network_security_config.xml with pin-set directives "
                    "for all gambling API endpoints."
                ),
                gli_reference="GLI-GSF-4 Section 3.2 - Mobile Transport Security"
            ))
            return

        self.report.pinning_configs_found += 1

        with open(nsc_path, 'r', errors='ignore') as f:
            content = f.read()

        # Check for pin-set elements
        if "<pin-set" not in content:
            self.report.add_finding(Finding(
                title="Network Security Config lacks pin-set",
                severity=Severity.HIGH,
                description="NSC exists but has no certificate pinning (pin-set) directives.",
                remediation="Add <pin-set> with SHA-256 pins for gambling API domains.",
                gli_reference="GLI-GSF-4 Section 3.2.1",
                evidence=content[:500]
            ))
        else:
            # Check pin expiration
            if 'expiration="' in content:
                exp_match = re.search(r'expiration="(\d{4}-\d{2}-\d{2})"', content)
                if exp_match:
                    exp_date = datetime.strptime(exp_match.group(1), "%Y-%m-%d")
                    if exp_date < datetime.utcnow():  # ty:ignore[deprecated]
                        self.report.add_finding(Finding(
                            title="Certificate pins expired",
                            severity=Severity.CRITICAL,
                            description=f"Pin-set expired on {exp_match.group(1)}. Pinning is not enforced.",
                            remediation="Update pin-set with current certificate pins and new expiration.",
                            gli_reference="GLI-GSF-4 Section 3.2.1"
                        ))
                    elif exp_date < datetime.utcnow() + timedelta(days=30):  # ty:ignore[deprecated]
                        self.report.add_finding(Finding(
                            title="Certificate pins expiring soon",
                            severity=Severity.MEDIUM,
                            description=f"Pin-set expires on {exp_match.group(1)} (within 30 days).",
                            remediation="Plan pin rotation before expiration.",
                            gli_reference="GLI-GSF-4 Section 3.2.1"
                        ))

            # Check for backup pins
            pin_count = content.count("<pin ")
            if pin_count < 2:
                self.report.add_finding(Finding(
                    title="No backup pin configured",
                    severity=Severity.MEDIUM,
                    description="Only one pin found. A backup pin is required for key rotation.",
                    remediation="Add at least one backup pin from a different CA.",
                    gli_reference="GLI-GSF-4 Section 3.2.2"
                ))

        # Check if cleartext traffic is allowed
        if 'cleartextTrafficPermitted="true"' in content:
            self.report.add_finding(Finding(
                title="Cleartext traffic permitted",
                severity=Severity.HIGH,
                description="Network Security Config allows cleartext (HTTP) traffic.",
                remediation='Set cleartextTrafficPermitted="false" for all domains.',
                gli_reference="GLI-GSF-4 Section 3.1",
                evidence="cleartextTrafficPermitted=\"true\""
            ))

        # Check for debug overrides in release build
        if "<debug-overrides" in content:
            self.report.add_finding(Finding(
                title="Debug overrides present",
                severity=Severity.LOW,
                description="Debug overrides found in NSC. Verify this is only active in debug builds.",
                remediation="Ensure debug-overrides do not weaken pinning in release builds.",
                gli_reference="GLI-GSF-4 Section 3.2.3"
            ))

    def _check_manifest_cleartext(self):
        """Check AndroidManifest.xml for cleartext settings."""
        manifest_path = os.path.join(self.temp_dir, "AndroidManifest.xml")  # ty:ignore[no-matching-overload]
        if not os.path.exists(manifest_path):
            return

        with open(manifest_path, 'rb') as f:
            content = f.read()

        # Binary XML - look for cleartext pattern
        if b"usesCleartextTraffic" in content:
            logger.info("Found usesCleartextTraffic in manifest")

    def _scan_dex_for_pinning(self):
        """Scan DEX files for pinning and bypass patterns."""
        dex_files = []
        for root, dirs, files in os.walk(self.temp_dir):
            for f in files:
                if f.endswith('.dex'):
                    dex_files.append(os.path.join(root, f))

        if not dex_files:
            return

        found_pinning = False
        found_bypass = False

        for dex_file in dex_files:
            with open(dex_file, 'rb') as f:
                content = f.read()
                text_content = content.decode('utf-8', errors='ignore')

            for pattern, desc in self.PINNING_PATTERNS:
                if re.search(pattern, text_content):
                    found_pinning = True
                    self.report.pinning_configs_found += 1
                    logger.info(f"Found pinning pattern: {desc}")

            for pattern, desc in self.BYPASS_PATTERNS:
                if re.search(pattern, text_content):
                    found_bypass = True
                    self.report.add_finding(Finding(
                        title=f"Pinning bypass pattern: {desc}",
                        severity=Severity.CRITICAL,
                        description=f"Detected code pattern that bypasses certificate validation: {desc}",
                        remediation="Remove all trust-all and hostname verification bypass code.",
                        gli_reference="GLI-GSF-4 Section 3.2.4"
                    ))

        if not found_pinning:
            self.report.add_finding(Finding(
                title="No code-level pinning detected",
                severity=Severity.MEDIUM,
                description="No certificate pinning patterns found in DEX code.",
                remediation="Implement CertificatePinner (OkHttp) or Network Security Config.",
                gli_reference="GLI-GSF-4 Section 3.2"
            ))

    def _check_okhttp_pins(self):
        """Look for OkHttp certificate pinner configurations."""
        for root, dirs, files in os.walk(self.temp_dir):
            for f in files:
                if f.endswith(('.json', '.xml', '.properties')):
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, 'r', errors='ignore') as fh:
                            content = fh.read()
                        if 'sha256/' in content:
                            pins = re.findall(r'sha256/([A-Za-z0-9+/=]{43,44})', content)
                            if pins:
                                logger.info(f"Found {len(pins)} SHA-256 pins in {f}")
                                self.report.pinning_configs_found += len(pins)
                    except Exception:
                        pass

    def _check_webview_config(self):
        """Check for insecure WebView configurations."""
        for root, dirs, files in os.walk(self.temp_dir):
            for f in files:
                if f.endswith('.dex'):
                    fpath = os.path.join(root, f)
                    with open(fpath, 'rb') as fh:
                        content = fh.read().decode('utf-8', errors='ignore')

                    if 'setWebViewClient' in content and 'onReceivedSslError' in content:
                        if 'proceed' in content.lower():
                            self.report.add_finding(Finding(
                                title="WebView SSL error handler may bypass validation",
                                severity=Severity.HIGH,
                                description="WebView onReceivedSslError with proceed() detected.",
                                remediation="Never call handler.proceed() in onReceivedSslError.",
                                gli_reference="GLI-GSF-4 Section 3.3"
                            ))

    def _evaluate_overall(self):
        """Determine overall pass/fail."""
        self.report.overall_pass = (
            self.report.pinning_configs_found > 0
            and not self.report.has_critical()
        )


# ---------------------------------------------------------------------------
# iOS IPA Analysis
# ---------------------------------------------------------------------------
class IOSPinningAnalyzer:
    """Analyze iOS IPA for certificate pinning implementation."""

    def __init__(self, ipa_path: str):
        self.ipa_path = ipa_path
        self.report = PinningReport(app_name=os.path.basename(ipa_path), platform="iOS")
        self.temp_dir = None

    def analyze(self) -> PinningReport:
        logger.info(f"Analyzing iOS IPA: {self.ipa_path}")

        if not os.path.exists(self.ipa_path):
            self.report.add_finding(Finding(
                title="IPA file not found",
                severity=Severity.CRITICAL,
                description=f"Cannot find IPA at {self.ipa_path}",
                remediation="Provide valid IPA path"
            ))
            return self.report

        self.temp_dir = tempfile.mkdtemp(prefix="pinning_check_ios_")

        try:
            self._extract_ipa()
            self._check_ats_config()
            self._check_embedded_pins()
            self._scan_binary_strings()
            self._evaluate_overall()
        except Exception as e:
            logger.error(f"iOS analysis error: {e}")
        finally:
            if self.temp_dir:
                subprocess.run(["rm", "-rf", self.temp_dir], capture_output=True)

        return self.report

    def _extract_ipa(self):
        try:
            with zipfile.ZipFile(self.ipa_path, 'r') as z:
                z.extractall(self.temp_dir)
        except zipfile.BadZipFile:
            raise ValueError("Invalid IPA file")

    def _check_ats_config(self):
        """Check App Transport Security settings in Info.plist."""
        payload_dir = os.path.join(self.temp_dir, "Payload")  # ty:ignore[no-matching-overload]
        if not os.path.exists(payload_dir):
            return

        for item in os.listdir(payload_dir):
            if item.endswith(".app"):
                plist_path = os.path.join(payload_dir, item, "Info.plist")
                if os.path.exists(plist_path):
                    self._analyze_plist(plist_path)
                    break

    def _analyze_plist(self, plist_path: str):
        """Parse Info.plist for ATS configuration."""
        try:
            with open(plist_path, 'rb') as f:
                content = f.read()

            text = content.decode('utf-8', errors='ignore')

            if "NSAllowsArbitraryLoads" in text:
                self.report.add_finding(Finding(
                    title="ATS allows arbitrary loads",
                    severity=Severity.CRITICAL,
                    description="NSAllowsArbitraryLoads is enabled, disabling App Transport Security.",
                    remediation="Remove NSAllowsArbitraryLoads and configure per-domain exceptions only.",
                    gli_reference="GLI-GSF-4 Section 4.1 - iOS Transport Security"
                ))

            if "NSExceptionAllowsInsecureHTTPLoads" in text:
                self.report.add_finding(Finding(
                    title="ATS exception allows insecure HTTP",
                    severity=Severity.HIGH,
                    description="Domain exception allows insecure HTTP connections.",
                    remediation="Remove insecure HTTP exceptions for gambling API domains.",
                    gli_reference="GLI-GSF-4 Section 4.1"
                ))

            if "NSExceptionMinimumTLSVersion" in text:
                if "TLSv1.0" in text or "TLSv1.1" in text:
                    self.report.add_finding(Finding(
                        title="Deprecated TLS version allowed",
                        severity=Severity.HIGH,
                        description="ATS exception allows TLS 1.0 or 1.1.",
                        remediation="Require TLS 1.2 minimum (TLS 1.3 preferred).",
                        gli_reference="GLI-GSF-4 Section 4.1.1"
                    ))

        except Exception as e:
            logger.warning(f"Could not parse plist: {e}")

    def _check_embedded_pins(self):
        """Look for embedded certificate pins in the app bundle."""
        pin_found = False
        for root, dirs, files in os.walk(self.temp_dir):
            for f in files:
                if f.endswith(('.cer', '.der', '.pem', '.p12')):
                    pin_found = True
                    self.report.pinning_configs_found += 1
                    logger.info(f"Found embedded certificate: {f}")

                if f.endswith('.json'):
                    fpath = os.path.join(root, f)
                    try:
                        with open(fpath, 'r', errors='ignore') as fh:
                            content = fh.read()
                        if 'sha256/' in content or 'pin-sha256' in content:
                            pin_found = True
                            self.report.pinning_configs_found += 1
                    except Exception:
                        pass

        if not pin_found:
            self.report.add_finding(Finding(
                title="No embedded certificate pins found",
                severity=Severity.MEDIUM,
                description="No certificate files or pin hashes found in app bundle.",
                remediation="Embed SHA-256 pins using TrustKit or URLSessionDelegate.",
                gli_reference="GLI-GSF-4 Section 4.2"
            ))

    def _scan_binary_strings(self):
        """Scan Mach-O binaries for pinning-related strings."""
        pinning_libs = [
            ("TrustKit", "TrustKit SSL pinning library"),
            ("AFSecurityPolicy", "AFNetworking security policy"),
            ("SSLPinningMode", "SSL pinning mode configuration"),
            ("evaluateServerTrust", "Server trust evaluation"),
            ("SecTrustEvaluate", "SecTrust API usage"),
            ("CertificatePinner", "Certificate pinner implementation"),
        ]

        for root, dirs, files in os.walk(self.temp_dir):
            for f in files:
                fpath = os.path.join(root, f)
                try:
                    with open(fpath, 'rb') as fh:
                        header = fh.read(4)
                        if header in (b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf',
                                      b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe',
                                      b'\xca\xfe\xba\xbe'):
                            fh.seek(0)
                            content = fh.read().decode('utf-8', errors='ignore')
                            for pattern, desc in pinning_libs:
                                if pattern in content:
                                    self.report.pinning_configs_found += 1
                                    logger.info(f"Found iOS pinning: {desc}")
                except Exception:
                    pass

    def _evaluate_overall(self):
        self.report.overall_pass = (
            self.report.pinning_configs_found > 0
            and not self.report.has_critical()
        )


# ---------------------------------------------------------------------------
# Live Endpoint Pinning Check
# ---------------------------------------------------------------------------
class EndpointPinningChecker:
    """Check live endpoints for pinning-related headers and TLS config."""

    GAMBLING_ENDPOINTS = [
        "/api/v1/auth/login",
        "/api/v1/wallet/balance",
        "/api/v1/games/launch",
        "/api/v1/bets/place",
        "/api/v1/kyc/verify",
        "/api/v1/withdrawals/request",
        "/api/v1/responsible-gaming/limits",
    ]

    def __init__(self, domain: str, port: int = 443):
        self.domain = domain
        self.port = port
        self.report = PinningReport(app_name=domain, platform="server")

    def check(self) -> PinningReport:
        logger.info(f"Checking endpoint: {self.domain}:{self.port}")

        self._check_tls_config()
        self._check_hpkp_headers()
        self._check_expect_ct()
        self._check_hsts()
        self._check_certificate_chain()
        self._evaluate_overall()

        return self.report

    def _check_tls_config(self):
        """Verify TLS version and cipher suite."""
        try:
            context = ssl.create_default_context()
            with socket.create_connection((self.domain, self.port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=self.domain) as ssock:
                    version = ssock.version()
                    cipher = ssock.cipher()

                    self.report.endpoints_checked.append({
                        "domain": self.domain,
                        "tls_version": version,
                        "cipher": cipher[0] if cipher else "unknown"
                    })

                    if version in ("TLSv1", "TLSv1.1"):
                        self.report.add_finding(Finding(
                            title=f"Deprecated TLS version: {version}",
                            severity=Severity.CRITICAL,
                            description=f"Server supports {version} which is deprecated.",
                            remediation="Disable TLS 1.0 and 1.1. Require TLS 1.2+.",
                            gli_reference="GLI-GSF-1 Section 2.4.1"
                        ))

                    if cipher and "RC4" in cipher[0]:
                        self.report.add_finding(Finding(
                            title="Weak cipher: RC4",
                            severity=Severity.CRITICAL,
                            description="Server uses RC4 cipher which is broken.",
                            remediation="Disable RC4. Use AES-GCM or ChaCha20.",
                            gli_reference="GLI-GSF-1 Section 2.4.2"
                        ))

        except ssl.SSLError as e:
            self.report.add_finding(Finding(
                title="TLS connection error",
                severity=Severity.HIGH,
                description=str(e),
                remediation="Verify server TLS configuration."
            ))
        except socket.timeout:
            self.report.add_finding(Finding(
                title="Connection timeout",
                severity=Severity.MEDIUM,
                description=f"Could not connect to {self.domain}:{self.port}",
                remediation="Verify endpoint availability."
            ))
        except Exception as e:
            logger.error(f"TLS check error: {e}")

    def _check_hpkp_headers(self):
        """Check for HTTP Public Key Pinning headers (deprecated but informative)."""
        try:
            import urllib.request
            url = f"https://{self.domain}:{self.port}/"
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "GLI-GSF-PinningCheck/1.0")

            with urllib.request.urlopen(req, timeout=10) as resp:
                hpkp = resp.headers.get("Public-Key-Pins")
                hpkp_ro = resp.headers.get("Public-Key-Pins-Report-Only")

                if hpkp:
                    logger.info("HPKP header found (enforcement mode)")
                    self.report.pinning_configs_found += 1
                elif hpkp_ro:
                    logger.info("HPKP header found (report-only mode)")
                    self.report.add_finding(Finding(
                        title="HPKP in report-only mode",
                        severity=Severity.LOW,
                        description="Public-Key-Pins-Report-Only header found but not enforced.",
                        remediation="Consider enforcing HPKP or migrating to Expect-CT.",
                        gli_reference="GLI-GSF-1 Section 2.4.3"
                    ))

        except Exception as e:
            logger.debug(f"Header check: {e}")

    def _check_expect_ct(self):
        """Check for Expect-CT header (Certificate Transparency)."""
        try:
            import urllib.request
            url = f"https://{self.domain}:{self.port}/"
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "GLI-GSF-PinningCheck/1.0")

            with urllib.request.urlopen(req, timeout=10) as resp:
                expect_ct = resp.headers.get("Expect-CT")
                if not expect_ct:
                    self.report.add_finding(Finding(
                        title="No Expect-CT header",
                        severity=Severity.LOW,
                        description="Certificate Transparency enforcement not configured via headers.",
                        remediation="Add Expect-CT header with enforce directive.",
                        gli_reference="GLI-GSF-1 Section 2.4.4"
                    ))
        except Exception:
            pass

    def _check_hsts(self):
        """Check Strict-Transport-Security header."""
        try:
            import urllib.request
            url = f"https://{self.domain}:{self.port}/"
            req = urllib.request.Request(url, method="HEAD")
            req.add_header("User-Agent", "GLI-GSF-PinningCheck/1.0")

            with urllib.request.urlopen(req, timeout=10) as resp:
                hsts = resp.headers.get("Strict-Transport-Security")
                if not hsts:
                    self.report.add_finding(Finding(
                        title="No HSTS header",
                        severity=Severity.HIGH,
                        description="Strict-Transport-Security header missing.",
                        remediation="Add HSTS with max-age >= 31536000 and includeSubDomains.",
                        gli_reference="GLI-GSF-1 Section 2.4.5"
                    ))
                else:
                    max_age_match = re.search(r'max-age=(\d+)', hsts)
                    if max_age_match:
                        max_age = int(max_age_match.group(1))
                        if max_age < 31536000:
                            self.report.add_finding(Finding(
                                title="HSTS max-age too short",
                                severity=Severity.MEDIUM,
                                description=f"HSTS max-age is {max_age}s (< 1 year).",
                                remediation="Set max-age to at least 31536000 (1 year).",
                                gli_reference="GLI-GSF-1 Section 2.4.5"
                            ))
                    if "includeSubDomains" not in hsts:
                        self.report.add_finding(Finding(
                            title="HSTS missing includeSubDomains",
                            severity=Severity.MEDIUM,
                            description="HSTS does not include subdomains.",
                            remediation="Add includeSubDomains to HSTS header.",
                            gli_reference="GLI-GSF-1 Section 2.4.5"
                        ))
        except Exception:
            pass

    def _check_certificate_chain(self):
        """Analyze the certificate chain."""
        if not HAS_CRYPTO:
            return

        try:
            context = ssl.create_default_context()
            with socket.create_connection((self.domain, self.port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=self.domain) as ssock:
                    cert_bin = ssock.getpeercert(binary_form=True)
                    cert = x509.load_der_x509_certificate(cert_bin)  # ty:ignore[invalid-argument-type]

                    # Check key size
                    pubkey = cert.public_key()
                    if isinstance(pubkey, rsa.RSAPublicKey):
                        key_size = pubkey.key_size
                        if key_size < 2048:
                            self.report.add_finding(Finding(
                                title=f"Weak RSA key: {key_size} bits",
                                severity=Severity.CRITICAL,
                                description=f"Certificate uses {key_size}-bit RSA key.",
                                remediation="Use minimum 2048-bit RSA or 256-bit ECDSA.",
                                gli_reference="GLI-GSF-1 Section 2.4.6"
                            ))
                    elif isinstance(pubkey, ec.EllipticCurvePublicKey):
                        key_size = pubkey.key_size
                        if key_size < 256:
                            self.report.add_finding(Finding(
                                title=f"Weak EC key: {key_size} bits",
                                severity=Severity.HIGH,
                                description=f"Certificate uses {key_size}-bit EC key.",
                                remediation="Use minimum 256-bit ECDSA (P-256 or better).",
                                gli_reference="GLI-GSF-1 Section 2.4.6"
                            ))

                    # Check expiration
                    days_left = (cert.not_valid_after_utc - datetime.utcnow()).days  # ty:ignore[deprecated]
                    if days_left < 0:
                        self.report.add_finding(Finding(
                            title="Certificate expired",
                            severity=Severity.CRITICAL,
                            description=f"Certificate expired {abs(days_left)} days ago.",
                            remediation="Renew certificate immediately.",
                            gli_reference="GLI-GSF-1 Section 2.4.7"
                        ))
                    elif days_left < 30:
                        self.report.add_finding(Finding(
                            title="Certificate expiring soon",
                            severity=Severity.HIGH,
                            description=f"Certificate expires in {days_left} days.",
                            remediation="Renew certificate before expiration.",
                            gli_reference="GLI-GSF-1 Section 2.4.7"
                        ))

                    # Generate pin for reporting
                    pub_bytes = pubkey.public_bytes(
                        serialization.Encoding.DER,
                        serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                    pin = hashlib.sha256(pub_bytes).digest()
                    import base64
                    pin_b64 = base64.b64encode(pin).decode()
                    logger.info(f"Current pin: sha256/{pin_b64}")

        except Exception as e:
            logger.error(f"Certificate chain check error: {e}")

    def _evaluate_overall(self):
        self.report.overall_pass = not self.report.has_critical()


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------
def generate_report(report: PinningReport, output_format: str = "json") -> str:
    """Generate audit report."""
    if output_format == "json":
        data = {
            "app_name": report.app_name,
            "platform": report.platform,
            "scan_date": report.scan_date,
            "app_version": report.app_version,
            "overall_pass": report.overall_pass,
            "pinning_configs_found": report.pinning_configs_found,
            "findings_count": len(report.findings),
            "critical_count": sum(1 for f in report.findings if f.severity == Severity.CRITICAL),
            "high_count": sum(1 for f in report.findings if f.severity == Severity.HIGH),
            "findings": [
                {
                    "title": f.title,
                    "severity": f.severity.value,
                    "description": f.description,
                    "remediation": f.remediation,
                    "gli_reference": f.gli_reference,
                    "evidence": f.evidence
                }
                for f in report.findings
            ],
            "endpoints_checked": report.endpoints_checked,
            "gli_compliance": {
                "gsf4_mobile_transport": report.pinning_configs_found > 0,
                "gsf4_no_bypass_patterns": not any(
                    "bypass" in f.title.lower() for f in report.findings
                ),
                "gsf1_tls_config": not any(
                    "TLS" in f.title and f.severity in (Severity.CRITICAL, Severity.HIGH)
                    for f in report.findings
                )
            }
        }
        return json.dumps(data, indent=2)

    # Text report
    lines = [
        "=" * 70,
        "GLI-GSF Certificate Pinning Validation Report",
        "=" * 70,
        f"Application: {report.app_name}",
        f"Platform:    {report.platform}",
        f"Scan Date:   {report.scan_date}",
        f"Overall:     {'PASS' if report.overall_pass else 'FAIL'}",
        f"Configs:     {report.pinning_configs_found} pinning configurations found",
        "",
        f"Findings:    {len(report.findings)} total",
        "-" * 70,
    ]

    for f in sorted(report.findings, key=lambda x: list(Severity).index(x.severity)):
        lines.extend([
            f"\n[{f.severity.value}] {f.title}",
            f"  Description: {f.description}",
            f"  Remediation: {f.remediation}",
        ])
        if f.gli_reference:
            lines.append(f"  GLI Ref:     {f.gli_reference}")
        if f.evidence:
            lines.append(f"  Evidence:    {f.evidence[:200]}")

    lines.append("\n" + "=" * 70)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="GLI-GSF Certificate Pinning Validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Analyze Android APK
  %(prog)s --apk casino-app.apk

  # Analyze iOS IPA
  %(prog)s --ipa casino-app.ipa

  # Check live endpoint
  %(prog)s --endpoint api.casino.example.com

  # Full scan with JSON output
  %(prog)s --apk app.apk --endpoint api.casino.example.com -f json -o report.json
        """
    )

    parser.add_argument("--apk", help="Path to Android APK file")
    parser.add_argument("--ipa", help="Path to iOS IPA file")
    parser.add_argument("--endpoint", help="Domain to check (e.g., api.casino.com)")
    parser.add_argument("--port", type=int, default=443, help="TLS port (default: 443)")
    parser.add_argument("-f", "--format", choices=["json", "text"], default="text",
                        help="Output format")
    parser.add_argument("-o", "--output", help="Output file path")

    args = parser.parse_args()

    if not any([args.apk, args.ipa, args.endpoint]):
        parser.print_help()
        sys.exit(1)

    reports = []

    if args.apk:
        analyzer = AndroidPinningAnalyzer(args.apk)
        reports.append(analyzer.analyze())

    if args.ipa:
        analyzer = IOSPinningAnalyzer(args.ipa)
        reports.append(analyzer.analyze())

    if args.endpoint:
        checker = EndpointPinningChecker(args.endpoint, args.port)
        reports.append(checker.check())

    all_pass = True
    for report in reports:
        output = generate_report(report, args.format)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            logger.info(f"Report saved to {args.output}")
        else:
            print(output)

        if not report.overall_pass:
            all_pass = False

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
