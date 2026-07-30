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
AcmeToCasino - On-Premise Firewall Manager
Automated firewall rule management via SOAR integration.

Supports both iptables and nftables, auto-detecting which backend
is available at runtime. Uses a dedicated SOAR_BLOCK chain/set so
all SOAR-managed rules are isolated from manually configured rules.

Usage:
    firewall_manager.py block-ip   --ip 1.2.3.4 [--comment "fraud"]
    firewall_manager.py unblock-ip --ip 1.2.3.4
    firewall_manager.py block-cidr --cidr 10.0.0.0/8
    firewall_manager.py rate-limit --ip 1.2.3.4 --rate 10 --burst 20
    firewall_manager.py list
    firewall_manager.py flush
    firewall_manager.py status
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import logging.handlers
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SOAR_CHAIN = "SOAR_BLOCK"
SOAR_IPSET = "soar_blocklist"
SOAR_TABLE = "soar"          # nftables table name
SOAR_LOG_PREFIX = "SOAR_DROP: "
AUDIT_LOG_PATH = Path("/var/log/acmetocasino/firewall_audit.jsonl")
DRY_RUN_AUDIT_LOG_PATH = Path("/tmp/firewall_audit_dryrun.jsonl")

# nftables family
NFT_FAMILY = "inet"

# iptables chain positions
IPTABLES_JUMP_RULE_COMMENT = "soar-managed-jump"

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(dry_run: bool = False) -> tuple[logging.Logger, Path]:
    """Configure structured logging to syslog and a JSON audit file."""
    logger = logging.getLogger("firewall_manager")
    logger.setLevel(logging.DEBUG)

    # Syslog handler
    try:
        syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
    except (OSError, AttributeError):
        syslog_handler = logging.handlers.SysLogHandler()
    syslog_handler.setFormatter(
        logging.Formatter("firewall_manager[%(process)d]: %(levelname)s %(message)s")
    )
    logger.addHandler(syslog_handler)

    # Stderr handler for interactive use
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(stderr_handler)

    audit_path = DRY_RUN_AUDIT_LOG_PATH if dry_run else AUDIT_LOG_PATH
    if not dry_run:
        audit_path.parent.mkdir(parents=True, exist_ok=True)

    return logger, audit_path


def _audit(audit_path: Path, action: str, details: dict[str, Any]) -> None:
    """Append a structured JSON audit record."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "pid": os.getpid(),
        **details,
    }
    try:
        with audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass  # audit failure must not interrupt the security action


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

class Backend:
    IPTABLES = "iptables"
    NFTABLES = "nftables"


def detect_backend() -> str:
    """Return 'nftables' if nft is available and functional, else 'iptables'."""
    if shutil.which("nft"):
        result = subprocess.run(
            ["nft", "list", "tables"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return Backend.NFTABLES
    if shutil.which("iptables"):
        return Backend.IPTABLES
    raise RuntimeError(
        "Neither nftables (nft) nor iptables is available on this system."
    )


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], dry_run: bool, logger: logging.Logger) -> subprocess.CompletedProcess[str]:
    """Execute a system command, respecting dry-run mode."""
    logger.debug("CMD: %s", " ".join(cmd))
    if dry_run:
        logger.info("[DRY-RUN] Would run: %s", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        logger.error(
            "Command failed (rc=%d): %s\nstdout: %s\nstderr: %s",
            result.returncode, " ".join(cmd), result.stdout.strip(), result.stderr.strip()
        )
    return result


def _validate_ip(ip: str) -> str:
    """Return the normalised IP string or raise ValueError."""
    return str(ipaddress.ip_address(ip))


def _validate_cidr(cidr: str) -> str:
    """Return the normalised CIDR string (strict=False) or raise ValueError."""
    return str(ipaddress.ip_network(cidr, strict=False))


# ---------------------------------------------------------------------------
# iptables backend
# ---------------------------------------------------------------------------

class IptablesBackend:
    """Manages SOAR firewall rules via iptables + ipset."""

    def __init__(self, logger: logging.Logger, audit_path: Path, dry_run: bool = False) -> None:
        self.logger = logger
        self.audit_path = audit_path
        self.dry_run = dry_run
        self._ensure_chain()
        self._ensure_ipset()

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return _run(cmd, self.dry_run, self.logger)

    def _ensure_chain(self) -> None:
        """Create SOAR_BLOCK chain and install a jump rule if missing."""
        # Create the chain if it does not exist
        check = subprocess.run(
            ["iptables", "-n", "--list", SOAR_CHAIN],
            capture_output=True, text=True
        )
        if check.returncode != 0:
            self.logger.info("Creating iptables chain %s", SOAR_CHAIN)
            self._run(["iptables", "-N", SOAR_CHAIN])

        # Install jump from INPUT if not present
        check_jump = subprocess.run(
            ["iptables", "-C", "INPUT", "-j", SOAR_CHAIN],
            capture_output=True, text=True
        )
        if check_jump.returncode != 0:
            self._run([
                "iptables", "-I", "INPUT", "1",
                "-m", "comment", "--comment", IPTABLES_JUMP_RULE_COMMENT,
                "-j", SOAR_CHAIN,
            ])

        # Install jump from FORWARD if not present
        check_fwd = subprocess.run(
            ["iptables", "-C", "FORWARD", "-j", SOAR_CHAIN],
            capture_output=True, text=True
        )
        if check_fwd.returncode != 0:
            self._run([
                "iptables", "-I", "FORWARD", "1",
                "-m", "comment", "--comment", IPTABLES_JUMP_RULE_COMMENT,
                "-j", SOAR_CHAIN,
            ])

    def _ensure_ipset(self) -> None:
        """Create the ipset if it does not exist."""
        if not shutil.which("ipset"):
            self.logger.warning("ipset not found; large blocklists will use individual rules")
            return
        check = subprocess.run(
            ["ipset", "list", "-n", SOAR_IPSET],
            capture_output=True, text=True
        )
        if check.returncode != 0:
            self.logger.info("Creating ipset %s", SOAR_IPSET)
            self._run(["ipset", "create", SOAR_IPSET, "hash:net", "maxelem", "1048576"])
            # Add a single iptables rule that matches the entire set
            self._run([
                "iptables", "-A", SOAR_CHAIN,
                "-m", "set", "--match-set", SOAR_IPSET, "src",
                "-j", "DROP",
            ])

    def _ipset_available(self) -> bool:
        return shutil.which("ipset") is not None and subprocess.run(
            ["ipset", "list", "-n", SOAR_IPSET],
            capture_output=True
        ).returncode == 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def block_ip(self, ip: str, comment: str = "") -> bool:
        ip = _validate_ip(ip)
        if self._ipset_available():
            result = self._run(["ipset", "add", SOAR_IPSET, ip, "-exist"])
        else:
            result = self._run([
                "iptables", "-I", SOAR_CHAIN, "1",
                "-s", ip, "-j", "DROP",
                "-m", "comment", "--comment", f"soar:{comment or 'blocked'}",
            ])
        _audit(self.audit_path, "block_ip", {"ip": ip, "comment": comment, "backend": "iptables"})
        self.logger.info("Blocked IP %s (iptables)", ip)
        return result.returncode == 0

    def unblock_ip(self, ip: str) -> bool:
        ip = _validate_ip(ip)
        if self._ipset_available():
            result = self._run(["ipset", "del", SOAR_IPSET, ip, "-exist"])
        else:
            result = self._run([
                "iptables", "-D", SOAR_CHAIN,
                "-s", ip, "-j", "DROP",
            ])
        _audit(self.audit_path, "unblock_ip", {"ip": ip, "backend": "iptables"})
        self.logger.info("Unblocked IP %s (iptables)", ip)
        return result.returncode == 0

    def block_cidr(self, cidr: str, comment: str = "") -> bool:
        cidr = _validate_cidr(cidr)
        if self._ipset_available():
            result = self._run(["ipset", "add", SOAR_IPSET, cidr, "-exist"])
        else:
            result = self._run([
                "iptables", "-I", SOAR_CHAIN, "1",
                "-s", cidr, "-j", "DROP",
                "-m", "comment", "--comment", f"soar:{comment or 'cidr-block'}",
            ])
        _audit(self.audit_path, "block_cidr", {"cidr": cidr, "comment": comment, "backend": "iptables"})
        self.logger.info("Blocked CIDR %s (iptables)", cidr)
        return result.returncode == 0

    def create_rate_limit(self, ip: str, rate: int, burst: int) -> bool:
        """Insert a hashlimit rule that drops traffic exceeding rate/s from ip."""
        ip = _validate_ip(ip)
        result = self._run([
            "iptables", "-I", SOAR_CHAIN, "1",
            "-s", ip,
            "-m", "hashlimit",
            "--hashlimit-mode", "srcip",
            "--hashlimit-name", f"soar_rl_{ip.replace('.', '_')}",
            "--hashlimit-above", f"{rate}/sec",
            "--hashlimit-burst", str(burst),
            "-j", "DROP",
            "-m", "comment", "--comment", f"soar:rate-limit:{ip}",
        ])
        _audit(self.audit_path, "rate_limit", {"ip": ip, "rate": rate, "burst": burst, "backend": "iptables"})
        self.logger.info("Rate-limited IP %s at %d/s burst %d (iptables)", ip, rate, burst)
        return result.returncode == 0

    def list_blocked(self) -> dict[str, Any]:
        chain_result = subprocess.run(
            ["iptables", "-n", "-L", SOAR_CHAIN, "-v", "--line-numbers"],
            capture_output=True, text=True
        )
        ipset_result = subprocess.CompletedProcess([], returncode=1, stdout="", stderr="")
        if self._ipset_available():
            ipset_result = subprocess.run(
                ["ipset", "list", SOAR_IPSET],
                capture_output=True, text=True
            )
        return {
            "backend": "iptables",
            "chain_rules": chain_result.stdout,
            "ipset_members": ipset_result.stdout if ipset_result.returncode == 0 else "",
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def flush_soar_rules(self) -> bool:
        """Remove all SOAR-managed rules without touching other chains."""
        r1 = self._run(["iptables", "-F", SOAR_CHAIN])
        if self._ipset_available():
            self._run(["ipset", "flush", SOAR_IPSET])
            # Re-add the ipset match rule after flush
            self._run([
                "iptables", "-A", SOAR_CHAIN,
                "-m", "set", "--match-set", SOAR_IPSET, "src",
                "-j", "DROP",
            ])
        _audit(self.audit_path, "flush", {"backend": "iptables"})
        self.logger.info("Flushed all SOAR rules (iptables)")
        return r1.returncode == 0


# ---------------------------------------------------------------------------
# nftables backend
# ---------------------------------------------------------------------------

class NftablesBackend:
    """Manages SOAR firewall rules via nftables."""

    def __init__(self, logger: logging.Logger, audit_path: Path, dry_run: bool = False) -> None:
        self.logger = logger
        self.audit_path = audit_path
        self.dry_run = dry_run
        self._ensure_table()

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return _run(cmd, self.dry_run, self.logger)

    def _nft(self, *args: str) -> subprocess.CompletedProcess[str]:
        return self._run(["nft", *args])

    def _table_exists(self) -> bool:
        result = subprocess.run(
            ["nft", "list", "table", NFT_FAMILY, SOAR_TABLE],
            capture_output=True, text=True
        )
        return result.returncode == 0

    def _ensure_table(self) -> None:
        """Create the SOAR nftables table and its sets/chains if absent."""
        if self._table_exists() and not self.dry_run:
            return

        self.logger.info("Initialising nftables SOAR table")
        nft_script = f"""
add table {NFT_FAMILY} {SOAR_TABLE}

add set {NFT_FAMILY} {SOAR_TABLE} blocklist {{
    type ipv4_addr
    flags interval
    auto-merge
    size 1048576
}}

add set {NFT_FAMILY} {SOAR_TABLE} blocklist6 {{
    type ipv6_addr
    flags interval
    auto-merge
    size 1048576
}}

add chain {NFT_FAMILY} {SOAR_TABLE} input {{
    type filter hook input priority -10 ; policy accept
    ip saddr @blocklist drop
    ip6 saddr @blocklist6 drop
}}

add chain {NFT_FAMILY} {SOAR_TABLE} forward {{
    type filter hook forward priority -10 ; policy accept
    ip saddr @blocklist drop
    ip6 saddr @blocklist6 drop
}}
"""
        self._run(["nft", "-f", "-"], )
        if not self.dry_run:
            proc = subprocess.run(
                ["nft", "-f", "-"],
                input=nft_script, text=True,
                capture_output=True, timeout=30
            )
            if proc.returncode != 0:
                self.logger.error("nft init failed: %s", proc.stderr.strip())

    def _set_for_addr(self, addr: str) -> str:
        """Return the correct nftables set name based on address family."""
        try:
            ipaddress.IPv4Network(addr, strict=False)
            return "blocklist"
        except ValueError:
            return "blocklist6"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def block_ip(self, ip: str, comment: str = "") -> bool:
        ip = _validate_ip(ip)
        set_name = self._set_for_addr(ip)
        result = self._nft("add", "element", NFT_FAMILY, SOAR_TABLE, set_name, f"{{ {ip} }}")
        _audit(self.audit_path, "block_ip", {"ip": ip, "comment": comment, "backend": "nftables"})
        self.logger.info("Blocked IP %s (nftables)", ip)
        return result.returncode == 0

    def unblock_ip(self, ip: str) -> bool:
        ip = _validate_ip(ip)
        set_name = self._set_for_addr(ip)
        result = self._nft("delete", "element", NFT_FAMILY, SOAR_TABLE, set_name, f"{{ {ip} }}")
        _audit(self.audit_path, "unblock_ip", {"ip": ip, "backend": "nftables"})
        self.logger.info("Unblocked IP %s (nftables)", ip)
        return result.returncode == 0

    def block_cidr(self, cidr: str, comment: str = "") -> bool:
        cidr = _validate_cidr(cidr)
        set_name = self._set_for_addr(cidr)
        result = self._nft("add", "element", NFT_FAMILY, SOAR_TABLE, set_name, f"{{ {cidr} }}")
        _audit(self.audit_path, "block_cidr", {"cidr": cidr, "comment": comment, "backend": "nftables"})
        self.logger.info("Blocked CIDR %s (nftables)", cidr)
        return result.returncode == 0

    def create_rate_limit(self, ip: str, rate: int, burst: int) -> bool:
        """Add a named rate-limit rule for a specific source IP."""
        ip = _validate_ip(ip)
        # nftables rate limiting via a named meter (dynamic set)
        rule_handle_name = f"rl_{ip.replace('.', '_').replace(':', '_')}"
        nft_rule = (
            f"add rule {NFT_FAMILY} {SOAR_TABLE} input "
            f"ip saddr {ip} "
            f"meter {rule_handle_name} {{ ip saddr limit rate over {rate}/second burst {burst} packets }} "
            f"drop"
        )
        result = self._run(["nft", "-f", "-"])
        if not self.dry_run:
            proc = subprocess.run(
                ["nft", "-f", "-"],
                input=nft_rule, text=True,
                capture_output=True, timeout=30
            )
            if proc.returncode != 0:
                self.logger.error("Rate-limit rule failed: %s", proc.stderr.strip())
                return False
        _audit(self.audit_path, "rate_limit", {"ip": ip, "rate": rate, "burst": burst, "backend": "nftables"})
        self.logger.info("Rate-limited IP %s at %d/s burst %d (nftables)", ip, rate, burst)
        return True

    def list_blocked(self) -> dict[str, Any]:
        result = subprocess.run(
            ["nft", "list", "table", NFT_FAMILY, SOAR_TABLE],
            capture_output=True, text=True
        )
        return {
            "backend": "nftables",
            "table_dump": result.stdout if result.returncode == 0 else result.stderr,
            "ts": datetime.now(timezone.utc).isoformat(),
        }

    def flush_soar_rules(self) -> bool:
        """Flush all elements from both block sets."""
        r1 = self._nft("flush", "set", NFT_FAMILY, SOAR_TABLE, "blocklist")
        r2 = self._nft("flush", "set", NFT_FAMILY, SOAR_TABLE, "blocklist6")
        _audit(self.audit_path, "flush", {"backend": "nftables"})
        self.logger.info("Flushed all SOAR rules (nftables)")
        return r1.returncode == 0 and r2.returncode == 0


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_backend(
    logger: logging.Logger,
    audit_path: Path,
    dry_run: bool = False,
    force_backend: Optional[str] = None,
) -> IptablesBackend | NftablesBackend:
    backend_name = force_backend or detect_backend()
    logger.info("Using firewall backend: %s", backend_name)
    if backend_name == Backend.NFTABLES:
        return NftablesBackend(logger, audit_path, dry_run)
    return IptablesBackend(logger, audit_path, dry_run)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="firewall_manager.py",
        description="AcmeToCasino SOAR on-premise firewall manager",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print commands without executing them",
    )
    parser.add_argument(
        "--backend", choices=["iptables", "nftables"],
        help="Force a specific firewall backend (default: auto-detect)",
    )
    parser.add_argument(
        "--output", choices=["text", "json"], default="text",
        help="Output format for list/status commands",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # block-ip
    p_block = sub.add_parser("block-ip", help="Block a single IP address")
    p_block.add_argument("--ip", required=True, help="IPv4 or IPv6 address")
    p_block.add_argument("--comment", default="", help="Reason / incident ID")

    # unblock-ip
    p_unblock = sub.add_parser("unblock-ip", help="Remove a single IP block")
    p_unblock.add_argument("--ip", required=True, help="IPv4 or IPv6 address")

    # block-cidr
    p_cidr = sub.add_parser("block-cidr", help="Block an entire CIDR range")
    p_cidr.add_argument("--cidr", required=True, help="CIDR notation e.g. 203.0.113.0/24")
    p_cidr.add_argument("--comment", default="", help="Reason / incident ID")

    # rate-limit
    p_rl = sub.add_parser("rate-limit", help="Apply per-IP rate limiting")
    p_rl.add_argument("--ip", required=True, help="IPv4 or IPv6 address")
    p_rl.add_argument("--rate", type=int, required=True, help="Packets per second threshold")
    p_rl.add_argument("--burst", type=int, default=50, help="Burst size (default 50)")

    # list
    sub.add_parser("list", help="List all SOAR-managed block rules")

    # flush
    sub.add_parser("flush", help="Remove all SOAR-managed rules")

    # status
    sub.add_parser("status", help="Show backend status and rule counts")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    logger, audit_path = _setup_logging(dry_run=args.dry_run)

    if os.geteuid() != 0 and not args.dry_run:
        logger.error("This script must be run as root (or with sudo).")
        return 1

    try:
        fw = get_backend(logger, audit_path, dry_run=args.dry_run, force_backend=args.backend)
    except RuntimeError as exc:
        logger.error("%s", exc)
        return 1

    success = True

    if args.command == "block-ip":
        success = fw.block_ip(args.ip, comment=args.comment)

    elif args.command == "unblock-ip":
        success = fw.unblock_ip(args.ip)

    elif args.command == "block-cidr":
        success = fw.block_cidr(args.cidr, comment=args.comment)

    elif args.command == "rate-limit":
        success = fw.create_rate_limit(args.ip, args.rate, args.burst)

    elif args.command == "list":
        data = fw.list_blocked()
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            for k, v in data.items():
                print(f"--- {k} ---\n{v}")

    elif args.command == "flush":
        confirm = input("This will flush ALL SOAR rules. Type 'yes' to confirm: ")
        if confirm.strip().lower() == "yes":
            success = fw.flush_soar_rules()
        else:
            print("Aborted.")

    elif args.command == "status":
        backend_name = type(fw).__name__
        data = {
            "backend": backend_name,
            "dry_run": args.dry_run,
            "audit_log": str(audit_path),
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        if args.output == "json":
            print(json.dumps(data, indent=2))
        else:
            for k, v in data.items():
                print(f"{k}: {v}")

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
