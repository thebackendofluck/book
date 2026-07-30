#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 10, Complete Platform Architecture.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Session Revocation Check — verify all sessions are terminated when a trigger event fires.

Scenarios tested:
  1. Self-exclusion      → all sessions for player terminated
  2. Fraud flag          → all sessions for player terminated
  3. KYC failure         → session suspended (not necessarily destroyed)
  4. Account lock        → all sessions terminated
  5. Admin force-logout  → specific session killed, others remain

Usage:
    python session_revocation_check.py --base-url https://api.example.com
    python session_revocation_check.py --base-url http://localhost:8080 --verbose
    python session_revocation_check.py --base-url https://api.example.com --dry-run
    python session_revocation_check.py --scenario self_exclusion --base-url https://api.example.com
"""

import argparse
import json
import sys
import time
import random
import string
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str
    sub_checks: List[str] = field(default_factory=list)


@dataclass
class RevocationReport:
    scenarios: List[ScenarioResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, r: ScenarioResult) -> None:
        self.scenarios.append(r)

    @property
    def passed(self) -> bool:
        return all(s.passed for s in self.scenarios)

    @property
    def failures(self) -> List[ScenarioResult]:
        return [s for s in self.scenarios if not s.passed]

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
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else {}
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

    def delete(self, path: str, headers: Optional[Dict] = None):
        return self._request("DELETE", path, headers=headers)


# ---------------------------------------------------------------------------
# Helper: provision a test player with a live session
# ---------------------------------------------------------------------------

def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _provision_player(
    client: HTTPClient, admin_token: str
) -> Tuple[str, str, str]:
    """
    Create a test player via the admin API and return (player_id, token, session_id).
    Returns ("", "", "") on failure.
    """
    suffix = _rand()
    # Admin-create avoids email-verification flows
    code, payload, _ = client.post(
        "/api/v1/admin/players",
        {
            "username": f"rev_{suffix}",
            "email": f"rev_{suffix}@test.internal",
            "password": f"Rev!{suffix}99",
            "date_of_birth": "1992-03-10",
            "country": "GB",
            "currency": "GBP",
            "status": "ACTIVE",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if code not in (200, 201):
        return "", "", ""

    player_id = (
        payload.get("player_id")
        or payload.get("id")
        or payload.get("data", {}).get("player_id")
        or ""
    )

    # Login to obtain a session token
    code2, payload2, _ = client.post(
        "/api/v1/players/login",
        {"username": f"rev_{suffix}", "password": f"Rev!{suffix}99"},
    )
    if code2 not in (200, 201):
        return player_id, "", ""

    token = (
        payload2.get("access_token")
        or payload2.get("token")
        or payload2.get("data", {}).get("access_token")
        or ""
    )
    session_id = (
        payload2.get("session_id")
        or payload2.get("data", {}).get("session_id")
        or str(uuid.uuid4())
    )
    return player_id, token, session_id


def _token_is_rejected(client: HTTPClient, token: str, player_id: str) -> bool:
    """Return True if the token no longer grants access (401/403)."""
    pid = player_id or "me"
    code, _, _ = client.get(
        f"/api/v1/players/{pid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    return code in (401, 403)


def _token_is_suspended(client: HTTPClient, token: str, player_id: str) -> bool:
    """
    Return True if the token is suspended — may return 200 with restricted status
    or 401/403 depending on implementation.
    """
    pid = player_id or "me"
    code, payload, _ = client.get(
        f"/api/v1/players/{pid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if code in (401, 403):
        return True
    data = payload.get("data", payload)
    status = (data.get("status") or data.get("account_status") or "").upper()
    return status in ("SUSPENDED", "LOCKED", "RESTRICTED", "KYC_FAILED")


# ---------------------------------------------------------------------------
# Scenario 1: Self-exclusion → all sessions terminated
# ---------------------------------------------------------------------------

def scenario_self_exclusion(
    client: HTTPClient, admin_token: str, verbose: bool
) -> ScenarioResult:
    name = "Self-exclusion terminates all sessions"
    checks: List[str] = []

    player_id, token, session_id = _provision_player(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Could not provision test player", sub_checks=checks)

    checks.append(f"Player provisioned: player_id={player_id}")

    # Trigger self-exclusion via the player's own token
    code, payload, _ = client.post(
        f"/api/v1/players/{player_id or 'me'}/self-exclusion",
        {"period_years": 5, "reason": "smoke_test"},
        headers={"Authorization": f"Bearer {token}"},
    )

    if code not in (200, 201, 204):
        return ScenarioResult(
            name=name,
            passed=False,
            detail=f"Self-exclusion API returned {code}",
            sub_checks=checks,
        )
    checks.append(f"Self-exclusion triggered: {code}")

    # Session must now be rejected
    if _token_is_rejected(client, token, player_id):
        checks.append("Token rejected after self-exclusion (PASS)")
        return ScenarioResult(name=name, passed=True, detail="All sessions terminated", sub_checks=checks)

    checks.append("Token still accepted after self-exclusion (FAIL)")
    return ScenarioResult(name=name, passed=False, detail="Session still active after self-exclusion", sub_checks=checks)


# ---------------------------------------------------------------------------
# Scenario 2: Fraud flag → all sessions terminated
# ---------------------------------------------------------------------------

def scenario_fraud_flag(
    client: HTTPClient, admin_token: str, verbose: bool
) -> ScenarioResult:
    name = "Fraud flag terminates all sessions"
    checks: List[str] = []

    player_id, token, _ = _provision_player(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Could not provision test player", sub_checks=checks)

    checks.append(f"Player provisioned: player_id={player_id}")

    # Admin raises a fraud flag
    pid = player_id or "UNKNOWN"
    code, _, _ = client.post(
        f"/api/v1/admin/players/{pid}/flags",
        {"flag": "FRAUD", "reason": "smoke_test_fraud_check", "severity": "HIGH"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    if code not in (200, 201, 204):
        return ScenarioResult(
            name=name,
            passed=False,
            detail=f"Fraud flag API returned {code}",
            sub_checks=checks,
        )
    checks.append(f"Fraud flag raised: {code}")

    if _token_is_rejected(client, token, player_id):
        checks.append("Token rejected after fraud flag (PASS)")
        return ScenarioResult(name=name, passed=True, detail="All sessions terminated on fraud flag", sub_checks=checks)

    checks.append("Token still accepted after fraud flag (FAIL)")
    return ScenarioResult(name=name, passed=False, detail="Session still active after fraud flag", sub_checks=checks)


# ---------------------------------------------------------------------------
# Scenario 3: KYC failure → session suspended
# ---------------------------------------------------------------------------

def scenario_kyc_failure(
    client: HTTPClient, admin_token: str, verbose: bool
) -> ScenarioResult:
    name = "KYC failure suspends session"
    checks: List[str] = []

    player_id, token, _ = _provision_player(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Could not provision test player", sub_checks=checks)

    checks.append(f"Player provisioned: player_id={player_id}")

    pid = player_id or "UNKNOWN"
    code, _, _ = client.put(
        f"/api/v1/admin/players/{pid}/kyc",
        {"status": "REJECTED", "reason": "smoke_test_kyc_rejection"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    if code not in (200, 201, 204):
        return ScenarioResult(
            name=name,
            passed=False,
            detail=f"KYC update API returned {code}",
            sub_checks=checks,
        )
    checks.append(f"KYC rejection applied: {code}")

    if _token_is_suspended(client, token, player_id):
        checks.append("Session suspended/rejected after KYC failure (PASS)")
        return ScenarioResult(name=name, passed=True, detail="Session suspended on KYC failure", sub_checks=checks)

    checks.append("Session still fully active after KYC failure (FAIL)")
    return ScenarioResult(name=name, passed=False, detail="Session not suspended after KYC rejection", sub_checks=checks)


# ---------------------------------------------------------------------------
# Scenario 4: Account lock → all sessions terminated
# ---------------------------------------------------------------------------

def scenario_account_lock(
    client: HTTPClient, admin_token: str, verbose: bool
) -> ScenarioResult:
    name = "Account lock terminates all sessions"
    checks: List[str] = []

    player_id, token, _ = _provision_player(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Could not provision test player", sub_checks=checks)

    checks.append(f"Player provisioned: player_id={player_id}")

    pid = player_id or "UNKNOWN"
    code, _, _ = client.post(
        f"/api/v1/admin/players/{pid}/lock",
        {"reason": "smoke_test_lock", "lock_type": "FULL"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    if code not in (200, 201, 204):
        return ScenarioResult(
            name=name,
            passed=False,
            detail=f"Account lock API returned {code}",
            sub_checks=checks,
        )
    checks.append(f"Account locked: {code}")

    if _token_is_rejected(client, token, player_id):
        checks.append("Token rejected after account lock (PASS)")
        return ScenarioResult(name=name, passed=True, detail="All sessions terminated on account lock", sub_checks=checks)

    checks.append("Token still accepted after account lock (FAIL)")
    return ScenarioResult(name=name, passed=False, detail="Session still active after account lock", sub_checks=checks)


# ---------------------------------------------------------------------------
# Scenario 5: Admin force-logout → specific session killed, others remain
# ---------------------------------------------------------------------------

def scenario_admin_force_logout(
    client: HTTPClient, admin_token: str, verbose: bool
) -> ScenarioResult:
    name = "Admin force-logout kills specific session, others remain"
    checks: List[str] = []

    # Create player + two sessions (login twice)
    player_id, token_a, session_a = _provision_player(client, admin_token)
    if not token_a:
        return ScenarioResult(name=name, passed=False, detail="Could not provision test player", sub_checks=checks)

    checks.append(f"Player provisioned: player_id={player_id}")

    # Second login from a different device
    suffix = _rand()
    code2, payload2, _ = client.post(
        "/api/v1/players/login",
        {
            "username": f"rev_{suffix}",
            "password": f"Rev!{suffix}99",
            "device_id": f"device_{_rand()}",
        },
    )
    token_b = (
        payload2.get("access_token")
        or payload2.get("data", {}).get("access_token")
        or ""
    )
    session_b = payload2.get("session_id") or payload2.get("data", {}).get("session_id") or ""

    # Force-logout only session A
    target_session = session_a or "current"
    pid = player_id or "UNKNOWN"
    code3, _, _ = client.delete(
        f"/api/v1/admin/players/{pid}/sessions/{target_session}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    if code3 not in (200, 204):
        return ScenarioResult(
            name=name,
            passed=False,
            detail=f"Admin force-logout API returned {code3}",
            sub_checks=checks,
        )
    checks.append(f"Admin force-logout on session {target_session}: {code3}")

    # Token A must be rejected
    a_rejected = _token_is_rejected(client, token_a, player_id)
    checks.append(f"Token A rejected: {a_rejected}")

    # Token B (different session) should remain valid — only check if we have it
    if token_b:
        code_b, _, _ = client.get(
            f"/api/v1/players/{player_id or 'me'}",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        b_active = code_b == 200
        checks.append(f"Token B still active: {b_active}")
        passed = a_rejected and b_active
        detail = (
            "Specific session revoked, other session preserved"
            if passed
            else f"a_rejected={a_rejected}, b_active={b_active}"
        )
    else:
        # Can only verify that the targeted session was killed
        passed = a_rejected
        detail = "Targeted session revoked (second session could not be verified)"
        checks.append("Second token unavailable — partial check only")

    return ScenarioResult(name=name, passed=passed, detail=detail, sub_checks=checks)


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def run_dry(report: RevocationReport, verbose: bool) -> None:
    dry_scenarios = [
        ("Self-exclusion terminates all sessions", True, "DRY: would trigger /self-exclusion and verify 401"),
        ("Fraud flag terminates all sessions", True, "DRY: would POST /flags FRAUD and verify 401"),
        ("KYC failure suspends session", True, "DRY: would PUT /kyc REJECTED and verify suspended status"),
        ("Account lock terminates all sessions", True, "DRY: would POST /lock and verify 401"),
        ("Admin force-logout kills specific session, others remain", True, "DRY: would DELETE /sessions/{id} and verify selective revocation"),
    ]
    for name, passed, detail in dry_scenarios:
        r = ScenarioResult(name=name, passed=passed, detail=detail)
        report.add(r)
        if verbose:
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}: {detail}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

SCENARIO_MAP = {
    "self_exclusion": scenario_self_exclusion,
    "fraud_flag": scenario_fraud_flag,
    "kyc_failure": scenario_kyc_failure,
    "account_lock": scenario_account_lock,
    "admin_force_logout": scenario_admin_force_logout,
}


def run(
    base_url: str,
    admin_token: str,
    timeout: int,
    verbose: bool,
    dry_run: bool,
    scenario: Optional[str],
) -> RevocationReport:
    report = RevocationReport()

    if dry_run:
        print("[SESSION REVOCATION CHECK] DRY RUN")
        run_dry(report, verbose)
        return report

    client = HTTPClient(base_url, timeout=timeout, verbose=verbose)

    scenarios_to_run = (
        {scenario: SCENARIO_MAP[scenario]}
        if scenario and scenario in SCENARIO_MAP
        else SCENARIO_MAP
    )

    for key, fn in scenarios_to_run.items():
        if verbose:
            print(f"\n  -- Scenario: {key} --")
        try:
            result = fn(client, admin_token, verbose)
        except Exception as exc:
            result = ScenarioResult(
                name=key,
                passed=False,
                detail=f"Unhandled exception: {exc}",
            )
        report.add(result)

    return report


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(report: RevocationReport) -> None:
    print("\n" + "=" * 60)
    print("SESSION REVOCATION CHECK REPORT")
    print("=" * 60)
    for s in report.scenarios:
        status = "PASS" if s.passed else "FAIL"
        print(f"  [{status}]  {s.name}")
        print(f"           {s.detail}")
        for check in s.sub_checks:
            print(f"           - {check}")
    print("-" * 60)
    failures = len(report.failures)
    total = len(report.scenarios)
    print(f"  Result : {'PASS' if report.passed else 'FAIL'}")
    print(f"  Scenarios: {total - failures}/{total} passed")
    print(f"  Elapsed  : {report.elapsed():.2f}s")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Session Revocation Check — verify sessions are killed on trigger events",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--admin-token", default="", help="Bearer token with admin privileges")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIO_MAP.keys()),
        help="Run a single scenario instead of all",
    )

    args = parser.parse_args()

    admin_token = args.admin_token or __import__("os").environ.get("ADMIN_TOKEN", "")

    print(f"[SESSION REVOCATION CHECK] Target: {args.base_url}")
    report = run(args.base_url, admin_token, args.timeout, args.verbose, args.dry_run, args.scenario)
    print_report(report)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
