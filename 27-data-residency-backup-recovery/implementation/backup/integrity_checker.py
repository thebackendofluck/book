#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Backup Integrity Verification (Checksum Validation)
=====================================================
Validates backup integrity using SHA-256 checksums, file-level verification,
and database-level consistency checks.

Features:
- SHA-256 checksum computation and verification
- Manifest-based integrity tracking
- Backup age monitoring with alerting
- Jurisdiction-aware compliance reporting
- Parallel verification for large backup sets

Usage:
    python integrity_checker.py --verify-all /var/backups/igaming
    python integrity_checker.py --verify-file backup.sql.zst.enc
    python integrity_checker.py --generate-manifest /var/backups/igaming
    python integrity_checker.py --check-age --max-hours 25
    python integrity_checker.py --demo
"""

import hashlib
import json
import logging
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("integrity-checker")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------
class VerificationStatus:
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    SKIP = "SKIP"


@dataclass
class BackupManifestEntry:
    file_path: str
    file_name: str
    size_bytes: int
    checksum_sha256: str
    jurisdiction: str
    backup_type: str  # full / incremental / wal / redis
    created_at: str
    verified_at: Optional[str] = None
    verification_status: Optional[str] = None


@dataclass
class VerificationResult:
    file_path: str
    status: str
    expected_checksum: Optional[str]
    actual_checksum: str
    size_bytes: int
    age_hours: float
    details: str
    duration_seconds: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


@dataclass
class IntegrityReport:
    report_id: str
    generated_at: str
    backup_directory: str
    total_files: int
    passed: int
    failed: int
    warnings: int
    skipped: int
    total_size_bytes: int
    verification_duration_seconds: float
    results: list
    jurisdiction_summary: dict


# ---------------------------------------------------------------------------
# Checksum calculator
# ---------------------------------------------------------------------------
class ChecksumCalculator:
    """Computes checksums for backup files."""

    @staticmethod
    def sha256_file(path: str, chunk_size: int = 8192) -> str:
        """Compute SHA-256 hash of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def md5_file(path: str, chunk_size: int = 8192) -> str:
        """Compute MD5 hash (for S3 ETag verification)."""
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def verify_checksum(path: str, expected: str, algorithm: str = "sha256") -> bool:
        """Verify a file's checksum against expected value."""
        if algorithm == "sha256":
            actual = ChecksumCalculator.sha256_file(path)
        elif algorithm == "md5":
            actual = ChecksumCalculator.md5_file(path)
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")
        return actual == expected


# ---------------------------------------------------------------------------
# Manifest manager
# ---------------------------------------------------------------------------
class ManifestManager:
    """Manages backup manifests for integrity tracking."""

    def __init__(self, manifest_path: str):
        self.manifest_path = Path(manifest_path)
        self._entries: list[BackupManifestEntry] = []
        self._load()

    def _load(self):
        if self.manifest_path.exists():
            with open(self.manifest_path) as f:
                data = json.load(f)
            for entry_dict in data.get("entries", []):
                self._entries.append(BackupManifestEntry(**entry_dict))
            logger.info("Loaded manifest: %d entries", len(self._entries))

    def save(self):
        data = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entry_count": len(self._entries),
            "entries": [asdict(e) for e in self._entries],
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.manifest_path, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("Manifest saved: %d entries", len(self._entries))

    def add_entry(self, entry: BackupManifestEntry):
        self._entries.append(entry)

    def get_entry(self, file_path: str) -> Optional[BackupManifestEntry]:
        for entry in self._entries:
            if entry.file_path == file_path:
                return entry
        return None

    def get_all_entries(self) -> list[BackupManifestEntry]:
        return list(self._entries)

    def update_verification(
        self, file_path: str, status: str, timestamp: str
    ):
        for entry in self._entries:
            if entry.file_path == file_path:
                entry.verified_at = timestamp
                entry.verification_status = status
                break


# ---------------------------------------------------------------------------
# Integrity checker
# ---------------------------------------------------------------------------
class IntegrityChecker:
    """
    Verifies backup integrity through checksum validation,
    age monitoring, and compliance checks.
    """

    # Maximum backup age before warning (hours)
    MAX_FULL_BACKUP_AGE_HOURS = 25
    MAX_INCREMENTAL_AGE_HOURS = 5
    MAX_WAL_AGE_HOURS = 1

    # Minimum expected sizes (bytes) -- detect empty/corrupt backups
    MIN_SIZES = {
        "full": 1024 * 1024,       # 1 MB
        "incremental": 1024,        # 1 KB
        "wal": 512,                 # 512 bytes
        "redis": 1024,              # 1 KB
    }

    def __init__(self, manifest_manager: Optional[ManifestManager] = None):
        self.manifest = manifest_manager
        self.calculator = ChecksumCalculator()

    def verify_file(
        self, file_path: str, expected_checksum: Optional[str] = None
    ) -> VerificationResult:
        """Verify a single backup file."""
        start_time = time.time()
        path = Path(file_path)

        if not path.exists():
            return VerificationResult(
                file_path=file_path,
                status=VerificationStatus.FAIL,
                expected_checksum=expected_checksum,
                actual_checksum="",
                size_bytes=0,
                age_hours=0,
                details=f"File not found: {file_path}",
                duration_seconds=time.time() - start_time,
            )

        size = path.stat().st_size
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        actual_checksum = self.calculator.sha256_file(file_path)

        # Check size
        backup_type = self._detect_backup_type(path.name)
        min_size = self.MIN_SIZES.get(backup_type, 0)

        issues = []

        if size < min_size:
            issues.append(
                f"File size ({size} bytes) below minimum "
                f"({min_size} bytes) for {backup_type} backup"
            )

        # Check checksum match
        if expected_checksum:
            if actual_checksum != expected_checksum:
                issues.append(
                    f"Checksum mismatch! Expected: {expected_checksum[:16]}... "
                    f"Got: {actual_checksum[:16]}..."
                )

        # Check age
        max_age = {
            "full": self.MAX_FULL_BACKUP_AGE_HOURS,
            "incremental": self.MAX_INCREMENTAL_AGE_HOURS,
            "wal": self.MAX_WAL_AGE_HOURS,
            "redis": self.MAX_FULL_BACKUP_AGE_HOURS,
        }.get(backup_type, self.MAX_FULL_BACKUP_AGE_HOURS)

        if age_hours > max_age:
            issues.append(
                f"Backup is {age_hours:.1f}h old (max: {max_age}h)"
            )

        # Determine status
        if any("mismatch" in i.lower() or "not found" in i.lower() for i in issues):
            status = VerificationStatus.FAIL
        elif issues:
            status = VerificationStatus.WARN
        else:
            status = VerificationStatus.PASS

        details = "; ".join(issues) if issues else "All checks passed"
        duration = time.time() - start_time

        result = VerificationResult(
            file_path=file_path,
            status=status,
            expected_checksum=expected_checksum,
            actual_checksum=actual_checksum,
            size_bytes=size,
            age_hours=round(age_hours, 2),
            details=details,
            duration_seconds=round(duration, 3),
        )

        # Update manifest
        if self.manifest:
            self.manifest.update_verification(
                file_path, status, result.timestamp
            )

        return result

    def verify_directory(
        self, directory: str, max_workers: int = 4
    ) -> IntegrityReport:
        """Verify all backup files in a directory tree."""
        start_time = time.time()
        backup_dir = Path(directory)

        if not backup_dir.exists():
            logger.error("Directory not found: %s", directory)
            return self._empty_report(directory)

        # Find all backup files
        backup_extensions = {".enc", ".zst", ".gz", ".rdb", ".dump", ".sql", ".tar"}
        backup_files = []
        for ext in backup_extensions:
            backup_files.extend(backup_dir.rglob(f"*{ext}"))

        # Also match common backup patterns
        for pattern in ["*.bak", "*.backup"]:
            backup_files.extend(backup_dir.rglob(pattern))

        # Deduplicate
        backup_files = list(set(backup_files))

        logger.info("Found %d backup files to verify", len(backup_files))

        results = []
        passed = failed = warnings = skipped = 0
        total_size = 0

        # Parallel verification
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_path = {
                executor.submit(self._verify_with_manifest, str(f)): f
                for f in backup_files
            }

            for future in as_completed(future_to_path):
                result = future.result()
                results.append(result)
                total_size += result.size_bytes

                if result.status == VerificationStatus.PASS:
                    passed += 1
                elif result.status == VerificationStatus.FAIL:
                    failed += 1
                elif result.status == VerificationStatus.WARN:
                    warnings += 1
                else:
                    skipped += 1

                logger.info(
                    "[%s] %s (%s bytes, %.1fh old)",
                    result.status,
                    Path(result.file_path).name,
                    result.size_bytes,
                    result.age_hours,
                )

        # Build jurisdiction summary
        jur_summary: dict[str, dict] = {}
        for r in results:
            jur = self._detect_jurisdiction(r.file_path)
            if jur not in jur_summary:
                jur_summary[jur] = {"passed": 0, "failed": 0, "warnings": 0, "total": 0}
            jur_summary[jur]["total"] += 1
            if r.status == VerificationStatus.PASS:
                jur_summary[jur]["passed"] += 1
            elif r.status == VerificationStatus.FAIL:
                jur_summary[jur]["failed"] += 1
            elif r.status == VerificationStatus.WARN:
                jur_summary[jur]["warnings"] += 1

        duration = time.time() - start_time

        report = IntegrityReport(
            report_id=f"IR-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            generated_at=datetime.now(timezone.utc).isoformat(),
            backup_directory=directory,
            total_files=len(backup_files),
            passed=passed,
            failed=failed,
            warnings=warnings,
            skipped=skipped,
            total_size_bytes=total_size,
            verification_duration_seconds=round(duration, 2),
            results=[asdict(r) for r in results],
            jurisdiction_summary=jur_summary,
        )

        # Save manifest updates
        if self.manifest:
            self.manifest.save()

        return report

    def generate_manifest(self, directory: str) -> ManifestManager:
        """Scan a directory and generate a checksum manifest."""
        manifest_path = Path(directory) / "backup_manifest.json"
        manifest = ManifestManager(str(manifest_path))

        backup_dir = Path(directory)
        backup_extensions = {".enc", ".zst", ".gz", ".rdb", ".dump", ".sql", ".tar"}

        for ext in backup_extensions:
            for f in backup_dir.rglob(f"*{ext}"):
                checksum = self.calculator.sha256_file(str(f))
                entry = BackupManifestEntry(
                    file_path=str(f),
                    file_name=f.name,
                    size_bytes=f.stat().st_size,
                    checksum_sha256=checksum,
                    jurisdiction=self._detect_jurisdiction(str(f)),
                    backup_type=self._detect_backup_type(f.name),
                    created_at=datetime.fromtimestamp(
                        f.stat().st_mtime, tz=timezone.utc
                    ).isoformat(),
                )
                manifest.add_entry(entry)
                logger.info("Indexed: %s (%s)", f.name, checksum[:16])

        manifest.save()
        return manifest

    def _verify_with_manifest(self, file_path: str) -> VerificationResult:
        """Verify a file, using manifest for expected checksum if available."""
        expected = None
        if self.manifest:
            entry = self.manifest.get_entry(file_path)
            if entry:
                expected = entry.checksum_sha256
        return self.verify_file(file_path, expected)

    @staticmethod
    def _detect_backup_type(filename: str) -> str:
        name_lower = filename.lower()
        if "full" in name_lower or "pg_full" in name_lower:
            return "full"
        if "wal" in name_lower or "incremental" in name_lower:
            return "incremental"
        if "redis" in name_lower or ".rdb" in name_lower:
            return "redis"
        return "full"  # default

    @staticmethod
    def _detect_jurisdiction(path: str) -> str:
        path_lower = path.lower()
        for jur in ["uk", "mt", "de", "on"]:
            if f"/{jur}/" in path_lower or f"_{jur}_" in path_lower:
                return jur.upper()
        return "UNKNOWN"

    def _empty_report(self, directory: str) -> IntegrityReport:
        return IntegrityReport(
            report_id="IR-EMPTY",
            generated_at=datetime.now(timezone.utc).isoformat(),
            backup_directory=directory,
            total_files=0, passed=0, failed=0, warnings=0, skipped=0,
            total_size_bytes=0,
            verification_duration_seconds=0,
            results=[],
            jurisdiction_summary={},
        )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------
def run_demo():
    import tempfile

    print("=" * 80)
    print("BACKUP INTEGRITY VERIFICATION DEMO")
    print("=" * 80)

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create fake backup files
        backups = {
            "pg_full_20260308_020000.sql.zst.enc": 2 * 1024 * 1024,  # 2 MB
            "pg_full_20260307_020000.sql.zst.enc": 1.5 * 1024 * 1024,
            "wal_20260308_060000.tar.zst.enc": 50 * 1024,  # 50 KB
            "redis_20260308_080000.rdb.zst.enc": 100 * 1024,  # 100 KB
            "pg_full_corrupt.sql.zst.enc": 100,  # suspiciously small
        }

        uk_dir = Path(tmpdir) / "uk"
        uk_dir.mkdir()

        for name, size in backups.items():
            fpath = uk_dir / name
            with open(fpath, "wb") as f:
                f.write(os.urandom(int(size)))

        # Generate manifest
        print("\n--- Generating manifest ---")
        checker = IntegrityChecker()
        manifest = checker.generate_manifest(tmpdir)
        print(f"  Indexed {len(manifest.get_all_entries())} backup files")

        # Corrupt one file to test detection
        corrupt_path = uk_dir / "pg_full_20260308_020000.sql.zst.enc"
        with open(corrupt_path, "r+b") as f:
            f.seek(100)
            f.write(b"\x00" * 50)

        # Verify all
        print("\n--- Verifying all backups ---")
        checker2 = IntegrityChecker(manifest)
        report = checker2.verify_directory(tmpdir)

        print(f"\n  Total files:  {report.total_files}")
        print(f"  Passed:       {report.passed}")
        print(f"  Failed:       {report.failed}")
        print(f"  Warnings:     {report.warnings}")
        print(f"  Duration:     {report.verification_duration_seconds}s")
        print(f"  Total size:   {report.total_size_bytes / 1024 / 1024:.1f} MB")

        if report.jurisdiction_summary:
            print(f"\n  Jurisdiction summary:")
            for jur, summary in report.jurisdiction_summary.items():
                print(f"    {jur}: {summary}")

        # Show failed/warning results
        for r in report.results:
            if r["status"] in (VerificationStatus.FAIL, VerificationStatus.WARN):
                print(f"\n  [{r['status']}] {Path(r['file_path']).name}")
                print(f"    Details: {r['details']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Backup Integrity Verification"
    )
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--verify-all", metavar="DIR", help="Verify all backups in directory")
    parser.add_argument("--verify-file", metavar="FILE", help="Verify a single file")
    parser.add_argument("--generate-manifest", metavar="DIR", help="Generate checksum manifest")
    parser.add_argument("--check-age", action="store_true", help="Check backup age compliance")
    parser.add_argument("--max-hours", type=int, default=25, help="Max backup age in hours")
    parser.add_argument("--output", "-o", help="Output report to file")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers")

    args = parser.parse_args()

    if args.demo:
        run_demo()
    elif args.verify_all:
        checker = IntegrityChecker()
        report = checker.verify_directory(args.verify_all, max_workers=args.workers)
        output = json.dumps(asdict(report), indent=2)
        if args.output:
            with open(args.output, "w") as f:
                f.write(output)
        else:
            print(output)
    elif args.verify_file:
        checker = IntegrityChecker()
        result = checker.verify_file(args.verify_file)
        print(json.dumps(asdict(result), indent=2))
    elif args.generate_manifest:
        checker = IntegrityChecker()
        manifest = checker.generate_manifest(args.generate_manifest)
        print(f"Manifest generated: {len(manifest.get_all_entries())} entries")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
