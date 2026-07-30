#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 06, Licensing Guide.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
Jurisdiction Matrix Test — verify per-jurisdiction compliance rules are enforced
before gameplay and at account-creation time.

Jurisdictions tested:
  UK     — GamStop check before play, deposit limits required before first deposit
  Malta  — MGA PPD (player protection deposit) limits, session timer enforced
  Sweden — Spelpaus check, single-bonus rule, mandatory deposit limits
  Brazil — CPF validation, PIX-only payments, SIGAP reporting, welfare block, 30-min geo recheck
  Denmark — ROFUS check before play

Usage:
    python jurisdiction_matrix_test.py --base-url https://api.example.com
    python jurisdiction_matrix_test.py --base-url http://localhost:8080 --verbose
    python jurisdiction_matrix_test.py --base-url https://api.example.com --dry-run
    python jurisdiction_matrix_test.py --jurisdiction UK --base-url https://api.example.com
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
class RuleResult:
    jurisdiction: str
    rule: str
    passed: bool
    detail: str
    http_code: Optional[int] = None


@dataclass
class JurisdictionReport:
    results: List[RuleResult] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def add(self, r: RuleResult) -> None:
        self.results.append(r)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> List[RuleResult]:
        return [r for r in self.results if not r.passed]

    def elapsed(self) -> float:
        return time.time() - self.start_time

    def by_jurisdiction(self) -> Dict[str, List[RuleResult]]:
        out: Dict[str, List[RuleResult]] = {}
        for r in self.results:
            out.setdefault(r.jurisdiction, []).append(r)
        return out


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _blocked(code: int, payload: Dict) -> bool:
    """Return True if the response indicates the action was blocked."""
    if code in (400, 403, 409, 422, 451):
        return True
    error = (payload.get("error") or payload.get("message") or "").upper()
    block_keywords = ["EXCLUDED", "BLOCKED", "DENIED", "SUSPENDED", "GAMSTOP",
                      "SPELPAUS", "ROFUS", "SIGAP", "WELFARE", "CPF", "PIX_ONLY"]
    return any(kw in error for kw in block_keywords)


def _reason_contains(payload: Dict, *keywords: str) -> bool:
    blob = json.dumps(payload).upper()
    return any(kw.upper() in blob for kw in keywords)


# ---------------------------------------------------------------------------
# UK checks
# ---------------------------------------------------------------------------

def check_uk(client: HTTPClient, admin_token: str) -> List[RuleResult]:
    results = []
    j = "UK"
    auth = {"Authorization": f"Bearer {admin_token}"}

    # Rule: GamStop-registered player must be blocked from play
    suffix = _rand()
    gamstop_player = f"uk_gamstop_{suffix}"

    # Register a player flagged as GamStop-excluded
    client.post(
        "/api/v1/admin/players",
        {
            "username": gamstop_player,
            "email": f"{gamstop_player}@test.internal",
            "password": f"UK!{suffix}99",
            "country": "GB",
            "currency": "GBP",
            "gamstop_status": "SELF_EXCLUDED",
        },
        headers=auth,
    )

    code, payload, _ = client.post(
        "/api/v1/compliance/uk/gamstop-check",
        {"username": gamstop_player, "country": "GB"},
        headers=auth,
    )
    blocked = _blocked(code, payload) or _reason_contains(payload, "GAMSTOP", "EXCLUDED")
    results.append(RuleResult(
        jurisdiction=j,
        rule="GamStop excluded player blocked before play",
        passed=blocked,
        detail=f"HTTP {code} | {'blocked' if blocked else 'NOT blocked — FAIL'}",
        http_code=code,
    ))

    # Rule: Deposit limits required before first deposit for UK players
    clean_player = f"uk_clean_{_rand()}"
    client.post(
        "/api/v1/admin/players",
        {
            "username": clean_player,
            "email": f"{clean_player}@test.internal",
            "password": f"UK!{_rand()}99",
            "country": "GB",
            "currency": "GBP",
        },
        headers=auth,
    )

    # Attempt a deposit without setting limits first — must be blocked
    code2, payload2, _ = client.post(
        "/api/v1/payments/deposit",
        {
            "username": clean_player,
            "amount": 50.00,
            "currency": "GBP",
            "method": "CARD",
            "jurisdiction": "GB",
        },
        headers=auth,
    )
    limit_required = _blocked(code2, payload2) or _reason_contains(payload2, "LIMIT", "DEPOSIT_LIMIT")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Deposit blocked until limits are set (UK)",
        passed=limit_required,
        detail=f"HTTP {code2} | {'limit required — PASS' if limit_required else 'deposit accepted without limits — FAIL'}",
        http_code=code2,
    ))

    return results


# ---------------------------------------------------------------------------
# Malta checks
# ---------------------------------------------------------------------------

def check_malta(client: HTTPClient, admin_token: str) -> List[RuleResult]:
    results = []
    j = "Malta"
    auth = {"Authorization": f"Bearer {admin_token}"}

    suffix = _rand()
    player = f"mt_{suffix}"
    client.post(
        "/api/v1/admin/players",
        {
            "username": player,
            "email": f"{player}@test.internal",
            "password": f"MT!{suffix}99",
            "country": "MT",
            "currency": "EUR",
        },
        headers=auth,
    )

    # Rule: MGA PPD limits must be applied at account creation
    code, payload, _ = client.get(
        f"/api/v1/compliance/malta/ppd-limits/{player}",
        headers=auth,
    )
    ppd_set = code == 200 and _reason_contains(payload, "PPD", "LIMIT", "DAILY", "WEEKLY", "MONTHLY")
    results.append(RuleResult(
        jurisdiction=j,
        rule="MGA PPD limits present at account creation",
        passed=ppd_set,
        detail=f"HTTP {code} | {'PPD limits found' if ppd_set else 'PPD limits not found'}",
        http_code=code,
    ))

    # Rule: Session timer enforced — session must have a maximum duration
    code2, payload2, _ = client.get(
        f"/api/v1/compliance/malta/session-config/{player}",
        headers=auth,
    )
    timer_set = code2 == 200 and _reason_contains(payload2, "SESSION", "TIMER", "MAX_DURATION", "TIMEOUT")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Session timer configured (Malta)",
        passed=timer_set,
        detail=f"HTTP {code2} | {'timer configured' if timer_set else 'session timer not configured'}",
        http_code=code2,
    ))

    return results


# ---------------------------------------------------------------------------
# Sweden checks
# ---------------------------------------------------------------------------

def check_sweden(client: HTTPClient, admin_token: str) -> List[RuleResult]:
    results = []
    j = "Sweden"
    auth = {"Authorization": f"Bearer {admin_token}"}

    suffix = _rand()
    spelpaus_player = f"se_spelpaus_{suffix}"

    # Register a Spelpaus-excluded player
    client.post(
        "/api/v1/admin/players",
        {
            "username": spelpaus_player,
            "email": f"{spelpaus_player}@test.internal",
            "password": f"SE!{suffix}99",
            "country": "SE",
            "currency": "SEK",
            "spelpaus_status": "EXCLUDED",
        },
        headers=auth,
    )

    # Rule: Spelpaus-excluded player blocked
    code, payload, _ = client.post(
        "/api/v1/compliance/sweden/spelpaus-check",
        {"username": spelpaus_player, "country": "SE"},
        headers=auth,
    )
    blocked = _blocked(code, payload) or _reason_contains(payload, "SPELPAUS", "EXCLUDED")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Spelpaus excluded player blocked",
        passed=blocked,
        detail=f"HTTP {code} | {'blocked' if blocked else 'NOT blocked — FAIL'}",
        http_code=code,
    ))

    # Rule: Single bonus rule — second bonus must be rejected
    clean_player = f"se_clean_{_rand()}"
    client.post(
        "/api/v1/admin/players",
        {
            "username": clean_player,
            "email": f"{clean_player}@test.internal",
            "password": f"SE!{_rand()}99",
            "country": "SE",
            "currency": "SEK",
        },
        headers=auth,
    )
    # Claim first bonus
    client.post(
        "/api/v1/bonuses/claim",
        {"username": clean_player, "bonus_id": "SE_WELCOME_BONUS"},
        headers=auth,
    )
    # Attempt second bonus
    code2, payload2, _ = client.post(
        "/api/v1/bonuses/claim",
        {"username": clean_player, "bonus_id": "SE_RELOAD_BONUS"},
        headers=auth,
    )
    second_blocked = _blocked(code2, payload2) or _reason_contains(payload2, "SINGLE_BONUS", "ONE_BONUS", "ALREADY_CLAIMED")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Single bonus rule: second bonus blocked (Sweden)",
        passed=second_blocked,
        detail=f"HTTP {code2} | {'blocked' if second_blocked else 'second bonus accepted — FAIL'}",
        http_code=code2,
    ))

    # Rule: Mandatory deposit limits at registration
    code3, payload3, _ = client.get(
        f"/api/v1/compliance/sweden/deposit-limits/{clean_player}",
        headers=auth,
    )
    limits_set = code3 == 200 and _reason_contains(payload3, "LIMIT", "DEPOSIT", "SEK")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Mandatory deposit limits set (Sweden)",
        passed=limits_set,
        detail=f"HTTP {code3} | {'limits set' if limits_set else 'limits not found'}",
        http_code=code3,
    ))

    return results


# ---------------------------------------------------------------------------
# Brazil checks
# ---------------------------------------------------------------------------

def check_brazil(client: HTTPClient, admin_token: str) -> List[RuleResult]:
    results = []
    j = "Brazil"
    auth = {"Authorization": f"Bearer {admin_token}"}

    suffix = _rand()
    br_player = f"br_{suffix}"

    # Rule: CPF must be validated at registration — bad CPF rejected
    code, payload, _ = client.post(
        "/api/v1/admin/players",
        {
            "username": br_player,
            "email": f"{br_player}@test.internal",
            "password": f"BR!{suffix}99",
            "country": "BR",
            "currency": "BRL",
            "cpf": "000.000.000-00",   # invalid CPF
        },
        headers=auth,
    )
    cpf_rejected = _blocked(code, payload) or _reason_contains(payload, "CPF", "INVALID", "TAX_ID")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Invalid CPF rejected at registration",
        passed=cpf_rejected,
        detail=f"HTTP {code} | {'CPF rejected' if cpf_rejected else 'invalid CPF accepted — FAIL'}",
        http_code=code,
    ))

    # Register a valid player to run payment checks
    valid_player = f"br_valid_{_rand()}"
    client.post(
        "/api/v1/admin/players",
        {
            "username": valid_player,
            "email": f"{valid_player}@test.internal",
            "password": f"BR!{_rand()}99",
            "country": "BR",
            "currency": "BRL",
            "cpf": "123.456.789-09",   # mock valid CPF for sandbox
        },
        headers=auth,
    )

    # Rule: PIX-only — non-PIX deposit must be rejected
    code2, payload2, _ = client.post(
        "/api/v1/payments/deposit",
        {
            "username": valid_player,
            "amount": 100.00,
            "currency": "BRL",
            "method": "CARD",   # should be blocked — PIX only
            "jurisdiction": "BR",
        },
        headers=auth,
    )
    pix_only = _blocked(code2, payload2) or _reason_contains(payload2, "PIX", "PAYMENT_METHOD", "INVALID_METHOD")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Non-PIX deposit rejected (Brazil PIX-only rule)",
        passed=pix_only,
        detail=f"HTTP {code2} | {'blocked' if pix_only else 'non-PIX accepted — FAIL'}",
        http_code=code2,
    ))

    # Rule: SIGAP reporting — transaction must generate a reporting event
    code3, payload3, _ = client.post(
        "/api/v1/payments/deposit",
        {
            "username": valid_player,
            "amount": 100.00,
            "currency": "BRL",
            "method": "PIX",
            "jurisdiction": "BR",
        },
        headers=auth,
    )
    sigap_reported = _reason_contains(payload3, "SIGAP", "REGULATORY_REPORT", "REPORTED")
    # SIGAP reporting might be async — check the compliance log
    if not sigap_reported:
        code3b, payload3b, _ = client.get(
            f"/api/v1/compliance/brazil/sigap-events/{valid_player}",
            headers=auth,
        )
        sigap_reported = code3b == 200 and _reason_contains(payload3b, "SIGAP", "EVENT", "REPORTED")
    results.append(RuleResult(
        jurisdiction=j,
        rule="SIGAP reporting event generated after PIX deposit",
        passed=sigap_reported,
        detail="SIGAP event found" if sigap_reported else "SIGAP event not found",
        http_code=code3,
    ))

    # Rule: Welfare block — blocked welfare player cannot deposit
    welfare_player = f"br_welfare_{_rand()}"
    client.post(
        "/api/v1/admin/players",
        {
            "username": welfare_player,
            "email": f"{welfare_player}@test.internal",
            "password": f"BR!{_rand()}99",
            "country": "BR",
            "currency": "BRL",
            "cpf": "987.654.321-00",
            "welfare_block": True,
        },
        headers=auth,
    )
    code4, payload4, _ = client.post(
        "/api/v1/payments/deposit",
        {
            "username": welfare_player,
            "amount": 50.00,
            "currency": "BRL",
            "method": "PIX",
            "jurisdiction": "BR",
        },
        headers=auth,
    )
    welfare_blocked = _blocked(code4, payload4) or _reason_contains(payload4, "WELFARE", "BLOCKED", "RESTRICTION")
    results.append(RuleResult(
        jurisdiction=j,
        rule="Welfare-blocked player cannot deposit (Brazil)",
        passed=welfare_blocked,
        detail=f"HTTP {code4} | {'blocked' if welfare_blocked else 'deposit accepted — FAIL'}",
        http_code=code4,
    ))

    # Rule: 30-minute geo recheck — flag must be present in session config
    code5, payload5, _ = client.get(
        f"/api/v1/compliance/brazil/geo-config/{valid_player}",
        headers=auth,
    )
    geo_check = code5 == 200 and _reason_contains(payload5, "GEO", "30", "RECHECK", "INTERVAL")
    results.append(RuleResult(
        jurisdiction=j,
        rule="30-min geo recheck configured (Brazil)",
        passed=geo_check,
        detail=f"HTTP {code5} | {'geo recheck found' if geo_check else 'geo recheck not configured'}",
        http_code=code5,
    ))

    return results


# ---------------------------------------------------------------------------
# Denmark checks
# ---------------------------------------------------------------------------

def check_denmark(client: HTTPClient, admin_token: str) -> List[RuleResult]:
    results = []
    j = "Denmark"
    auth = {"Authorization": f"Bearer {admin_token}"}

    suffix = _rand()
    rofus_player = f"dk_rofus_{suffix}"

    # Register a ROFUS-excluded player
    client.post(
        "/api/v1/admin/players",
        {
            "username": rofus_player,
            "email": f"{rofus_player}@test.internal",
            "password": f"DK!{suffix}99",
            "country": "DK",
            "currency": "DKK",
            "rofus_status": "EXCLUDED",
        },
        headers=auth,
    )

    # Rule: ROFUS-excluded player blocked before play
    code, payload, _ = client.post(
        "/api/v1/compliance/denmark/rofus-check",
        {"username": rofus_player, "country": "DK"},
        headers=auth,
    )
    blocked = _blocked(code, payload) or _reason_contains(payload, "ROFUS", "EXCLUDED", "BLOCKED")
    results.append(RuleResult(
        jurisdiction=j,
        rule="ROFUS excluded player blocked before play (Denmark)",
        passed=blocked,
        detail=f"HTTP {code} | {'blocked' if blocked else 'NOT blocked — FAIL'}",
        http_code=code,
    ))

    return results


# ---------------------------------------------------------------------------
# JURISDICTION_MAP
# ---------------------------------------------------------------------------

JURISDICTION_MAP = {
    "UK": check_uk,
    "Malta": check_malta,
    "Sweden": check_sweden,
    "Brazil": check_brazil,
    "Denmark": check_denmark,
}


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def run_dry(report: JurisdictionReport, verbose: bool) -> None:
    dry = [
        ("UK", "GamStop check before play"),
        ("UK", "Deposit limits required before first deposit"),
        ("Malta", "MGA PPD limits at account creation"),
        ("Malta", "Session timer configured"),
        ("Sweden", "Spelpaus check"),
        ("Sweden", "Single bonus rule"),
        ("Sweden", "Mandatory deposit limits"),
        ("Brazil", "CPF validation at registration"),
        ("Brazil", "PIX-only payment method"),
        ("Brazil", "SIGAP reporting event generated"),
        ("Brazil", "Welfare block enforced"),
        ("Brazil", "30-min geo recheck configured"),
        ("Denmark", "ROFUS check before play"),
    ]
    for jur, rule in dry:
        r = RuleResult(
            jurisdiction=jur,
            rule=rule,
            passed=True,
            detail="DRY: no network call made",
        )
        report.add(r)
        if verbose:
            print(f"  [PASS] [{jur}] {rule}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(
    base_url: str,
    admin_token: str,
    timeout: int,
    verbose: bool,
    dry_run: bool,
    jurisdiction: Optional[str],
) -> JurisdictionReport:
    report = JurisdictionReport()

    if dry_run:
        print("[JURISDICTION MATRIX TEST] DRY RUN")
        run_dry(report, verbose)
        return report

    client = HTTPClient(base_url, timeout=timeout, verbose=verbose)

    jurs_to_run = (
        {jurisdiction: JURISDICTION_MAP[jurisdiction]}
        if jurisdiction and jurisdiction in JURISDICTION_MAP
        else JURISDICTION_MAP
    )

    for jname, fn in jurs_to_run.items():
        if verbose:
            print(f"\n  -- Jurisdiction: {jname} --")
        try:
            sub_results = fn(client, admin_token)
        except Exception as exc:
            sub_results = [RuleResult(
                jurisdiction=jname,
                rule="exception",
                passed=False,
                detail=f"Unhandled exception: {exc}",
            )]
        for r in sub_results:
            report.add(r)
            if verbose:
                status = "PASS" if r.passed else "FAIL"
                print(f"    [{status}] {r.rule}: {r.detail}")

    return report


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def print_report(report: JurisdictionReport) -> None:
    print("\n" + "=" * 65)
    print("JURISDICTION MATRIX TEST REPORT")
    print("=" * 65)
    for jur, results in report.by_jurisdiction().items():
        j_pass = all(r.passed for r in results)
        j_status = "PASS" if j_pass else "FAIL"
        print(f"\n  [{j_status}] {jur}")
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            code_str = f" (HTTP {r.http_code})" if r.http_code else ""
            print(f"    [{status}]{code_str} {r.rule}")
            print(f"             {r.detail}")
    print("\n" + "-" * 65)
    failures = len(report.failures)
    total = len(report.results)
    print(f"  Result : {'PASS' if report.passed else 'FAIL'}")
    print(f"  Rules  : {total - failures}/{total} passed")
    print(f"  Elapsed: {report.elapsed():.2f}s")
    print("=" * 65)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Jurisdiction Matrix Test — per-jurisdiction compliance rules",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--admin-token", default="")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--jurisdiction",
        choices=list(JURISDICTION_MAP.keys()),
        help="Test a single jurisdiction",
    )

    args = parser.parse_args()

    import os
    admin_token = args.admin_token or os.environ.get("ADMIN_TOKEN", "")

    print(f"[JURISDICTION MATRIX TEST] Target: {args.base_url}")
    report = run(args.base_url, admin_token, args.timeout, args.verbose, args.dry_run, args.jurisdiction)
    print_report(report)

    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
