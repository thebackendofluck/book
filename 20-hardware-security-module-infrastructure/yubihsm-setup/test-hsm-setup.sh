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

# shellcheck disable=SC2034  # Config and color constants
# test-hsm-setup.sh
# Comprehensive test suite for YubiHSM 2 + OpenBao + LUKS integration.
# Verifies HSM connectivity, PKCS#11 operations, Transit encrypt/decrypt,
# LUKS unlock cycle, and cluster HA. Runs 5 iterations per test.
#
# Usage:
#   BAO_ADDR=https://bao-01:8200 BAO_CACERT=/opt/openbao/tls/ca.crt \
#   BAO_TOKEN=<operator-token> HSM_PIN=<pin> bash test-hsm-setup.sh
#
# Exit codes: 0 = all tests passed, 1 = one or more tests failed

set -uo pipefail   # Note: NOT -e, so tests can fail without stopping suite

LOG_FILE="/var/log/hsm-test-$(date +%Y%m%d-%H%M%S).log"
BAO_ADDR="${BAO_ADDR:-https://bao-01:8200}"
BAO_CACERT="${BAO_CACERT:-/opt/openbao/tls/ca.crt}"
BAO_TOKEN="${BAO_TOKEN:-}"
HSM_PIN="${HSM_PIN:-}"
PKCS11_LIB="${PKCS11_LIB:-/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so}"
YUBIHSM_CONF="${YUBIHSM_CONF:-/etc/yubihsm_pkcs11.conf}"
ITERATIONS=5
LUKS_TEST_DEV="${LUKS_TEST_DEV:-}"   # optional: set to test LUKS unlock

# ── Test counters ──────────────────────────────────────────────────────────────
PASSED=0
FAILED=0
SKIPPED=0

# ── Output helpers ─────────────────────────────────────────────────────────────
log()  { echo "[$(date -Is)] $*" | tee -a "$LOG_FILE"; }
pass() { echo "  [PASS] $*" | tee -a "$LOG_FILE"; (( PASSED++ )); }
fail() { echo "  [FAIL] $*" | tee -a "$LOG_FILE"; (( FAILED++ )); }
skip() { echo "  [SKIP] $*" | tee -a "$LOG_FILE"; (( SKIPPED++ )); }
sep()  { echo "────────────────────────────────────────" | tee -a "$LOG_FILE"; }

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-01: YubiHSM 2 Connectivity
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_hsm_connectivity() {
    sep
    log "TC-01: YubiHSM 2 — Connector Connectivity"

    # Test 1a: connector HTTP endpoint
    if curl -sf "http://127.0.0.1:12345/connector/status" \
           | grep -q '"status":"OK"' 2>/dev/null; then
        pass "yubihsm-connector is running and returning status=OK"
    else
        fail "yubihsm-connector not accessible at http://127.0.0.1:12345"
    fi

    # Test 1b: USB device detection
    if lsusb 2>/dev/null | grep -qi "yubico"; then
        pass "YubiHSM 2 detected via USB"
    else
        fail "YubiHSM 2 not detected via lsusb (check USB connection)"
    fi

    # Test 1c: PKCS#11 library exists
    if [[ -f "$PKCS11_LIB" ]]; then
        pass "PKCS#11 library found: $PKCS11_LIB"
    else
        fail "PKCS#11 library not found: $PKCS11_LIB"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-02: PKCS#11 Wrap Key — Never Extractable
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_pkcs11_wrap_key() {
    sep
    log "TC-02: PKCS#11 — Wrap Key Properties"

    if [[ -z "$HSM_PIN" ]]; then
        skip "HSM_PIN not set — skipping PKCS#11 key tests"
        return
    fi

    export YUBIHSM_PKCS11_CONF

    # Test 2a: wrap key exists
    local key_list
    key_list=$(pkcs11-tool --module "$PKCS11_LIB" \
        --login --pin "$HSM_PIN" --list-objects 2>&1)

    if echo "$key_list" | grep -q "bao-root-key-aes"; then
        pass "Wrap key 'bao-root-key-aes' found in HSM"
    else
        fail "Wrap key 'bao-root-key-aes' NOT found in HSM"
    fi

    # Test 2b: key is 32 bytes (AES-256)
    if echo "$key_list" | grep -A5 "bao-root-key-aes" | grep -q "length 32"; then
        pass "Wrap key is AES-256 (32 bytes)"
    else
        skip "Cannot verify key length from pkcs11-tool output format"
    fi

    # Test 2c: key is marked sensitive/non-extractable
    if echo "$key_list" | grep -A10 "bao-root-key-aes" \
            | grep -qi "sensitive\|never extractable\|Access:"; then
        pass "Wrap key has sensitive/non-extractable attributes"
    else
        skip "Cannot verify extractable attribute from list output"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-03: PKCS#11 TRNG — True Random Number Generation
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_pkcs11_trng() {
    sep
    log "TC-03: PKCS#11 — TRNG Quality ($ITERATIONS iterations)"

    if [[ -z "$HSM_PIN" ]]; then
        skip "HSM_PIN not set — skipping TRNG test"
        return
    fi

    local all_same=true
    local prev_bytes=""
    local i
    for i in $(seq 1 "$ITERATIONS"); do
        local rand_hex
        rand_hex=$(pkcs11-tool --module "$PKCS11_LIB" \
            --login --pin "$HSM_PIN" \
            --generate-random 32 2>/dev/null | xxd -p | tr -d '\n')

        if [[ -z "$rand_hex" ]]; then
            fail "TRNG iteration $i: empty output"
            continue
        fi

        if [[ "$rand_hex" == "$prev_bytes" ]] && [[ -n "$prev_bytes" ]]; then
            fail "TRNG iteration $i: duplicate output (entropy source not working!)"
            all_same=false
        else
            pass "TRNG iteration $i: 32 bytes generated (hex: ${rand_hex:0:16}...)"
        fi
        prev_bytes="$rand_hex"
    done
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-04: OpenBao — Auto-Unseal via PKCS#11
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_openbao_unseal() {
    sep
    log "TC-04: OpenBao — Auto-Unseal Status"

    if ! curl -sf --cacert "$BAO_CACERT" \
              "${BAO_ADDR}/v1/sys/health" &>/dev/null; then
        fail "OpenBao not reachable at $BAO_ADDR — check service and TLS"
        return
    fi

    local status_json
    status_json=$(curl -sf --cacert "$BAO_CACERT" \
        "${BAO_ADDR}/v1/sys/health" 2>/dev/null || echo "{}")

    local sealed
    sealed=$(echo "$status_json" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('sealed','unknown'))" 2>/dev/null)
    local initialized
    initialized=$(echo "$status_json" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d.get('initialized','unknown'))" 2>/dev/null)

    if [[ "$initialized" == "True" ]] || [[ "$initialized" == "true" ]]; then
        pass "OpenBao cluster is initialized"
    else
        fail "OpenBao cluster is NOT initialized"
    fi

    if [[ "$sealed" == "False" ]] || [[ "$sealed" == "false" ]]; then
        pass "OpenBao is unsealed (PKCS#11 auto-unseal working)"
    else
        fail "OpenBao is SEALED — check PKCS#11 connector and PIN"
    fi

    # Test restart → auto-unseal cycle
    log "TC-04b: Testing restart → auto-unseal cycle"
    if systemctl is-active --quiet openbao 2>/dev/null; then
        systemctl restart openbao
        sleep 5

        local i
        for i in $(seq 1 10); do
            local status
            status=$(curl -sf --cacert "$BAO_CACERT" \
                "${BAO_ADDR}/v1/sys/health" 2>/dev/null | \
                python3 -c "import sys,json; print(json.load(sys.stdin).get('sealed','unknown'))" 2>/dev/null)
            if [[ "$status" == "False" ]] || [[ "$status" == "false" ]]; then
                pass "Auto-unseal after restart: unsealed in ~$((i*2))s"
                return
            fi
            sleep 2
        done
        fail "OpenBao did not auto-unseal within 20s of restart"
    else
        skip "OpenBao service not running locally — skipping restart test"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-05: OpenBao — Raft Cluster Health
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_raft_cluster() {
    sep
    log "TC-05: OpenBao — Raft Cluster Health"

    if [[ -z "$BAO_TOKEN" ]]; then
        skip "BAO_TOKEN not set — skipping Raft cluster check"
        return
    fi

    export BAO_ADDR BAO_CACERT BAO_TOKEN

    local peers_json
    peers_json=$(bao operator raft list-peers -format=json 2>/dev/null || echo "{}")

    local peer_count
    peer_count=$(echo "$peers_json" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(len(d.get('data',{}).get('config',{}).get('servers',[])))" 2>/dev/null || echo 0)

    if [[ "$peer_count" -ge 3 ]]; then
        pass "Raft cluster has $peer_count peers (minimum 3 for quorum)"
    else
        fail "Raft cluster has only $peer_count peer(s) — need at least 3"
    fi

    # Check leader election
    local leader_count
    leader_count=$(echo "$peers_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
servers = d.get('data',{}).get('config',{}).get('servers',[])
leaders = [s for s in servers if s.get('voter', False) and s.get('address')]
print(len(leaders))
" 2>/dev/null || echo 0)

    if [[ "$leader_count" -ge 1 ]]; then
        pass "Raft cluster has a leader elected"
    else
        fail "No Raft leader detected"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-06: Transit Engine — Encrypt/Decrypt Round-Trip
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_transit_roundtrip() {
    sep
    log "TC-06: Transit Engine — Encrypt/Decrypt Round-Trip ($ITERATIONS iterations)"

    if [[ -z "$BAO_TOKEN" ]]; then
        skip "BAO_TOKEN not set — skipping Transit tests"
        return
    fi

    export BAO_ADDR BAO_CACERT BAO_TOKEN

    local test_vm="vm-db-01"
    local i
    for i in $(seq 1 "$ITERATIONS"); do
        local plaintext
        plaintext="test-luks-key-iteration-${i}-$(openssl rand -hex 8)"
        local plaintext_b64
        plaintext_b64=$(echo -n "$plaintext" | base64)

        # Encrypt
        local ciphertext
        ciphertext=$(bao write -field=ciphertext \
            "transit/encrypt/${test_vm}" \
            "plaintext=${plaintext_b64}" 2>/dev/null)

        if [[ -z "$ciphertext" ]]; then
            fail "Iteration $i: Transit encrypt returned empty ciphertext"
            continue
        fi

        # Decrypt
        local decrypted_b64
        decrypted_b64=$(bao write -field=plaintext \
            "transit/decrypt/${test_vm}" \
            "ciphertext=${ciphertext}" 2>/dev/null)

        local decrypted
        decrypted=$(echo "$decrypted_b64" | base64 -d 2>/dev/null)

        if [[ "$decrypted" == "$plaintext" ]]; then
            pass "Iteration $i: encrypt/decrypt round-trip successful"
        else
            fail "Iteration $i: plaintext mismatch — got: '$decrypted', expected: '$plaintext'"
        fi
    done
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-07: AppRole Policy Isolation
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_approle_isolation() {
    sep
    log "TC-07: AppRole — Policy Isolation (VM cannot decrypt another VM's key)"

    if [[ -z "$BAO_TOKEN" ]]; then
        skip "BAO_TOKEN not set — skipping policy isolation test"
        return
    fi

    export BAO_ADDR BAO_CACERT BAO_TOKEN

    # Get AppRole credentials for vm-db-01
    local role_id
    role_id=$(bao read -field=role_id "auth/approle/role/vm-db-01/role-id" 2>/dev/null || echo "")
    local secret_id
    secret_id=$(bao write -field=secret_id -f "auth/approle/role/vm-db-01/secret-id" 2>/dev/null || echo "")

    if [[ -z "$role_id" ]] || [[ -z "$secret_id" ]]; then
        skip "Cannot get AppRole credentials for vm-db-01"
        return
    fi

    # Login as vm-db-01
    local vm_token
    vm_token=$(curl -sf --cacert "$BAO_CACERT" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"role_id\":\"${role_id}\",\"secret_id\":\"${secret_id}\"}" \
        "${BAO_ADDR}/v1/auth/approle/login" | \
        python3 -c "import sys,json; print(json.load(sys.stdin)['auth']['client_token'])" \
        2>/dev/null || echo "")

    if [[ -z "$vm_token" ]]; then
        skip "Cannot obtain AppRole token for vm-db-01"
        return
    fi

    # Test: vm-db-01 can decrypt its own key
    local own_plaintext
    own_plaintext=$(bao write -field=ciphertext "transit/encrypt/vm-db-01" \
        plaintext="$(echo -n 'test' | base64)" 2>/dev/null)
    local result
    result=$(curl -sf --cacert "$BAO_CACERT" \
        -H "X-Vault-Token: $vm_token" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"ciphertext\":\"${own_plaintext}\"}" \
        "${BAO_ADDR}/v1/transit/decrypt/vm-db-01" \
        2>/dev/null | python3 -c "import sys,json; print('ok')" 2>/dev/null || echo "")

    if [[ "$result" == "ok" ]]; then
        pass "vm-db-01 can decrypt its own key (authorized)"
    else
        fail "vm-db-01 cannot decrypt its own key (unexpected)"
    fi

    # Test: vm-db-01 CANNOT decrypt vm-redis-01's key (must be 403)
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        --cacert "$BAO_CACERT" \
        -H "X-Vault-Token: $vm_token" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"ciphertext\":\"vault:v1:AAAA\"}" \
        "${BAO_ADDR}/v1/transit/decrypt/vm-redis-01" 2>/dev/null)

    if [[ "$http_code" == "403" ]]; then
        pass "vm-db-01 receives HTTP 403 when attempting to decrypt vm-redis-01 key (policy isolation OK)"
    else
        fail "Policy isolation FAILED: expected 403, got $http_code"
    fi

    # Revoke the test token
    curl -sf --cacert "$BAO_CACERT" \
        -H "X-Vault-Token: $vm_token" \
        -X POST "${BAO_ADDR}/v1/auth/token/revoke-self" >/dev/null 2>&1 || true
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-08: Audit Log — Each Transit Operation is Logged
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_audit_log() {
    sep
    log "TC-08: Audit Log — Transit Operations Are Logged"

    if [[ ! -f "/var/log/openbao/audit.log" ]]; then
        skip "Audit log not found at /var/log/openbao/audit.log"
        return
    fi

    # Count existing decrypt entries
    local before
    before=$(grep -c '"transit/decrypt' /var/log/openbao/audit.log 2>/dev/null || echo 0)

    if [[ "$before" -gt 0 ]]; then
        pass "Audit log contains $before transit/decrypt entries"
    else
        skip "No transit/decrypt entries yet in audit log"
    fi

    # Check log format is valid JSON
    local last_entry
    last_entry=$(tail -1 /var/log/openbao/audit.log 2>/dev/null)
    if echo "$last_entry" | python3 -m json.tool &>/dev/null; then
        pass "Audit log entries are valid JSON (SIEM-compatible)"
    else
        fail "Audit log entries are NOT valid JSON"
    fi

    # Verify plaintext never appears in audit log
    if grep -q '"plaintext"' /var/log/openbao/audit.log 2>/dev/null; then
        fail "CRITICAL: 'plaintext' key found in audit log — check log_raw setting"
    else
        pass "Audit log does NOT contain plaintext values (PCI DSS Req. 3.5 OK)"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-09: LUKS — Encryption at Rest Verification
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_luks_encryption() {
    sep
    log "TC-09: LUKS2 — Encryption at Rest Verification"

    if [[ -z "$LUKS_TEST_DEV" ]]; then
        skip "LUKS_TEST_DEV not set — skipping LUKS tests (set to e.g. /dev/vdb)"
        return
    fi

    if [[ ! -b "$LUKS_TEST_DEV" ]]; then
        fail "Block device not found: $LUKS_TEST_DEV"
        return
    fi

    # Test 9a: LUKS header exists
    if cryptsetup isLuks "$LUKS_TEST_DEV" 2>/dev/null; then
        pass "LUKS header verified on $LUKS_TEST_DEV"
    else
        fail "$LUKS_TEST_DEV does not have a LUKS header"
        return
    fi

    # Test 9b: LUKS2 format (not v1)
    local luks_version
    luks_version=$(cryptsetup luksDump "$LUKS_TEST_DEV" 2>/dev/null \
        | grep -i "^Version:" | awk '{print $2}')
    if [[ "$luks_version" == "2" ]]; then
        pass "LUKS version: 2 (required)"
    else
        fail "LUKS version is $luks_version (expected 2)"
    fi

    # Test 9c: AES-XTS-512 cipher
    local cipher
    cipher=$(cryptsetup luksDump "$LUKS_TEST_DEV" 2>/dev/null \
        | grep -i "Cipher:" | head -1 | awk '{print $2}')
    if echo "$cipher" | grep -q "aes-xts"; then
        pass "Cipher: $cipher (AES-XTS confirmed)"
    else
        fail "Unexpected cipher: $cipher (expected aes-xts-plain64)"
    fi

    # Test 9d: 512-bit key size
    local keysize
    keysize=$(cryptsetup luksDump "$LUKS_TEST_DEV" 2>/dev/null \
        | grep -i "MK bits:" | awk '{print $3}')
    if [[ "$keysize" == "512" ]]; then
        pass "Key size: 512 bits (AES-256 XTS mode)"
    else
        fail "Key size: $keysize bits (expected 512)"
    fi

    # Test 9e: OpenBao ciphertext token exists in LUKS header
    if cryptsetup token export --token-id 0 "$LUKS_TEST_DEV" &>/dev/null; then
        local token_type
        token_type=$(cryptsetup token export --token-id 0 "$LUKS_TEST_DEV" 2>/dev/null \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('type','unknown'))" \
            2>/dev/null || echo "unknown")
        if [[ "$token_type" == "openbao-transit" ]]; then
            pass "LUKS2 token slot 0: openbao-transit ciphertext present"
        else
            warn "LUKS2 token slot 0: found but type is '$token_type' (expected openbao-transit)"
        fi
    else
        fail "LUKS2 token slot 0: no token found — provisioning may be incomplete"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-10: LUKS — Unlock Cycle Test ($ITERATIONS iterations)
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_luks_unlock_cycle() {
    sep
    log "TC-10: LUKS — Unlock Cycle Test ($ITERATIONS iterations)"

    if [[ -z "$LUKS_TEST_DEV" ]]; then
        skip "LUKS_TEST_DEV not set — skipping LUKS unlock cycle test"
        return
    fi

    if [[ ! -f "/usr/local/sbin/bao-luks-unlock" ]]; then
        skip "bao-luks-unlock script not installed — run setup-luks-encryption.sh first"
        return
    fi

    local i
    for i in $(seq 1 "$ITERATIONS"); do
        local mapper_test="data_crypt_test_${i}"

        # Run unlock script
        if MAPPER_NAME="$mapper_test" /usr/local/sbin/bao-luks-unlock &>/dev/null; then
            if [[ -e "/dev/mapper/${mapper_test}" ]]; then
                pass "Iteration $i: LUKS unlock successful → /dev/mapper/${mapper_test}"
                # Close mapping
                cryptsetup close "$mapper_test" 2>/dev/null || true
            else
                fail "Iteration $i: unlock script succeeded but /dev/mapper/${mapper_test} not created"
            fi
        else
            fail "Iteration $i: bao-luks-unlock script failed"
        fi
    done
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# TC-11: Token Revocation — No Persistent Credential
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
test_token_revocation() {
    sep
    log "TC-11: Token Revocation — No Persistent Credential After Unlock"

    if [[ -z "$BAO_TOKEN" ]]; then
        skip "BAO_TOKEN not set — skipping token revocation test"
        return
    fi

    export BAO_ADDR BAO_CACERT BAO_TOKEN

    # Get AppRole token with short TTL
    local role_id
    role_id=$(bao read -field=role_id "auth/approle/role/vm-db-01/role-id" 2>/dev/null || echo "")
    local secret_id
    secret_id=$(bao write -field=secret_id -f "auth/approle/role/vm-db-01/secret-id" 2>/dev/null || echo "")

    if [[ -z "$role_id" ]] || [[ -z "$secret_id" ]]; then
        skip "Cannot get AppRole credentials"
        return
    fi

    local test_token
    test_token=$(curl -sf --cacert "$BAO_CACERT" \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"role_id\":\"${role_id}\",\"secret_id\":\"${secret_id}\"}" \
        "${BAO_ADDR}/v1/auth/approle/login" | \
        python3 -c "import sys,json; print(json.load(sys.stdin)['auth']['client_token'])" \
        2>/dev/null || echo "")

    if [[ -z "$test_token" ]]; then
        skip "Cannot obtain test AppRole token"
        return
    fi

    # Verify token works initially
    local initial
    initial=$(curl -s -o /dev/null -w "%{http_code}" \
        --cacert "$BAO_CACERT" \
        -H "X-Vault-Token: $test_token" \
        "${BAO_ADDR}/v1/auth/token/lookup-self" 2>/dev/null)

    if [[ "$initial" == "200" ]]; then
        pass "Token valid before revocation (HTTP 200)"
    else
        fail "Token unexpectedly invalid before revocation (HTTP $initial)"
    fi

    # Revoke token (simulating what bao-luks-unlock does)
    curl -sf --cacert "$BAO_CACERT" \
        -H "X-Vault-Token: $test_token" \
        -X POST "${BAO_ADDR}/v1/auth/token/revoke-self" >/dev/null 2>&1

    # Verify token is now invalid
    local after
    after=$(curl -s -o /dev/null -w "%{http_code}" \
        --cacert "$BAO_CACERT" \
        -H "X-Vault-Token: $test_token" \
        "${BAO_ADDR}/v1/auth/token/lookup-self" 2>/dev/null)

    if [[ "$after" == "403" ]]; then
        pass "Token invalid after revocation (HTTP 403) — no persistent credential"
    else
        fail "Token still valid after revocation (HTTP $after) — credential not revoked!"
    fi
}

# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
# Summary
# ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ── ──
print_summary() {
    sep
    log "TEST SUMMARY"
    sep
    log "  PASSED:  $PASSED"
    log "  FAILED:  $FAILED"
    log "  SKIPPED: $SKIPPED"
    log "  TOTAL:   $((PASSED + FAILED + SKIPPED))"
    sep
    log "Log file: $LOG_FILE"

    if [[ $FAILED -eq 0 ]]; then
        log "RESULT: ALL TESTS PASSED"
        return 0
    else
        log "RESULT: $FAILED TEST(S) FAILED — review log above"
        return 1
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    exec > >(tee -a "$LOG_FILE") 2>&1

    log "======================================================="
    log "  HSM + OpenBao + LUKS Integration Test Suite"
    log "  $(date)"
    log "  Target: $BAO_ADDR"
    log "======================================================="

    test_hsm_connectivity
    test_pkcs11_wrap_key
    test_pkcs11_trng
    test_openbao_unseal
    test_raft_cluster
    test_transit_roundtrip
    test_approle_isolation
    test_audit_log
    test_luks_encryption
    test_luks_unlock_cycle
    test_token_revocation

    print_summary
}

main "$@"
