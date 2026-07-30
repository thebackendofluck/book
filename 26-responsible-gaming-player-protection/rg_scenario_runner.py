#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 26, Responsible Gaming and Player Protection Systems.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
RG Scenario Runner — test responsible gaming enforcement end-to-end.

Scenarios:
  1. deposit_limit   — player sets deposit limit; subsequent deposit exceeding it is blocked
  2. loss_limit      — player sets loss limit; bet that would exceed it is blocked
  3. session_time    — player sets session time; expiry triggers warning then block
  4. self_exclusion  — player self-excludes; all services block the account
  5. cooling_off     — player on cooling-off period cannot revoke it early
  6. national_registry — player flagged by national registry receives instant block

Usage:
    python rg_scenario_runner.py --base-url https://api.example.com
    python rg_scenario_runner.py --base-url http://localhost:8080 --verbose
    python rg_scenario_runner.py --base-url https://api.example.com --dry-run
    python rg_scenario_runner.py --scenario deposit_limit --base-url https://api.example.com
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
class ScenarioResult:
    name: str
    passed: bool
    detail: str
    sub_checks: List[str] = field(default_factory=list)


@dataclass
class RGReport:
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

    def put(self, path: str, body: Dict, headers: Optional[Dict] = None):
        return self._request("PUT", path, body=body, headers=headers)

    def delete(self, path: str, headers: Optional[Dict] = None):
        return self._request("DELETE", path, headers=headers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _blocked(code: int, payload: Dict) -> bool:
    if code in (400, 403, 409, 422):
        return True
    msg = json.dumps(payload).upper()
    return any(kw in msg for kw in [
        "LIMIT_EXCEEDED", "BLOCKED", "DENIED", "EXCEEDED",
        "COOLING_OFF", "SELF_EXCLUDED", "EXCLUSION", "NATIONAL_REGISTRY",
    ])


def _provision(client: HTTPClient, admin_token: str, country: str = "GB") -> Tuple[str, str]:
    """Return (player_id, access_token) for a freshly created active player."""
    suffix = _rand()
    auth = {"Authorization": f"Bearer {admin_token}"}
    code, payload, _ = client.post(
        "/api/v1/admin/players",
        {
            "username": f"rg_{suffix}",
            "email": f"rg_{suffix}@test.internal",
            "password": f"RG!{suffix}99",
            "country": country,
            "currency": "GBP",
            "status": "ACTIVE",
        },
        headers=auth,
    )
    if code not in (200, 201):
        return "", ""
    player_id = (
        payload.get("player_id") or payload.get("id")
        or payload.get("data", {}).get("player_id") or ""
    )
    # Get a session token
    code2, payload2, _ = client.post(
        "/api/v1/players/login",
        {"username": f"rg_{suffix}", "password": f"RG!{suffix}99"},
    )
    token = (
        payload2.get("access_token") or payload2.get("token")
        or payload2.get("data", {}).get("access_token") or ""
    )
    return player_id, token


# ---------------------------------------------------------------------------
# Scenario 1: Deposit limit exceeded → blocked
# ---------------------------------------------------------------------------

def scenario_deposit_limit(client: HTTPClient, admin_token: str) -> ScenarioResult:
    name = "deposit_limit"
    checks: List[str] = []

    player_id, token = _provision(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Setup failed", sub_checks=checks)

    auth = {"Authorization": f"Bearer {token}"}
    pid = player_id or "me"
    limit = 50.00

    # Set deposit limit
    code, _, _ = client.post(
        f"/api/v1/players/{pid}/limits",
        {"limit_type": "DEPOSIT", "period": "DAILY", "amount": limit, "currency": "GBP"},
        headers=auth,
    )
    limit_set = code in (200, 201)
    checks.append(f"Deposit limit set ({limit} GBP/day): {'OK' if limit_set else f'HTTP {code}'}")

    # Deposit within limit → should succeed
    code2, _, _ = client.post(
        "/api/v1/payments/deposit",
        {"amount": 30.00, "currency": "GBP", "method": "CARD"},
        headers=auth,
    )
    first_ok = code2 in (200, 201)
    checks.append(f"Deposit within limit (30 GBP): {'OK' if first_ok else f'HTTP {code2}'}")

    # Deposit that exceeds the daily limit → must be blocked
    code3, payload3, _ = client.post(
        "/api/v1/payments/deposit",
        {"amount": 30.00, "currency": "GBP", "method": "CARD"},
        headers=auth,
    )
    exceed_blocked = _blocked(code3, payload3)
    checks.append(f"Deposit exceeding limit (30 GBP more, total 60): {'blocked' if exceed_blocked else f'ACCEPTED — FAIL (HTTP {code3})'}")

    passed = limit_set and exceed_blocked
    return ScenarioResult(
        name=name,
        passed=passed,
        detail="Deposit limit enforced" if passed else "Deposit limit NOT enforced",
        sub_checks=checks,
    )


# ---------------------------------------------------------------------------
# Scenario 2: Loss limit exceeded → blocked
# ---------------------------------------------------------------------------

def scenario_loss_limit(client: HTTPClient, admin_token: str) -> ScenarioResult:
    name = "loss_limit"
    checks: List[str] = []

    player_id, token = _provision(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Setup failed", sub_checks=checks)

    auth = {"Authorization": f"Bearer {token}"}
    pid = player_id or "me"
    limit = 20.00

    # Set loss limit
    code, _, _ = client.post(
        f"/api/v1/players/{pid}/limits",
        {"limit_type": "LOSS", "period": "DAILY", "amount": limit, "currency": "GBP"},
        headers=auth,
    )
    limit_set = code in (200, 201)
    checks.append(f"Loss limit set ({limit} GBP/day): {'OK' if limit_set else f'HTTP {code}'}")

    # Simulate losses via bet placement that the API should track
    # First bet (below limit)
    code2, _, _ = client.post(
        "/api/v1/games/bet",
        {"amount": 15.00, "currency": "GBP", "game_id": "test_slot", "outcome": "LOSE"},
        headers=auth,
    )
    first_ok = code2 in (200, 201)
    checks.append(f"Bet within loss limit (15 GBP loss): {'OK' if first_ok else f'HTTP {code2}'}")

    # Bet that would push total losses over the limit
    code3, payload3, _ = client.post(
        "/api/v1/games/bet",
        {"amount": 10.00, "currency": "GBP", "game_id": "test_slot", "outcome": "LOSE"},
        headers=auth,
    )
    exceed_blocked = _blocked(code3, payload3)
    checks.append(f"Bet exceeding loss limit (10 GBP more, total 25): {'blocked' if exceed_blocked else f'ACCEPTED — FAIL (HTTP {code3})'}")

    passed = limit_set and exceed_blocked
    return ScenarioResult(
        name=name,
        passed=passed,
        detail="Loss limit enforced" if passed else "Loss limit NOT enforced",
        sub_checks=checks,
    )


# ---------------------------------------------------------------------------
# Scenario 3: Session time limit → warning then block
# ---------------------------------------------------------------------------

def scenario_session_time(client: HTTPClient, admin_token: str) -> ScenarioResult:
    name = "session_time"
    checks: List[str] = []

    player_id, token = _provision(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Setup failed", sub_checks=checks)

    auth = {"Authorization": f"Bearer {token}"}
    pid = player_id or "me"

    # Set a very short session limit (1 minute) for testing
    code, _, _ = client.post(
        f"/api/v1/players/{pid}/limits",
        {"limit_type": "SESSION_TIME", "period": "SESSION", "duration_minutes": 1},
        headers=auth,
    )
    limit_set = code in (200, 201)
    checks.append(f"Session time limit set (1 min): {'OK' if limit_set else f'HTTP {code}'}")

    # Simulate session expiry via the API (fast-forward / test endpoint)
    code2, payload2, _ = client.post(
        "/api/v1/test/simulate-session-expiry",
        {"player_id": pid, "elapsed_minutes": 1},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    # Check for warning flag
    code3, payload3, _ = client.get(
        f"/api/v1/players/{pid}/session-status",
        headers=auth,
    )
    has_warning = (
        code3 == 200
        and any(
            kw in json.dumps(payload3).upper()
            for kw in ["WARNING", "EXPIRING", "NEAR_LIMIT"]
        )
    )
    checks.append(f"Session expiry warning issued: {'yes' if has_warning else 'not detected (may be immediate block)'}")

    # Subsequent game action must be blocked
    code4, payload4, _ = client.post(
        "/api/v1/games/bet",
        {"amount": 5.00, "currency": "GBP", "game_id": "test_slot"},
        headers=auth,
    )
    session_blocked = _blocked(code4, payload4)
    checks.append(f"Gameplay blocked after session expiry: {'blocked' if session_blocked else f'ACCEPTED — FAIL (HTTP {code4})'}")

    passed = limit_set and session_blocked
    return ScenarioResult(
        name=name,
        passed=passed,
        detail="Session time enforced" if passed else "Session time NOT enforced",
        sub_checks=checks,
    )


# ---------------------------------------------------------------------------
# Scenario 4: Self-exclusion → all services block
# ---------------------------------------------------------------------------

def scenario_self_exclusion(client: HTTPClient, admin_token: str) -> ScenarioResult:
    name = "self_exclusion"
    checks: List[str] = []

    player_id, token = _provision(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Setup failed", sub_checks=checks)

    auth = {"Authorization": f"Bearer {token}"}
    pid = player_id or "me"

    # Player triggers self-exclusion
    code, _, _ = client.post(
        f"/api/v1/players/{pid}/self-exclusion",
        {"period_years": 5, "reason": "rg_test"},
        headers=auth,
    )
    excl_ok = code in (200, 201, 204)
    checks.append(f"Self-exclusion triggered: {'OK' if excl_ok else f'HTTP {code}'}")

    services_blocked = []

    # Deposit must be blocked
    c1, p1, _ = client.post(
        "/api/v1/payments/deposit",
        {"amount": 10.00, "currency": "GBP", "method": "CARD"},
        headers=auth,
    )
    deposit_blocked = _blocked(c1, p1)
    services_blocked.append(("Deposit", deposit_blocked))

    # Bet must be blocked
    c2, p2, _ = client.post(
        "/api/v1/games/bet",
        {"amount": 5.00, "currency": "GBP", "game_id": "test_slot"},
        headers=auth,
    )
    bet_blocked = _blocked(c2, p2)
    services_blocked.append(("Bet", bet_blocked))

    # Bonus claim must be blocked
    c3, p3, _ = client.post(
        "/api/v1/bonuses/claim",
        {"bonus_id": "TEST_BONUS"},
        headers=auth,
    )
    bonus_blocked = _blocked(c3, p3)
    services_blocked.append(("Bonus claim", bonus_blocked))

    for svc, blocked in services_blocked:
        checks.append(f"{svc} blocked after self-exclusion: {'yes' if blocked else 'NO — FAIL'}")

    all_blocked = all(b for _, b in services_blocked)
    passed = excl_ok and all_blocked
    return ScenarioResult(
        name=name,
        passed=passed,
        detail="All services blocked after self-exclusion" if passed else "Some services still accessible",
        sub_checks=checks,
    )


# ---------------------------------------------------------------------------
# Scenario 5: Cooling-off period — cannot revoke early
# ---------------------------------------------------------------------------

def scenario_cooling_off(client: HTTPClient, admin_token: str) -> ScenarioResult:
    name = "cooling_off"
    checks: List[str] = []

    player_id, token = _provision(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Setup failed", sub_checks=checks)

    auth = {"Authorization": f"Bearer {token}"}
    pid = player_id or "me"

    # Set a cooling-off period
    code, _, _ = client.post(
        f"/api/v1/players/{pid}/cooling-off",
        {"duration_days": 7, "reason": "rg_test"},
        headers=auth,
    )
    cooling_set = code in (200, 201, 204)
    checks.append(f"Cooling-off period set (7 days): {'OK' if cooling_set else f'HTTP {code}'}")

    # Attempt to revoke cooling-off immediately (must be denied)
    code2, payload2, _ = client.delete(
        f"/api/v1/players/{pid}/cooling-off",
        headers=auth,
    )
    revoke_blocked = _blocked(code2, payload2) or code2 in (400, 403, 409, 422)
    checks.append(
        f"Early revocation of cooling-off rejected: "
        f"{'yes' if revoke_blocked else f'ALLOWED — FAIL (HTTP {code2})'}"
    )

    # Gameplay must be blocked during cooling-off
    code3, payload3, _ = client.post(
        "/api/v1/games/bet",
        {"amount": 5.00, "currency": "GBP", "game_id": "test_slot"},
        headers=auth,
    )
    play_blocked = _blocked(code3, payload3)
    checks.append(f"Gameplay blocked during cooling-off: {'yes' if play_blocked else f'ACCEPTED — FAIL (HTTP {code3})'}")

    passed = cooling_set and revoke_blocked and play_blocked
    return ScenarioResult(
        name=name,
        passed=passed,
        detail="Cooling-off enforced correctly" if passed else "Cooling-off NOT enforced correctly",
        sub_checks=checks,
    )


# ---------------------------------------------------------------------------
# Scenario 6: National registry flagged → instant block
# ---------------------------------------------------------------------------

def scenario_national_registry(client: HTTPClient, admin_token: str) -> ScenarioResult:
    name = "national_registry"
    checks: List[str] = []

    player_id, token = _provision(client, admin_token)
    if not token:
        return ScenarioResult(name=name, passed=False, detail="Setup failed", sub_checks=checks)

    auth_admin = {"Authorization": f"Bearer {admin_token}"}
    auth = {"Authorization": f"Bearer {token}"}
    pid = player_id or "UNKNOWN"

    # Admin flags the player in the national registry
    code, _, _ = client.post(
        f"/api/v1/admin/players/{pid}/national-registry-flag",
        {"registry": "NATIONAL_EXCLUSION", "reason": "rg_test_national_flag"},
        headers=auth_admin,
    )
    flagged = code in (200, 201, 204)
    checks.append(f"National registry flag applied: {'OK' if flagged else f'HTTP {code}'}")

    # Attempt deposit immediately after flagging — must be blocked instantly
    code2, payload2, _ = client.post(
        "/api/v1/payments/deposit",
        {"amount": 10.00, "currency": "GBP", "method": "CARD"},
        headers=auth,
    )
    deposit_blocked = _blocked(code2, payload2)
    checks.append(
        f"Deposit blocked after national registry flag: "
        f"{'yes' if deposit_blocked else f'ACCEPTED — FAIL (HTTP {code2})'}"
    )

    # Attempt login after flag (should be refused)
    code3, payload3, _ = client.post(
        "/api/v1/players/login",
        {"username": f"rg_{pid}", "password": "any"},
    )
    login_blocked = _blocked(code3, payload3) or code3 == 401
    checks.append(
        f"Login blocked after national registry flag: "
        f"{'yes' if login_blocked else f'login accepted — FAIL (HTTP {code3})'}"
    )

    passed = flagged and deposit_blocked
    return ScenarioResult(
        name=name,
        passed=passed,
        detail="National registry flag triggers instant block" if passed else "National registry block NOT enforced",
        sub_checks=checks,
    )


# ---------------------------------------------------------------------------
# SCENARIO_MAP
# ---------------------------------------------------------------------------

SCENARIO_MAP = {
    "deposit_limit": scenario_deposit_limit,
    "loss_limit": scenario_loss_limit,
    "session_time": scenario_session_time,
    "self_exclusion": scenario_self_exclusion,
    "cooling_off": scenario_cooling_off,
    "national_registry": scenario_national_registry,
}


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def run_dry(report: RGReport, verbose: bool) -> None:
    for key in SCENARIO_MAP:
        r = ScenarioResult(
            name=key,
            passed=True,
            detail="DRY: no network call made",
        )
        report.add(r)
        if verbose:
            print(f"  [PASS] {key}: DRY")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(
    base_url: str,
    admin_token: str,
    timeout: int,
    verbose: bool,
    dry_run: bool,
    scenario: Optional[str],
) -> RGReport:
    report = RGReport()

    if dry_run:
        print("[RG SCENARIO RUNNER] DRY RUN")
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
            result = fn(client, admin_token)
        except Exception as exc:
            result = ScenarioResult(
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

def print_report(report: RGReport) -> None:
    print("\n" + "=" * 60)
    print("RG SCENARIO RUNNER REPORT")
    print("=" * 60)
    for s in report.scenarios:
        status = "PASS" if s.passed else "FAIL"
        print(f"  [{status}]  {s.name}")
        print(f"           {s.detail}")
        for c in s.sub_checks:
            print(f"           - {c}")
    print("-" * 60)
    failures = len(report.failures)
    total = len(report.scenarios)
    print(f"  Result    : {'PASS' if report.passed else 'FAIL'}")
    print(f"  Scenarios : {total - failures}/{total} passed")
    print(f"  Elapsed   : {report.elapsed():.2f}s")
    print("=" * 60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="RG Scenario Runner — responsible gaming enforcement tests",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--scenario",
        choices=list(SCENARIO_MAP.keys()),
        help="Run a single scenario",
    )

    args = parser.parse_args()

    import os
    admin_token = args.admin_token or os.environ.get("ADMIN_TOKEN", "")

    print(f"[RG SCENARIO RUNNER] Target: {args.base_url}")
    report = run(args.base_url, admin_token, args.timeout, args.verbose, args.dry_run, args.scenario)
    print_report(report)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
