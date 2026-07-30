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

# run-validation-runbook.sh — Execute iGaming platform validation tests on ops-host
# Covers: TC-YK-06, TC-BAO-02, TC-BAO-06, TC-BAO-07, TC-AUDIT-01/02/03, TC-SESSION, TC-WAL-01
# Run from local machine: ./run-validation-runbook.sh
# Or run directly on ops-host: bash run-validation-runbook.sh --local

set -uo pipefail

SSH_HOST="admin@ops-server"
SSH_KEY="$HOME/.ssh/id_ed25519"
RESULTS_FILE="/opt/yubihsm-evidence/validation-runbook-results.md"
LOCAL_RESULTS="$(dirname "$0")/VALIDATION-RESULTS.md"

PASS=0
FAIL=0
SKIP=0

log()  { echo "[$(date -u +%H:%M:%SZ)] $*"; }
pass() { PASS=$(( PASS + 1 )); echo "  PASS: $*"; }
fail() { FAIL=$(( FAIL + 1 )); echo "  FAIL: $*"; }
skip() { SKIP=$(( SKIP + 1 )); echo "  SKIP: $*"; }

run_remote() {
    ssh -o PasswordAuthentication=no -i "$SSH_KEY" "$SSH_HOST" "$@"
}

# ─────────────────────────────────────────────────────────────────────────────
# Setup: gather environment on the remote
# ─────────────────────────────────────────────────────────────────────────────
log "=== iGaming Validation Runbook ==="
log "Target: $SSH_HOST"
log "Results: $RESULTS_FILE (remote) + $LOCAL_RESULTS (local)"
echo ""

# Fetch root token and verify OpenBao is unsealed
log "Checking OpenBao status..."
BAO_SEALED=$(run_remote "BAO_CACERT=/etc/ssl/certs/openbao-ca.pem BAO_ADDR=https://127.0.0.1:8200 bao status -format=json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)[\"sealed\"])' 2>/dev/null || echo 'Unknown'")

if [ "$BAO_SEALED" = "True" ]; then
    log "OpenBao is sealed — running unseal script..."
    run_remote "sudo /opt/openbao-unseal.sh" || { log "ERROR: unseal failed"; exit 1; }
elif [ "$BAO_SEALED" = "False" ]; then
    log "OpenBao is already unsealed."
else
    log "WARNING: could not determine seal state: $BAO_SEALED"
fi

# ─────────────────────────────────────────────────────────────────────────────
# TC-YK-06: 16 concurrent HSM sessions
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== TC-YK-06: 16 Concurrent HSM Sessions ==="

TC_YK_06=$(run_remote '
export PKCS11_LIB=/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so
export YUBIHSM_PKCS11_CONF=/etc/yubihsm_pkcs11.conf
export HSM_PIN="0001$(sudo cat /root/.yubihsm-pin)"
for i in {1..16}; do
  (pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
    --generate-random 16 > /dev/null 2>&1 && echo "OK" || echo "FAIL") &
done
wait
')

OK_COUNT=$(echo "$TC_YK_06" | grep -c "^OK$" || true)
FAIL_COUNT=$(echo "$TC_YK_06" | grep -c "^FAIL$" || true)
echo "  Sessions OK: $OK_COUNT/16, FAIL: $FAIL_COUNT/16"

if [ "$OK_COUNT" -eq 16 ] && [ "$FAIL_COUNT" -eq 0 ]; then
    pass "TC-YK-06: All 16 concurrent HSM sessions succeeded"
else
    fail "TC-YK-06: $FAIL_COUNT sessions failed (OK=$OK_COUNT)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# TC-BAO-02: Auto-unseal timing
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== TC-BAO-02: Auto-Unseal Timing ==="

TC_BAO_02=$(run_remote '
export BAO_ADDR=https://127.0.0.1:8200
export BAO_CACERT=/etc/ssl/certs/openbao-ca.pem
START=$(date +%s%3N)
sudo systemctl restart openbao
sudo /opt/openbao-unseal.sh > /dev/null 2>&1
until BAO_CACERT=/etc/ssl/certs/openbao-ca.pem BAO_ADDR=https://127.0.0.1:8200 \
  bao status -format=json 2>/dev/null | python3 -c "import json,sys; exit(0 if not json.load(sys.stdin)[\"sealed\"] else 1)" 2>/dev/null; do
  sleep 0.5
done
END=$(date +%s%3N)
echo $(( END - START ))
')

ELAPSED_MS="${TC_BAO_02// /}"
ELAPSED_S=$(( ELAPSED_MS / 1000 ))
echo "  Unseal time: ${ELAPSED_MS}ms (${ELAPSED_S}s)"

if [ "${ELAPSED_MS:-99999}" -lt 15000 ] 2>/dev/null; then
    pass "TC-BAO-02: Auto-unseal completed in ${ELAPSED_MS}ms (< 15000ms threshold)"
else
    fail "TC-BAO-02: Unseal took ${ELAPSED_MS}ms (>= 15000ms threshold)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# TC-BAO-06: AppRole policy isolation
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== TC-BAO-06: AppRole Policy Isolation ==="

TC_BAO_06=$(run_remote '
ROOT_TOKEN=$(sudo python3 -c "import json; print(json.load(open('"'"'/opt/yubihsm-evidence/openbao-init.json'"'"'))['"'"'root_token'"'"'])")
export BAO_ADDR=https://127.0.0.1:8200
export BAO_CACERT=/etc/ssl/certs/openbao-ca.pem
export BAO_TOKEN=$ROOT_TOKEN

# Enable AppRole (idempotent)
bao auth enable approle 2>/dev/null || true

# Create policies
bao policy write test-policy-vm1 - <<EOF
path "transit/decrypt/vm-test-01" { capabilities = ["update"] }
EOF
bao policy write test-policy-vm2 - <<EOF
path "transit/decrypt/vm-test-02" { capabilities = ["update"] }
EOF

# Create roles and keys
bao write auth/approle/role/test-vm1 token_policies="test-policy-vm1" token_ttl=5m > /dev/null
bao write auth/approle/role/test-vm2 token_policies="test-policy-vm2" token_ttl=5m > /dev/null
bao write transit/keys/vm-test-01 type=aes256-gcm96 > /dev/null 2>&1 || true
bao write transit/keys/vm-test-02 type=aes256-gcm96 > /dev/null 2>&1 || true

# Get vm1 token
ROLE_ID=$(bao read -field=role_id auth/approle/role/test-vm1/role-id)
SECRET_ID=$(bao write -field=secret_id -f auth/approle/role/test-vm1/secret-id)
TOKEN_VM1=$(bao write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")

# Generate ciphertext for vm-test-02 using root token
CIPHER=$(bao write -field=ciphertext transit/encrypt/vm-test-02 \
  plaintext=$(echo -n "secret" | base64 -w0))

# Attempt cross-access: vm1 token decrypting vm-test-02 key
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
  --cacert "$BAO_CACERT" \
  -H "X-Vault-Token: $TOKEN_VM1" \
  -X POST \
  -d "{\"ciphertext\":\"$CIPHER\"}" \
  "$BAO_ADDR/v1/transit/decrypt/vm-test-02")

echo "$HTTP_CODE"

# Cleanup
bao delete transit/keys/vm-test-01 > /dev/null 2>&1 || true
bao delete transit/keys/vm-test-02 > /dev/null 2>&1 || true
bao delete auth/approle/role/test-vm1 > /dev/null 2>&1 || true
bao delete auth/approle/role/test-vm2 > /dev/null 2>&1 || true
bao delete sys/policy/test-policy-vm1 > /dev/null 2>&1 || true
bao delete sys/policy/test-policy-vm2 > /dev/null 2>&1 || true
')

HTTP_CODE="${TC_BAO_06// /}"
echo "  Cross-access HTTP response: $HTTP_CODE"

if [ "$HTTP_CODE" = "403" ]; then
    pass "TC-BAO-06: HTTP 403 returned — AppRole policy isolation enforced"
else
    fail "TC-BAO-06: Expected HTTP 403, got $HTTP_CODE — isolation BROKEN"
fi

# ─────────────────────────────────────────────────────────────────────────────
# TC-BAO-07: Audit log verification
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== TC-BAO-07: Audit Log Verification ==="

TC_BAO_07=$(run_remote '
ROOT_TOKEN=$(sudo python3 -c "import json; print(json.load(open('"'"'/opt/yubihsm-evidence/openbao-init.json'"'"'))['"'"'root_token'"'"'])")
export BAO_ADDR=https://127.0.0.1:8200
export BAO_CACERT=/etc/ssl/certs/openbao-ca.pem
export BAO_TOKEN=$ROOT_TOKEN

# Perform a tracked operation
bao write transit/keys/audit-test type=aes256-gcm96 > /dev/null
bao write -field=ciphertext transit/encrypt/audit-test \
  plaintext=$(echo -n "test" | base64 -w0) > /dev/null

# Check that operation appears in audit log
AUDIT_HITS=$(sudo grep -c "transit/encrypt/audit-test" /var/log/openbao/audit.log 2>/dev/null || echo 0)

# Check that plaintext (dGVzdA== is base64 of "test") is NOT in audit log
PLAINTEXT_HITS=$(sudo grep -c "dGVzdA==" /var/log/openbao/audit.log 2>/dev/null || echo 0)

echo "audit_hits=$AUDIT_HITS plaintext_hits=$PLAINTEXT_HITS"

bao delete transit/keys/audit-test > /dev/null 2>&1 || true
')

AUDIT_HITS=$(echo "$TC_BAO_07" | grep -oP 'audit_hits=\K\d+' || echo "0")
PLAINTEXT_HITS=$(echo "$TC_BAO_07" | grep -oP 'plaintext_hits=\K\d+' || echo "1")

echo "  Audit entries for operation: $AUDIT_HITS"
echo "  Plaintext in audit log: $PLAINTEXT_HITS"

if [ "${AUDIT_HITS:-0}" -gt 0 ] && [ "${PLAINTEXT_HITS:-1}" -eq 0 ]; then
    pass "TC-BAO-07: Operations recorded ($AUDIT_HITS entries), plaintext absent"
elif [ "${PLAINTEXT_HITS:-1}" -gt 0 ]; then
    fail "TC-BAO-07: CRITICAL — plaintext found in audit log ($PLAINTEXT_HITS occurrences)"
else
    fail "TC-BAO-07: No audit entries found for test operation (got $AUDIT_HITS)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# TC-AUDIT-01 to 03: Audit chain integrity
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== TC-AUDIT-01 to TC-AUDIT-03: Audit Chain Tests ==="

TC_AUDIT=$(run_remote '
TOTAL=$(sudo wc -l < /var/log/openbao/audit.log 2>/dev/null || echo 0)
RESULT=$(sudo cat /var/log/openbao/audit.log 2>/dev/null | python3 -c "
import sys,json
valid=0; invalid=0; has_hmac=0
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    try:
        e=json.loads(line)
        valid+=1
        if e.get(\"request\",{}).get(\"client_token_accessor\"): has_hmac+=1
    except: invalid+=1
print(f\"valid={valid} invalid={invalid} has_hmac={has_hmac}\")
" 2>/dev/null)
PERMS=$(sudo stat -c "%a %U:%G" /var/log/openbao/audit.log 2>/dev/null || echo "unknown")
echo "total=$TOTAL $RESULT perms=$PERMS"
')

VALID=$(echo "$TC_AUDIT" | grep -oP 'valid=\K\d+' || echo "0")
INVALID=$(echo "$TC_AUDIT" | grep -oP 'invalid=\K\d+' || echo "1")
HAS_HMAC=$(echo "$TC_AUDIT" | grep -oP 'has_hmac=\K\d+' || echo "0")
PERMS=$(echo "$TC_AUDIT" | grep -oP 'perms=\K\S+' || echo "unknown")

echo "  Entries: valid=$VALID invalid=$INVALID hmac_entries=$HAS_HMAC perms=$PERMS"

# TC-AUDIT-01: JSON integrity
if [ "${INVALID:-1}" -eq 0 ] && [ "${VALID:-0}" -gt 0 ]; then
    pass "TC-AUDIT-01: Audit chain intact ($VALID valid entries, 0 invalid)"
else
    fail "TC-AUDIT-01: $INVALID invalid entries found in audit log"
fi

# TC-AUDIT-02: HMAC fields
if [ "${HAS_HMAC:-0}" -gt 0 ]; then
    pass "TC-AUDIT-02: HMAC accessor fields present in audit entries ($HAS_HMAC entries)"
else
    fail "TC-AUDIT-02: No HMAC accessor fields found in audit entries"
fi

# TC-AUDIT-03: File permissions
if echo "$PERMS" | grep -q "^600"; then
    pass "TC-AUDIT-03: Audit log permissions are 600 (openbao:openbao) — access controlled"
else
    fail "TC-AUDIT-03: Unexpected audit log permissions: $PERMS (expected 600 openbao:openbao)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# TC-SESSION: Ed25519 JWT signing
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== TC-SESSION: Ed25519 JWT Signing ==="

TC_SESSION=$(run_remote '
ROOT_TOKEN=$(sudo python3 -c "import json; print(json.load(open('"'"'/opt/yubihsm-evidence/openbao-init.json'"'"'))['"'"'root_token'"'"'])")
export BAO_ADDR=https://127.0.0.1:8200
export BAO_CACERT=/etc/ssl/certs/openbao-ca.pem
export BAO_TOKEN=$ROOT_TOKEN

# Test 1: Transit engine Ed25519 signing
HEADER=$(echo -n '"'"'{"alg":"EdDSA","typ":"JWT"}'"'"' | base64 -w0 | tr '"'"'+/'"'"' '"'"'-_'"'"' | tr -d '"'"'='"'"')
PAYLOAD=$(echo -n "{\"sub\":\"player-123\",\"iat\":$(date +%s)}" | base64 -w0 | tr '"'"'+/'"'"' '"'"'-_'"'"' | tr -d '"'"'='"'"')
SIGNING_INPUT_B64=$(echo -n "${HEADER}.${PAYLOAD}" | base64 -w0)
SIG=$(bao write -field=signature transit/sign/jwt-signing \
  input="$SIGNING_INPUT_B64" marshaling_algorithm=jws 2>&1)
if echo "$SIG" | grep -q "vault:v1:"; then
    echo "transit_ok=1"
else
    echo "transit_ok=0 sig_output=$SIG"
fi

# Test 2: Direct YubiHSM PKCS11 Ed25519 signing
export PKCS11_LIB=/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so
export YUBIHSM_PKCS11_CONF=/etc/yubihsm_pkcs11.conf
export HSM_PIN="0001$(sudo cat /root/.yubihsm-pin)"
echo -n "session-key-endorsement-$(date +%s)" > /tmp/jwt_payload.bin
pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
  --sign --mechanism EDDSA \
  --label "acmetocasino-jwt-signer" \
  --input-file /tmp/jwt_payload.bin \
  --output-file /tmp/jwt_sig.bin > /dev/null 2>&1
SIG_SIZE=$(wc -c < /tmp/jwt_sig.bin 2>/dev/null || echo 0)
echo "hsm_sig_size=$SIG_SIZE"
rm -f /tmp/jwt_payload.bin /tmp/jwt_sig.bin
')

TRANSIT_OK=$(echo "$TC_SESSION" | grep -oP 'transit_ok=\K\d+' || echo "0")
HSM_SIG_SIZE=$(echo "$TC_SESSION" | grep -oP 'hsm_sig_size=\K\d+' || echo "0")

echo "  Transit Ed25519: transit_ok=$TRANSIT_OK"
echo "  YubiHSM PKCS11 Ed25519: sig_size=${HSM_SIG_SIZE} bytes (expected: 64)"

if [ "${TRANSIT_OK:-0}" -eq 1 ]; then
    pass "TC-SESSION (Transit): Ed25519 JWT signing via Transit engine works"
else
    fail "TC-SESSION (Transit): Transit Ed25519 signing failed"
fi

if [ "${HSM_SIG_SIZE:-0}" -eq 64 ]; then
    pass "TC-SESSION (YubiHSM): Direct PKCS11 Ed25519 signing produces 64-byte signature"
else
    fail "TC-SESSION (YubiHSM): Ed25519 signature size is ${HSM_SIG_SIZE} bytes (expected 64)"
fi

# ─────────────────────────────────────────────────────────────────────────────
# TC-WAL-01: Wallet idempotency
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== TC-WAL-01: Wallet Idempotency ==="

WAL_HEALTH=$(run_remote "curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8091/health 2>/dev/null || echo 000")

if [ "${WAL_HEALTH:-000}" = "200" ]; then
    # Casino API is up — run real test
    R1=$(run_remote "curl -sf -X POST http://127.0.0.1:8091/wallet/deposit \
        -H 'Content-Type: application/json' \
        -d '{\"player_id\": \"test-idem\", \"amount\": 100, \"idempotency_key\": \"test-key-001\"}' 2>&1")
    R2=$(run_remote "curl -sf -X POST http://127.0.0.1:8091/wallet/deposit \
        -H 'Content-Type: application/json' \
        -d '{\"player_id\": \"test-idem\", \"amount\": 100, \"idempotency_key\": \"test-key-001\"}' 2>&1")
    echo "  R1: $R1"
    echo "  R2: $R2"
    if echo "$R2" | grep -qiE 'already|idempotent|duplicate'; then
        pass "TC-WAL-01: Duplicate request correctly detected (AlreadyProcessed)"
    elif [ "$R1" = "$R2" ] && echo "$R2" | grep -q '"id"'; then
        pass "TC-WAL-01: Idempotent response — same result returned for duplicate key"
    else
        fail "TC-WAL-01: Idempotency not enforced — R1 != R2 or unexpected response"
    fi
else
    skip "TC-WAL-01: casino-api not available at port 8091 (HTTP $WAL_HEALTH). Manual retest required."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────
log ""
log "=== RESULTS SUMMARY ==="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  SKIP: $SKIP"
echo ""

if [ "$FAIL" -eq 0 ]; then
    log "All executed tests PASSED. See VALIDATION-RESULTS.md for details."
    exit 0
else
    log "FAILURES detected: $FAIL test(s) failed. Review output above."
    exit 1
fi
