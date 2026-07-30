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
verify_ccp.py - Critical Control Program Signature Verification Daemon

OGIS-1 requires cryptographic verification of all Critical Control Programs
(CCPs) on a 24-hour cycle with comprehensive audit logging.

OGIS-1 verification triggers:
  1. On installation or update of any CCP
  2. On system power-up or recovery from failure
  3. Every 24 hours (automated cycle)
  4. On-demand (manual verification request)

Audit log requirements (OGIS-1):
  - NTP-synchronized timestamps
  - Program identification (name, path, version)
  - Expected vs. actual signatures
  - User ID for manual verifications
  - Export in CSV, JSON, and XML formats
  - 5-year retention (stored to MinIO/S3)

Alert requirements:
  - Signature mismatch: alert within 30 minutes
  - Alert channels: PagerDuty, email, SIEM
  - Response SLA: 30 minutes

Usage:
    # Register CCPs
    python3 verify_ccp.py register --name "rng-service" --path /opt/rng/rng-engine \
        --version "2.1.0" --algorithm sha256

    # Run single verification cycle
    python3 verify_ccp.py verify

    # Run as daemon (24-hour cycle)
    python3 verify_ccp.py daemon --interval 86400

    # Export logs
    python3 verify_ccp.py export --format json --since 2025-01-01
    python3 verify_ccp.py export --format csv --since 2025-01-01
    python3 verify_ccp.py export --format xml --since 2025-01-01

Requirements:
    pip install schedule  (for daemon mode, optional)
    Standard library for core functionality
"""

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import signal
import socket
import struct
import sys
import time
import threading
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VERSION = "1.0.0"
CONFIG_DIR = os.environ.get("CCP_CONFIG_DIR", "/etc/gsf/ccp")
LOG_DIR = os.environ.get("CCP_LOG_DIR", "/var/log/gsf")
REGISTRY_FILE = os.path.join(CONFIG_DIR, "ccp-registry.json")
VERIFICATION_LOG = os.path.join(LOG_DIR, "signature-verification.log")

# Supported hash algorithms
ALGORITHMS = {"sha256", "sha384", "sha512", "blake2b"}

# Alert configuration (environment variables)
PAGERDUTY_KEY = os.environ.get("PAGERDUTY_ROUTING_KEY", "")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL", "gis-officer@acmetocasino.com")
SIEM_ENDPOINT = os.environ.get("SIEM_ENDPOINT", "http://wazuh:514")

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("verify-ccp")


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------
@dataclass
class CriticalControlProgram:
    """A registered Critical Control Program per OGIS-1."""
    name: str
    path: str
    version: str
    algorithm: str
    expected_signature: str
    description: str = ""
    category: str = "game-logic"  # rng, game-logic, payout, platform
    registered_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    registered_by: str = ""
    last_verified: str = ""
    last_status: str = "pending"


@dataclass
class VerificationResult:
    """Result of a single CCP verification."""
    timestamp: str
    program_name: str
    program_path: str
    program_version: str
    algorithm: str
    expected_signature: str
    actual_signature: str
    status: str  # PASS, FAIL, ERROR
    trigger: str  # scheduled, startup, on-demand, update
    hostname: str
    user_id: str
    verification_duration_ms: float
    error_message: str = ""
    ntp_synchronized: bool = True

    @property
    def is_match(self) -> bool:
        return self.expected_signature == self.actual_signature


# ---------------------------------------------------------------------------
# NTP Synchronization Check
# ---------------------------------------------------------------------------
def check_ntp_sync() -> bool:
    """Verify NTP synchronization (OGIS-1 requires NTP-synced timestamps)."""
    try:
        # Check timedatectl on Linux
        import subprocess
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized"],
            capture_output=True, text=True, timeout=5,
        )
        return "NTPSynchronized=yes" in result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Fallback: check /etc/ntp.conf existence or systemd-timesyncd
        return Path("/etc/ntp.conf").exists() or Path("/run/systemd/timesync").exists()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Signature Computation
# ---------------------------------------------------------------------------
def compute_signature(file_path: str, algorithm: str = "sha256") -> str:
    """Compute cryptographic signature of a file."""
    if algorithm not in ALGORITHMS:
        raise ValueError(f"Unsupported algorithm: {algorithm}. Use: {ALGORITHMS}")

    if algorithm == "blake2b":
        hasher = hashlib.blake2b()
    else:
        hasher = hashlib.new(algorithm)

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"CCP binary not found: {file_path}")

    # Read in chunks for large binaries
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def compute_directory_signature(dir_path: str, algorithm: str = "sha256") -> str:
    """Compute aggregate signature for a directory of files."""
    if algorithm == "blake2b":
        hasher = hashlib.blake2b()
    else:
        hasher = hashlib.new(algorithm)

    path = Path(dir_path)
    if not path.is_dir():
        raise NotADirectoryError(f"Not a directory: {dir_path}")

    # Sort files for deterministic hashing
    for file_path in sorted(path.rglob("*")):
        if file_path.is_file():
            hasher.update(str(file_path.relative_to(path)).encode())
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)

    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# CCP Registry
# ---------------------------------------------------------------------------
class CCPRegistry:
    """Manages the registry of Critical Control Programs."""

    def __init__(self, registry_path: str = REGISTRY_FILE):
        self.registry_path = registry_path
        self.programs: Dict[str, CriticalControlProgram] = {}
        self._load()

    def _load(self):
        """Load registry from disk."""
        path = Path(self.registry_path)
        if path.exists():
            try:
                data = json.loads(path.read_text())
                for name, prog_data in data.items():
                    self.programs[name] = CriticalControlProgram(**prog_data)
                logger.info(f"Loaded {len(self.programs)} CCPs from registry")
            except (json.JSONDecodeError, TypeError) as e:
                logger.error(f"Failed to load registry: {e}")
        else:
            logger.info("No existing registry found. Starting fresh.")

    def _save(self):
        """Persist registry to disk."""
        path = Path(self.registry_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {name: asdict(prog) for name, prog in self.programs.items()}
        path.write_text(json.dumps(data, indent=2))

    def register(self, ccp: CriticalControlProgram) -> str:
        """Register a new CCP and compute its baseline signature."""
        # Compute initial signature
        if Path(ccp.path).is_dir():
            ccp.expected_signature = compute_directory_signature(ccp.path, ccp.algorithm)
        else:
            ccp.expected_signature = compute_signature(ccp.path, ccp.algorithm)

        ccp.registered_by = os.environ.get("USER", "system")
        self.programs[ccp.name] = ccp
        self._save()

        logger.info(
            f"Registered CCP: {ccp.name} ({ccp.path}) "
            f"sig={ccp.expected_signature[:16]}..."
        )
        return ccp.expected_signature

    def update_signature(self, name: str, new_signature: str):
        """Update expected signature after approved change."""
        if name not in self.programs:
            raise KeyError(f"CCP not found: {name}")
        self.programs[name].expected_signature = new_signature
        self._save()

    def list_programs(self) -> List[CriticalControlProgram]:
        return list(self.programs.values())


# ---------------------------------------------------------------------------
# Verification Engine
# ---------------------------------------------------------------------------
class VerificationEngine:
    """Executes signature verification for all registered CCPs."""

    def __init__(self, registry: CCPRegistry):
        self.registry = registry
        self.hostname = socket.gethostname()
        self.ntp_synced = check_ntp_sync()
        self.results: List[VerificationResult] = []

    def verify_all(self, trigger: str = "scheduled") -> List[VerificationResult]:
        """Verify all registered CCPs. Returns list of results."""
        results = []
        user_id = os.environ.get("USER", "system")

        if not self.ntp_synced:
            logger.warning(
                "NTP synchronization not confirmed. OGIS-1 requires "
                "NTP-synchronized timestamps on all verification logs."
            )

        logger.info(
            f"Starting verification cycle: {len(self.registry.programs)} CCPs "
            f"(trigger: {trigger})"
        )

        for name, ccp in self.registry.programs.items():
            result = self._verify_single(ccp, trigger, user_id)
            results.append(result)

            # Log to structured log file
            self._append_log(result)

            if result.status == "FAIL":
                logger.critical(
                    f"SIGNATURE MISMATCH: {name} - "
                    f"expected={ccp.expected_signature[:16]}... "
                    f"actual={result.actual_signature[:16]}..."
                )
                self._send_alert(result)
            elif result.status == "ERROR":
                logger.error(f"Verification error for {name}: {result.error_message}")
                self._send_alert(result)
            else:
                logger.info(f"PASS: {name} ({ccp.algorithm}: {result.actual_signature[:16]}...)")

            # Update last verified
            ccp.last_verified = result.timestamp
            ccp.last_status = result.status

        self.registry._save()
        self.results = results

        # Summary
        passed = sum(1 for r in results if r.status == "PASS")
        failed = sum(1 for r in results if r.status == "FAIL")
        errors = sum(1 for r in results if r.status == "ERROR")
        logger.info(
            f"Verification complete: {passed} PASS, {failed} FAIL, "
            f"{errors} ERROR out of {len(results)} CCPs"
        )

        return results

    def _verify_single(
        self, ccp: CriticalControlProgram, trigger: str, user_id: str
    ) -> VerificationResult:
        """Verify a single CCP."""
        start_time = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()

        try:
            if Path(ccp.path).is_dir():
                actual_sig = compute_directory_signature(ccp.path, ccp.algorithm)
            else:
                actual_sig = compute_signature(ccp.path, ccp.algorithm)

            duration_ms = (time.monotonic() - start_time) * 1000
            status = "PASS" if actual_sig == ccp.expected_signature else "FAIL"

            return VerificationResult(
                timestamp=timestamp,
                program_name=ccp.name,
                program_path=ccp.path,
                program_version=ccp.version,
                algorithm=ccp.algorithm,
                expected_signature=ccp.expected_signature,
                actual_signature=actual_sig,
                status=status,
                trigger=trigger,
                hostname=self.hostname,
                user_id=user_id,
                verification_duration_ms=round(duration_ms, 2),
                ntp_synchronized=self.ntp_synced,
            )

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            return VerificationResult(
                timestamp=timestamp,
                program_name=ccp.name,
                program_path=ccp.path,
                program_version=ccp.version,
                algorithm=ccp.algorithm,
                expected_signature=ccp.expected_signature,
                actual_signature="",
                status="ERROR",
                trigger=trigger,
                hostname=self.hostname,
                user_id=user_id,
                verification_duration_ms=round(duration_ms, 2),
                error_message=str(e),
                ntp_synchronized=self.ntp_synced,
            )

    def _append_log(self, result: VerificationResult):
        """Append verification result to structured log file."""
        log_path = Path(VERIFICATION_LOG)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_entry = json.dumps(asdict(result), default=str)
        with open(log_path, "a") as f:
            f.write(log_entry + "\n")

    def _send_alert(self, result: VerificationResult):
        """Send alert on signature mismatch or error."""
        alert_message = (
            f"CCP VERIFICATION {result.status}: {result.program_name}\n"
            f"Path: {result.program_path}\n"
            f"Expected: {result.expected_signature}\n"
            f"Actual: {result.actual_signature}\n"
            f"Timestamp: {result.timestamp}\n"
            f"Host: {result.hostname}\n"
            f"Error: {result.error_message}\n"
        )

        logger.critical(f"ALERT: {alert_message}")

        # PagerDuty integration
        if PAGERDUTY_KEY:
            self._alert_pagerduty(result)

        # SIEM forwarding
        if SIEM_ENDPOINT:
            self._alert_siem(result)

    def _alert_pagerduty(self, result: VerificationResult):
        """Send PagerDuty alert."""
        try:
            import urllib.request
            payload = {
                "routing_key": PAGERDUTY_KEY,
                "event_action": "trigger",
                "payload": {
                    "summary": f"CCP Signature {result.status}: {result.program_name}",
                    "severity": "critical",
                    "source": result.hostname,
                    "component": result.program_name,
                    "group": "ogis-1-verification",
                    "custom_details": asdict(result),
                },
            }
            req = urllib.request.Request(
                "https://events.pagerduty.com/v2/enqueue",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
            logger.info("PagerDuty alert sent successfully")
        except Exception as e:
            logger.error(f"Failed to send PagerDuty alert: {e}")

    def _alert_siem(self, result: VerificationResult):
        """Forward alert to SIEM (syslog format)."""
        try:
            import urllib.request
            syslog_msg = (
                f"<2>gsf-ccp-verify: {result.status} "
                f"program={result.program_name} "
                f"path={result.program_path} "
                f"expected={result.expected_signature} "
                f"actual={result.actual_signature}"
            )
            # Send as syslog UDP
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(syslog_msg.encode(), ("wazuh", 514))
            sock.close()
        except Exception as e:
            logger.error(f"Failed to forward to SIEM: {e}")


# ---------------------------------------------------------------------------
# Log Export (CSV, JSON, XML per OGIS-1 requirements)
# ---------------------------------------------------------------------------
class LogExporter:
    """Export verification logs in required formats (CSV, JSON, XML)."""

    @staticmethod
    def read_logs(since: Optional[str] = None) -> List[dict]:
        """Read verification logs from log file."""
        log_path = Path(VERIFICATION_LOG)
        if not log_path.exists():
            return []

        entries = []
        for line in log_path.read_text().strip().split("\n"):
            if not line:
                continue
            try:
                entry = json.loads(line)
                if since:
                    if entry.get("timestamp", "") >= since:
                        entries.append(entry)
                else:
                    entries.append(entry)
            except json.JSONDecodeError:
                continue

        return entries

    @staticmethod
    def to_json(entries: List[dict]) -> str:
        """Export as JSON."""
        return json.dumps({
            "export_type": "OGIS-1 Signature Verification Log",
            "export_date": datetime.now(timezone.utc).isoformat(),
            "record_count": len(entries),
            "entries": entries,
        }, indent=2)

    @staticmethod
    def to_csv(entries: List[dict]) -> str:
        """Export as CSV."""
        if not entries:
            return ""
        output = io.StringIO()
        fields = [
            "timestamp", "program_name", "program_path", "program_version",
            "algorithm", "expected_signature", "actual_signature", "status",
            "trigger", "hostname", "user_id", "verification_duration_ms",
            "ntp_synchronized", "error_message",
        ]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            writer.writerow(entry)
        return output.getvalue()

    @staticmethod
    def to_xml(entries: List[dict]) -> str:
        """Export as XML."""
        root = ET.Element("VerificationLog")
        root.set("xmlns", "urn:gli-gsf:ogis-1:verification-log")
        root.set("exportDate", datetime.now(timezone.utc).isoformat())
        root.set("recordCount", str(len(entries)))

        for entry in entries:
            record = ET.SubElement(root, "VerificationRecord")
            for key, value in entry.items():
                elem = ET.SubElement(record, key)
                elem.text = str(value)

        return ET.tostring(root, encoding="unicode", xml_declaration=True)


# ---------------------------------------------------------------------------
# Daemon Mode
# ---------------------------------------------------------------------------
class VerificationDaemon:
    """Run verification on a 24-hour cycle."""

    def __init__(self, registry: CCPRegistry, interval: int = 86400):
        self.registry = registry
        self.interval = interval
        self.running = True

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum, frame):
        logger.info(f"Received signal {signum}. Shutting down gracefully.")
        self.running = False

    def run(self):
        """Main daemon loop."""
        logger.info(
            f"Starting CCP verification daemon (interval: {self.interval}s / "
            f"{self.interval / 3600:.1f}h)"
        )

        # Initial verification on startup (OGIS-1 trigger: power-up)
        engine = VerificationEngine(self.registry)
        engine.verify_all(trigger="startup")

        while self.running:
            # Sleep until next cycle
            sleep_remaining = self.interval
            while sleep_remaining > 0 and self.running:
                time.sleep(min(sleep_remaining, 60))
                sleep_remaining -= 60

            if self.running:
                engine = VerificationEngine(self.registry)
                engine.verify_all(trigger="scheduled")

        logger.info("Daemon stopped.")


# ---------------------------------------------------------------------------
# Main CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="OGIS-1 Critical Control Program Signature Verification",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Command")

    # Register command
    reg_parser = subparsers.add_parser("register", help="Register a CCP")
    reg_parser.add_argument("--name", required=True, help="CCP name")
    reg_parser.add_argument("--path", required=True, help="Path to binary/directory")
    reg_parser.add_argument("--version", required=True, help="Version string")
    reg_parser.add_argument("--algorithm", default="sha256", choices=ALGORITHMS)
    reg_parser.add_argument("--description", default="", help="Description")
    reg_parser.add_argument(
        "--category", default="game-logic",
        choices=["rng", "game-logic", "payout", "platform"],
    )

    # Verify command
    ver_parser = subparsers.add_parser("verify", help="Run verification cycle")
    ver_parser.add_argument("--trigger", default="on-demand", help="Trigger type")

    # Daemon command
    daemon_parser = subparsers.add_parser("daemon", help="Run as daemon")
    daemon_parser.add_argument(
        "--interval", type=int, default=86400,
        help="Verification interval in seconds (default: 86400 = 24h)",
    )

    # Export command
    exp_parser = subparsers.add_parser("export", help="Export verification logs")
    exp_parser.add_argument("--format", required=True, choices=["json", "csv", "xml"])
    exp_parser.add_argument("--since", help="Export logs since date (ISO format)")
    exp_parser.add_argument("--output", help="Output file path")

    # List command
    subparsers.add_parser("list", help="List registered CCPs")

    # Status command
    subparsers.add_parser("status", help="Show verification status")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    registry = CCPRegistry()

    if args.command == "register":
        ccp = CriticalControlProgram(
            name=args.name,
            path=args.path,
            version=args.version,
            algorithm=args.algorithm,
            expected_signature="",
            description=args.description,
            category=args.category,
        )
        sig = registry.register(ccp)
        print(f"Registered: {args.name}")
        print(f"  Path: {args.path}")
        print(f"  Algorithm: {args.algorithm}")
        print(f"  Signature: {sig}")

    elif args.command == "verify":
        engine = VerificationEngine(registry)
        results = engine.verify_all(trigger=args.trigger)
        failed = [r for r in results if r.status != "PASS"]
        sys.exit(1 if failed else 0)

    elif args.command == "daemon":
        daemon = VerificationDaemon(registry, interval=args.interval)
        daemon.run()

    elif args.command == "export":
        exporter = LogExporter()
        entries = exporter.read_logs(since=args.since)

        if args.format == "json":
            output = exporter.to_json(entries)
        elif args.format == "csv":
            output = exporter.to_csv(entries)
        elif args.format == "xml":
            output = exporter.to_xml(entries)

        if args.output:
            Path(args.output).write_text(output)
            logger.info(f"Exported {len(entries)} records to {args.output}")
        else:
            print(output)

    elif args.command == "list":
        programs = registry.list_programs()
        if not programs:
            print("No CCPs registered.")
        else:
            print(f"{'Name':<25} {'Category':<12} {'Algorithm':<10} {'Status':<8} {'Path'}")
            print("-" * 90)
            for p in programs:
                print(f"{p.name:<25} {p.category:<12} {p.algorithm:<10} {p.last_status:<8} {p.path}")

    elif args.command == "status":
        programs = registry.list_programs()
        total = len(programs)
        passed = sum(1 for p in programs if p.last_status == "PASS")
        failed = sum(1 for p in programs if p.last_status == "FAIL")
        pending = sum(1 for p in programs if p.last_status == "pending")

        print(f"CCP Verification Status")
        print(f"  Total programs: {total}")
        print(f"  PASS: {passed}")
        print(f"  FAIL: {failed}")
        print(f"  Pending: {pending}")
        print(f"  NTP Synchronized: {check_ntp_sync()}")


if __name__ == "__main__":
    main()
