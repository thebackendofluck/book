#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# pre_switch_validation.sh — Full smoke test suite for a new cluster
#
# Called by rotation-driver.sh after provisioning and again by switchover.sh
# immediately before traffic moves. A non-zero exit aborts the switchover.
#
# Deliberately NOT "set -e". Every check has to run even after an earlier one
# fails, because the point of a smoke suite is to tell the operator everything
# that is wrong with the new cluster, not the first thing. The exit code at the
# bottom is what gates the switchover.
set -uo pipefail

CLUSTER_COLOR="${1:?Usage: $0 <blue|green>}"

# create_casino_cluster.sh writes this to /tmp, which systemd's PrivateTmp=true
# makes invisible to any other unit. Prefer a real state directory.
CASINO_STATE_DIR="${CASINO_STATE_DIR:-/var/lib/casino}"
INGRESS_IP=""
for candidate in "${CASINO_STATE_DIR}/casino-${CLUSTER_COLOR}-ingress-ip" \
                 "/tmp/casino-${CLUSTER_COLOR}-ingress-ip"; do
    if [[ -r "$candidate" ]]; then
        INGRESS_IP="$(tr -d '[:space:]' < "$candidate")"
        break
    fi
done
if [[ -z "$INGRESS_IP" ]]; then
    echo "FATAL: no ingress IP recorded for ${CLUSTER_COLOR}" >&2
    exit 1
fi

CASINO_HOST="casino.internal"
FAILURES=0

log()  { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [SMOKE] $*"; }
pass() { log "PASS: $*"; }
# FAILURES=$((...)) rather than ((FAILURES++)): the arithmetic form evaluates to
# the old value, so it returns exit status 1 the first time FAILURES is 0. As the
# last command in a "... && pass || fail" list under set -e, that aborted the
# whole suite at the first failed check and tests 2 through 7 never ran.
fail() { log "FAIL: $*"; FAILURES=$((FAILURES + 1)); return 0; }

# ── Test 1: Basic HTTP health ─────────────────────────────────────────────────
response=$(curl -sf --max-time 10 \
    -H "Host: $CASINO_HOST" \
    "http://${INGRESS_IP}/health" 2>/dev/null)
status=$(echo "$response" | jq -r '.status' 2>/dev/null)
cluster=$(echo "$response" | jq -r '.cluster' 2>/dev/null)

if [[ "$status" == "ok" && "$cluster" == "$CLUSTER_COLOR" ]]; then
    pass "Health endpoint: status=$status cluster=$cluster"
else
    fail "Health endpoint: response=$response"
fi

# ── Test 2: Wallet service API reachable ──────────────────────────────────────
wallet_health=$(curl -sf --max-time 10 \
    -H "Host: $CASINO_HOST" \
    "http://${INGRESS_IP}/api/v1/wallet/health" 2>/dev/null \
    | jq -r '.status' 2>/dev/null)

if [[ "$wallet_health" == "ok" ]]; then
    pass "Wallet service health"
else
    fail "Wallet service health: $wallet_health"
fi

# ── Test 3: Auth service returns valid JWT for test player ────────────────────
token_response=$(curl -sf --max-time 15 \
    -H "Host: $CASINO_HOST" \
    -H "Content-Type: application/json" \
    -d '{"username":"smoke-test-player","password":"SmokeTest!2024","synthetic":true}' \
    "http://${INGRESS_IP}/api/v1/auth/login" 2>/dev/null)

access_token=$(echo "$token_response" | jq -r '.access_token' 2>/dev/null)
if [[ -n "$access_token" && "$access_token" != "null" ]]; then
    pass "Auth service issued JWT"
else
    fail "Auth service: no token in response"
fi

# ── Test 4: Game API lists available games ────────────────────────────────────
if [[ -n "$access_token" && "$access_token" != "null" ]]; then
    games_count=$(curl -sf --max-time 10 \
        -H "Host: $CASINO_HOST" \
        -H "Authorization: Bearer $access_token" \
        "http://${INGRESS_IP}/api/v1/games" 2>/dev/null \
        | jq '.games | length' 2>/dev/null)

    if [[ "${games_count:-0}" -gt 0 ]]; then
        pass "Game API: $games_count games available"
    else
        fail "Game API: no games returned"
    fi
fi

# ── Test 5: Redis connectivity (via wallet service read) ──────────────────────
if [[ -n "$access_token" && "$access_token" != "null" ]]; then
    balance_response=$(curl -sf --max-time 10 \
        -H "Host: $CASINO_HOST" \
        -H "Authorization: Bearer $access_token" \
        "http://${INGRESS_IP}/api/v1/wallet/balance" 2>/dev/null)

    balance=$(echo "$balance_response" | jq -r '.balance' 2>/dev/null)
    if [[ -n "$balance" && "$balance" != "null" ]]; then
        pass "Wallet balance read (Redis session valid): $balance"
    else
        fail "Wallet balance read failed: $balance_response"
    fi
fi

# ── Test 6: WebSocket connectivity ────────────────────────────────────────────
ws_test=$(python3 -c "
import asyncio, websockets, json, sys

async def test():
    try:
        uri = 'ws://${INGRESS_IP}/ws/live'
        headers = {'Host': '${CASINO_HOST}'}
        async with websockets.connect(uri, extra_headers=headers, open_timeout=10) as ws:
            await ws.send(json.dumps({'type': 'ping', 'synthetic': True}))
            msg = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(msg)
            if data.get('type') == 'pong':
                print('ok')
            else:
                print(f'unexpected: {data}')
    except Exception as e:
        print(f'error: {e}')
        sys.exit(1)

asyncio.run(test())
" 2>/dev/null)

if [[ "$ws_test" == "ok" ]]; then
    pass "WebSocket connectivity"
else
    fail "WebSocket: $ws_test"
fi

# ── Test 7: TLS certificate valid (for HTTPS ingress) ────────────────────────
cert_expiry=$(echo | openssl s_client \
    -servername "$CASINO_HOST" \
    -connect "${INGRESS_IP}:443" 2>/dev/null \
    | openssl x509 -noout -enddate 2>/dev/null \
    | cut -d= -f2)

if [[ -z "$cert_expiry" ]]; then
    # Previously this whole check was wrapped in "if [[ -n ... ]]", so a cluster
    # whose TLS handshake failed outright scored neither a pass nor a fail: the
    # certificate check the switchover gate depends on silently did not happen.
    fail "TLS: could not read a certificate from ${INGRESS_IP}:443"
else
    expiry_epoch=$(date -d "$cert_expiry" +%s 2>/dev/null \
        || date -j -f "%b %d %T %Y %Z" "$cert_expiry" +%s 2>/dev/null)
    if [[ -z "$expiry_epoch" ]]; then
        fail "TLS: could not parse certificate expiry '$cert_expiry'"
    else
        days_until_expiry=$(( (expiry_epoch - $(date +%s)) / 86400 ))
        if [[ "$days_until_expiry" -gt 30 ]]; then
            pass "TLS certificate valid for ${days_until_expiry} days"
        else
            fail "TLS certificate expires in ${days_until_expiry} days — too soon"
        fi
    fi
fi

# ── Result ────────────────────────────────────────────────────────────────────
log "Smoke tests complete: failures=$FAILURES"
# Exit 1 on any failure rather than the raw count, which would wrap to 0 modulo
# 256 and read as success.
[[ "$FAILURES" -eq 0 ]] || exit 1
exit 0
