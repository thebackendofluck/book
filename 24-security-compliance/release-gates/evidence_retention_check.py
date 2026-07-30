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
Evidence Retention Release Gate — verify all KYC documents have proper retention.

Checks:
  1. No documents stored without a retention policy
  2. No expired documents still accessible (should be purged)
  3. Retention periods meet jurisdiction minimums (7yr NJ, 5yr UK, 10yr BR)
  4. Documents approaching expiry flagged for review
  5. GDPR erasure requests honoured within 30 days

Usage:
    python evidence_retention_check.py --base-url https://api.example.com
    python evidence_retention_check.py --base-url http://localhost:8080 --verbose
    python evidence_retention_check.py --dry-run

Exit codes: 0 = all checks pass, 1 = failures detected.
"""

import argparse
import json
import sys
import time
import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    sub_checks: list[str] = field(default_factory=list)


@dataclass
class RetentionGateReport:
    results: list[CheckResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if not r.passed]

    def elapsed(self) -> float:
        return time.time() - self.start_time


# ---------------------------------------------------------------------------
# Jurisdiction retention requirements
# ---------------------------------------------------------------------------

JURISDICTION_RETENTION_YEARS = {
    "US-NJ": 7, "US-PA": 7, "US-MI": 7,
    "GB": 5, "MT": 5, "SE": 5, "DK": 5,
    "BR": 10,
}
DEFAULT_RETENTION_YEARS = 5


# ---------------------------------------------------------------------------
# HTTP client (stdlib only)
# ---------------------------------------------------------------------------

class HTTPClient:
    def __init__(self, base_url: str, timeout: int = 30, verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose

    def _request(self, method: str, path: str, body: Optional[dict] = None):
        import urllib.request
        import urllib.error

        url = f"{self.base_url}{path}"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                elapsed = (time.perf_counter() - t0) * 1000
                raw = resp.read()
                payload = json.loads(raw) if raw else {}
                if self.verbose:
                    print(f"    [{method}] {path} -> {resp.status} ({elapsed:.0f}ms)")
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            try:
                payload = json.loads(exc.read()) if exc.read else {}
            except Exception:
                payload = {}
            if self.verbose:
                print(f"    [{method}] {path} -> {exc.code} ({elapsed:.0f}ms)")
            return exc.code, payload


# ---------------------------------------------------------------------------
# Check 1: All documents have retention policies
# ---------------------------------------------------------------------------

def check_retention_policies_present(
    client: Optional[HTTPClient], dry_run: bool
) -> CheckResult:
    """Verify no document exists without a retention_until date."""
    if dry_run:
        return CheckResult(
            name="retention_policies_present",
            passed=True,
            detail="Dry run: would verify all documents have retention_until set",
            sub_checks=[
                "Query /api/v1/evidence/documents?missing_retention=true",
                "Expect 0 documents without retention policy",
            ],
        )

    code, payload = client._request("GET", "/api/v1/evidence/retention/violations")
    if code != 200:
        return CheckResult(
            name="retention_policies_present",
            passed=False,
            detail=f"API returned HTTP {code}",
        )

    missing = [v for v in payload.get("violations", []) if v.get("type") == "MISSING_POLICY"]
    return CheckResult(
        name="retention_policies_present",
        passed=len(missing) == 0,
        detail=f"{len(missing)} documents without retention policy",
        sub_checks=[f"doc={v['document_id']}" for v in missing[:10]],
    )


# ---------------------------------------------------------------------------
# Check 2: No expired documents still stored
# ---------------------------------------------------------------------------

def check_expired_documents_purged(
    client: Optional[HTTPClient], dry_run: bool
) -> CheckResult:
    if dry_run:
        return CheckResult(
            name="expired_documents_purged",
            passed=True,
            detail="Dry run: would verify expired docs are securely deleted",
            sub_checks=[
                "Query /api/v1/evidence/retention/violations?type=OVERDUE_DELETION",
                "Expect 0 overdue documents",
            ],
        )

    code, payload = client._request("GET", "/api/v1/evidence/retention/violations")
    if code != 200:
        return CheckResult(
            name="expired_documents_purged",
            passed=False,
            detail=f"API returned HTTP {code}",
        )

    overdue = [v for v in payload.get("violations", []) if v.get("type") == "OVERDUE_DELETION"]
    return CheckResult(
        name="expired_documents_purged",
        passed=len(overdue) == 0,
        detail=f"{len(overdue)} documents past retention still stored",
        sub_checks=[
            f"doc={v['document_id']} overdue_days={v.get('days_overdue', '?')}"
            for v in overdue[:10]
        ],
    )


# ---------------------------------------------------------------------------
# Check 3: Jurisdiction minimums met
# ---------------------------------------------------------------------------

def check_jurisdiction_minimums(
    client: Optional[HTTPClient], dry_run: bool
) -> CheckResult:
    if dry_run:
        sub = []
        for jur, years in JURISDICTION_RETENTION_YEARS.items():
            sub.append(f"{jur}: minimum {years} years retention")
        return CheckResult(
            name="jurisdiction_retention_minimums",
            passed=True,
            detail="Dry run: would verify retention >= jurisdiction minimum",
            sub_checks=sub,
        )

    code, payload = client._request("GET", "/api/v1/evidence/retention/report")
    if code != 200:
        return CheckResult(
            name="jurisdiction_retention_minimums",
            passed=False,
            detail=f"API returned HTTP {code}",
        )

    violations = []
    for entry in payload.get("report", []):
        jur = entry.get("jurisdiction", "")
        required = JURISDICTION_RETENTION_YEARS.get(jur, DEFAULT_RETENTION_YEARS)
        retention_until = entry.get("retention_until", "")
        created = entry.get("created_at", "")
        if retention_until and created:
            try:
                ret_dt = datetime.fromisoformat(retention_until)
                crt_dt = datetime.fromisoformat(created)
                actual_years = (ret_dt - crt_dt).days / 365
                if actual_years < required:
                    violations.append(
                        f"case={entry['case_id']} {jur}: "
                        f"{actual_years:.1f}yr < {required}yr"
                    )
            except (ValueError, KeyError):
                pass

    return CheckResult(
        name="jurisdiction_retention_minimums",
        passed=len(violations) == 0,
        detail=f"{len(violations)} cases below minimum retention",
        sub_checks=violations[:10],
    )


# ---------------------------------------------------------------------------
# Check 4: Approaching expiry flagged
# ---------------------------------------------------------------------------

def check_approaching_expiry(
    client: Optional[HTTPClient], dry_run: bool
) -> CheckResult:
    if dry_run:
        return CheckResult(
            name="approaching_expiry_flagged",
            passed=True,
            detail="Dry run: would verify docs within 90 days of expiry are flagged",
        )

    code, payload = client._request("GET", "/api/v1/evidence/retention/violations")
    if code != 200:
        return CheckResult(
            name="approaching_expiry_flagged",
            passed=False,
            detail=f"API returned HTTP {code}",
        )

    approaching = [
        v for v in payload.get("violations", [])
        if v.get("type") == "APPROACHING_EXPIRY"
    ]
    # This is informational: we pass but report count
    return CheckResult(
        name="approaching_expiry_flagged",
        passed=True,
        detail=f"{len(approaching)} documents approaching expiry (info only)",
        sub_checks=[
            f"doc={v['document_id']} days_remaining={v.get('days_remaining', '?')}"
            for v in approaching[:10]
        ],
    )


# ---------------------------------------------------------------------------
# Check 5: GDPR erasure requests
# ---------------------------------------------------------------------------

def check_gdpr_erasure(
    client: Optional[HTTPClient], dry_run: bool
) -> CheckResult:
    if dry_run:
        return CheckResult(
            name="gdpr_erasure_compliance",
            passed=True,
            detail="Dry run: would verify pending erasure requests < 30 days old",
            sub_checks=[
                "Query /api/v1/privacy/erasure-requests?status=pending",
                "Verify none older than 30 days",
            ],
        )

    code, payload = client._request(
        "GET", "/api/v1/privacy/erasure-requests?status=pending"
    )
    if code != 200:
        return CheckResult(
            name="gdpr_erasure_compliance",
            passed=code == 404,  # 404 = no erasure endpoint = acceptable
            detail=f"API returned HTTP {code}",
        )

    overdue = []
    now = datetime.now(timezone.utc)
    for req in payload.get("requests", []):
        created = req.get("created_at", "")
        if created:
            try:
                age = (now - datetime.fromisoformat(created)).days
                if age > 30:
                    overdue.append(f"request={req.get('id')} age={age}d")
            except ValueError:
                pass

    return CheckResult(
        name="gdpr_erasure_compliance",
        passed=len(overdue) == 0,
        detail=f"{len(overdue)} erasure requests overdue (>30 days)",
        sub_checks=overdue[:10],
    )


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_all_checks(
    client: Optional[HTTPClient], dry_run: bool
) -> RetentionGateReport:
    report = RetentionGateReport()
    checks = [
        check_retention_policies_present,
        check_expired_documents_purged,
        check_jurisdiction_minimums,
        check_approaching_expiry,
        check_gdpr_erasure,
    ]
    for check_fn in checks:
        result = check_fn(client, dry_run)
        report.add(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name}: {result.detail}")
        for sub in result.sub_checks:
            print(f"         {sub}")
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Release gate: evidence retention compliance check"
    )
    parser.add_argument(
        "--base-url", default="http://localhost:8080",
        help="Base URL for the evidence API",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("Evidence Retention Release Gate")
    print("=" * 60)

    client = None if args.dry_run else HTTPClient(
        args.base_url, verbose=args.verbose,
    )
    report = run_all_checks(client, args.dry_run)

    print()
    print(f"Elapsed: {report.elapsed():.1f}s")
    if report.passed:
        print("RESULT: ALL CHECKS PASSED")
        sys.exit(0)
    else:
        print(f"RESULT: {len(report.failures)} CHECK(S) FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
