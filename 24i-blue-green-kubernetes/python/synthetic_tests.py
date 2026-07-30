#!/usr/bin/env python3
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# synthetic_tests.py — Synthetic transaction validation before switchover
"""
Runs a controlled set of player-like transactions against the new cluster
using dedicated synthetic test accounts. These accounts:
  - Are flagged as synthetic in the player database (excluded from regulatory reports)
  - Have a fixed test balance that is reset after each test run
  - Can trigger all code paths including bonus activation, withdrawals, and game rounds
"""

import requests
import json
import sys
import time
import os

INGRESS_IP = os.environ['INGRESS_IP']
CLUSTER_COLOR = os.environ['CLUSTER_COLOR']
BASE_URL = f"http://{INGRESS_IP}"
HOST = "casino.internal"
HEADERS = {"Host": HOST, "Content-Type": "application/json"}

SYNTHETIC_PLAYER = {
    "username": "synthetic-fullflow-001",
    "password": os.environ['SYNTHETIC_TEST_PASSWORD'],
}

failures = []
passes = []

def fail(test_name, reason):
    print(f"FAIL [{test_name}]: {reason}")
    failures.append({"test": test_name, "reason": reason})

def ok(test_name, detail=""):
    print(f"PASS [{test_name}]{': ' + detail if detail else ''}")
    passes.append(test_name)

def api(method, path, **kwargs):
    resp = requests.request(
        method, f"{BASE_URL}{path}",
        headers=HEADERS,
        timeout=15,
        **kwargs
    )
    resp.raise_for_status()
    return resp.json()

# ── Login ─────────────────────────────────────────────────────────────────────
try:
    auth = api("POST", "/api/v1/auth/login", json=SYNTHETIC_PLAYER)
    token = auth["access_token"]
    HEADERS["Authorization"] = f"Bearer {token}"
    ok("login", f"cluster={auth.get('cluster', 'unknown')}")
    assert auth.get("cluster") == CLUSTER_COLOR, f"Expected {CLUSTER_COLOR}, got {auth.get('cluster')}"
    ok("cluster-identity", f"serving cluster is {CLUSTER_COLOR}")
except Exception as e:
    fail("login", str(e))
    sys.exit(1)  # Cannot proceed without auth

# ── Balance check ─────────────────────────────────────────────────────────────
try:
    balance_resp = api("GET", "/api/v1/wallet/balance")
    balance = balance_resp["balance"]
    currency = balance_resp["currency"]
    ok("balance-read", f"balance={balance} {currency}")
except Exception as e:
    fail("balance-read", str(e))

# ── Deposit (synthetic) ───────────────────────────────────────────────────────
try:
    deposit = api("POST", "/api/v1/wallet/deposit", json={
        "amount": "10.00",
        "currency": "EUR",
        "payment_method": "synthetic",
        "synthetic_test": True
    })
    ok("deposit", f"txid={deposit.get('transaction_id')}")
except Exception as e:
    fail("deposit", str(e))

# ── Place bet ─────────────────────────────────────────────────────────────────
try:
    bet = api("POST", "/api/v1/games/roulette/bet", json={
        "amount": "1.00",
        "currency": "EUR",
        "bet_type": "red",
        "synthetic_test": True
    })
    round_id = bet.get("round_id")
    ok("bet-placement", f"round_id={round_id}")

    # Wait for round result
    time.sleep(2)
    result = api("GET", f"/api/v1/games/roulette/round/{round_id}")
    ok("bet-result", f"outcome={result.get('outcome')} payout={result.get('payout_amount')}")
except Exception as e:
    fail("bet-and-result", str(e))

# ── Bonus trigger ─────────────────────────────────────────────────────────────
try:
    bonus = api("POST", "/api/v1/bonus/trigger", json={
        "bonus_code": "SYNTHETIC_TEST_BONUS",
        "synthetic_test": True
    })
    ok("bonus-trigger", f"bonus_id={bonus.get('bonus_id')}")
except Exception as e:
    fail("bonus-trigger", str(e))

# ── Withdrawal request ────────────────────────────────────────────────────────
try:
    withdrawal = api("POST", "/api/v1/wallet/withdraw", json={
        "amount": "5.00",
        "currency": "EUR",
        "payment_method": "synthetic",
        "synthetic_test": True
    })
    ok("withdrawal-request", f"status={withdrawal.get('status')}")
except Exception as e:
    fail("withdrawal-request", str(e))

# ── Audit log entry created ───────────────────────────────────────────────────
try:
    time.sleep(3)  # Allow audit pipeline to flush
    audit = api("GET", "/api/v1/audit/last-entry", params={"synthetic": "true"})
    assert audit.get("cluster") == CLUSTER_COLOR
    ok("audit-log-written", f"entry_id={audit.get('entry_id')}")
except Exception as e:
    fail("audit-log", str(e))

# ── Result ────────────────────────────────────────────────────────────────────
# Count what actually ran. The previous version computed
# "len(failures) + sum(1 for _ in range(6 - len(failures)))", which is just 6, and
# reported "6 - len(failures)) / 6" against a hardcoded 6 while the suite has
# seven checks — so the summary line disagreed with the PASS/FAIL lines above it.
total = len(passes) + len(failures)
print(f"\nSynthetic transaction tests: {len(passes)}/{total} passed")

if failures:
    print("\nFailed tests:")
    for f in failures:
        print(f"  - {f['test']}: {f['reason']}")
    sys.exit(1)

print(f"All synthetic tests passed on {CLUSTER_COLOR} cluster.")
sys.exit(0)
