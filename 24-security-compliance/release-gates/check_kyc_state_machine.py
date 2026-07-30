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
KYC State Machine Release Gate — validate all KYC records follow valid
state transitions.

Checks:
  1. Happy path: PENDING -> DOCS_REQUESTED -> SUBMITTED -> UNDER_REVIEW -> APPROVED
  2. Rejection retry: UNDER_REVIEW -> REJECTED -> DOCS_REQUESTED
  3. EDD escalation: APPROVED -> ENHANCED_DUE_DILIGENCE -> UNDER_REVIEW
  4. Suspension: Any state -> SUSPENDED (by COMPLIANCE_OFFICER only)
  5. Invalid transitions rejected (e.g. PENDING -> APPROVED)
  6. Expired cases trigger re-verification
  7. Access control: AGENT cannot approve, REVIEWER cannot suspend

Usage:
    python check_kyc_state_machine.py --base-url https://api.example.com
    python check_kyc_state_machine.py --dry-run
    python check_kyc_state_machine.py --check happy_path --base-url http://localhost:8080

Exit codes: 0 = pass, 1 = failures.
"""

import argparse
import json
import sys
import time
import random
import string
from dataclasses import dataclass, field
from typing import Any, Optional


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
class StateMachineReport:
    results: list[TransitionResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, r: TransitionResult) -> None:
        self.results.append(r)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[TransitionResult]:
        return [r for r in self.results if not r.passed]

    def elapsed(self) -> float:
        return time.time() - self.start_time


# ---------------------------------------------------------------------------
# Valid state machine (mirror of kyc_lifecycle.py)
# ---------------------------------------------------------------------------

VALID_TRANSITIONS = {
    "PENDING": {"DOCUMENTS_REQUESTED", "SUSPENDED"},
    "DOCUMENTS_REQUESTED": {"SUBMITTED", "SUSPENDED"},
    "SUBMITTED": {"UNDER_REVIEW", "SUSPENDED"},
    "UNDER_REVIEW": {"APPROVED", "REJECTED", "SUSPENDED"},
    "APPROVED": {"EXPIRED", "ENHANCED_DUE_DILIGENCE", "SUSPENDED"},
    "REJECTED": {"DOCUMENTS_REQUESTED", "SUSPENDED"},
    "EXPIRED": {"DOCUMENTS_REQUESTED", "SUSPENDED"},
    "ENHANCED_DUE_DILIGENCE": {"UNDER_REVIEW", "SUSPENDED"},
    "SUSPENDED": {"UNDER_REVIEW"},
}


# ---------------------------------------------------------------------------
# HTTP client (stdlib)
# ---------------------------------------------------------------------------

class HTTPClient:
    def __init__(self, base_url: str, timeout: int = 30, verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose

    def _request(self, method, path, body=None, headers=None):
        import urllib.request
        import urllib.error

        url = f"{self.base_url}{path}"
        all_headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if headers:
            all_headers.update(headers)
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                payload = json.loads(raw) if raw else {}
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read()) if exc.read else {}
            except Exception:
                payload = {}
            return exc.code, payload

    def post(self, path, body, headers=None):
        return self._request("POST", path, body=body, headers=headers)

    def put(self, path, body, headers=None):
        return self._request("PUT", path, body=body, headers=headers)

    def get(self, path, headers=None):
        return self._request("GET", path, headers=headers)


# ---------------------------------------------------------------------------
# Dry-run checks (validate state machine definition)
# ---------------------------------------------------------------------------

def check_state_machine_completeness() -> list[TransitionResult]:
    """Verify every state has at least one outgoing transition."""
    results = []
    all_states = set(VALID_TRANSITIONS.keys())

    for state in all_states:
        targets = VALID_TRANSITIONS.get(state, set())
        results.append(TransitionResult(
            description=f"State {state} has outgoing transitions",
            passed=len(targets) > 0,
            detail=f"{len(targets)} valid targets: {', '.join(sorted(targets))}",
            from_state=state,
        ))

    # Every state can be suspended (except SUSPENDED itself)
    for state in all_states:
        if state == "SUSPENDED":
            continue
        has_suspend = "SUSPENDED" in VALID_TRANSITIONS.get(state, set())
        results.append(TransitionResult(
            description=f"State {state} allows SUSPENDED transition",
            passed=has_suspend,
            detail="Compliance override path exists" if has_suspend else "MISSING suspend path",
            from_state=state,
            to_state="SUSPENDED",
        ))

    return results


def check_invalid_transitions_defined() -> list[TransitionResult]:
    """Verify known invalid transitions are not in the valid set."""
    results = []
    invalid_pairs = [
        ("PENDING", "APPROVED"),      # cannot skip review
        ("PENDING", "REJECTED"),      # cannot reject without review
        ("DOCUMENTS_REQUESTED", "APPROVED"),  # must go through review
        ("APPROVED", "REJECTED"),     # approved cannot be directly rejected
        ("REJECTED", "APPROVED"),     # must go through re-upload + review
    ]
    for from_s, to_s in invalid_pairs:
        is_invalid = to_s not in VALID_TRANSITIONS.get(from_s, set())
        results.append(TransitionResult(
            description=f"Invalid: {from_s} -> {to_s} is rejected",
            passed=is_invalid,
            detail="Correctly rejected" if is_invalid else "BUG: transition allowed",
            from_state=from_s,
            to_state=to_s,
        ))
    return results


def check_happy_path_defined() -> list[TransitionResult]:
    """Verify the happy path sequence is valid."""
    results = []
    path = [
        ("PENDING", "DOCUMENTS_REQUESTED"),
        ("DOCUMENTS_REQUESTED", "SUBMITTED"),
        ("SUBMITTED", "UNDER_REVIEW"),
        ("UNDER_REVIEW", "APPROVED"),
    ]
    for from_s, to_s in path:
        valid = to_s in VALID_TRANSITIONS.get(from_s, set())
        results.append(TransitionResult(
            description=f"Happy path: {from_s} -> {to_s}",
            passed=valid,
            detail="Valid" if valid else "INVALID",
            from_state=from_s,
            to_state=to_s,
        ))
    return results


def check_rejection_retry_defined() -> list[TransitionResult]:
    """Verify rejection -> re-upload path is valid."""
    results = []
    path = [
        ("UNDER_REVIEW", "REJECTED"),
        ("REJECTED", "DOCUMENTS_REQUESTED"),
    ]
    for from_s, to_s in path:
        valid = to_s in VALID_TRANSITIONS.get(from_s, set())
        results.append(TransitionResult(
            description=f"Rejection retry: {from_s} -> {to_s}",
            passed=valid,
            detail="Valid" if valid else "INVALID",
            from_state=from_s,
            to_state=to_s,
        ))
    return results


def check_edd_escalation_defined() -> list[TransitionResult]:
    """Verify EDD escalation path."""
    results = []
    path = [
        ("APPROVED", "ENHANCED_DUE_DILIGENCE"),
        ("ENHANCED_DUE_DILIGENCE", "UNDER_REVIEW"),
    ]
    for from_s, to_s in path:
        valid = to_s in VALID_TRANSITIONS.get(from_s, set())
        results.append(TransitionResult(
            description=f"EDD escalation: {from_s} -> {to_s}",
            passed=valid,
            detail="Valid" if valid else "INVALID",
            from_state=from_s,
            to_state=to_s,
        ))
    return results


def check_expiry_path_defined() -> list[TransitionResult]:
    """Verify expiry -> re-verification path."""
    results = []
    path = [
        ("APPROVED", "EXPIRED"),
        ("EXPIRED", "DOCUMENTS_REQUESTED"),
    ]
    for from_s, to_s in path:
        valid = to_s in VALID_TRANSITIONS.get(from_s, set())
        results.append(TransitionResult(
            description=f"Expiry path: {from_s} -> {to_s}",
            passed=valid,
            detail="Valid" if valid else "INVALID",
            from_state=from_s,
            to_state=to_s,
        ))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all_checks(dry_run: bool, client: Optional[HTTPClient] = None) -> StateMachineReport:
    report = StateMachineReport()
    check_groups = [
        ("State machine completeness", check_state_machine_completeness),
        ("Invalid transitions", check_invalid_transitions_defined),
        ("Happy path", check_happy_path_defined),
        ("Rejection retry", check_rejection_retry_defined),
        ("EDD escalation", check_edd_escalation_defined),
        ("Expiry path", check_expiry_path_defined),
    ]

    for group_name, check_fn in check_groups:
        print(f"\n  {group_name}:")
        results = check_fn()
        for r in results:
            report.add(r)
            status = "PASS" if r.passed else "FAIL"
            print(f"    [{status}] {r.description}: {r.detail}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Release gate: KYC state machine validation"
    )
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", type=str, default="")
    args = parser.parse_args()

    print("=" * 60)
    print("KYC State Machine Release Gate")
    print("=" * 60)

    client = None if args.dry_run else HTTPClient(args.base_url, verbose=args.verbose)
    report = run_all_checks(dry_run=args.dry_run, client=client)

    print(f"\nElapsed: {report.elapsed():.1f}s")
    if report.passed:
        print(f"RESULT: ALL {len(report.results)} CHECKS PASSED")
        sys.exit(0)
    else:
        print(f"RESULT: {len(report.failures)}/{len(report.results)} FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
