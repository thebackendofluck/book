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
KYC State Machine Check — verify all valid transitions are accepted and invalid
ones are rejected by the KYC service.

Transitions tested:
  1. PENDING → DOCUMENTS_REQUESTED → UNDER_REVIEW → APPROVED
  2. PENDING → DOCUMENTS_REQUESTED → UNDER_REVIEW → REJECTED → DOCUMENTS_REQUESTED (retry)
  3. APPROVED → ENHANCED_DUE_DILIGENCE  (high-value trigger)
  4. Any state → SUSPENDED              (fraud/compliance override)
  5. Invalid transitions rejected        (e.g. PENDING → APPROVED)

Usage:
    python kyc_state_machine_check.py --base-url https://api.example.com
    python kyc_state_machine_check.py --base-url http://localhost:8080 --verbose
    python kyc_state_machine_check.py --base-url https://api.example.com --dry-run
    python kyc_state_machine_check.py --check invalid_transitions --base-url https://...
"""

import argparse
import json
import sys
import time
import random
import string
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class TransitionResult:
    description: str
    passed: bool
    detail: str
    from_state: str = ""
    to_state: str = ""
    http_code: Optional[int] = None


@dataclass
class KYCCheckReport:
    results: List[TransitionResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, r: TransitionResult) -> None:
        self.results.append(r)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> List[TransitionResult]:
        return [r for r in self.results if not r.passed]

    def elapsed(self) -> float:
        return time.time() - self.start_time


# ---------------------------------------------------------------------------
# HTTP client (stdlib only)
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
        all_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            all_headers.update(headers)

        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
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

    def put(self, path: str, body: Dict, headers: Optional[Dict] = None):
        return self._request("PUT", path, body=body, headers=headers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _create_kyc_case(client: HTTPClient, admin_token: str) -> str:
    """Create a fresh KYC case in PENDING state. Returns case_id or ''."""
    suffix = _rand()
    code, payload, _ = client.post(
        "/api/v1/admin/kyc/cases",
        {
            "player_id": f"smoke_{suffix}",
            "jurisdiction": "GB",
            "trigger": "REGISTRATION",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if code in (200, 201):
        return (
            payload.get("case_id")
            or payload.get("id")
            or payload.get("data", {}).get("case_id")
            or ""
        )
    return ""


def _transition(
    client: HTTPClient,
    admin_token: str,
    case_id: str,
    new_state: str,
    extra: Optional[Dict] = None,
) -> Tuple[int, Dict]:
    body: Dict = {"status": new_state}
    if extra:
        body.update(extra)
    code, payload, _ = client.put(
        f"/api/v1/admin/kyc/cases/{case_id}/status",
        body,
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    return code, payload


def _get_state(client: HTTPClient, admin_token: str, case_id: str) -> str:
    code, payload, _ = client.get(
        f"/api/v1/admin/kyc/cases/{case_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if code == 200:
        data = payload.get("data", payload)
        return (data.get("status") or data.get("kyc_status") or "").upper()
    return ""


def _assert_state(
    client: HTTPClient,
    admin_token: str,
    case_id: str,
    expected: str,
) -> bool:
    actual = _get_state(client, admin_token, case_id)
    return actual == expected.upper() or actual == ""  # tolerate empty if GET not supported


# ---------------------------------------------------------------------------
# Check 1: Happy path — PENDING → DOCUMENTS_REQUESTED → UNDER_REVIEW → APPROVED
# ---------------------------------------------------------------------------

def check_happy_path(
    client: HTTPClient, admin_token: str
) -> List[TransitionResult]:
    results = []
    case_id = _create_kyc_case(client, admin_token)
    if not case_id:
        return [TransitionResult(
            description="Happy path (setup)",
            passed=False,
            detail="Could not create KYC case",
        )]

    transitions = [
        ("PENDING", "DOCUMENTS_REQUESTED", {}),
        ("DOCUMENTS_REQUESTED", "UNDER_REVIEW", {"documents": ["passport", "utility_bill"]}),
        ("UNDER_REVIEW", "APPROVED", {"reviewer_id": "smoke_reviewer"}),
    ]

    for from_state, to_state, extra in transitions:
        code, payload = _transition(client, admin_token, case_id, to_state, extra)
        success = code in (200, 201, 204)
        results.append(TransitionResult(
            description=f"Happy path: {from_state} → {to_state}",
            passed=success,
            detail=f"HTTP {code}" if success else f"Unexpected HTTP {code}: {json.dumps(payload)[:100]}",
            from_state=from_state,
            to_state=to_state,
            http_code=code,
        ))

    return results


# ---------------------------------------------------------------------------
# Check 2: Rejection + retry — UNDER_REVIEW → REJECTED → DOCUMENTS_REQUESTED
# ---------------------------------------------------------------------------

def check_rejection_retry(
    client: HTTPClient, admin_token: str
) -> List[TransitionResult]:
    results = []
    case_id = _create_kyc_case(client, admin_token)
    if not case_id:
        return [TransitionResult(
            description="Rejection/retry (setup)",
            passed=False,
            detail="Could not create KYC case",
        )]

    # Advance to UNDER_REVIEW
    for state in ["DOCUMENTS_REQUESTED", "UNDER_REVIEW"]:
        _transition(client, admin_token, case_id, state)

    # Reject
    code, payload = _transition(
        client, admin_token, case_id, "REJECTED",
        {"reason": "DOCUMENT_QUALITY_POOR"},
    )
    results.append(TransitionResult(
        description="Rejection retry: UNDER_REVIEW → REJECTED",
        passed=code in (200, 201, 204),
        detail=f"HTTP {code}",
        from_state="UNDER_REVIEW",
        to_state="REJECTED",
        http_code=code,
    ))

    # Retry
    code2, payload2 = _transition(
        client, admin_token, case_id, "DOCUMENTS_REQUESTED",
        {"reason": "RETRY_AFTER_REJECTION"},
    )
    results.append(TransitionResult(
        description="Rejection retry: REJECTED → DOCUMENTS_REQUESTED (retry)",
        passed=code2 in (200, 201, 204),
        detail=f"HTTP {code2}",
        from_state="REJECTED",
        to_state="DOCUMENTS_REQUESTED",
        http_code=code2,
    ))

    return results


# ---------------------------------------------------------------------------
# Check 3: APPROVED → ENHANCED_DUE_DILIGENCE on high-value trigger
# ---------------------------------------------------------------------------

def check_edd_trigger(
    client: HTTPClient, admin_token: str
) -> List[TransitionResult]:
    results = []
    case_id = _create_kyc_case(client, admin_token)
    if not case_id:
        return [TransitionResult(
            description="EDD trigger (setup)",
            passed=False,
            detail="Could not create KYC case",
        )]

    for state in ["DOCUMENTS_REQUESTED", "UNDER_REVIEW", "APPROVED"]:
        _transition(client, admin_token, case_id, state)

    code, payload = _transition(
        client, admin_token, case_id, "ENHANCED_DUE_DILIGENCE",
        {"trigger": "HIGH_VALUE_TRANSACTION", "transaction_amount": 50000},
    )
    results.append(TransitionResult(
        description="EDD: APPROVED → ENHANCED_DUE_DILIGENCE (high-value trigger)",
        passed=code in (200, 201, 204),
        detail=f"HTTP {code}",
        from_state="APPROVED",
        to_state="ENHANCED_DUE_DILIGENCE",
        http_code=code,
    ))

    return results


# ---------------------------------------------------------------------------
# Check 4: Any state → SUSPENDED (fraud/compliance override)
# ---------------------------------------------------------------------------

def check_suspended_override(
    client: HTTPClient, admin_token: str
) -> List[TransitionResult]:
    results = []

    test_from_states = ["PENDING", "UNDER_REVIEW", "APPROVED", "REJECTED"]

    for from_state in test_from_states:
        case_id = _create_kyc_case(client, admin_token)
        if not case_id:
            results.append(TransitionResult(
                description=f"SUSPENDED override from {from_state} (setup)",
                passed=False,
                detail="Could not create KYC case",
            ))
            continue

        # Advance to desired state
        state_sequence = {
            "PENDING": [],
            "UNDER_REVIEW": ["DOCUMENTS_REQUESTED", "UNDER_REVIEW"],
            "APPROVED": ["DOCUMENTS_REQUESTED", "UNDER_REVIEW", "APPROVED"],
            "REJECTED": ["DOCUMENTS_REQUESTED", "UNDER_REVIEW", "REJECTED"],
        }
        for s in state_sequence.get(from_state, []):
            _transition(client, admin_token, case_id, s)

        # Apply SUSPENDED override
        code, payload = _transition(
            client, admin_token, case_id, "SUSPENDED",
            {"reason": "FRAUD_DETECTED", "override": True},
        )
        results.append(TransitionResult(
            description=f"SUSPENDED override: {from_state} → SUSPENDED",
            passed=code in (200, 201, 204),
            detail=f"HTTP {code}",
            from_state=from_state,
            to_state="SUSPENDED",
            http_code=code,
        ))

    return results


# ---------------------------------------------------------------------------
# Check 5: Invalid transitions are rejected
# ---------------------------------------------------------------------------

INVALID_TRANSITIONS = [
    ("PENDING", "APPROVED"),
    ("PENDING", "UNDER_REVIEW"),
    ("PENDING", "REJECTED"),
    ("APPROVED", "PENDING"),
    ("REJECTED", "APPROVED"),
]


def check_invalid_transitions(
    client: HTTPClient, admin_token: str
) -> List[TransitionResult]:
    results = []

    for from_state, to_state in INVALID_TRANSITIONS:
        case_id = _create_kyc_case(client, admin_token)
        if not case_id:
            results.append(TransitionResult(
                description=f"Invalid transition {from_state} → {to_state} (setup)",
                passed=False,
                detail="Could not create KYC case",
            ))
            continue

        # Advance to from_state
        state_sequence = {
            "PENDING": [],
            "APPROVED": ["DOCUMENTS_REQUESTED", "UNDER_REVIEW", "APPROVED"],
            "REJECTED": ["DOCUMENTS_REQUESTED", "UNDER_REVIEW", "REJECTED"],
        }
        for s in state_sequence.get(from_state, []):
            _transition(client, admin_token, case_id, s)

        # Attempt invalid transition — must be rejected (4xx)
        code, payload = _transition(client, admin_token, case_id, to_state)
        rejected = code in (400, 409, 422, 403)
        results.append(TransitionResult(
            description=f"Invalid transition {from_state} → {to_state} must be rejected",
            passed=rejected,
            detail=f"HTTP {code} ({'correctly rejected' if rejected else 'INCORRECTLY ACCEPTED'})",
            from_state=from_state,
            to_state=to_state,
            http_code=code,
        ))

    return results


# ---------------------------------------------------------------------------
# CHECK_MAP
# ---------------------------------------------------------------------------

CHECK_MAP = {
    "happy_path": check_happy_path,
    "rejection_retry": check_rejection_retry,
    "edd_trigger": check_edd_trigger,
    "suspended_override": check_suspended_override,
    "invalid_transitions": check_invalid_transitions,
}


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def run_dry(report: KYCCheckReport, verbose: bool) -> None:
    dry = [
        ("Happy path: PENDING → DOCUMENTS_REQUESTED → UNDER_REVIEW → APPROVED", "PENDING", "APPROVED"),
        ("Rejection retry: UNDER_REVIEW → REJECTED → DOCUMENTS_REQUESTED", "UNDER_REVIEW", "DOCUMENTS_REQUESTED"),
        ("EDD: APPROVED → ENHANCED_DUE_DILIGENCE", "APPROVED", "ENHANCED_DUE_DILIGENCE"),
        ("SUSPENDED override from PENDING, UNDER_REVIEW, APPROVED, REJECTED", "ANY", "SUSPENDED"),
        ("Invalid transitions rejected (e.g. PENDING → APPROVED)", "PENDING", "APPROVED (rejected)"),
    ]
    for desc, frm, to in dry:
        r = TransitionResult(
            description=f"DRY: {desc}",
            passed=True,
            detail="DRY: would call PUT /api/v1/admin/kyc/cases/{id}/status",
            from_state=frm,
            to_state=to,
        )
        report.add(r)
        if verbose:
            print(f"  [PASS] {desc}")


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
) -> KYCCheckReport:
    report = KYCCheckReport()

    if dry_run:
        print("[KYC STATE MACHINE CHECK] DRY RUN")
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
            sub_results = fn(client, admin_token)
        except Exception as exc:
            sub_results = [TransitionResult(
                description=key,
                passed=False,
                detail=f"Unhandled exception: {exc}",
            )]
        for r in sub_results:
            report.add(r)
            if verbose:
                status = "PASS" if r.passed else "FAIL"
                print(f"    [{status}] {r.description}: {r.detail}")

    return report


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(report: KYCCheckReport) -> None:
    print("\n" + "=" * 60)
    print("KYC STATE MACHINE CHECK REPORT")
    print("=" * 60)
    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        arrow = f"{r.from_state} → {r.to_state}" if r.from_state else ""
        print(f"  [{status}]  {r.description}")
        if arrow:
            print(f"           Transition: {arrow}  |  {r.detail}")
        else:
            print(f"           {r.detail}")
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
        description="KYC State Machine Check — validate KYC transition rules",
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
        help="Run a single check instead of all",
    )

    args = parser.parse_args()

    import os
    admin_token = args.admin_token or os.environ.get("ADMIN_TOKEN", "")

    print(f"[KYC STATE MACHINE CHECK] Target: {args.base_url}")
    report = run(args.base_url, admin_token, args.timeout, args.verbose, args.dry_run, args.check)
    print_report(report)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
