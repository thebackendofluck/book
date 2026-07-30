#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test-pg-mtls.sh — Verify PostgreSQL mTLS configuration
#
# Tests 4 scenarios:
#   1. Connection WITH valid client certificate    — must SUCCEED
#   2. Connection WITHOUT any client certificate  — must FAIL
#   3. Connection with WRONG client certificate   — must FAIL
#   4. Connection with EXPIRED client certificate — must FAIL
#
# Cross-reference: Chapter 24h, Chapter 27

set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
pass()   { echo -e "${GREEN}[PASS]${NC}  $*"; }
fail_test() { echo -e "${RED}[FAIL]${NC}  $*"; FAILURES=$(( FAILURES + 1 )); }
info()   { echo -e "${YELLOW}[..]${NC}   $*"; }
banner() { echo -e "\n${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
           echo -e "${BOLD} $*${NC}"
           echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"; }

FAILURES=0

# ─── Defaults ──────────────────────────────────────────────────────────────
PG_HOST="10.0.10.30"
PG_PORT=5432
PG_VERSION=16
APP_USER="casino_app"
APP_DB="casino"
CA_DIR="/etc/postgresql-ha/certs"

usage() {
cat <<EOF
Usage: $0 [OPTIONS]

  --pg-host HOST   PostgreSQL server (default: 10.0.10.30)
  --pg-port PORT   PostgreSQL port (default: 5432)
  --app-user USER  Application DB username (default: casino_app)
  --app-db DB      Database to connect to (default: casino)
  --ca-dir DIR     Directory containing CA and client certs (default: /etc/postgresql-ha/certs)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --pg-host)    PG_HOST="$2";    shift 2 ;;
        --pg-port)    PG_PORT="$2";    shift 2 ;;
        --app-user)   APP_USER="$2";   shift 2 ;;
        --app-db)     APP_DB="$2";     shift 2 ;;
        --ca-dir)     CA_DIR="$2";     shift 2 ;;
        --help|-h)    usage; exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

VALID_CERT="${CA_DIR}/client-${APP_USER}.crt"
VALID_KEY="${CA_DIR}/client-${APP_USER}.key"
CA_CERT="${CA_DIR}/ca.crt"

# Check prerequisites
for f in "$VALID_CERT" "$VALID_KEY" "$CA_CERT"; do
    [[ -f "$f" ]] || { echo "Missing: $f — run setup-pg-mtls.sh first"; exit 1; }
done

psql_test() {
    # psql_test DESCRIPTION EXPECTED_SUCCESS [extra psql args...]
    local desc="$1" expect_ok="$2"
    shift 2
    if psql "host=${PG_HOST} port=${PG_PORT} dbname=${APP_DB} user=${APP_USER} connect_timeout=5 $*" \
        -c "SELECT 1;" >/dev/null 2>&1; then
        if [[ "$expect_ok" == "yes" ]]; then
            pass "${desc}"
        else
            fail_test "${desc} — connection SUCCEEDED but should have FAILED"
        fi
    else
        if [[ "$expect_ok" == "no" ]]; then
            pass "${desc} (connection correctly rejected)"
        else
            fail_test "${desc} — connection FAILED but should have SUCCEEDED"
        fi
    fi
}

banner "PostgreSQL mTLS Connection Tests"
info "Target: ${PG_HOST}:${PG_PORT}/${APP_DB} as ${APP_USER}"
echo ""

# ─── Test 1: valid client certificate ─────────────────────────────────────
info "Test 1: Connect WITH valid client certificate (should succeed)"
psql_test "Valid cert connection" "yes" \
    "sslmode=verify-full sslcert=${VALID_CERT} sslkey=${VALID_KEY} sslrootcert=${CA_CERT}"

# ─── Test 2: no client certificate ────────────────────────────────────────
info "Test 2: Connect WITHOUT client certificate (should fail)"
psql_test "No client cert" "no" \
    "sslmode=verify-ca sslrootcert=${CA_CERT}"

# ─── Test 3: wrong client certificate (use a freshly generated self-signed) ─
info "Test 3: Connect with WRONG (self-signed) client certificate (should fail)"
WRONG_KEY=$(mktemp /tmp/wrong-XXXXXX.key)
WRONG_CERT=$(mktemp /tmp/wrong-XXXXXX.crt)
openssl req -x509 -newkey rsa:2048 -keyout "$WRONG_KEY" -out "$WRONG_CERT" \
    -days 1 -nodes -subj "/CN=${APP_USER}" -sha256 >/dev/null 2>&1
psql_test "Wrong client cert (self-signed, not from our CA)" "no" \
    "sslmode=verify-full sslcert=${WRONG_CERT} sslkey=${WRONG_KEY} sslrootcert=${CA_CERT}"
rm -f "$WRONG_KEY" "$WRONG_CERT"

# ─── Test 4: plaintext (no SSL) ───────────────────────────────────────────
info "Test 4: Connect with sslmode=disable / plaintext (should fail due to hostnossl reject)"
psql_test "Plaintext / sslmode=disable" "no" \
    "sslmode=disable"

# ─── Test 5: expired client certificate ───────────────────────────────────
info "Test 5: Connect with EXPIRED client certificate (should fail)"
EXP_KEY=$(mktemp /tmp/expired-XXXXXX.key)
EXP_CSR=$(mktemp /tmp/expired-XXXXXX.csr)
EXP_CERT=$(mktemp /tmp/expired-XXXXXX.crt)
openssl genrsa -out "$EXP_KEY" 2048 >/dev/null 2>&1
openssl req -new -key "$EXP_KEY" -out "$EXP_CSR" -subj "/CN=${APP_USER}" >/dev/null 2>&1
# Issue cert that expired 1 day ago
openssl x509 -req -in "$EXP_CSR" \
    -CA "$CA_CERT" -CAkey "${CA_DIR}/ca.key" -CAcreateserial \
    -out "$EXP_CERT" -days 1 -sha256 \
    -startdate "$(date -d '3 days ago' +%y%m%d%H%M%SZ 2>/dev/null || date -v-3d +%y%m%d%H%M%SZ)" \
    -enddate "$(date -d '2 days ago' +%y%m%d%H%M%SZ 2>/dev/null || date -v-2d +%y%m%d%H%M%SZ)" \
    >/dev/null 2>&1 || true
psql_test "Expired client certificate" "no" \
    "sslmode=verify-full sslcert=${EXP_CERT} sslkey=${EXP_KEY} sslrootcert=${CA_CERT}"
rm -f "$EXP_KEY" "$EXP_CSR" "$EXP_CERT"

# ─── Summary ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
if [[ $FAILURES -eq 0 ]]; then
    echo -e "${GREEN}${BOLD} All mTLS tests passed.${NC}"
else
    echo -e "${RED}${BOLD} ${FAILURES} test(s) FAILED.${NC}"
    exit 1
fi
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
