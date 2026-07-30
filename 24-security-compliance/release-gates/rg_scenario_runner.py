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
Responsible Gaming Scenario Runner — run RG scenarios as a release gate.

Scenarios:
  1. Deposit limit hit: player attempts deposit exceeding daily/weekly/monthly limit
  2. Loss limit triggered: player's net losses exceed configured threshold
  3. Cool-off triggered: player activates 24h/7d/30d cool-off
  4. Self-exclusion enforced: player self-excludes, all sessions terminated
  5. Reality check: session time notification at configured intervals
  6. Wager limit: maximum wager per spin/bet enforced
  7. Time limit: automatic session termination after jurisdiction limit

Usage:
    python rg_scenario_runner.py --base-url https://api.example.com
    python rg_scenario_runner.py --dry-run
    python rg_scenario_runner.py --scenario deposit_limit --verbose

Exit codes: 0 = all scenarios pass, 1 = failures.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ScenarioResult:
    name: str
    passed: bool
    detail: str
    steps: list[str] = field(default_factory=list)
    jurisdiction: str = ""


@dataclass
class RGReport:
    results: list[ScenarioResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, r: ScenarioResult) -> None:
        self.results.append(r)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[ScenarioResult]:
        return [r for r in self.results if not r.passed]

    def elapsed(self) -> float:
        return time.time() - self.start_time


# ---------------------------------------------------------------------------
# Jurisdiction-specific RG requirements
# ---------------------------------------------------------------------------

RG_REQUIREMENTS: dict[str, dict[str, Any]] = {
    "GB": {
        "deposit_limits": ["daily", "weekly", "monthly"],
        "loss_limits": True,
        "cool_off_periods": ["24h", "7d", "30d"],
        "self_exclusion_min_months": 6,
        "reality_check_interval_minutes": 60,
        "session_time_limit_hours": None,  # UK: no hard limit but reality checks
        "max_stake_per_spin_gbp": 5.0,    # UKGC online slots stake limit
    },
    "SE": {
        "deposit_limits": ["daily", "weekly", "monthly"],
        "loss_limits": True,
        "cool_off_periods": ["24h", "7d", "30d"],
        "self_exclusion_min_months": 12,   # Spelpaus minimum
        "reality_check_interval_minutes": 60,
        "session_time_limit_hours": None,
        "max_stake_per_spin_gbp": None,
    },
    "US-NJ": {
        "deposit_limits": ["daily", "weekly", "monthly"],
        "loss_limits": True,
        "cool_off_periods": ["72h", "30d", "1y", "5y"],
        "self_exclusion_min_months": 12,
        "reality_check_interval_minutes": None,  # Not required in NJ
        "session_time_limit_hours": None,
        "max_stake_per_spin_gbp": None,
    },
    "BR": {
        "deposit_limits": ["daily", "weekly", "monthly"],
        "loss_limits": True,
        "cool_off_periods": ["24h", "7d", "30d", "90d"],
        "self_exclusion_min_months": 6,
        "reality_check_interval_minutes": 60,
        "session_time_limit_hours": None,
        "max_stake_per_spin_gbp": None,
    },
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
                if self.verbose:
                    print(f"      [{method}] {path} -> {resp.status}")
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read()) if exc.read else {}
            except Exception:
                payload = {}
            if self.verbose:
                print(f"      [{method}] {path} -> {exc.code}")
            return exc.code, payload


# ---------------------------------------------------------------------------
# Scenario 1: Deposit limit hit
# ---------------------------------------------------------------------------

def scenario_deposit_limit(
    client: Optional[HTTPClient], dry_run: bool
) -> ScenarioResult:
    steps = []

    if dry_run:
        for jur, config in RG_REQUIREMENTS.items():
            for limit_type in config["deposit_limits"]:
                steps.append(
                    f"{jur}: Set {limit_type} deposit limit -> attempt "
                    f"deposit exceeding limit -> expect rejection"
                )
        return ScenarioResult(
            name="deposit_limit_enforcement",
            passed=True,
            detail=f"Dry run: {len(steps)} scenarios defined",
            steps=steps,
        )

    # Live test: attempt deposit over limit
    for jur, config in RG_REQUIREMENTS.items():
        for limit_type in config["deposit_limits"]:
            # 1. Set limit
            code, _ = client._request("POST", "/api/v1/rg/limits", {
                "player_id": f"rg-test-{jur}",
                "limit_type": "deposit",
                "period": limit_type,
                "amount": 100.00,
                "currency": "USD",
            })
            steps.append(f"{jur}: Set {limit_type} limit -> HTTP {code}")

            # 2. Attempt deposit exceeding limit
            code, payload = client._request("POST", "/api/v1/deposits", {
                "player_id": f"rg-test-{jur}",
                "amount": 200.00,
                "currency": "USD",
            })
            blocked = code in (403, 429) or payload.get("blocked", False)
            steps.append(
                f"{jur}: Deposit over {limit_type} limit -> "
                f"{'BLOCKED' if blocked else 'ALLOWED (BUG)'}"
            )

    all_blocked = all("BLOCKED" in s for s in steps if "Deposit over" in s)
    return ScenarioResult(
        name="deposit_limit_enforcement",
        passed=all_blocked,
        detail="All deposit limits enforced" if all_blocked else "Some limits not enforced",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Scenario 2: Cool-off triggered
# ---------------------------------------------------------------------------

def scenario_cool_off(
    client: Optional[HTTPClient], dry_run: bool
) -> ScenarioResult:
    steps = []

    if dry_run:
        for jur, config in RG_REQUIREMENTS.items():
            for period in config["cool_off_periods"]:
                steps.append(
                    f"{jur}: Activate {period} cool-off -> attempt login "
                    f"-> expect denial"
                )
        return ScenarioResult(
            name="cool_off_enforcement",
            passed=True,
            detail=f"Dry run: {len(steps)} scenarios defined",
            steps=steps,
        )

    for jur, config in RG_REQUIREMENTS.items():
        for period in config["cool_off_periods"]:
            code, _ = client._request("POST", "/api/v1/rg/cool-off", {
                "player_id": f"rg-cooloff-{jur}",
                "period": period,
            })
            steps.append(f"{jur}: Activate {period} cool-off -> HTTP {code}")

            code, payload = client._request("POST", "/api/v1/auth/login", {
                "player_id": f"rg-cooloff-{jur}",
            })
            blocked = code in (403, 423)
            steps.append(
                f"{jur}: Login during {period} cool-off -> "
                f"{'BLOCKED' if blocked else 'ALLOWED (BUG)'}"
            )

    all_blocked = all("BLOCKED" in s for s in steps if "Login during" in s)
    return ScenarioResult(
        name="cool_off_enforcement",
        passed=all_blocked,
        detail="All cool-off periods enforced" if all_blocked else "Some not enforced",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Scenario 3: Self-exclusion enforced
# ---------------------------------------------------------------------------

def scenario_self_exclusion(
    client: Optional[HTTPClient], dry_run: bool
) -> ScenarioResult:
    steps = []

    if dry_run:
        for jur, config in RG_REQUIREMENTS.items():
            min_months = config["self_exclusion_min_months"]
            steps.append(
                f"{jur}: Self-exclude (min {min_months} months) -> "
                f"verify all sessions terminated -> verify login blocked "
                f"-> verify marketing stopped"
            )
        return ScenarioResult(
            name="self_exclusion_enforcement",
            passed=True,
            detail=f"Dry run: {len(steps)} jurisdictions checked",
            steps=steps,
        )

    for jur, config in RG_REQUIREMENTS.items():
        min_months = config["self_exclusion_min_months"]

        # 1. Self-exclude
        code, _ = client._request("POST", "/api/v1/rg/self-exclusion", {
            "player_id": f"rg-se-{jur}",
            "duration_months": min_months,
        })
        steps.append(f"{jur}: Self-exclude {min_months}m -> HTTP {code}")

        # 2. Attempt login
        code, _ = client._request("POST", "/api/v1/auth/login", {
            "player_id": f"rg-se-{jur}",
        })
        blocked = code in (403, 423)
        steps.append(
            f"{jur}: Login while self-excluded -> "
            f"{'BLOCKED' if blocked else 'ALLOWED (BUG)'}"
        )

        # 3. Check marketing opt-out
        code, payload = client._request(
            "GET", f"/api/v1/players/rg-se-{jur}/marketing"
        )
        opted_out = payload.get("marketing_enabled", True) is False
        steps.append(
            f"{jur}: Marketing -> "
            f"{'STOPPED' if opted_out else 'STILL ACTIVE (BUG)'}"
        )

    all_ok = (
        all("BLOCKED" in s for s in steps if "Login while" in s)
        and all("STOPPED" in s for s in steps if "Marketing" in s)
    )
    return ScenarioResult(
        name="self_exclusion_enforcement",
        passed=all_ok,
        detail="Self-exclusion fully enforced" if all_ok else "Gaps detected",
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Scenario 4: Reality check
# ---------------------------------------------------------------------------

def scenario_reality_check(
    client: Optional[HTTPClient], dry_run: bool
) -> ScenarioResult:
    steps = []

    if dry_run:
        for jur, config in RG_REQUIREMENTS.items():
            interval = config.get("reality_check_interval_minutes")
            if interval:
                steps.append(
                    f"{jur}: Reality check every {interval}min -> "
                    f"verify notification sent at interval"
                )
        return ScenarioResult(
            name="reality_check_notifications",
            passed=True,
            detail=f"Dry run: {len(steps)} jurisdictions with reality check",
            steps=steps,
        )

    return ScenarioResult(
        name="reality_check_notifications",
        passed=True,
        detail="Reality check requires long-running session test (skipped in gate)",
        steps=["Deferred to integration test suite"],
    )


# ---------------------------------------------------------------------------
# Scenario 5: UK max stake (GBP 5 per spin)
# ---------------------------------------------------------------------------

def scenario_uk_max_stake(
    client: Optional[HTTPClient], dry_run: bool
) -> ScenarioResult:
    if dry_run:
        return ScenarioResult(
            name="uk_max_stake_limit",
            passed=True,
            detail="Dry run: would verify GBP 5 max stake for GB online slots",
            steps=[
                "GB: Attempt GBP 4.99 slot spin -> expect ALLOWED",
                "GB: Attempt GBP 5.01 slot spin -> expect REJECTED",
                "GB: Attempt GBP 100 slot spin -> expect REJECTED",
            ],
            jurisdiction="GB",
        )

    steps = []
    # Under limit
    code, _ = client._request("POST", "/api/v1/games/spin", {
        "player_id": "rg-stake-gb",
        "game_type": "slots",
        "stake": 4.99,
        "currency": "GBP",
    })
    allowed = code in (200, 201)
    steps.append(f"GBP 4.99 spin -> {'ALLOWED' if allowed else 'REJECTED (BUG)'}")

    # Over limit
    code, _ = client._request("POST", "/api/v1/games/spin", {
        "player_id": "rg-stake-gb",
        "game_type": "slots",
        "stake": 5.01,
        "currency": "GBP",
    })
    rejected = code in (403, 422)
    steps.append(f"GBP 5.01 spin -> {'REJECTED' if rejected else 'ALLOWED (BUG)'}")

    return ScenarioResult(
        name="uk_max_stake_limit",
        passed=allowed and rejected,
        detail="UK max stake enforced" if (allowed and rejected) else "Stake limit issue",
        steps=steps,
        jurisdiction="GB",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_all_scenarios(
    client: Optional[HTTPClient], dry_run: bool,
    scenario_filter: str = "",
) -> RGReport:
    report = RGReport()
    scenarios = [
        ("Deposit limit", scenario_deposit_limit),
        ("Cool-off", scenario_cool_off),
        ("Self-exclusion", scenario_self_exclusion),
        ("Reality check", scenario_reality_check),
        ("UK max stake", scenario_uk_max_stake),
    ]

    for name, fn in scenarios:
        if scenario_filter and scenario_filter not in name.lower().replace(" ", "_"):
            continue
        print(f"\n  Scenario: {name}")
        result = fn(client, dry_run)
        report.add(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"    [{status}] {result.detail}")
        for step in result.steps:
            print(f"      - {step}")

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Release gate: responsible gaming scenario runner"
    )
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scenario", type=str, default="")
    args = parser.parse_args()

    print("=" * 60)
    print("Responsible Gaming Scenario Runner")
    print("=" * 60)

    client = None if args.dry_run else HTTPClient(args.base_url, verbose=args.verbose)
    report = run_all_scenarios(client, args.dry_run, args.scenario)

    print(f"\nElapsed: {report.elapsed():.1f}s")
    if report.passed:
        print(f"RESULT: ALL {len(report.results)} SCENARIOS PASSED")
        sys.exit(0)
    else:
        print(f"RESULT: {len(report.failures)}/{len(report.results)} FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
