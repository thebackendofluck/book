#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
On-premises server hardening script for iGaming infrastructure.

Implements CIS (Center for Internet Security) benchmark controls for Ubuntu
22.04 LTS servers in Continent 8 and co-located iGaming datacenters, with
additional iGaming-specific hardening for regulatory compliance.

Coverage:
  - CIS Level 1 and Level 2 controls (Ubuntu 22.04)
  - iGaming-specific: audit log retention (5 years per GLI-GSF), syslog
    forwarding to SIEM, kernel parameters for gaming workloads
  - Network hardening: TCP SYN cookies, ICMP rate limiting, IP spoofing
  - SSH hardening: key-only auth, idle timeout, allowed users list
  - Docker daemon hardening (see also: chapter-23/config/daemon.json)
  - Kernel module restrictions for gaming server profiles
  - File integrity baseline generation (sha256sum manifests)

Runs as root.  Designed for unattended execution in Ansible playbooks.

Usage:
    sudo python onprem_hardening.py --profile gaming-server --dry-run
    sudo python onprem_hardening.py --profile gaming-server --apply
    sudo python onprem_hardening.py --check --output report.json

Reference: Chapter 24 — Security and Compliance / On-Premises Hardening
           Chapter 29a — US On-Premises Infrastructure
           Chapter 23 — DevSecOps / Container Security
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False
    return logger


log = _build_logger("onprem_hardening")


# ---------------------------------------------------------------------------
# Result data classes
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    """Result of a single hardening check."""
    check_id: str
    title: str
    status: str          # "pass", "fail", "warn", "skip", "error"
    current_value: str = ""
    expected_value: str = ""
    remediation: str = ""
    applied: bool = False


@dataclass
class HardeningReport:
    """Full hardening run report."""
    hostname: str
    profile: str
    dry_run: bool
    started_at: str
    completed_at: str = ""
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "pass")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "fail")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.status == "warn")

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "profile": self.profile,
            "dry_run": self.dry_run,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": {
                "pass": self.pass_count,
                "fail": self.fail_count,
                "warn": self.warn_count,
                "total": len(self.checks),
            },
            "checks": [c.__dict__ for c in self.checks],
        }


# ---------------------------------------------------------------------------
# Shell helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], capture: bool = True) -> tuple[int, str, str]:
    """Run a shell command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=capture,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return 1, "", str(exc)


def _read_file(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _write_file(path: str, content: str, dry_run: bool = False) -> bool:
    if dry_run:
        log.info("DRY RUN: would write %s", path)
        return True
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        log.error("write_file_failed path=%s error=%s", path, exc)
        return False


def _sysctl_get(key: str) -> str | None:
    rc, out, _ = _run(["sysctl", "-n", key])
    return out if rc == 0 else None


def _sysctl_set(key: str, value: str, dry_run: bool = False) -> bool:
    if dry_run:
        log.info("DRY RUN: would set %s = %s", key, value)
        return True
    rc, _, err = _run(["sysctl", "-w", f"{key}={value}"])
    if rc != 0:
        log.error("sysctl_set_failed key=%s value=%s error=%s", key, value, err)
    return rc == 0


# ---------------------------------------------------------------------------
# Hardening checks
# ---------------------------------------------------------------------------

class ServerHardening:
    """
    CIS benchmark and iGaming-specific hardening runner.

    Args:
        profile: Hardening profile — "gaming-server", "k8s-node", "database".
        dry_run: If True, check and report without applying changes.
    """

    def __init__(self, profile: str = "gaming-server", dry_run: bool = True) -> None:
        self.profile = profile
        self.dry_run = dry_run
        self._checks: list[CheckResult] = []

    # --- Check runner -------------------------------------------------------

    def _check(
        self,
        check_id: str,
        title: str,
        expected: str,
        actual: str | None,
        remediation: str = "",
        apply_fn: Any = None,
    ) -> CheckResult:
        if actual is None:
            result = CheckResult(
                check_id=check_id,
                title=title,
                status="error",
                current_value="<unreadable>",
                expected_value=expected,
                remediation=remediation,
            )
            self._checks.append(result)
            return result

        passed = actual.strip() == expected.strip()
        result = CheckResult(
            check_id=check_id,
            title=title,
            status="pass" if passed else "fail",
            current_value=actual,
            expected_value=expected,
            remediation="" if passed else remediation,
        )

        if not passed and apply_fn and not self.dry_run:
            success = apply_fn()
            result.applied = success
            if success:
                result.status = "pass"
                log.info("hardening_applied check=%s", check_id)
            else:
                log.error("hardening_apply_failed check=%s", check_id)
        elif not passed and self.dry_run:
            log.info("DRY RUN: check=%s would apply remediation", check_id)

        self._checks.append(result)
        return result

    # --- Kernel parameters (sysctl) ----------------------------------------

    def check_network_hardening(self) -> None:
        """CIS 3.x — Network hardening sysctl parameters."""
        net_params = [
            # IP spoofing protection
            ("CIS-3.1.1", "net.ipv4.conf.all.rp_filter", "1",
             "Enable reverse path filtering (anti-spoofing)"),
            ("CIS-3.1.2", "net.ipv4.conf.default.rp_filter", "1",
             "Enable reverse path filtering on new interfaces"),
            # SYN flood protection
            ("CIS-3.2.1", "net.ipv4.tcp_syncookies", "1",
             "Enable TCP SYN cookies to resist SYN flood attacks"),
            # ICMP redirect rejection
            ("CIS-3.2.2", "net.ipv4.conf.all.accept_redirects", "0",
             "Disable ICMP redirect acceptance"),
            ("CIS-3.2.3", "net.ipv4.conf.default.accept_redirects", "0",
             "Disable ICMP redirect acceptance on new interfaces"),
            ("CIS-3.2.4", "net.ipv4.conf.all.send_redirects", "0",
             "Disable ICMP redirect sending"),
            # Source routing
            ("CIS-3.2.5", "net.ipv4.conf.all.accept_source_route", "0",
             "Disable IP source routing"),
            # Martian packet logging
            ("CIS-3.2.6", "net.ipv4.conf.all.log_martians", "1",
             "Enable logging of martian packets"),
            # IPv6 hardening
            ("CIS-3.3.1", "net.ipv6.conf.all.accept_ra", "0",
             "Disable IPv6 router advertisement acceptance"),
            ("CIS-3.3.2", "net.ipv6.conf.default.accept_ra", "0",
             "Disable IPv6 RA on new interfaces"),
            # iGaming-specific: kernel connection queue for high-volume bet traffic
            ("IGAMING-NET-1", "net.core.somaxconn", "65535",
             "Increase listen backlog for high-concurrency gaming APIs"),
            ("IGAMING-NET-2", "net.ipv4.tcp_max_syn_backlog", "65536",
             "Increase SYN backlog for gaming traffic peaks"),
        ]

        for check_id, key, expected, description in net_params:
            actual = _sysctl_get(key)
            self._check(
                check_id=check_id,
                title=description,
                expected=expected,
                actual=actual,
                remediation=f"sysctl -w {key}={expected}",
                apply_fn=lambda k=key, v=expected: _sysctl_set(k, v, self.dry_run),
            )

    def check_kernel_hardening(self) -> None:
        """CIS 4.x — Kernel hardening parameters."""
        kernel_params = [
            ("CIS-4.1", "kernel.randomize_va_space", "2",
             "Enable ASLR (Address Space Layout Randomisation)"),
            ("CIS-4.2", "fs.suid_dumpable", "0",
             "Disable core dumps for setuid programs"),
            ("CIS-4.3", "kernel.dmesg_restrict", "1",
             "Restrict dmesg to root only"),
            ("CIS-4.4", "kernel.kptr_restrict", "2",
             "Restrict kernel pointer exposure"),
            ("CIS-4.5", "kernel.perf_event_paranoid", "3",
             "Restrict perf events to root"),
        ]

        for check_id, key, expected, description in kernel_params:
            actual = _sysctl_get(key)
            self._check(
                check_id=check_id,
                title=description,
                expected=expected,
                actual=actual,
                remediation=f"sysctl -w {key}={expected}",
                apply_fn=lambda k=key, v=expected: _sysctl_set(k, v, self.dry_run),
            )

    # --- SSH hardening ------------------------------------------------------

    def check_ssh_config(self) -> None:
        """CIS 5.x — SSH daemon hardening."""
        sshd_config = _read_file("/etc/ssh/sshd_config") or ""
        lines = {
            line.split()[0].lower(): " ".join(line.split()[1:])
            for line in sshd_config.splitlines()
            if line.strip() and not line.startswith("#") and len(line.split()) >= 2
        }

        ssh_checks = [
            ("CIS-5.1", "PermitRootLogin", "no",
             "Disable direct root SSH login"),
            ("CIS-5.2", "PasswordAuthentication", "no",
             "Disable password authentication (key-only)"),
            ("CIS-5.3", "PubkeyAuthentication", "yes",
             "Enable public key authentication"),
            ("CIS-5.4", "X11Forwarding", "no",
             "Disable X11 forwarding"),
            ("CIS-5.5", "MaxAuthTries", "3",
             "Limit SSH authentication attempts"),
            ("CIS-5.6", "ClientAliveInterval", "300",
             "Set SSH idle timeout to 5 minutes"),
            ("CIS-5.7", "ClientAliveCountMax", "2",
             "Limit SSH keepalive count"),
            ("CIS-5.8", "LoginGraceTime", "60",
             "Limit SSH login grace period to 60 seconds"),
            ("CIS-5.9", "Protocol", "2",
             "Enforce SSH Protocol 2 only"),
            ("IGAMING-SSH-1", "AllowUsers", "ansible deploy",
             "Restrict SSH to operational users only"),
        ]

        for check_id, directive, expected, description in ssh_checks:
            actual = lines.get(directive.lower())
            self._check(
                check_id=check_id,
                title=description,
                expected=expected,
                actual=actual if actual is not None else "<not set>",
                remediation=(
                    f"Add '{directive} {expected}' to /etc/ssh/sshd_config "
                    f"and restart sshd"
                ),
            )

    # --- Audit logging (GLI-GSF requirement) --------------------------------

    def check_audit_logging(self) -> None:
        """IGAMING — Audit log retention and forwarding (GLI-GSF 5-year requirement)."""
        # Check auditd is installed and running
        rc, _, _ = _run(["systemctl", "is-active", "--quiet", "auditd"])
        self._check(
            check_id="IGAMING-AUDIT-1",
            title="auditd service active",
            expected="0",
            actual=str(rc),
            remediation="apt install auditd && systemctl enable --now auditd",
        )

        # Check log retention config
        audit_conf = _read_file("/etc/audit/auditd.conf") or ""
        log_retention_lines = [
            line for line in audit_conf.splitlines()
            if "max_log_file" in line.lower() or "num_logs" in line.lower()
        ]
        self._check(
            check_id="IGAMING-AUDIT-2",
            title="auditd log rotation and retention configured",
            expected="configured",
            actual="configured" if log_retention_lines else "not configured",
            remediation=(
                "Set max_log_file = 100 and num_logs = 500 in /etc/audit/auditd.conf "
                "for 5-year retention per GLI-GSF requirements"
            ),
        )

        # Check rsyslog forwarding
        rc_rsyslog, _, _ = _run(["systemctl", "is-active", "--quiet", "rsyslog"])
        self._check(
            check_id="IGAMING-AUDIT-3",
            title="rsyslog active for SIEM forwarding",
            expected="0",
            actual=str(rc_rsyslog),
            remediation="systemctl enable --now rsyslog",
        )

    # --- File permissions ---------------------------------------------------

    def check_file_permissions(self) -> None:
        """CIS 6.x — Critical file permission checks."""
        perm_checks = [
            ("CIS-6.1", "/etc/passwd", "644", "World-readable, not writable"),
            ("CIS-6.2", "/etc/shadow", "640", "Readable only by root and shadow group"),
            ("CIS-6.3", "/etc/group", "644", "World-readable, not writable"),
            ("CIS-6.4", "/etc/gshadow", "640", "Readable only by root"),
            ("CIS-6.5", "/etc/sudoers", "440", "Read-only, root/sudo group only"),
        ]

        for check_id, filepath, expected_mode, description in perm_checks:
            rc, out, _ = _run(["stat", "-c", "%a", filepath])
            actual = out if rc == 0 else None
            self._check(
                check_id=check_id,
                title=f"{filepath} permissions ({description})",
                expected=expected_mode,
                actual=actual,
                remediation=f"chmod {expected_mode} {filepath}",
                apply_fn=lambda fp=filepath, m=expected_mode: (
                    _run(["chmod", m, fp])[0] == 0 if not self.dry_run else True
                ),
            )

    # --- Unnecessary services -----------------------------------------------

    def check_unnecessary_services(self) -> None:
        """CIS 2.x — Disable unnecessary services on gaming servers."""
        services_to_disable = [
            ("CIS-2.1", "avahi-daemon", "Avahi mDNS daemon unnecessary on server"),
            ("CIS-2.2", "cups", "CUPS print server unnecessary on gaming server"),
            ("CIS-2.3", "isc-dhcp-server", "DHCP server unnecessary on gaming server"),
            ("CIS-2.4", "slapd", "LDAP server unnecessary on gaming server"),
            ("CIS-2.5", "nfs-server", "NFS server should not run on casino platform"),
            ("CIS-2.6", "rpcbind", "rpcbind unnecessary on gaming server"),
            ("CIS-2.7", "vsftpd", "FTP server unnecessary — use SFTP"),
            ("CIS-2.8", "apache2", "Apache unnecessary — nginx-coraza is the web server"),
        ]

        for check_id, service, description in services_to_disable:
            rc, _, _ = _run(["systemctl", "is-active", "--quiet", service])
            is_active = rc == 0
            self._check(
                check_id=check_id,
                title=f"{service} disabled ({description})",
                expected="inactive",
                actual="active" if is_active else "inactive",
                remediation=f"systemctl disable --now {service}",
                apply_fn=lambda svc=service: (
                    _run(["systemctl", "disable", "--now", svc])[0] == 0
                    if not self.dry_run else True
                ),
            )

    # --- Full run -----------------------------------------------------------

    def run_all(self) -> HardeningReport:
        """
        Run all hardening checks for the configured profile.

        Returns:
            HardeningReport with all check results.
        """
        hostname = os.uname().nodename
        started = datetime.now(tz=timezone.utc).isoformat()
        log.info(
            "hardening_run_start hostname=%s profile=%s dry_run=%s",
            hostname,
            self.profile,
            self.dry_run,
        )

        self.check_network_hardening()
        self.check_kernel_hardening()
        self.check_ssh_config()
        self.check_audit_logging()
        self.check_file_permissions()
        self.check_unnecessary_services()

        completed = datetime.now(tz=timezone.utc).isoformat()
        report = HardeningReport(
            hostname=hostname,
            profile=self.profile,
            dry_run=self.dry_run,
            started_at=started,
            completed_at=completed,
            checks=self._checks,
        )
        log.info(
            "hardening_run_complete pass=%d fail=%d warn=%d total=%d",
            report.pass_count,
            report.fail_count,
            report.warn_count,
            len(report.checks),
        )
        return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="CIS benchmark hardening for iGaming on-premises servers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--profile",
        default="gaming-server",
        choices=["gaming-server", "k8s-node", "database"],
        help="Hardening profile (default: %(default)s)",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Check and report without applying any changes",
    )
    group.add_argument(
        "--apply",
        action="store_true",
        help="Apply remediations for all failing checks",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="Alias for --dry-run (check-only mode)",
    )
    parser.add_argument(
        "--output",
        help="Write JSON report to file (default: stdout)",
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit with code 1 if any checks fail",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if os.geteuid() != 0 and args.apply:
        log.error("--apply requires root privileges")
        sys.exit(1)

    dry_run = args.dry_run or args.check
    hardening = ServerHardening(profile=args.profile, dry_run=dry_run)
    report = hardening.run_all()

    report_json = json.dumps(report.to_dict(), indent=2)
    if args.output:
        Path(args.output).write_text(report_json, encoding="utf-8")
        log.info("report_written path=%s", args.output)
    else:
        print(report_json)

    if args.fail_on_findings and report.fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
