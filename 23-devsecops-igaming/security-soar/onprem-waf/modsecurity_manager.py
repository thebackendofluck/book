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
AcmeToCasino - ModSecurity Rule Manager
Dynamically manages ModSecurity v3 (libmodsecurity) rules via the SOAR platform.

Supports:
  - Adding / removing custom rules at runtime via nginx config reload
  - Virtual patching for zero-day vulnerabilities
  - OWASP CRS paranoia level management
  - iGaming-specific fraud pattern rules
  - Rule staging (audit-only before enforcement)

Usage:
    modsecurity_manager.py add-rule   --id 9000001 --msg "Block fraud IP" \\
                                       --vars "REMOTE_ADDR" --operator "@ipMatch 1.2.3.4" \\
                                       --action block
    modsecurity_manager.py remove-rule --id 9000001
    modsecurity_manager.py virtual-patch --cve CVE-2024-12345 \\
                                          --target "/api/v1/login" --operator "@rx malicious"
    modsecurity_manager.py list-rules
    modsecurity_manager.py crs-paranoia --level 2
    modsecurity_manager.py reload
    modsecurity_manager.py audit-only  --id 9000001
    modsecurity_manager.py enforce     --id 9000001
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import logging.handlers
import os
import re
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

MODSEC_RULES_DIR = Path("/etc/nginx/modsec/soar_rules")
SOAR_RULES_FILE = MODSEC_RULES_DIR / "soar_dynamic.conf"
VIRTUAL_PATCH_FILE = MODSEC_RULES_DIR / "virtual_patches.conf"
IGAMING_RULES_FILE = MODSEC_RULES_DIR / "igaming.conf"
MODSEC_CONF = Path("/etc/nginx/modsec/modsecurity.conf")
CRS_SETUP_CONF = Path("/etc/nginx/modsec/crs-setup.conf")
NGINX_BIN = "/usr/sbin/nginx"
RULE_REGISTRY = MODSEC_RULES_DIR / ".rule_registry.json"
AUDIT_LOG = Path("/var/log/acmetocasino/modsec_manager_audit.jsonl")

# Rule ID namespace allocation
RULE_ID_MIN = 9_000_000
RULE_ID_MAX = 9_999_999
VIRTUAL_PATCH_ID_MIN = 9_500_000
VIRTUAL_PATCH_ID_MAX = 9_599_999

# Severity mapping
SEVERITY = {
    "critical": "CRITICAL",
    "error": "ERROR",
    "warning": "WARNING",
    "notice": "NOTICE",
    "info": "INFO",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    logger = logging.getLogger("modsecurity_manager")
    logger.setLevel(logging.DEBUG)

    try:
        sh = logging.handlers.SysLogHandler(address="/dev/log")
    except (OSError, AttributeError):
        sh = logging.handlers.SysLogHandler()
    sh.setFormatter(logging.Formatter("modsecurity_manager[%(process)d]: %(levelname)s %(message)s"))
    logger.addHandler(sh)

    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(ch)

    return logger


def _audit(action: str, details: dict[str, Any]) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "pid": os.getpid(),
        **details,
    }
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Rule registry (persists rule metadata as JSON)
# ---------------------------------------------------------------------------

class RuleRegistry:
    """JSON-backed registry of all SOAR-managed ModSecurity rules."""

    def __init__(self) -> None:
        RULE_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if RULE_REGISTRY.exists():
            try:
                with RULE_REGISTRY.open(encoding="utf-8") as fh:
                    self._data = json.load(fh)
            except (json.JSONDecodeError, OSError):
                self._data = {}

    def _save(self) -> None:
        with RULE_REGISTRY.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2)

    def add(self, rule_id: int, metadata: dict[str, Any]) -> None:
        self._data[str(rule_id)] = {
            **metadata,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "id": rule_id,
        }
        self._save()

    def remove(self, rule_id: int) -> bool:
        key = str(rule_id)
        if key not in self._data:
            return False
        del self._data[key]
        self._save()
        return True

    def get(self, rule_id: int) -> Optional[dict[str, Any]]:
        return self._data.get(str(rule_id))

    def exists(self, rule_id: int) -> bool:
        return str(rule_id) in self._data

    def all_rules(self) -> list[dict[str, Any]]:
        return list(self._data.values())

    def next_available_id(self, start: int = RULE_ID_MIN, end: int = RULE_ID_MAX) -> int:
        used = {int(k) for k in self._data.keys()}
        for rid in range(start, end + 1):
            if rid not in used:
                return rid
        raise RuntimeError(f"No available rule IDs in range {start}-{end}")


# ---------------------------------------------------------------------------
# Rule file management
# ---------------------------------------------------------------------------

def _ensure_rules_dir() -> None:
    MODSEC_RULES_DIR.mkdir(parents=True, exist_ok=True)
    if not SOAR_RULES_FILE.exists():
        SOAR_RULES_FILE.write_text(
            "# AcmeToCasino SOAR Dynamic Rules\n"
            "# Managed by modsecurity_manager.py - do not edit by hand\n\n",
            encoding="utf-8",
        )
    if not VIRTUAL_PATCH_FILE.exists():
        VIRTUAL_PATCH_FILE.write_text(
            "# AcmeToCasino Virtual Patches\n"
            "# Managed by modsecurity_manager.py - do not edit by hand\n\n",
            encoding="utf-8",
        )


def _read_rules_file(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _write_rules_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via temp file
    tmp = path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _append_rule(path: Path, rule_text: str) -> None:
    content = _read_rules_file(path)
    content += rule_text + "\n"
    _write_rules_file(path, content)


def _remove_rule_from_file(path: Path, rule_id: int) -> bool:
    """Remove a rule block identified by its ID comment anchor."""
    if not path.exists():
        return False
    content = path.read_text(encoding="utf-8")
    # Match from the # SOAR-RULE-BEGIN comment to the # SOAR-RULE-END comment
    pattern = rf"# SOAR-RULE-BEGIN:{rule_id}\n.*?# SOAR-RULE-END:{rule_id}\n?"
    new_content, count = re.subn(pattern, "", content, flags=re.DOTALL)
    if count == 0:
        return False
    _write_rules_file(path, new_content)
    return True


# ---------------------------------------------------------------------------
# ModSecurity rule builder
# ---------------------------------------------------------------------------

def _build_rule(
    rule_id: int,
    msg: str,
    variables: str,
    operator: str,
    action: str = "block",
    severity: str = "WARNING",
    tags: Optional[list[str]] = None,
    audit_only: bool = False,
    phase: int = 2,
) -> str:
    """Build a ModSecurity SecRule directive string."""
    tags = tags or []
    tag_str = "".join(f',\\\n    tag:"{t}"' for t in tags)

    if audit_only:
        action_directive = 'log,auditlog,pass'
    elif action == "block":
        action_directive = 'deny,status:403,log,auditlog'
    elif action == "redirect":
        action_directive = 'redirect:https://acmetocasino.com/blocked,log,auditlog'
    elif action == "drop":
        action_directive = 'drop,log,auditlog'
    else:
        action_directive = 'log,auditlog,pass'

    rule = (
        f'# SOAR-RULE-BEGIN:{rule_id}\n'
        f'SecRule {variables} "{operator}" \\\n'
        f'    "id:{rule_id},\\\n'
        f'    phase:{phase},\\\n'
        f'    {action_directive},\\\n'
        f'    msg:\\"{msg}\\",\\\n'
        f'    severity:{severity},\\\n'
        f'    logdata:\\"%{{MATCHED_VAR_NAME}}=%{{MATCHED_VAR}}\\"{tag_str}"\n'
        f'# SOAR-RULE-END:{rule_id}\n'
    )
    return rule


# ---------------------------------------------------------------------------
# Main manager class
# ---------------------------------------------------------------------------

class ModSecurityManager:
    """High-level interface for SOAR-driven ModSecurity rule management."""

    def __init__(self, logger: logging.Logger, dry_run: bool = False) -> None:
        self.logger = logger
        self.dry_run = dry_run
        self.registry = RuleRegistry()
        if not dry_run:
            _ensure_rules_dir()

    # ------------------------------------------------------------------
    # nginx interaction
    # ------------------------------------------------------------------

    def _test_nginx(self) -> bool:
        result = subprocess.run(
            [NGINX_BIN, "-t"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            self.logger.error("nginx config test failed:\n%s", result.stderr)
            return False
        return True

    def reload_nginx(self) -> bool:
        """Test nginx config then perform a graceful reload (zero-downtime)."""
        if self.dry_run:
            self.logger.info("[DRY-RUN] Would reload nginx")
            return True
        if not self._test_nginx():
            return False
        result = subprocess.run(
            [NGINX_BIN, "-s", "reload"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode != 0:
            self.logger.error("nginx reload failed: %s", result.stderr)
            return False
        self.logger.info("nginx reloaded successfully")
        _audit("nginx_reload", {"status": "ok"})
        return True

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    def add_rule(
        self,
        rule_id: Optional[int],
        msg: str,
        variables: str,
        operator: str,
        action: str = "block",
        severity: str = "WARNING",
        tags: Optional[list[str]] = None,
        audit_only: bool = False,
        phase: int = 2,
        auto_reload: bool = True,
    ) -> int:
        """Add a custom ModSecurity rule. Returns the assigned rule ID."""
        tags = tags or []

        if rule_id is None:
            rule_id = self.registry.next_available_id()
        elif self.registry.exists(rule_id):
            raise ValueError(f"Rule ID {rule_id} already exists. Use a different ID.")
        elif not (RULE_ID_MIN <= rule_id <= RULE_ID_MAX):
            raise ValueError(
                f"Rule ID {rule_id} is outside the SOAR namespace "
                f"({RULE_ID_MIN}-{RULE_ID_MAX})."
            )

        rule_text = _build_rule(
            rule_id, msg, variables, operator,
            action=action, severity=severity,
            tags=["SOAR", "AcmeToCasino"] + tags,
            audit_only=audit_only,
            phase=phase,
        )

        if not self.dry_run:
            _append_rule(SOAR_RULES_FILE, rule_text)

        self.registry.add(rule_id, {
            "msg": msg,
            "variables": variables,
            "operator": operator,
            "action": action,
            "severity": severity,
            "tags": tags,
            "audit_only": audit_only,
            "phase": phase,
            "file": str(SOAR_RULES_FILE),
        })

        _audit("add_rule", {"rule_id": rule_id, "msg": msg, "action": action, "audit_only": audit_only})
        self.logger.info("Added rule %d: %s", rule_id, msg)

        if auto_reload and not self.dry_run:
            self.reload_nginx()

        return rule_id

    def remove_rule(self, rule_id: int, auto_reload: bool = True) -> bool:
        """Remove a SOAR-managed rule by ID."""
        meta = self.registry.get(rule_id)
        if meta is None:
            self.logger.error("Rule %d not found in registry", rule_id)
            return False

        target_file = Path(meta.get("file", str(SOAR_RULES_FILE)))
        if not self.dry_run:
            removed = _remove_rule_from_file(target_file, rule_id)
            if not removed:
                # Also try virtual patch file
                removed = _remove_rule_from_file(VIRTUAL_PATCH_FILE, rule_id)
            if not removed:
                self.logger.warning("Rule %d not found in config file (registry cleaned up)", rule_id)

        self.registry.remove(rule_id)
        _audit("remove_rule", {"rule_id": rule_id, "msg": meta.get("msg", "")})
        self.logger.info("Removed rule %d", rule_id)

        if auto_reload and not self.dry_run:
            self.reload_nginx()

        return True

    def virtual_patch(
        self,
        cve: str,
        target_uri: str,
        operator: str,
        msg: Optional[str] = None,
        rule_id: Optional[int] = None,
        auto_reload: bool = True,
    ) -> int:
        """
        Create a virtual patch rule for a CVE.

        Virtual patches are placed in virtual_patches.conf and use the
        9500000-9599999 ID range for easy identification.
        """
        if rule_id is None:
            rule_id = self.registry.next_available_id(
                start=VIRTUAL_PATCH_ID_MIN, end=VIRTUAL_PATCH_ID_MAX
            )
        patch_msg = msg or f"Virtual patch for {cve}"

        # Build the rule targeting the specific URI
        rule_text = _build_rule(
            rule_id,
            msg=patch_msg,
            variables=f'REQUEST_URI|ARGS',
            operator=operator,
            action="block",
            severity="CRITICAL",
            tags=["VIRTUAL_PATCH", cve, "SOAR"],
            audit_only=False,
            phase=2,
        )

        # Prepend a URI check to scope the patch to the target endpoint
        scoped_rule = (
            f'# SOAR-RULE-BEGIN:{rule_id}\n'
            f'# Virtual patch for {cve} - target: {target_uri}\n'
            f'SecRule REQUEST_URI "@beginsWith {target_uri}" \\\n'
            f'    "id:{rule_id},\\\n'
            f'    phase:2,\\\n'
            f'    deny,status:403,log,auditlog,\\\n'
            f'    chain,\\\n'
            f'    msg:\\"{patch_msg}\\",\\\n'
            f'    severity:CRITICAL,\\\n'
            f'    tag:\\"VIRTUAL_PATCH\\",\\\n'
            f'    tag:\\"{cve}\\""\n'
            f'    SecRule REQUEST_URI|ARGS|REQUEST_BODY "{operator}" \\\n'
            f'        "t:none,t:urlDecodeUni,t:htmlEntityDecode"\n'
            f'# SOAR-RULE-END:{rule_id}\n'
        )

        if not self.dry_run:
            _append_rule(VIRTUAL_PATCH_FILE, scoped_rule)

        self.registry.add(rule_id, {
            "type": "virtual_patch",
            "cve": cve,
            "target_uri": target_uri,
            "operator": operator,
            "msg": patch_msg,
            "file": str(VIRTUAL_PATCH_FILE),
        })

        _audit("virtual_patch", {"rule_id": rule_id, "cve": cve, "target_uri": target_uri})
        self.logger.info("Virtual patch %s applied as rule %d", cve, rule_id)

        if auto_reload and not self.dry_run:
            self.reload_nginx()

        return rule_id

    def set_audit_only(self, rule_id: int, audit_only: bool, auto_reload: bool = True) -> bool:
        """Toggle a rule between audit-only (log) and enforcement (block) mode."""
        meta = self.registry.get(rule_id)
        if meta is None:
            self.logger.error("Rule %d not found", rule_id)
            return False

        # Remove and re-add with updated mode
        current_meta = dict(meta)
        current_meta["audit_only"] = audit_only

        if not self.dry_run:
            target_file = Path(meta.get("file", str(SOAR_RULES_FILE)))
            _remove_rule_from_file(target_file, rule_id)
            new_rule_text = _build_rule(
                rule_id,
                msg=current_meta["msg"],
                variables=current_meta["variables"],
                operator=current_meta["operator"],
                action=current_meta.get("action", "block"),
                severity=current_meta.get("severity", "WARNING"),
                tags=current_meta.get("tags", []),
                audit_only=audit_only,
                phase=current_meta.get("phase", 2),
            )
            _append_rule(target_file, new_rule_text)

        self.registry.add(rule_id, current_meta)
        mode = "audit-only" if audit_only else "enforcing"
        _audit("set_mode", {"rule_id": rule_id, "mode": mode})
        self.logger.info("Rule %d set to %s mode", rule_id, mode)

        if auto_reload and not self.dry_run:
            self.reload_nginx()

        return True

    def set_crs_paranoia(self, level: int) -> bool:
        """Update the OWASP CRS paranoia level in crs-setup.conf."""
        if not 1 <= level <= 4:
            raise ValueError("CRS paranoia level must be between 1 and 4")

        if not CRS_SETUP_CONF.exists():
            self.logger.error("CRS setup config not found at %s", CRS_SETUP_CONF)
            return False

        if self.dry_run:
            self.logger.info("[DRY-RUN] Would set CRS paranoia level to %d", level)
            return True

        content = CRS_SETUP_CONF.read_text(encoding="utf-8")
        # Replace or insert the tx.paranoia_level setting
        pattern = r'(tx\.paranoia_level\s*=\s*)\d+'
        replacement = rf'\g<1>{level}'
        new_content, count = re.subn(pattern, replacement, content)

        if count == 0:
            # Setting not found - append it before the end of the file
            new_content = content.rstrip() + f'\nSecAction "id:900000,phase:1,nolog,pass,t:none,setvar:tx.paranoia_level={level}"\n'

        _write_rules_file(CRS_SETUP_CONF, new_content)
        _audit("crs_paranoia", {"level": level})
        self.logger.info("CRS paranoia level set to %d", level)
        return True

    def add_igaming_block(
        self,
        pattern_type: str,
        value: str,
        comment: str = "",
    ) -> int:
        """
        Shortcut for common iGaming fraud block patterns.

        pattern_type options:
          - 'ip'       : block a single IP
          - 'useragent': block a known fraud bot user-agent
          - 'referer'  : block a known fraud referrer domain
          - 'param'    : block requests with a specific parameter value
          - 'country'  : block a GeoIP country code (requires mod_geoip2)
        """
        rule_id = self.registry.next_available_id()

        if pattern_type == "ip":
            return self.add_rule(
                rule_id=rule_id,
                msg=f"iGaming fraud IP block: {value} {comment}",
                variables="REMOTE_ADDR",
                operator=f"@ipMatch {value}",
                action="block",
                severity="CRITICAL",
                tags=["igaming-fraud", "ip-block"],
            )
        elif pattern_type == "useragent":
            return self.add_rule(
                rule_id=rule_id,
                msg=f"iGaming fraud bot user-agent: {comment or value[:30]}",
                variables="REQUEST_HEADERS:User-Agent",
                operator=f"@rx {re.escape(value)}",
                action="block",
                severity="WARNING",
                tags=["igaming-fraud", "bot-ua"],
            )
        elif pattern_type == "referer":
            return self.add_rule(
                rule_id=rule_id,
                msg=f"iGaming fraud referrer block: {comment or value[:30]}",
                variables="REQUEST_HEADERS:Referer",
                operator=f"@contains {value}",
                action="block",
                severity="WARNING",
                tags=["igaming-fraud", "fraud-referrer"],
            )
        elif pattern_type == "param":
            return self.add_rule(
                rule_id=rule_id,
                msg=f"iGaming fraud parameter pattern: {comment}",
                variables="ARGS",
                operator=f"@rx {value}",
                action="block",
                severity="ERROR",
                tags=["igaming-fraud", "param-injection"],
            )
        elif pattern_type == "country":
            return self.add_rule(
                rule_id=rule_id,
                msg=f"iGaming GeoIP country block: {value} {comment}",
                variables="GEO:COUNTRY_CODE",
                operator=f"@streq {value}",
                action="block",
                severity="NOTICE",
                tags=["igaming-geo-block"],
            )
        else:
            raise ValueError(f"Unknown pattern_type: {pattern_type}")

    def list_rules(self) -> list[dict[str, Any]]:
        return self.registry.all_rules()

    def get_status(self) -> dict[str, Any]:
        rules = self.registry.all_rules()
        return {
            "total_rules": len(rules),
            "enforcing": sum(1 for r in rules if not r.get("audit_only")),
            "audit_only": sum(1 for r in rules if r.get("audit_only")),
            "virtual_patches": sum(1 for r in rules if r.get("type") == "virtual_patch"),
            "rules_file": str(SOAR_RULES_FILE),
            "virtual_patch_file": str(VIRTUAL_PATCH_FILE),
            "audit_log": str(AUDIT_LOG),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="modsecurity_manager.py",
        description="AcmeToCasino SOAR ModSecurity v3 rule manager",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", choices=["text", "json"], default="text")

    sub = parser.add_subparsers(dest="command", required=True)

    # add-rule
    p_add = sub.add_parser("add-rule", help="Add a custom ModSecurity rule")
    p_add.add_argument("--id", type=int, dest="rule_id", default=None)
    p_add.add_argument("--msg", required=True)
    p_add.add_argument("--vars", required=True, dest="variables")
    p_add.add_argument("--operator", required=True)
    p_add.add_argument("--action", choices=["block", "drop", "redirect", "log"], default="block")
    p_add.add_argument("--severity", default="WARNING")
    p_add.add_argument("--phase", type=int, default=2, choices=[1, 2, 3, 4, 5])
    p_add.add_argument("--audit-only", action="store_true")
    p_add.add_argument("--tags", nargs="*", default=[])
    p_add.add_argument("--no-reload", action="store_true")

    # remove-rule
    p_rm = sub.add_parser("remove-rule", help="Remove a SOAR-managed rule")
    p_rm.add_argument("--id", type=int, required=True, dest="rule_id")
    p_rm.add_argument("--no-reload", action="store_true")

    # virtual-patch
    p_vp = sub.add_parser("virtual-patch", help="Apply a virtual patch for a CVE")
    p_vp.add_argument("--cve", required=True)
    p_vp.add_argument("--target", required=True, dest="target_uri")
    p_vp.add_argument("--operator", required=True)
    p_vp.add_argument("--msg", default=None)
    p_vp.add_argument("--id", type=int, dest="rule_id", default=None)
    p_vp.add_argument("--no-reload", action="store_true")

    # audit-only
    p_ao = sub.add_parser("audit-only", help="Set a rule to audit-only (log, no block)")
    p_ao.add_argument("--id", type=int, required=True, dest="rule_id")

    # enforce
    p_en = sub.add_parser("enforce", help="Set a rule to enforcing (block) mode")
    p_en.add_argument("--id", type=int, required=True, dest="rule_id")

    # igaming-block
    p_ig = sub.add_parser("igaming-block", help="Add iGaming-specific fraud block")
    p_ig.add_argument("--type", required=True,
                      choices=["ip", "useragent", "referer", "param", "country"],
                      dest="pattern_type")
    p_ig.add_argument("--value", required=True)
    p_ig.add_argument("--comment", default="")

    # crs-paranoia
    p_crs = sub.add_parser("crs-paranoia", help="Set OWASP CRS paranoia level (1-4)")
    p_crs.add_argument("--level", type=int, required=True, choices=[1, 2, 3, 4])

    # reload
    sub.add_parser("reload", help="Test and reload nginx")

    # list-rules
    sub.add_parser("list-rules", help="List all SOAR-managed rules")

    # status
    sub.add_parser("status", help="Show WAF management status")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logger = _setup_logging()

    if os.geteuid() != 0 and not args.dry_run:
        logger.error("This script must be run as root (or with sudo).")
        return 1

    mgr = ModSecurityManager(logger, dry_run=args.dry_run)
    success = True

    try:
        if args.command == "add-rule":
            rid = mgr.add_rule(
                rule_id=args.rule_id,
                msg=args.msg,
                variables=args.variables,
                operator=args.operator,
                action=args.action,
                severity=args.severity.upper(),
                tags=args.tags,
                audit_only=args.audit_only,
                phase=args.phase,
                auto_reload=not args.no_reload,
            )
            print(f"Rule {rid} added successfully")

        elif args.command == "remove-rule":
            success = mgr.remove_rule(args.rule_id, auto_reload=not args.no_reload)
            if success:
                print(f"Rule {args.rule_id} removed")

        elif args.command == "virtual-patch":
            rid = mgr.virtual_patch(
                cve=args.cve,
                target_uri=args.target_uri,
                operator=args.operator,
                msg=args.msg,
                rule_id=args.rule_id,
                auto_reload=not args.no_reload,
            )
            print(f"Virtual patch {args.cve} applied as rule {rid}")

        elif args.command == "audit-only":
            success = mgr.set_audit_only(args.rule_id, audit_only=True)

        elif args.command == "enforce":
            success = mgr.set_audit_only(args.rule_id, audit_only=False)

        elif args.command == "igaming-block":
            rid = mgr.add_igaming_block(
                pattern_type=args.pattern_type,
                value=args.value,
                comment=args.comment,
            )
            print(f"iGaming block rule {rid} added")

        elif args.command == "crs-paranoia":
            success = mgr.set_crs_paranoia(args.level)

        elif args.command == "reload":
            success = mgr.reload_nginx()

        elif args.command == "list-rules":
            rules = mgr.list_rules()
            if args.output == "json":
                print(json.dumps(rules, indent=2))
            else:
                if not rules:
                    print("No SOAR-managed rules found.")
                else:
                    fmt = "{:<12} {:<10} {:<12} {:<8} {}"
                    print(fmt.format("ID", "Mode", "Action", "Phase", "Message"))
                    print("-" * 80)
                    for r in sorted(rules, key=lambda x: x.get("id", 0)):
                        mode = "audit" if r.get("audit_only") else "enforce"
                        print(fmt.format(
                            str(r.get("id", "?")),
                            mode,
                            r.get("action", "?"),
                            str(r.get("phase", "?")),
                            r.get("msg", "")[:50],
                        ))

        elif args.command == "status":
            data = mgr.get_status()
            if args.output == "json":
                print(json.dumps(data, indent=2))
            else:
                for k, v in data.items():
                    print(f"{k}: {v}")

    except (ValueError, RuntimeError) as exc:
        logger.error("%s", exc)
        return 1

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
