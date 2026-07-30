#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

#
# Post-deploy validation for the bet-brazil edge tier. Read-only probes only.
# Exits non-zero if any invariant is broken, so it can gate a CI promotion.
#
# Usage: bash scripts/smoke-test.sh
# Override targets with SITE=, SPORTS=, ACCOUNT_SUBDOMAIN= env vars.
#
set -euo pipefail

SITE="${SITE:-https://bet-brazil.cloud-acmetocasino.com}"
SPORTS="${SPORTS:-https://sports-api.cloud-acmetocasino.com}"
ACCOUNT_SUBDOMAIN="${ACCOUNT_SUBDOMAIN:?set ACCOUNT_SUBDOMAIN}"

fail=0

expect_header() { # description url header-name
  local count
  count="$(curl -s -o /dev/null -D - "$2" | grep -ci "$3" || true)"
  if [ "$count" -ge 1 ]; then
    echo "PASS: $1"
  else
    echo "FAIL: $1 (header '$3' absent)"
    fail=1
  fi
}

# 1. Public site still serves.
code="$(curl -s -o /dev/null -w '%{http_code}' "$SITE/")"
case "$code" in
  200 | 301 | 302) echo "PASS: public site reachable ($code)" ;;
  *) echo "FAIL: public site returned $code"; fail=1 ;;
esac

# 2. sports-api now carries its security headers (added in this release).
expect_header "sports-api HSTS present"    "$SPORTS/standings" "strict-transport-security"
expect_header "sports-api nosniff present" "$SPORTS/standings" "x-content-type-options"

# 3. Backend workers are NOT publicly routable (workers_dev = false).
for w in bet-brazil-pix-webhook bet-brazil-odds-feed bet-brazil-sigap-reporter bet-brazil-wallet bet-brazil-session-manager; do
  wcode="$(curl -s -o /dev/null -w '%{http_code}' "https://$w.$ACCOUNT_SUBDOMAIN.workers.dev/")"
  case "$wcode" in
    404 | 000 | 530) echo "PASS: $w not routable ($wcode)" ;;
    *) echo "FAIL: $w reachable at *.workers.dev ($wcode)"; fail=1 ;;
  esac
done

if [ "$fail" -eq 0 ]; then
  echo "SMOKE TEST PASSED"
else
  echo "SMOKE TEST FAILED" >&2
fi
exit "$fail"
