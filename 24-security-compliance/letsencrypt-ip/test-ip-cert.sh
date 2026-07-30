#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# Verify a Let's Encrypt IP address certificate is correctly issued and served.
#
# Checks performed:
#   1. Certificate file exists at expected path
#   2. Certificate SAN contains IP Address (not DNS)
#   3. Certificate is issued by Let's Encrypt (YE2 intermediate for shortlived)
#   4. Certificate validity window is ~6 days (shortlived profile)
#   5. TLS handshake succeeds on the configured port
#   6. Certificate chain validates against Let's Encrypt root
#   7. No OCSP/CRL URLs (expected for shortlived certs)
#
# Usage:
#   ./test-ip-cert.sh <ip-address> [port]
#
# Examples:
#   ./test-ip-cert.sh 203.0.113.1
#   ./test-ip-cert.sh 203.0.113.1 443
#   ./test-ip-cert.sh 203.0.113.1 9443

set -euo pipefail

IP="${1:?Usage: $0 <ip-address> [port]}"
PORT="${2:-443}"
CERT_PATH="/etc/letsencrypt/live/${IP}/fullchain.pem"
PASS=0
FAIL=0
WARN=0

check() {
    local desc="$1"
    local result="$2"
    local expected="$3"
    if echo "$result" | grep -q "$expected"; then
        echo "  PASS  $desc"
        ((PASS++))
    else
        echo "  FAIL  $desc"
        echo "        Expected: $expected"
        echo "        Got:      $result"
        ((FAIL++))
    fi
}

warn_if_missing() {
    local desc="$1"
    local result="$2"
    local pattern="$3"
    if echo "$result" | grep -q "$pattern"; then
        echo "  WARN  $desc (present, unexpected for shortlived)"
        ((WARN++))
    else
        echo "  PASS  $desc (absent, expected for shortlived)"
        ((PASS++))
    fi
}

echo "=== Testing IP Address Certificate: ${IP} ==="
echo ""

# 1. Cert file existence
echo "--- File checks ---"
if [[ -f "${CERT_PATH}" ]]; then
    echo "  PASS  Certificate file exists: ${CERT_PATH}"
    ((PASS++))
else
    echo "  FAIL  Certificate file not found: ${CERT_PATH}"
    ((FAIL++))
    echo ""
    echo "TOTAL: ${PASS} passed, ${FAIL} failed, ${WARN} warnings"
    exit 1
fi

# 2. Parse cert details
CERT_TEXT=$(openssl x509 -in "${CERT_PATH}" -noout -text 2>/dev/null)
CERT_DATES=$(openssl x509 -in "${CERT_PATH}" -noout -dates 2>/dev/null)

echo ""
echo "--- Certificate content checks ---"

# SAN must contain IP Address, not DNS
check "SAN contains IP Address" \
    "$(echo "${CERT_TEXT}" | grep 'Subject Alternative Name' -A1)" \
    "IP Address:${IP}"

# Issuer must be Let's Encrypt
check "Issuer is Let's Encrypt" \
    "$(echo "${CERT_TEXT}" | grep 'Issuer:')" \
    "Let's Encrypt"

# Intermediate should be YE2 (shortlived chain as of 2025-2026)
check "Intermediate CA is YE2 (shortlived chain)" \
    "$(echo "${CERT_TEXT}" | grep 'CN = YE')" \
    "YE"

# Subject should be empty (IP certs have no CN)
SUBJECT_LINE=$(openssl x509 -in "${CERT_PATH}" -noout -subject 2>/dev/null)
if [[ "${SUBJECT_LINE}" == "subject=" ]]; then
    echo "  PASS  Subject is empty (correct for IP certs)"
    ((PASS++))
else
    echo "  WARN  Subject is not empty: ${SUBJECT_LINE}"
    ((WARN++))
fi

# Validity window: should be ~6 days (shortlived)
NOT_BEFORE=$(date -d "$(echo "${CERT_DATES}" | grep notBefore | cut -d= -f2)" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$(echo "${CERT_DATES}" | grep notBefore | cut -d= -f2)" +%s 2>/dev/null)
NOT_AFTER=$(date -d "$(echo "${CERT_DATES}" | grep notAfter | cut -d= -f2)" +%s 2>/dev/null || date -j -f "%b %d %T %Y %Z" "$(echo "${CERT_DATES}" | grep notAfter | cut -d= -f2)" +%s 2>/dev/null)
VALIDITY_DAYS=$(( (NOT_AFTER - NOT_BEFORE) / 86400 ))

if [[ ${VALIDITY_DAYS} -le 7 ]]; then
    echo "  PASS  Validity is ${VALIDITY_DAYS} days (shortlived profile, <= 7 days expected)"
    ((PASS++))
else
    echo "  FAIL  Validity is ${VALIDITY_DAYS} days (expected <= 7 for shortlived profile)"
    ((FAIL++))
fi

# No OCSP (shortlived certs omit OCSP URLs; a CRL URL may still be present)
warn_if_missing "No OCSP URL (expected absent for shortlived)" \
    "${CERT_TEXT}" \
    "OCSP"

# Check expiry
DAYS_REMAINING=$(( (NOT_AFTER - $(date +%s)) / 86400 ))
echo ""
echo "--- Validity window ---"
echo "  Not Before : $(echo "${CERT_DATES}" | grep notBefore | cut -d= -f2)"
echo "  Not After  : $(echo "${CERT_DATES}" | grep notAfter | cut -d= -f2)"
echo "  Days Left  : ${DAYS_REMAINING}"
if [[ ${DAYS_REMAINING} -le 0 ]]; then
    echo "  FAIL  Certificate has EXPIRED"
    ((FAIL++))
elif [[ ${DAYS_REMAINING} -le 1 ]]; then
    echo "  WARN  Certificate expires in ${DAYS_REMAINING} day(s) — renew immediately"
    ((WARN++))
fi

# 3. TLS handshake test
echo ""
echo "--- TLS handshake test (${IP}:${PORT}) ---"
TLS_OUTPUT=$(openssl s_client -connect "${IP}:${PORT}" </dev/null 2>&1)
VERIFY_CODE=$(echo "${TLS_OUTPUT}" | grep 'Verify return code' | grep -oP '\d+' | head -1)

if [[ "${VERIFY_CODE}" == "0" ]]; then
    echo "  PASS  TLS handshake succeeded (Verify return code: 0 (ok))"
    ((PASS++))
else
    echo "  FAIL  TLS handshake failed (Verify return code: ${VERIFY_CODE})"
    echo "        Full output: $(echo "${TLS_OUTPUT}" | grep -E 'Verify|error' | head -5)"
    ((FAIL++))
fi

# Check that the served cert matches the file cert
SERVED_FINGERPRINT=$(echo "${TLS_OUTPUT}" | openssl x509 -noout -fingerprint 2>/dev/null | cut -d= -f2 || echo "")
FILE_FINGERPRINT=$(openssl x509 -in "${CERT_PATH}" -noout -fingerprint 2>/dev/null | cut -d= -f2 || echo "")
if [[ -n "${SERVED_FINGERPRINT}" && "${SERVED_FINGERPRINT}" == "${FILE_FINGERPRINT}" ]]; then
    echo "  PASS  Served certificate matches file certificate"
    ((PASS++))
elif [[ -n "${SERVED_FINGERPRINT}" ]]; then
    echo "  FAIL  Served certificate does NOT match file certificate"
    echo "        Served:  ${SERVED_FINGERPRINT}"
    echo "        File:    ${FILE_FINGERPRINT}"
    ((FAIL++))
else
    echo "  WARN  Could not compare served vs. file certificate fingerprints"
    ((WARN++))
fi

echo ""
echo "=== Results: ${PASS} passed, ${FAIL} failed, ${WARN} warnings ==="

if [[ ${FAIL} -gt 0 ]]; then
    exit 1
fi
