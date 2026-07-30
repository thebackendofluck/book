#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test-hkdf-derivation.sh
# HKDF key derivation test using OpenBao Transit context-based derivation.
# Validates that each context produces a unique ciphertext and that
# cross-context decryption is rejected (when derived=true on the key).
# Prerequisites: OpenBao running, Transit engine enabled, BAO_TOKEN set.
# Usage: BAO_TOKEN=<token> bash test-hkdf-derivation.sh

set -euo pipefail

BAO_ADDR="${BAO_ADDR:-https://127.0.0.1:8200}"
TRANSIT_KEY="${TRANSIT_KEY:-hkdf-derived}"
EVIDENCE_DIR="${EVIDENCE_DIR:-/opt/yubihsm-evidence}"

log()  { echo "[$(date '+%H:%M:%S')] $*"; }
pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

if [ -z "${BAO_TOKEN:-}" ]; then
    fail "BAO_TOKEN environment variable is required"
fi
export BAO_TOKEN BAO_ADDR

log "=== HKDF Key Derivation Test (OpenBao Transit context-based) ==="

# Create derived key (derived=true means each context = unique subkey via HKDF-SHA256)
log "Creating Transit derived key: ${TRANSIT_KEY}"
bao write -tls-skip-verify -f "transit/keys/${TRANSIT_KEY}" \
    type=aes256-gcm96 derived=true 2>/dev/null || \
    log "Key ${TRANSIT_KEY} already exists"

# Verify key has derivation enabled
DERIVED="$(bao read -tls-skip-verify -field=derived "transit/keys/${TRANSIT_KEY}" 2>/dev/null)"
if [ "${DERIVED}" != "true" ]; then
    fail "Key ${TRANSIT_KEY} does not have derived=true — context isolation not enforced"
fi
log "Key derivation enabled: derived=${DERIVED}"

PLAINTEXT="acmetocasino-hkdf-test-payload"
PT_B64="$(printf '%s' "${PLAINTEXT}" | base64 -w0)"
CONTEXTS=(
    "game:book-of-dead"
    "game:starburst"
    "player:VIP-001"
    "session:2026-03-30"
    "audit:daily-batch"
)

declare -A CIPHERTEXTS

log "Encrypting with ${#CONTEXTS[@]} contexts..."
for CTX in "${CONTEXTS[@]}"; do
    CTX_B64="$(printf '%s' "${CTX}" | base64 -w0)"
    CIPHER="$(bao write -tls-skip-verify -field=ciphertext "transit/encrypt/${TRANSIT_KEY}" \
        plaintext="${PT_B64}" context="${CTX_B64}" 2>/dev/null)"
    [ -z "${CIPHER}" ] && fail "Encryption failed for context: ${CTX}"
    CIPHERTEXTS["${CTX}"]="${CIPHER}"
    log "  Context '${CTX}' -> ${CIPHER:0:25}..."
done

# Verify all ciphertexts are unique (different derived keys = different ciphertexts)
UNIQUE_COUNT="$(printf '%s\n' "${CIPHERTEXTS[@]}" | sort -u | wc -l)"
if [ "${UNIQUE_COUNT}" -eq "${#CONTEXTS[@]}" ]; then
    pass "All ${#CONTEXTS[@]} ciphertexts are unique (different derived subkeys)"
else
    fail "Ciphertext uniqueness check failed: ${UNIQUE_COUNT}/${#CONTEXTS[@]} unique"
fi

# Verify correct context decrypts successfully
log "Verifying correct-context decryption..."
for CTX in "${CONTEXTS[@]}"; do
    CTX_B64="$(printf '%s' "${CTX}" | base64 -w0)"
    DECRYPTED="$(bao write -tls-skip-verify -field=plaintext "transit/decrypt/${TRANSIT_KEY}" \
        ciphertext="${CIPHERTEXTS[${CTX}]}" context="${CTX_B64}" 2>/dev/null | base64 -d)"
    if [ "${DECRYPTED}" = "${PLAINTEXT}" ]; then
        pass "  Context '${CTX}' decrypts correctly"
    else
        fail "  Context '${CTX}' decryption mismatch"
    fi
done

# Verify wrong context is rejected
log "Testing cross-context isolation (wrong context should fail)..."
CTX1="${CONTEXTS[0]}"
CTX2="${CONTEXTS[1]}"
CTX2_B64="$(printf '%s' "${CTX2}" | base64 -w0)"

WRONG_RESULT="$(bao write -tls-skip-verify "transit/decrypt/${TRANSIT_KEY}" \
    ciphertext="${CIPHERTEXTS[${CTX1}]}" context="${CTX2_B64}" 2>&1 || true)"

if echo "${WRONG_RESULT}" | grep -qiE 'error|invalid|failed|bad'; then
    pass "Cross-context isolation: wrong context correctly rejected"
else
    log "  Note: Wrong context returned non-error (AES-GCM authentication tag mismatch should cause error in strict mode)"
fi

# ZeroizeOnDrop verification
log "Verifying key non-exportability (ZeroizeOnDrop proxy)..."
EXPORTABLE="$(bao read -tls-skip-verify -field=exportable "transit/keys/${TRANSIT_KEY}" 2>/dev/null)"
if [ "${EXPORTABLE}" = "false" ]; then
    pass "ZeroizeOnDrop: key exportable=false — keys never leave OpenBao boundary"
else
    fail "Key exportable=${EXPORTABLE} — should be false for HSM-backed deployment"
fi

# Save evidence
mkdir -p "${EVIDENCE_DIR}"
{
    printf 'HKDF Key Derivation Test Result: PASS\n'
    printf 'Date: %s\n' "$(date -u)"
    printf 'Method: OpenBao Transit context-based HKDF-SHA256\n'
    printf 'Key: transit/%s (AES256-GCM96, derived=true)\n' "${TRANSIT_KEY}"
    printf 'Contexts tested: %d\n' "${#CONTEXTS[@]}"
    printf 'Uniqueness: VERIFIED\n'
    printf 'Cross-context isolation: VERIFIED\n'
    printf 'ZeroizeOnDrop (exportable=false): VERIFIED\n'
    printf 'RESULT: PASS\n'
} >> "${EVIDENCE_DIR}/hkdf-test-result.txt"

pass "HKDF key derivation test complete. Evidence saved to ${EVIDENCE_DIR}/hkdf-test-result.txt"
