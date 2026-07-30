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
evidence-collector.py - OGIS Evidence Packager for GLI-GSF Compliance
GLI-GSF Phase 4 - Assessment Evidence Collection

Collects, organizes, and packages evidence for all OGIS control domains:
  OGIS-1: Critical Control Program verification logs
  OGIS-2: Back-office MFA audit reports, RBAC matrices
  OGIS-3: Application security scan results, game integrity tests
  OGIS-4: Bot detection logs, DDoS mitigation evidence
  OGIS-5: Availability metrics, incident response records

For each control domain, the tool:
  1. Scans configured directories for evidence files
  2. Validates completeness against required evidence checklist
  3. Collects screenshots, logs, scan reports, and configs
  4. Compiles into a structured evidence package (ZIP)
  5. Generates a manifest with SHA-256 hashes for integrity

GLI-GSF-4 Reference: Section 5.2 - Evidence Package Requirements
  - All evidence must be dated and attributable
  - SHA-256 integrity hashes required for all files
  - Evidence must cover the full assessment period
  - Organized by OGIS control domain

Usage:
    python3 evidence-collector.py collect --period 2026-Q1
    python3 evidence-collector.py validate --period 2026-Q1
    python3 evidence-collector.py package --period 2026-Q1 --output evidence.zip
    python3 evidence-collector.py demo

Requirements:
    No external dependencies (standard library only)
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

VERSION = "1.0.0"
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger("evidence-collector")


# ---------------------------------------------------------------------------
# OGIS Control Domains and Required Evidence
# ---------------------------------------------------------------------------
OGIS_DOMAINS = {
    "OGIS-1": {
        "name": "Critical Control Programs",
        "description": "RNG verification, CCP signature checks, game logic integrity",
        "required_evidence": [
            {"id": "OGIS-1.1", "desc": "CCP registry listing all programs", "file_pattern": "ccp-registry*"},
            {"id": "OGIS-1.2", "desc": "24-hour signature verification logs", "file_pattern": "signature-verification*"},
            {"id": "OGIS-1.3", "desc": "RNG statistical test results (NIST SP 800-22)", "file_pattern": "rng-test*"},
            {"id": "OGIS-1.4", "desc": "CCP change management records", "file_pattern": "change-management*"},
            {"id": "OGIS-1.5", "desc": "Alert response records for signature mismatches", "file_pattern": "alert-response*"},
        ],
    },
    "OGIS-2": {
        "name": "Back Office Administration",
        "description": "MFA enforcement, RBAC, session management, audit logs",
        "required_evidence": [
            {"id": "OGIS-2.1", "desc": "MFA coverage audit report (100% required)", "file_pattern": "mfa-audit*"},
            {"id": "OGIS-2.2", "desc": "RBAC matrix with role definitions", "file_pattern": "rbac*"},
            {"id": "OGIS-2.3", "desc": "Session timeout configuration evidence", "file_pattern": "session*"},
            {"id": "OGIS-2.4", "desc": "Admin access audit logs", "file_pattern": "admin-audit*"},
            {"id": "OGIS-2.5", "desc": "Vendor access session recordings", "file_pattern": "vendor-access*"},
        ],
    },
    "OGIS-3": {
        "name": "Application Security",
        "description": "DAST/SAST results, API security, input validation",
        "required_evidence": [
            {"id": "OGIS-3.1", "desc": "OWASP Top 10 scan results", "file_pattern": "owasp*"},
            {"id": "OGIS-3.2", "desc": "API security assessment", "file_pattern": "api-security*"},
            {"id": "OGIS-3.3", "desc": "Penetration test report", "file_pattern": "pentest*"},
            {"id": "OGIS-3.4", "desc": "Code review / SAST results", "file_pattern": "sast*"},
            {"id": "OGIS-3.5", "desc": "Game integrity validation records", "file_pattern": "game-integrity*"},
        ],
    },
    "OGIS-4": {
        "name": "Automated Threat Protection",
        "description": "Bot detection, DDoS mitigation, rate limiting",
        "required_evidence": [
            {"id": "OGIS-4.1", "desc": "Bot detection effectiveness report (99%+ block rate)", "file_pattern": "bot-detection*"},
            {"id": "OGIS-4.2", "desc": "DDoS mitigation configuration and test results", "file_pattern": "ddos*"},
            {"id": "OGIS-4.3", "desc": "Rate limiting configuration evidence", "file_pattern": "rate-limit*"},
            {"id": "OGIS-4.4", "desc": "WAF rule configuration", "file_pattern": "waf*"},
            {"id": "OGIS-4.5", "desc": "Threat intelligence feed integration", "file_pattern": "threat-intel*"},
        ],
    },
    "OGIS-5": {
        "name": "Platform Availability",
        "description": "Uptime metrics, incident response, disaster recovery",
        "required_evidence": [
            {"id": "OGIS-5.1", "desc": "Uptime metrics (99.9% SLA)", "file_pattern": "uptime*"},
            {"id": "OGIS-5.2", "desc": "Incident response records", "file_pattern": "incident*"},
            {"id": "OGIS-5.3", "desc": "Disaster recovery test results", "file_pattern": "dr-test*"},
            {"id": "OGIS-5.4", "desc": "Backup verification records", "file_pattern": "backup*"},
            {"id": "OGIS-5.5", "desc": "Capacity planning documentation", "file_pattern": "capacity*"},
        ],
    },
}


@dataclass
class EvidenceFile:
    control_id: str
    file_path: str
    file_name: str
    file_size: int
    sha256_hash: str
    collected_at: str
    description: str


@dataclass
class EvidencePackage:
    period: str
    organization: str
    collected_at: str
    collector: str
    domains: Dict[str, dict] = field(default_factory=dict)
    files: List[EvidenceFile] = field(default_factory=list)
    missing: List[dict] = field(default_factory=list)
    total_files: int = 0
    total_required: int = 0
    completeness_pct: float = 0.0


# ---------------------------------------------------------------------------
# Evidence Collector
# ---------------------------------------------------------------------------
class EvidenceCollector:
    def __init__(self, evidence_dirs: Optional[List[str]] = None, org: str = "AcmetoCasino"):
        self.evidence_dirs = evidence_dirs or [
            "/var/log/gsf",
            "/etc/gsf",
            "./evidence",
            "./reports",
        ]
        self.org = org

    def compute_hash(self, file_path: str) -> str:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    def find_evidence(self, pattern: str) -> List[Path]:
        found = []
        for base_dir in self.evidence_dirs:
            base = Path(base_dir)
            if base.exists():
                found.extend(base.rglob(pattern))
        return found

    def collect(self, period: str) -> EvidencePackage:
        now = datetime.now(timezone.utc).isoformat()
        package = EvidencePackage(
            period=period, organization=self.org,
            collected_at=now,
            collector=os.environ.get("USER", "system"),
        )

        total_required = 0
        total_found = 0

        for domain_id, domain_info in OGIS_DOMAINS.items():
            domain_files = []
            domain_missing = []

            for req in domain_info["required_evidence"]:
                total_required += 1
                files = self.find_evidence(req["file_pattern"])  # ty:ignore[invalid-argument-type]

                if files:
                    total_found += 1
                    for fp in files:
                        ef = EvidenceFile(
                            control_id=req["id"],  # ty:ignore[invalid-argument-type]
                            file_path=str(fp),
                            file_name=fp.name,
                            file_size=fp.stat().st_size,
                            sha256_hash=self.compute_hash(str(fp)),
                            collected_at=now,
                            description=req["desc"],  # ty:ignore[invalid-argument-type]
                        )
                        package.files.append(ef)
                        domain_files.append(asdict(ef))
                else:
                    package.missing.append({
                        "control_id": req["id"],  # ty:ignore[invalid-argument-type]
                        "description": req["desc"],  # ty:ignore[invalid-argument-type]
                        "expected_pattern": req["file_pattern"],  # ty:ignore[invalid-argument-type]
                        "domain": domain_id,
                    })
                    domain_missing.append(req["id"])  # ty:ignore[invalid-argument-type]

            package.domains[domain_id] = {
                "name": domain_info["name"],
                "files_found": len(domain_files),
                "files_required": len(domain_info["required_evidence"]),
                "missing_controls": domain_missing,
                "complete": len(domain_missing) == 0,
            }

        package.total_files = len(package.files)
        package.total_required = total_required
        package.completeness_pct = round(
            (total_found / total_required * 100) if total_required > 0 else 0, 1
        )

        return package

    def validate(self, package: EvidencePackage) -> bool:
        print(f"\n{'=' * 60}")
        print(f"  OGIS Evidence Validation - {package.period}")
        print(f"{'=' * 60}\n")

        all_complete = True
        for domain_id, info in package.domains.items():
            status = "\033[0;32mCOMPLETE\033[0m" if info["complete"] else "\033[0;31mINCOMPLETE\033[0m"
            print(f"  {domain_id} ({info['name']}): {status}")
            print(f"    Files: {info['files_found']}/{info['files_required']}")
            if info["missing_controls"]:
                all_complete = False
                for ctrl in info["missing_controls"]:
                    print(f"    \033[0;31mMISSING: {ctrl}\033[0m")
            print()

        print(f"  Overall Completeness: {package.completeness_pct}%")
        print(f"  Total Files: {package.total_files}")
        print(f"  Missing Items: {len(package.missing)}")
        print(f"\n  ISF Ready: {'YES' if all_complete else 'NO - collect missing evidence'}")
        print(f"{'=' * 60}\n")
        return all_complete

    def create_zip(self, package: EvidencePackage, output: str):
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            # Add manifest
            manifest = {
                "package_type": "GLI-GSF OGIS Evidence Package",
                "gli_gsf_reference": "GLI-GSF-4, Section 5.2",
                "period": package.period,
                "organization": package.organization,
                "collected_at": package.collected_at,
                "collector": package.collector,
                "completeness": f"{package.completeness_pct}%",
                "domains": package.domains,
                "files": [asdict(f) for f in package.files],
                "missing": package.missing,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

            # Add evidence files
            for ef in package.files:
                if Path(ef.file_path).exists():
                    arcname = f"{ef.control_id}/{ef.file_name}"
                    zf.write(ef.file_path, arcname)

        logger.info(f"Evidence package: {output} ({len(package.files)} files)")


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def run_demo():
    print("\n" + "=" * 60)
    print("  GLI-GSF Evidence Collector - Demo Mode")
    print("=" * 60)

    # Create sample evidence directory
    demo_dir = Path("./evidence-demo")
    demo_dir.mkdir(exist_ok=True)

    samples = {
        "ccp-registry.json": '{"rng-service": {"status": "verified"}}',
        "signature-verification.log": "2026-03-09 PASS rng-service sha256:abc123\n",
        "mfa-audit-2026-Q1.json": '{"coverage": "100%", "compliant": true}',
        "rbac-matrix.csv": "role,permission\nadmin,full-access\nplayer,read-only\n",
        "owasp-api-report.html": "<html><body>OWASP Scan: 0 Critical</body></html>",
        "bot-detection-report.json": '{"block_rate": "99.2%", "period": "2026-Q1"}',
        "ddos-mitigation-config.txt": "CloudFlare WAF enabled\nRate limiting active\n",
        "uptime-metrics-2026-Q1.json": '{"uptime": "99.95%", "incidents": 2}',
    }

    for name, content in samples.items():
        (demo_dir / name).write_text(content)

    collector = EvidenceCollector(evidence_dirs=[str(demo_dir)])
    package = collector.collect("2026-Q1")
    collector.validate(package)

    # Cleanup
    import shutil
    shutil.rmtree(demo_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="GLI-GSF OGIS Evidence Collector")
    sub = parser.add_subparsers(dest="command")

    collect = sub.add_parser("collect", help="Collect evidence for a period")
    collect.add_argument("--period", required=True, help="Assessment period (e.g., 2026-Q1)")
    collect.add_argument("--dirs", nargs="+", help="Evidence directories to scan")
    collect.add_argument("--org", default="AcmetoCasino")

    validate = sub.add_parser("validate", help="Validate evidence completeness")
    validate.add_argument("--period", required=True)
    validate.add_argument("--dirs", nargs="+")

    package = sub.add_parser("package", help="Create evidence ZIP package")
    package.add_argument("--period", required=True)
    package.add_argument("--output", required=True, help="Output ZIP path")
    package.add_argument("--dirs", nargs="+")

    sub.add_parser("demo", help="Run demo")

    args = parser.parse_args()

    if args.command == "collect":
        c = EvidenceCollector(evidence_dirs=args.dirs, org=args.org)
        pkg = c.collect(args.period)
        c.validate(pkg)

    elif args.command == "validate":
        c = EvidenceCollector(evidence_dirs=getattr(args, 'dirs', None))
        pkg = c.collect(args.period)
        ok = c.validate(pkg)
        sys.exit(0 if ok else 1)

    elif args.command == "package":
        c = EvidenceCollector(evidence_dirs=getattr(args, 'dirs', None))
        pkg = c.collect(args.period)
        c.validate(pkg)
        c.create_zip(pkg, args.output)

    elif args.command == "demo":
        run_demo()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
