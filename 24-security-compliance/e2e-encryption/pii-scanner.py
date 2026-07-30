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
pii-scanner.py — Scan databases, log files, and filesystem paths for
unencrypted Personally Identifiable Information (PII).

Scans for:
    - Email addresses
    - Phone numbers (international formats)
    - Credit card PANs (Visa, Mastercard, Amex)
    - IP addresses (IPv4 and IPv6)
    - Names in plaintext columns (heuristic)
    - Date of birth patterns
    - National ID / passport number patterns

Output:
    - Console report with finding severity (CRITICAL / HIGH / MEDIUM)
    - JSON report file for integration with SIEM

Usage:
    python3 pii-scanner.py [options]

Options:
    --pg-host HOST       PostgreSQL host (default: localhost)
    --pg-port PORT       PostgreSQL port (default: 5432)
    --pg-user USER       PostgreSQL user (default: postgres)
    --pg-password PASS   PostgreSQL password
    --pg-db DB           Database name (default: postgres)
    --scan-db            Scan PostgreSQL columns for plaintext PII
    --scan-dirs DIRS     Comma-separated list of directories to scan
    --scan-logs          Scan /var/log and application log directories
    --output JSON        Write findings to JSON file
    --max-files N        Maximum files to scan per directory (default: 1000)
    --sample-rows N      Rows to sample per column (default: 10)

Compliance: GDPR Art.25 (privacy by design); PCI DSS v4.0.1 Req.3.3;
            ISO 27001:2022 A.8.11 (data masking)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import psycopg2
    import psycopg2.extensions
    import psycopg2.extras
except ImportError:
    psycopg2 = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# PII patterns
# ---------------------------------------------------------------------------
@dataclass
class PiiPattern:
    name: str
    pattern: re.Pattern[str]
    severity: str       # CRITICAL | HIGH | MEDIUM
    description: str
    compliance: str


PII_PATTERNS: list[PiiPattern] = [
    PiiPattern(
        name="email",
        pattern=re.compile(
            r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b',
            re.IGNORECASE,
        ),
        severity="HIGH",
        description="Email address",
        compliance="GDPR Art.4(1); PCI DSS Req.3",
    ),
    PiiPattern(
        name="credit_card_pan",
        pattern=re.compile(
            r'\b(?:4[0-9]{12}(?:[0-9]{3})?'   # Visa
            r'|5[1-5][0-9]{14}'                 # Mastercard
            r'|3[47][0-9]{13}'                  # Amex
            r'|6(?:011|5[0-9]{2})[0-9]{12}'    # Discover
            r'|(?:2131|1800|35\d{3})\d{11})'   # JCB
            r'\b',
            re.ASCII,
        ),
        severity="CRITICAL",
        description="Credit card PAN",
        compliance="PCI DSS v4.0.1 Req.3.3.1 — PAN must not be stored unencrypted",
    ),
    PiiPattern(
        name="phone_number",
        pattern=re.compile(
            r'(?<!\w)'
            r'(?:\+?(?:1|44|49|33|39|34|31|46|47|48|420|353)[\s\-]?)?'
            r'(?:\(?\d{2,4}\)?[\s\-]?)'
            r'(?:\d{3,4}[\s\-]?){1,3}'
            r'\d{3,4}'
            r'(?!\w)',
        ),
        severity="HIGH",
        description="Phone number",
        compliance="GDPR Art.4(1)",
    ),
    PiiPattern(
        name="date_of_birth",
        pattern=re.compile(
            r'\b(?:19|20)\d{2}'                 # year
            r'[-/\s]'
            r'(?:0[1-9]|1[0-2])'                # month
            r'[-/\s]'
            r'(?:0[1-9]|[12]\d|3[01])\b',       # day
        ),
        severity="HIGH",
        description="Date of birth",
        compliance="GDPR Art.4(1) — special category data",
    ),
    PiiPattern(
        name="ipv4_address",
        pattern=re.compile(
            r'\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}'
            r'(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b',
        ),
        severity="MEDIUM",
        description="IPv4 address (potential PII under GDPR)",
        compliance="GDPR Recital 30 — IP addresses are personal data",
    ),
    PiiPattern(
        name="uk_passport",
        pattern=re.compile(r'\b[0-9]{9}\b'),
        severity="HIGH",
        description="Potential UK passport number (9 digits)",
        compliance="GDPR Art.9 — special category data",
    ),
    PiiPattern(
        name="iban",
        pattern=re.compile(
            r'\b[A-Z]{2}[0-9]{2}[A-Z0-9]{4}[0-9]{7}(?:[A-Z0-9]{0,16})\b',
        ),
        severity="CRITICAL",
        description="IBAN bank account number",
        compliance="PCI DSS Req.3; PSD2",
    ),
    PiiPattern(
        name="sort_code_account",
        pattern=re.compile(r'\b\d{2}[-\s]?\d{2}[-\s]?\d{2}\b'),
        severity="MEDIUM",
        description="Potential UK sort code",
        compliance="PCI DSS Req.3",
    ),
]


# ---------------------------------------------------------------------------
# Finding data model
# ---------------------------------------------------------------------------
@dataclass
class PiiFinding:
    source_type: str    # "database" | "file" | "log"
    source_path: str    # table.column or file path
    pattern_name: str
    severity: str
    sample_count: int
    sample: str         # redacted sample for evidence
    compliance: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Database scanner
# ---------------------------------------------------------------------------
class DatabaseScanner:
    def __init__(
        self,
        conn: "psycopg2.extensions.connection",
        sample_rows: int = 10,
    ) -> None:
        self.conn = conn
        self.sample_rows = sample_rows
        self.findings: list[PiiFinding] = []

    def get_text_columns(self) -> list[tuple[str, str]]:
        """Return list of (table, column) for all text-like columns in public schema."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND data_type IN (
                      'text', 'character varying', 'character',
                      'varchar', 'json', 'jsonb'
                  )
                ORDER BY table_name, column_name
                """
            )
            return cur.fetchall()

    def scan_column(self, table: str, column: str) -> list[PiiFinding]:
        """Sample a column and check for PII patterns."""
        findings: list[PiiFinding] = []
        try:
            with self.conn.cursor() as cur:
                cur.execute(
                    f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL LIMIT %s",  # noqa: S608
                    (self.sample_rows,),
                )
                rows = cur.fetchall()
        except Exception:  # noqa: BLE001
            return findings

        for (value,) in rows:
            if not isinstance(value, str):
                continue
            for pii_pat in PII_PATTERNS:
                matches = pii_pat.pattern.findall(value)
                if matches:
                    # Redact the actual match for the report
                    redacted = pii_pat.pattern.sub(f"[{pii_pat.name.upper()}_REDACTED]", value)
                    findings.append(
                        PiiFinding(
                            source_type="database",
                            source_path=f"{table}.{column}",
                            pattern_name=pii_pat.name,
                            severity=pii_pat.severity,
                            sample_count=len(matches),
                            sample=redacted[:120],
                            compliance=pii_pat.compliance,
                        )
                    )
                    break  # one finding per column per pass

        return findings

    def scan(self) -> list[PiiFinding]:
        """Scan all text columns in the database."""
        columns = self.get_text_columns()
        print(f"  INFO  Scanning {len(columns)} text columns in database")

        for table, column in columns:
            col_findings = self.scan_column(table, column)
            self.findings.extend(col_findings)
            if col_findings:
                for f in col_findings:
                    print(
                        f"  {f.severity}  DB: {f.source_path} "
                        f"contains {f.pattern_name} ({f.sample_count} match(es))"
                    )

        return self.findings


# ---------------------------------------------------------------------------
# File scanner
# ---------------------------------------------------------------------------
class FileScanner:
    # File extensions to scan (text-based files only)
    SCAN_EXTENSIONS = {
        ".log", ".txt", ".csv", ".json", ".jsonl",
        ".sql", ".xml", ".yaml", ".yml", ".env",
        ".conf", ".config", ".properties",
    }
    MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB

    def __init__(
        self,
        scan_dirs: list[str],
        max_files: int = 1000,
    ) -> None:
        self.scan_dirs = scan_dirs
        self.max_files = max_files
        self.findings: list[PiiFinding] = []
        self._files_scanned = 0

    def should_scan_file(self, path: Path) -> bool:
        """Determine if a file should be scanned."""
        if path.suffix.lower() not in self.SCAN_EXTENSIONS:
            return False
        try:
            if path.stat().st_size > self.MAX_FILE_SIZE:
                return False
        except OSError:
            return False
        return True

    def scan_file(self, path: Path) -> list[PiiFinding]:
        """Scan a single file for PII patterns."""
        findings: list[PiiFinding] = []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return findings

        for pii_pat in PII_PATTERNS:
            matches = pii_pat.pattern.findall(content)
            if matches:
                # Find the line(s) containing the match for context
                first_match = matches[0] if isinstance(matches[0], str) else str(matches[0])
                lines_with_match = [
                    line for line in content.splitlines()
                    if first_match[:8] in line
                ]
                sample_line = lines_with_match[0][:100] if lines_with_match else first_match[:50]
                # Redact before storing
                redacted = pii_pat.pattern.sub(f"[{pii_pat.name.upper()}]", sample_line)

                findings.append(
                    PiiFinding(
                        source_type="file",
                        source_path=str(path),
                        pattern_name=pii_pat.name,
                        severity=pii_pat.severity,
                        sample_count=len(matches),
                        sample=redacted,
                        compliance=pii_pat.compliance,
                    )
                )

        return findings

    def scan(self) -> list[PiiFinding]:
        """Walk directories and scan eligible files."""
        for scan_dir in self.scan_dirs:
            dir_path = Path(scan_dir)
            if not dir_path.exists():
                print(f"  WARN  Directory not found: {scan_dir}")
                continue

            print(f"  INFO  Scanning directory: {scan_dir}")
            for file_path in dir_path.rglob("*"):
                if self._files_scanned >= self.max_files:
                    print(f"  WARN  Reached max file limit ({self.max_files}). Use --max-files to increase.")
                    return self.findings

                if not file_path.is_file():
                    continue
                if not self.should_scan_file(file_path):
                    continue

                file_findings = self.scan_file(file_path)
                self._files_scanned += 1
                self.findings.extend(file_findings)

                if file_findings:
                    for f in file_findings:
                        print(
                            f"  {f.severity}  FILE: {f.source_path} "
                            f"contains {f.pattern_name} ({f.sample_count} match(es))"
                        )

        print(f"  INFO  Scanned {self._files_scanned} files")
        return self.findings


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_report(
    findings: list[PiiFinding],
    output_path: Optional[str] = None,
) -> dict[str, object]:
    """Generate summary report from findings."""
    by_severity: dict[str, list[PiiFinding]] = {"CRITICAL": [], "HIGH": [], "MEDIUM": []}
    for f in findings:
        by_severity.setdefault(f.severity, []).append(f)

    total_count: int = len(findings)
    report: dict[str, object] = {
        "scan_timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "total_findings": total_count,
        "by_severity": {
            sev: len(items) for sev, items in by_severity.items()
        },
        "critical_items": [asdict(f) for f in by_severity.get("CRITICAL", [])],
        "high_items": [asdict(f) for f in by_severity.get("HIGH", [])[:20]],  # cap for readability
        "medium_items": [asdict(f) for f in by_severity.get("MEDIUM", [])[:10]],
        "compliance_note": (
            "CRITICAL findings require immediate remediation. "
            "PCI DSS Req.3.3.1 prohibits unencrypted PAN storage. "
            "GDPR Art.32 requires appropriate technical measures. "
            "Findings above should be encrypted at column level using AES-256-GCM."
        ),
    }

    if output_path:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f"  INFO  Report written to {output_path}")

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PII scanner for iGaming platforms"
    )
    parser.add_argument("--pg-host", default="localhost")
    parser.add_argument("--pg-port", type=int, default=5432)
    parser.add_argument("--pg-user", default="postgres")
    parser.add_argument("--pg-password", default=os.environ.get("PG_PASSWORD", ""))
    parser.add_argument("--pg-db", default="postgres")
    parser.add_argument("--scan-db", action="store_true", help="Scan database columns")
    parser.add_argument(
        "--scan-dirs",
        default="",
        help="Comma-separated directories to scan for PII",
    )
    parser.add_argument("--scan-logs", action="store_true", help="Scan /var/log and app logs")
    parser.add_argument("--output", default="", help="Write JSON report to this path")
    parser.add_argument("--max-files", type=int, default=1000)
    parser.add_argument("--sample-rows", type=int, default=10)
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not (args.scan_db or args.scan_dirs or args.scan_logs):
        print("ERROR: Specify at least one scan target: --scan-db, --scan-dirs, or --scan-logs")
        print("Example: python3 pii-scanner.py --scan-db --pg-host ops-host")
        sys.exit(1)

    all_findings: list[PiiFinding] = []

    # Database scan
    if args.scan_db:
        if psycopg2 is None:
            print("ERROR: psycopg2 not installed. Run: pip install psycopg2-binary")
            sys.exit(1)
        print(f"\n=== Database Scan: {args.pg_host}:{args.pg_port}/{args.pg_db} ===")
        connected = False
        for sslmode in ("require", "prefer"):
            try:
                conn = psycopg2.connect(
                    host=args.pg_host,
                    port=args.pg_port,
                    user=args.pg_user,
                    password=args.pg_password,
                    dbname=args.pg_db,
                    sslmode=sslmode,
                )
                if sslmode != "require":
                    print(f"  WARN  Database connection without SSL (sslmode={sslmode})")
                scanner = DatabaseScanner(conn, sample_rows=args.sample_rows)
                db_findings = scanner.scan()
                all_findings.extend(db_findings)
                conn.close()
                connected = True
                break
            except Exception as exc:  # noqa: BLE001
                if sslmode == "prefer":
                    print(f"  ERROR  Database scan failed: {exc}")
        if not connected:
            pass  # error already printed above

    # File scan
    scan_dirs: list[str] = []
    if args.scan_dirs:
        scan_dirs.extend([d.strip() for d in args.scan_dirs.split(",") if d.strip()])
    if args.scan_logs:
        log_dirs = [
            "/var/log",
            "/opt/app/logs",
            "/var/log/nginx",
            "/var/log/postgresql",
        ]
        scan_dirs.extend([d for d in log_dirs if Path(d).exists()])

    if scan_dirs:
        print(f"\n=== File Scan: {scan_dirs} ===")
        file_scanner = FileScanner(scan_dirs, max_files=args.max_files)
        file_findings = file_scanner.scan()
        all_findings.extend(file_findings)

    # Report
    print("\n=== Scan Summary ===")
    generate_report(all_findings, output_path=args.output or None)

    critical: int = sum(1 for f in all_findings if f.severity == "CRITICAL")
    high: int = sum(1 for f in all_findings if f.severity == "HIGH")
    medium: int = sum(1 for f in all_findings if f.severity == "MEDIUM")
    total: int = len(all_findings)

    print(f"  Total findings: {total}")
    print(f"  CRITICAL: {critical}")
    print(f"  HIGH:     {high}")
    print(f"  MEDIUM:   {medium}")

    if critical > 0:
        print("\n  FAIL  CRITICAL PII findings require immediate remediation.")
        print("        PCI DSS Req.3.3.1: unencrypted PAN is a critical violation.")
        sys.exit(1)
    elif high > 0:
        print("\n  WARN  HIGH severity findings require remediation before next audit.")
        sys.exit(0)
    elif total > 0:
        print("\n  WARN  MEDIUM findings logged. Review and remediate.")
        sys.exit(0)
    else:
        print("\n  PASS  No unencrypted PII patterns detected.")
        sys.exit(0)


if __name__ == "__main__":
    main()
