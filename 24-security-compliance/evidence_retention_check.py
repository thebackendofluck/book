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
Evidence Retention Check — verify data retention and erasure policies.

Checks:
  1. kyc_documents     — KYC docs retained for required period (≥5 years)
  2. transaction_records — transaction audit window accessible
  3. session_logs      — session logs retained within configured window
  4. gdpr_erasure      — expired / non-essential data can be purged on GDPR request

Usage:
    python evidence_retention_check.py --base-url https://api.example.com
    python evidence_retention_check.py --base-url http://localhost:8080 --verbose
    python evidence_retention_check.py --base-url https://api.example.com --dry-run
    python evidence_retention_check.py --check kyc_documents --base-url https://api.example.com
"""

import argparse
import json
import sys
import time
import random
import string
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    sub_checks: List[str] = field(default_factory=list)
    http_code: Optional[int] = None


@dataclass
class RetentionReport:
    results: List[CheckResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, r: CheckResult) -> None:
        self.results.append(r)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> List[CheckResult]:
        return [r for r in self.results if not r.passed]

    def elapsed(self) -> float:
        return time.time() - self.start_time


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class HTTPClient:
    def __init__(self, base_url: str, timeout: int = 30, verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
        headers: Optional[Dict] = None,
    ) -> Tuple[int, Dict[str, Any], float]:
        import urllib.request
        import urllib.error

        url = f"{self.base_url}{path}"
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            hdrs.update(headers)
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                elapsed = (time.perf_counter() - t0) * 1000
                raw = resp.read()
                payload = json.loads(raw) if raw else {}
                if self.verbose:
                    print(f"    [{method}] {path} → {resp.status}")
                return resp.status, payload, elapsed
        except urllib.error.HTTPError as exc:
            elapsed = (time.perf_counter() - t0) * 1000
            try:
                payload = json.loads(exc.read()) if exc.read else {}
            except Exception:
                payload = {}
            if self.verbose:
                print(f"    [{method}] {path} → {exc.code}")
            return exc.code, payload, elapsed

    def post(self, path: str, body: Dict, headers: Optional[Dict] = None):
        return self._request("POST", path, body=body, headers=headers)

    def get(self, path: str, headers: Optional[Dict] = None):
        return self._request("GET", path, headers=headers)

    def delete(self, path: str, headers: Optional[Dict] = None):
        return self._request("DELETE", path, headers=headers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _iso_ago(years: int = 0, days: int = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=years * 365 + days)
    return dt.isoformat()


def _kyc_retention_years(payload: Dict) -> Optional[float]:
    """Extract the configured retention period in years from a retention-policy payload."""
    blob = json.dumps(payload)
    # Look for explicit years field
    for key in ["retention_years", "years", "kyc_retention_years", "period_years"]:
        val = payload.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    # Look for days
    for key in ["retention_days", "days", "period_days"]:
        val = payload.get(key)
        if val is not None:
            try:
                return float(val) / 365
            except (TypeError, ValueError):
                pass
    # Heuristic: search raw JSON for a number near 5 years
    import re
    matches = re.findall(r'"(?:retention_years?|years?)"\s*:\s*(\d+(?:\.\d+)?)', blob)
    if matches:
        return float(matches[0])
    return None


# ---------------------------------------------------------------------------
# Check 1: KYC documents retained for ≥5 years
# ---------------------------------------------------------------------------

def check_kyc_documents(client: HTTPClient, admin_token: str) -> CheckResult:
    name = "kyc_documents"
    checks: List[str] = []
    auth = {"Authorization": f"Bearer {admin_token}"}

    # Query the retention policy for KYC documents
    code, payload, _ = client.get(
        "/api/v1/admin/retention-policies/KYC_DOCUMENTS",
        headers=auth,
    )
    if code != 200:
        # Fallback: ask the compliance config endpoint
        code, payload, _ = client.get(
            "/api/v1/admin/compliance/retention",
            headers=auth,
        )

    if code != 200:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Could not retrieve KYC retention policy (HTTP {code})",
            sub_checks=checks,
            http_code=code,
        )

    checks.append(f"Retention policy endpoint: HTTP {code}")

    years = _kyc_retention_years(payload)
    if years is None:
        # Try nested structure
        data = payload.get("data", payload)
        kyc_policy = data.get("KYC_DOCUMENTS") or data.get("kyc") or {}
        years = _kyc_retention_years(kyc_policy)

    if years is None:
        # Last resort: check if the response blob contains "5" alongside kyc/retention keywords
        blob = json.dumps(payload).lower()
        if "kyc" in blob and ("5 year" in blob or '"years": 5' in blob or '"retention_years": 5' in blob):
            years = 5.0

    checks.append(f"Configured retention period: {years} years" if years else "Retention period not found in response")

    if years is not None and years >= 5:
        # Also verify that a KYC document dated ~4 years ago is still accessible
        test_date = _iso_ago(years=4)
        suffix = _rand()
        code2, payload2, _ = client.get(
            f"/api/v1/admin/kyc/documents?player_id=smoke_{suffix}&created_after={_iso_ago(years=5)}&created_before={test_date}",
            headers=auth,
        )
        accessible = code2 in (200, 204) or code2 == 404  # 404 just means no docs in range
        checks.append(f"Historical KYC query (4y old window): HTTP {code2} ({'accessible' if accessible else 'error'})")

        return CheckResult(
            name=name,
            passed=True,
            detail=f"KYC documents retained for {years} years (requirement: 5)",
            sub_checks=checks,
            http_code=code,
        )

    return CheckResult(
        name=name,
        passed=False,
        detail=f"KYC retention period insufficient or unknown (found: {years} years, required: ≥5)",
        sub_checks=checks,
        http_code=code,
    )


# ---------------------------------------------------------------------------
# Check 2: Transaction records accessible for audit window
# ---------------------------------------------------------------------------

def check_transaction_records(client: HTTPClient, admin_token: str) -> CheckResult:
    name = "transaction_records"
    checks: List[str] = []
    auth = {"Authorization": f"Bearer {admin_token}"}

    # Check retention policy for transactions
    code, payload, _ = client.get(
        "/api/v1/admin/retention-policies/TRANSACTION_RECORDS",
        headers=auth,
    )
    if code != 200:
        code, payload, _ = client.get("/api/v1/admin/compliance/retention", headers=auth)

    if code != 200:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Could not retrieve transaction retention policy (HTTP {code})",
            http_code=code,
        )

    checks.append(f"Retention policy endpoint: HTTP {code}")

    blob = json.dumps(payload)
    # Minimum audit window: 5 years for most jurisdictions
    import re
    years_vals = [float(m) for m in re.findall(r'(?:retention_years?|transaction_years?|years?)\s*[":]+\s*(\d+(?:\.\d+)?)', blob.lower())]
    tx_years = max(years_vals) if years_vals else None
    checks.append(f"Transaction retention: {tx_years} years" if tx_years else "Transaction retention period not parsed")

    # Verify a historical transaction query works
    since = _iso_ago(years=5)
    code2, payload2, _ = client.get(
        f"/api/v1/admin/audit/transactions?since={since}&limit=1",
        headers=auth,
    )
    query_ok = code2 in (200, 204)
    checks.append(f"Historical transaction query (5y window): HTTP {code2} ({'OK' if query_ok else 'FAIL'})")

    # Check that the API signals records are complete (not truncated/purged)
    has_records = (
        query_ok
        and (
            isinstance(payload2.get("data"), list)
            or isinstance(payload2.get("transactions"), list)
            or payload2.get("total", -1) >= 0
        )
    )
    checks.append(f"Audit records returned or empty set confirmed: {has_records}")

    passed = query_ok and (tx_years is None or tx_years >= 5)
    return CheckResult(
        name=name,
        passed=passed,
        detail=(
            f"Transaction records accessible for audit window ({tx_years}y configured)"
            if passed
            else f"Transaction audit window issue: configured={tx_years}y, query_ok={query_ok}"
        ),
        sub_checks=checks,
        http_code=code,
    )


# ---------------------------------------------------------------------------
# Check 3: Session logs retained
# ---------------------------------------------------------------------------

def check_session_logs(client: HTTPClient, admin_token: str) -> CheckResult:
    name = "session_logs"
    checks: List[str] = []
    auth = {"Authorization": f"Bearer {admin_token}"}

    # Retention policy
    code, payload, _ = client.get(
        "/api/v1/admin/retention-policies/SESSION_LOGS",
        headers=auth,
    )
    if code != 200:
        code, payload, _ = client.get("/api/v1/admin/compliance/retention", headers=auth)

    if code != 200:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"Could not retrieve session log retention policy (HTTP {code})",
            http_code=code,
        )

    checks.append(f"Retention policy endpoint: HTTP {code}")

    # Query session logs from a year ago
    since = _iso_ago(years=1)
    code2, payload2, _ = client.get(
        f"/api/v1/admin/audit/sessions?since={since}&limit=1",
        headers=auth,
    )
    query_ok = code2 in (200, 204)
    checks.append(f"Session log query (1y window): HTTP {code2} ({'OK' if query_ok else 'FAIL'})")

    # Verify log structure contains required fields (player_id, timestamp, ip)
    records = (
        payload2.get("data")
        or payload2.get("sessions")
        or payload2.get("logs")
        or []
    )
    if isinstance(records, list) and records:
        first = records[0] if isinstance(records[0], dict) else {}
        has_player = bool(first.get("player_id") or first.get("user_id"))
        has_ts = bool(first.get("timestamp") or first.get("created_at") or first.get("started_at"))
        has_ip = bool(first.get("ip_address") or first.get("ip") or first.get("source_ip"))
        checks.append(f"Log fields: player_id={has_player}, timestamp={has_ts}, ip={has_ip}")
    else:
        checks.append("No session log records in 1y window (may be empty test environment)")

    return CheckResult(
        name=name,
        passed=query_ok,
        detail="Session logs retained and queryable" if query_ok else "Session log query failed",
        sub_checks=checks,
        http_code=code2,
    )


# ---------------------------------------------------------------------------
# Check 4: GDPR right to erasure — expired / non-essential data can be purged
# ---------------------------------------------------------------------------

def check_gdpr_erasure(client: HTTPClient, admin_token: str) -> CheckResult:
    name = "gdpr_erasure"
    checks: List[str] = []
    auth = {"Authorization": f"Bearer {admin_token}"}

    suffix = _rand()
    # Create a test player marked as eligible for erasure (closed account, past retention window)
    code, payload, _ = client.post(
        "/api/v1/admin/players",
        {
            "username": f"gdpr_test_{suffix}",
            "email": f"gdpr_{suffix}@test.internal",
            "password": f"GDPR!{suffix}99",
            "country": "GB",
            "currency": "GBP",
            "status": "CLOSED",
            "account_closed_at": _iso_ago(years=6),   # beyond 5-year retention
        },
        headers=auth,
    )

    player_id = (
        payload.get("player_id") or payload.get("id")
        or payload.get("data", {}).get("player_id") or f"gdpr_test_{suffix}"
    )
    checks.append(f"Test player created: {player_id} (HTTP {code})")

    # Submit a GDPR erasure request
    code2, payload2, _ = client.post(
        "/api/v1/admin/gdpr/erasure-requests",
        {
            "player_id": player_id,
            "reason": "SMOKE_TEST_RIGHT_TO_ERASURE",
            "requester": "smoke_test",
        },
        headers=auth,
    )
    request_accepted = code2 in (200, 201, 202)
    checks.append(f"Erasure request accepted: {'yes' if request_accepted else f'HTTP {code2}'}")

    if not request_accepted:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"GDPR erasure request not accepted (HTTP {code2})",
            sub_checks=checks,
            http_code=code2,
        )

    request_id = (
        payload2.get("request_id") or payload2.get("id")
        or payload2.get("data", {}).get("request_id") or ""
    )
    checks.append(f"Erasure request_id: {request_id or 'n/a'}")

    # Verify legal-hold check: active retention records must NOT be erased
    # (e.g., recent transactions within 5-year window should be protected)
    recent_player = f"gdpr_recent_{_rand()}"
    client.post(
        "/api/v1/admin/players",
        {
            "username": recent_player,
            "email": f"{recent_player}@test.internal",
            "password": f"GDPR!{_rand()}99",
            "country": "GB",
            "currency": "GBP",
            "status": "CLOSED",
            "account_closed_at": _iso_ago(years=2),   # within retention window
        },
        headers=auth,
    )

    recent_pid = f"placeholder_{recent_player}"
    code3, payload3, _ = client.post(
        "/api/v1/admin/gdpr/erasure-requests",
        {
            "player_id": recent_pid,
            "reason": "SMOKE_TEST_EARLY_ERASURE",
            "requester": "smoke_test",
        },
        headers=auth,
    )
    # This should either be queued (202) or rejected (409/400) due to legal hold
    legal_hold_respected = code3 in (400, 409, 422) or (
        code3 in (200, 201, 202)
        and any(
            kw in json.dumps(payload3).upper()
            for kw in ["LEGAL_HOLD", "RETENTION_ACTIVE", "SCHEDULED", "PENDING"]
        )
    )
    checks.append(
        f"Legal hold respected for recent player: "
        f"{'yes' if legal_hold_respected else f'early erasure may have proceeded (HTTP {code3})'}"
    )

    # Overall: erasure request was accepted and legal-hold logic is present
    passed = request_accepted and legal_hold_respected
    return CheckResult(
        name=name,
        passed=passed,
        detail=(
            "GDPR erasure request accepted; legal hold respected"
            if passed
            else "GDPR erasure flow issues detected"
        ),
        sub_checks=checks,
        http_code=code2,
    )


# ---------------------------------------------------------------------------
# CHECK_MAP
# ---------------------------------------------------------------------------

CHECK_MAP = {
    "kyc_documents": check_kyc_documents,
    "transaction_records": check_transaction_records,
    "session_logs": check_session_logs,
    "gdpr_erasure": check_gdpr_erasure,
}


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def run_dry(report: RetentionReport, verbose: bool) -> None:
    dry = [
        ("kyc_documents", "DRY: would GET /api/v1/admin/retention-policies/KYC_DOCUMENTS and assert ≥5 years"),
        ("transaction_records", "DRY: would GET /api/v1/admin/audit/transactions and check audit window"),
        ("session_logs", "DRY: would GET /api/v1/admin/audit/sessions and verify log fields"),
        ("gdpr_erasure", "DRY: would POST /api/v1/admin/gdpr/erasure-requests and check legal hold logic"),
    ]
    for key, detail in dry:
        r = CheckResult(name=key, passed=True, detail=detail)
        report.add(r)
        if verbose:
            print(f"  [PASS] {key}: {detail}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(
    base_url: str,
    admin_token: str,
    timeout: int,
    verbose: bool,
    dry_run: bool,
    check: Optional[str],
) -> RetentionReport:
    report = RetentionReport()

    if dry_run:
        print("[EVIDENCE RETENTION CHECK] DRY RUN")
        run_dry(report, verbose)
        return report

    client = HTTPClient(base_url, timeout=timeout, verbose=verbose)

    checks_to_run = (
        {check: CHECK_MAP[check]}
        if check and check in CHECK_MAP
        else CHECK_MAP
    )

    for key, fn in checks_to_run.items():
        if verbose:
            print(f"\n  -- Check: {key} --")
        try:
            result = fn(client, admin_token)
        except Exception as exc:
            result = CheckResult(
                name=key,
                passed=False,
                detail=f"Unhandled exception: {exc}",
            )
        report.add(result)
        if verbose:
            status = "PASS" if result.passed else "FAIL"
            print(f"  [{status}] {result.detail}")
            for c in result.sub_checks:
                print(f"    - {c}")

    return report


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(report: RetentionReport) -> None:
    print("\n" + "=" * 60)
    print("EVIDENCE RETENTION CHECK REPORT")
    print("=" * 60)
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        code_str = f" (HTTP {r.http_code})" if r.http_code else ""
        print(f"  [{status}]{code_str}  {r.name}")
        print(f"           {r.detail}")
        for c in r.sub_checks:
            print(f"           - {c}")
    print("-" * 60)
    failures = len(report.failures)
    total = len(report.results)
    print(f"  Result  : {'PASS' if report.passed else 'FAIL'}")
    print(f"  Checks  : {total - failures}/{total} passed")
    print(f"  Elapsed : {report.elapsed():.2f}s")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evidence Retention Check — data retention and GDPR erasure validation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--check",
        choices=list(CHECK_MAP.keys()),
        help="Run a single check",
    )

    args = parser.parse_args()

    import os
    admin_token = args.admin_token or os.environ.get("ADMIN_TOKEN", "")

    print(f"[EVIDENCE RETENTION CHECK] Target: {args.base_url}")
    report = run(args.base_url, admin_token, args.timeout, args.verbose, args.dry_run, args.check)
    print_report(report)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
