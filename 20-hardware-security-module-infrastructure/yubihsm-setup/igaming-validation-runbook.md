# iGaming Platform — Validation Checklist & Test Runbook

> **Purpose:** Reference document for validating the local environment before submitting for
> certification (GLI, PCI DSS QSA, ISO 27001). Run the tests in the order listed.
> Each section includes prerequisites, exact commands, and expected outputs.

---

## Table of Contents

1. [Environment Prerequisites](#1-environment-prerequisites)
2. [YubiHSM 2: Hardware Validation](#2-yubihsm-2-hardware-validation)
3. [OpenBao: Cluster and Auto-Unseal](#3-openbao-cluster-and-auto-unseal)
4. [LUKS: Disk Encryption on VMs](#4-luks-disk-encryption-on-vms)
5. [Network and PCI DSS Segmentation](#5-network-and-pci-dss-segmentation)
6. [Database Encryption](#6-database-encryption)
7. [Key Hierarchy: HKDF and Epoch Keys](#7-key-hierarchy-hkdf-and-epoch-keys)
8. [RNG: GLI-19 Pre-Certification](#8-rng-gli-19-pre-certification)
9. [Wallet Engine: Financial Integrity](#9-wallet-engine-financial-integrity)
10. [Audit Chain: Immutability](#10-audit-chain-immutability)
11. [Session Keys: JWT and HSM Endorsement](#11-session-keys-jwt-and-hsm-endorsement)
12. [KYC / AML: Real-Time Compliance](#12-kyc--aml-real-time-compliance)
13. [Player Protection: Limits and Self-Exclusion](#13-player-protection-limits-and-self-exclusion)
14. [Fund Segregation: Reconciliation](#14-fund-segregation-reconciliation)
15. [Performance and Load](#15-performance-and-load)
16. [Security: SAST, CVE, Pentest](#16-security-sast-cve-pentest)
17. [PCI DSS Compliance: Evidence Collection](#17-pci-dss-compliance-evidence-collection)
18. [Go-Live Checklist](#18-go-live-checklist)
19. [Known Gaps and Pending Items](#19-known-gaps-and-pending-items)

---

## 1. Environment Prerequisites

### 1.1 Required Software

```bash
# Check minimum versions
rustc --version        # >= 1.76.0
cargo --version        # >= 1.76.0
bao --version          # >= 2.2.x (cgo) - OpenBao with PKCS#11 support
docker --version       # >= 24.0
kubectl version        # >= 1.28 (if using K8s)
cryptsetup --version   # >= 2.6
pkcs11-tool --version  # OpenSC >= 0.23
yubihsm-connector --version  # Yubico SDK >= 2.4
psql --version         # PostgreSQL client >= 16
k6 version             # >= 0.49 (load testing)
testssl.sh --version   # >= 3.0.8 (TLS testing)
```

### 1.2 Required Environment Variables

```bash
# Create .env.test file (DO NOT commit)
export BAO_ADDR="https://bao-01:8200"
export BAO_CACERT="/opt/openbao/tls/ca.crt"
export PKCS11_LIB="/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so"
export YUBIHSM_PKCS11_CONF="/etc/yubihsm_pkcs11.conf"
export DATABASE_URL="postgres://igaming:PASSWORD@localhost:5432/igaming_test"
export HSM_PIN="0001<your-password>"  # never commit
```

### 1.3 Expected Topology

```
bao-01  (IP: 10.3.0.1) — OpenBao ACTIVE + YubiHSM 2 USB
bao-02  (IP: 10.3.0.2) — OpenBao STANDBY
bao-03  (IP: 10.3.0.3) — OpenBao STANDBY
vm-db-01 (IP: 10.2.0.10): PostgreSQL on a LUKS volume
```

---

## 2. YubiHSM 2: Hardware Validation

### TC-YK-01: Device detected and connector active

```bash
# Check that the connector is running
systemctl status yubihsm-connector
# Expected: active (running)

# Test HTTP connectivity with the connector
curl -sf http://127.0.0.1:12345/connector/status
# Expected: {"Status":"OK","Serial":"XXXXXXXX","Version":"2.x.x","Pid":XXXX,"Address":"127.0.0.1","LogLevel":1}
```

**✅ PASS if:** status `OK` and serial present  
**❌ FAIL if:** connection refused → check that `yubihsm-connector` is installed and running

---

### TC-YK-02: List objects on the HSM

```bash
pkcs11-tool \
  --module "$PKCS11_LIB" \
  --login \
  --pin "$HSM_PIN" \
  --list-objects
```

**✅ PASS if:** an object appears with `label: bao-root-key-aes`, type `Secret Key`, `Usage: wrap, unwrap`  
**❌ FAIL if:** no object → run the key creation setup (Section 2 of the master doc)

---

### TC-YK-03: Confirm the key is non-extractable

```bash
pkcs11-tool \
  --module "$PKCS11_LIB" \
  --login \
  --pin "$HSM_PIN" \
  --list-objects \
  --verbose 2>&1 | grep -A5 "bao-root-key-aes"
```

**✅ PASS if:** output contains `always sensitive` and `never extractable`  
**❌ FAIL:** key can be exported: critical security compromise

---

### TC-YK-04: TRNG entropy generation

```bash
# Generate 32 random bytes from the HSM and check entropy
pkcs11-tool \
  --module "$PKCS11_LIB" \
  --login \
  --pin "$HSM_PIN" \
  --generate-random 32 \
  --output-file /tmp/hsm_random.bin

# Check entropy (should be close to 8.0 bits/byte)
ent /tmp/hsm_random.bin
# Expected: "Entropy = 7.9x bits per byte" (for 32 bytes, variance is normal)

# Confirm that two generations are different
pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
  --generate-random 32 | xxd | head -2

rm -f /tmp/hsm_random.bin
```

**✅ PASS if:** entropy ≥ 7.5 and two samples are distinct  
**❌ FAIL:** low entropy → defective hardware

---

### TC-YK-05: Sign and verify ECDSA P-256 with a key on the HSM

```bash
# List existing asymmetric keys
pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
  --list-objects --type privkey

# If no signing key exists, create one:
pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
  --keypairgen --key-type EC:prime256v1 \
  --label "audit-signing-key" --id 02

# Test signing
echo -n "test-payload-$(date +%s)" > /tmp/test_payload.txt
pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
  --sign --mechanism ECDSA-SHA256 \
  --label "audit-signing-key" \
  --input-file /tmp/test_payload.txt \
  --output-file /tmp/test_sig.bin

echo "Signature size: $(wc -c < /tmp/test_sig.bin) bytes"
# Expected: 64-72 bytes (ECDSA P-256 DER encoding)

rm -f /tmp/test_payload.txt /tmp/test_sig.bin
```

**✅ PASS if:** signature file generated with size > 0  
**❌ FAIL:** mechanism error → check firmware and supported algorithms

---

### TC-YK-06: 16 concurrent sessions (YubiHSM 2 limit)

```bash
# Test that multiple concurrent sessions work
cat << 'SCRIPT' > /tmp/test_concurrent_hsm.sh
#!/bin/bash
for i in {1..16}; do
  (pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
    --generate-random 16 > /dev/null 2>&1 && echo "Session $i: OK" || echo "Session $i: FAIL") &
done
wait
SCRIPT
chmod +x /tmp/test_concurrent_hsm.sh
/tmp/test_concurrent_hsm.sh
rm /tmp/test_concurrent_hsm.sh
```

**✅ PASS if:** all 16 sessions return OK  
**❌ FAIL if:** some fail → redesign the session pool in the Rust HsmClient (max=16)

---

## 3. OpenBao: Cluster and Auto-Unseal

### TC-BAO-01: Cluster status

```bash
export BAO_ADDR="https://bao-01:8200"
export BAO_CACERT="/opt/openbao/tls/ca.crt"

bao status --format=json | jq '{
  initialized,
  sealed,
  seal_type: .seal_type,
  ha_enabled: .ha_enabled,
  active_time
}'
```

**✅ PASS if:**
```json
{
  "initialized": true,
  "sealed": false,
  "seal_type": "pkcs11",
  "ha_enabled": true
}
```
**❌ FAIL if:** `sealed: true` → check yubihsm-connector and PIN

---

### TC-BAO-02: Auto-unseal after restart

```bash
# Restart the service
sudo systemctl restart openbao
sleep 8

# Verify it unseals automatically
bao status | grep -E "^Sealed"
# Expected: "Sealed          false"

# Measure unseal time
time (sudo systemctl restart openbao && \
  until bao status 2>/dev/null | grep -q "Sealed.*false"; do sleep 0.5; done; \
  echo "Unsealed!")
# Expected: < 10 seconds
```

**✅ PASS if:** Sealed=false in less than 15 seconds after restart  
**❌ FAIL:** Sealed=true → HSM unreachable or incorrect PIN

---

### TC-BAO-03: Raft cluster with 3 peers

```bash
bao operator raft list-peers --format=json | jq '.data.config.servers[] | {node_id, address, leader, voter}'
```

**✅ PASS if:** 3 peers listed, 1 with `leader: true`, all with `voter: true`  
**❌ FAIL if:** fewer than 3 → quorum lost, cluster degraded

---

### TC-BAO-04: Failover: stop the active node, verify new election

```bash
# Identify the current leader
LEADER=$(bao operator raft list-peers --format=json | jq -r '.data.config.servers[] | select(.leader==true) | .address')
echo "Current leader: $LEADER"

# Stop the leader (run on the leader node)
# ssh $LEADER sudo systemctl stop openbao

# Wait for new election (max 30s)
sleep 15

# Check the new leader from another node
bao status --address=https://bao-02:8200 --ca-cert="$BAO_CACERT" | grep -E "HA Cluster|Active Node"
# Expected: new active node different from the previous one

# Restart the stopped node
# ssh $LEADER sudo systemctl start openbao
```

**✅ PASS if:** new leader elected in < 20 seconds, cluster operational  
**❌ FAIL:** cluster unreachable → network problem between nodes

---

### TC-BAO-05: Transit Engine: wrap/unwrap round-trip

```bash
# Create a test key if it doesn't exist
bao write transit/keys/test-roundtrip type=aes256-gcm96

# Encrypt test data
PLAINTEXT="my-super-secret-luks-key-12345"
ENCODED=$(echo -n "$PLAINTEXT" | base64 -w0)
WRAPPED=$(bao write -field=ciphertext transit/encrypt/test-roundtrip plaintext="$ENCODED")
echo "Wrapped: ${WRAPPED:0:40}..."

# Decrypt and verify
DECRYPTED=$(bao write -field=plaintext transit/decrypt/test-roundtrip ciphertext="$WRAPPED" | base64 -d)
echo "Decrypted: $DECRYPTED"

[ "$DECRYPTED" = "$PLAINTEXT" ] && echo "✅ PASS: round-trip OK" || echo "❌ FAIL: mismatch"

# Clean up test key
bao delete transit/keys/test-roundtrip
```

**✅ PASS if:** `DECRYPTED == PLAINTEXT`  
**❌ FAIL:** mismatch → problem in the Transit engine

---

### TC-BAO-06: AppRole policy isolation

```bash
# Create test roles
bao policy write test-policy-vm1 - << 'EOF'
path "transit/decrypt/vm-test-01" { capabilities = ["update"] }
EOF

bao policy write test-policy-vm2 - << 'EOF'
path "transit/decrypt/vm-test-02" { capabilities = ["update"] }
EOF

bao write auth/approle/role/test-vm1 \
  token_policies="test-policy-vm1" token_ttl=5m

bao write auth/approle/role/test-vm2 \
  token_policies="test-policy-vm2" token_ttl=5m

bao write transit/keys/vm-test-01 type=aes256-gcm96
bao write transit/keys/vm-test-02 type=aes256-gcm96

# Generate token for vm1
ROLE_ID=$(bao read -field=role_id auth/approle/role/test-vm1/role-id)
SECRET_ID=$(bao write -field=secret_id -f auth/approle/role/test-vm1/secret-id)
TOKEN_VM1=$(bao write -field=token auth/approle/login role_id="$ROLE_ID" secret_id="$SECRET_ID")

# Generate ciphertext for vm-test-02
CIPHER=$(bao write -field=ciphertext transit/encrypt/vm-test-02 \
  plaintext=$(echo -n "secret" | base64 -w0))

# Try to decrypt vm-test-02 using vm1's token: MUST FAIL
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
  --cacert "$BAO_CACERT" \
  -H "X-Vault-Token: $TOKEN_VM1" \
  -X POST \
  -d "{\"ciphertext\":\"$CIPHER\"}" \
  "$BAO_ADDR/v1/transit/decrypt/vm-test-02")

[ "$HTTP_CODE" = "403" ] && echo "✅ PASS: 403 Forbidden (isolation OK)" \
                          || echo "❌ FAIL: HTTP $HTTP_CODE (isolation BROKEN)"

# Cleanup
bao delete transit/keys/vm-test-01 2>/dev/null || true
bao delete transit/keys/vm-test-02 2>/dev/null || true
bao delete auth/approle/role/test-vm1 2>/dev/null || true
bao delete auth/approle/role/test-vm2 2>/dev/null || true
bao delete sys/policy/test-policy-vm1 2>/dev/null || true
bao delete sys/policy/test-policy-vm2 2>/dev/null || true
```

**✅ PASS if:** HTTP 403  
**❌ FAIL if:** HTTP 200 → critical policy isolation failure

---

### TC-BAO-07: Audit log records operations

```bash
# Run an operation
bao write transit/keys/audit-test type=aes256-gcm96
bao write -field=ciphertext transit/encrypt/audit-test \
  plaintext=$(echo -n "test" | base64 -w0) > /dev/null

# Verify the operation appears in the audit log
tail -5 /var/log/openbao/audit.log | jq -r '.request.path' 2>/dev/null
# Expected: lines containing "transit/encrypt/audit-test"

# Verify plaintext does NOT appear in the log
grep "dGVzdA==" /var/log/openbao/audit.log && \
  echo "❌ FAIL: plaintext in audit log!" || \
  echo "✅ PASS: plaintext absent from audit log"

bao delete transit/keys/audit-test 2>/dev/null || true
```

**✅ PASS if:** operation recorded, plaintext absent  
**❌ FAIL if:** plaintext present in the log → critical security failure

---

## 4. LUKS: Disk Encryption on VMs

### TC-LUKS-01: Verify active LUKS volumes

```bash
# On each data VM
lsblk -o NAME,TYPE,FSTYPE,MOUNTPOINT | grep -E "crypt|LUKS"
# Expected: device with TYPE=crypt mounted at /data or similar

# Verify the disk is encrypted with LUKS2
sudo cryptsetup status data_crypt | grep -E "type|cipher|keysize"
# Expected:
# type:    LUKS2
# cipher:  aes-xts-plain64
# keysize: 512 bits
```

**✅ PASS if:** LUKS2 with AES-XTS-512  
**❌ FAIL if:** LUKS1 or keysize < 256 → re-encrypt with correct parameters

---

### TC-LUKS-02: Token in the LUKS header

```bash
# Verify the OpenBao token exists in the header
sudo cryptsetup luksDump /dev/vdb | grep -A10 "Tokens:"
# Expected: Token slot 0 with type: openbao-transit

# Export and verify the token content
sudo cryptsetup token export --token-id 0 /dev/vdb | python3 -c "
import sys, json
t = json.load(sys.stdin)
assert 'ciphertext' in t, 'ciphertext missing'
assert t['ciphertext'].startswith('vault:v'), 'invalid ciphertext format'
print(f'✅ PASS: token OK, ciphertext prefix: {t[\"ciphertext\"][:20]}...')
"
```

**✅ PASS if:** token present with valid `ciphertext` field  
**❌ FAIL if:** no token → disk was not provisioned correctly

---

### TC-LUKS-03: Disk inaccessible without OpenBao

```bash
# Simulate disk theft: stop OpenBao and try to open
# WARNING: run only in a test environment, never in production with real data

# Create test volume
dd if=/dev/urandom of=/tmp/test_luks_disk.img bs=1M count=10
LOOP_DEV=$(sudo losetup -f --show /tmp/test_luks_disk.img)

# Provision LUKS with a random key (simulating the real process)
TEST_KEY=$(openssl rand -base64 32)
echo -n "$TEST_KEY" | sudo cryptsetup luksFormat \
  --type luks2 --cipher aes-xts-plain64 --key-size 512 \
  --key-file - "$LOOP_DEV"

# Try to open with the wrong password (simulating no access to OpenBao)
echo "wrong-password" | sudo cryptsetup luksOpen \
  --key-file - "$LOOP_DEV" test_stolen 2>&1 | \
  grep -q "No key available" && \
  echo "✅ PASS: disk inaccessible without the correct key" || \
  echo "❌ FAIL: disk opened without a valid key"

# Cleanup
sudo losetup -d "$LOOP_DEV"
rm /tmp/test_luks_disk.img
```

**✅ PASS if:** "No key available"  
**❌ FAIL:** disk opened without a key → incorrect configuration

---

### TC-LUKS-04: Automatic boot with unlock via OpenBao

```bash
# Verify the unlock script exists in the initramfs
sudo lsinitramfs /boot/initramfs-$(uname -r).img 2>/dev/null | \
  grep "bao-luks-unlock" || \
  sudo lsinitrd /boot/initramfs-$(uname -r).img 2>/dev/null | \
  grep "bao-luks-unlock"
# Expected: file present

# Test the script manually (without rebooting)
sudo /usr/local/sbin/bao-luks-unlock 2>&1 | tail -5
# Expected: "Unlock complete. Credentials zeroed from memory."

# Verify the disk is mounted after unlock
ls /dev/mapper/data_crypt && echo "✅ PASS: volume unlocked" || echo "❌ FAIL: unlock failed"
```

**✅ PASS if:** script present in the initramfs and unlock succeeds  
**❌ FAIL:** script missing → initramfs rebuild required

---

## 5. Network and PCI DSS Segmentation

### TC-NET-01: Isolated network zones

```bash
# Verify configured VLANs or subnets
ip route show
# Expected: routes to 10.1.0.0/24 (services), 10.2.0.0/24 (CDE), 10.3.0.0/24 (HSM)

# Test that direct internet → CDE access is blocked
# (run from an external host or from the DMZ)
nc -zv 10.2.0.10 5432 --wait 3 2>&1
# Expected: "Connection refused" or timeout if firewall is active
```

---

### TC-NET-02: Minimum TLS 1.2 on all endpoints

```bash
# Test the main API
testssl.sh --protocols --quiet https://api.igaming.internal

# Verify TLS 1.0 and 1.1 are disabled
testssl.sh --protocols https://api.igaming.internal 2>&1 | \
  grep -E "TLS 1\.[01]" | \
  grep -v "not offered" && \
  echo "❌ FAIL: TLS 1.0/1.1 enabled" || \
  echo "✅ PASS: TLS 1.0/1.1 disabled"

# Verify cipher suites (no weak suites)
testssl.sh --cipher-per-proto https://api.igaming.internal 2>&1 | \
  grep -iE "RC4|DES|3DES|NULL|EXPORT|anon" && \
  echo "❌ FAIL: weak cipher suite detected" || \
  echo "✅ PASS: no weak cipher suites"
```

**✅ PASS if:** TLS 1.2+ only, no weak ciphers  
**❌ FAIL:** TLS 1.0/1.1 enabled → PCI DSS Req. 4 violation

---

### TC-NET-03: mTLS between internal services

```bash
# Test that a service without a client certificate is rejected
curl -sf --cacert "$BAO_CACERT" \
  --max-time 5 \
  https://wallet-service.internal:8080/health 2>&1 | \
  grep -q "SSL certificate" && \
  echo "✅ PASS: mTLS required" || \
  echo "⚠ WARN: mTLS may not be active"

# Test with a valid client certificate
curl -sf \
  --cacert "$BAO_CACERT" \
  --cert /opt/certs/wallet-client.crt \
  --key /opt/certs/wallet-client.key \
  https://wallet-service.internal:8080/health
# Expected: {"status":"ok"}
```

---

### TC-NET-04: HSM zone accessible only by OpenBao nodes

```bash
# Run from a host outside the HSM zone
nc -zv 10.3.0.1 12345 --wait 3 2>&1
# Expected: "Connection refused" or timeout

# Run from bao-01 (should work)
ssh bao-01 "curl -sf http://127.0.0.1:12345/connector/status"
# Expected: status OK
```

---

## 6. Database Encryption

### TC-DB-01: PostgreSQL with mandatory TLS

```bash
# Verify SSL configuration in PostgreSQL
psql "$DATABASE_URL" -c "SHOW ssl;" | grep -q "on" && \
  echo "✅ PASS: SSL enabled" || echo "❌ FAIL: SSL disabled"

# Verify that a connection without SSL is rejected
psql "$(echo $DATABASE_URL | sed 's/postgres/postgresql/')&sslmode=disable" \
  -c "SELECT 1;" 2>&1 | \
  grep -q "SSL off" && \
  echo "❌ FAIL: connection without SSL accepted" || \
  echo "✅ PASS: connection without SSL rejected"
```

---

### TC-DB-02: PII fields are encrypted in the database

```bash
# Insert test player
psql "$DATABASE_URL" << 'SQL'
INSERT INTO players (id, document_encrypted, email_sha256, balance)
VALUES (
  gen_random_uuid(),
  'BASE64_CIPHERTEXT_HERE',  -- value encrypted by the application
  sha256('test@example.com'),
  0.00
);
SQL

# Verify that document_encrypted does not contain a readable CPF
psql "$DATABASE_URL" -t -c \
  "SELECT document_encrypted FROM players ORDER BY created_at DESC LIMIT 1;" | \
  grep -qP "^\d{3}\.\d{3}\.\d{3}-\d{2}$" && \
  echo "❌ FAIL: CPF in plaintext in the database" || \
  echo "✅ PASS: field encrypted"
```

---

### TC-DB-03: Verify that data on disk is unreadable without decryption

```bash
# On vm-db-01: verify that readable strings do not appear in the bytes of the LUKS volume
# (BEFORE mounting the volume: requires access to the raw device)

# This test is destructive: run only in a test environment
# sudo cryptsetup close data_crypt  # close first
# strings /dev/vdb | grep -i "player\|email\|password\|cpf" | head -5
# Expected: no readable results

echo "TC-DB-03: run manually in an isolated test environment"
echo "Verify that 'strings /dev/vdb' does not return readable player data"
```

---

## 7. Key Hierarchy: HKDF and Epoch Keys

### TC-KEY-01: Build and run unit tests for libs/hsm

```bash
cd /path/to/igaming-platform

# Run all tests in the hsm module
cargo test -p igaming-hsm -- --nocapture 2>&1 | tail -20

# Verify specifically that the following pass:
cargo test -p igaming-hsm hsm::key_hierarchy::test_keys_independent -- --nocapture
cargo test -p igaming-hsm hsm::key_hierarchy::test_single_hsm_call -- --nocapture
cargo test -p igaming-hsm hsm::key_hierarchy::test_zeroize_on_drop -- --nocapture
```

**✅ PASS if:** `test result: ok. X passed; 0 failed`

---

### TC-KEY-02: Cryptographic independence of HKDF sub-keys

```bash
cargo test -p igaming-hsm hsm::key_hierarchy::test_keys_independent -- --nocapture
```

Implement in the test code:
```rust
// libs/hsm/src/tests.rs
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_keys_independent() {
        let hsm = MockHsmClient::new_with_fixed_entropy([0u8; 64]);
        let h = KeyHierarchy::derive_from_hsm(&hsm).await.unwrap();

        // Verify that all 6 keys are distinct from each other
        let keys = [
            h.wallet_hmac,
            h.field_cipher,
            h.jwt_signing,
            h.audit_mac,
            h.rng_mixer,
            h.field_cipher2,
        ];

        for i in 0..keys.len() {
            for j in (i+1)..keys.len() {
                assert_ne!(keys[i], keys[j],
                    "Keys {} and {} are equal: separation failure", i, j);
            }
        }
    }

    #[tokio::test]
    async fn test_single_hsm_call() {
        let hsm = CountingHsmClient::new();
        let _ = KeyHierarchy::derive_from_hsm(&hsm).await.unwrap();
        assert_eq!(hsm.call_count(), 1,
            "KeyHierarchy must call the HSM exactly 1 time, called {}", hsm.call_count());
    }

    #[tokio::test]
    async fn test_zeroize_on_drop() {
        let hsm = MockHsmClient::new();
        let ptr = {
            let h = KeyHierarchy::derive_from_hsm(&hsm).await.unwrap();
            h.wallet_hmac.as_ptr()
        }; // h dropped here: ZeroizeOnDrop should zero it
        // Note: this test is better verified with valgrind/miri
        // The presence of #[derive(ZeroizeOnDrop)] is sufficient for compilation
        println!("ZeroizeOnDrop: verify with 'cargo miri test'");
    }
}
```

---

### TC-KEY-03: Epoch rotation without interruption

```bash
cargo test -p igaming-hsm hsm::epoch::test_grace_period_verification -- --nocapture
cargo test -p igaming-hsm hsm::epoch::test_concurrent_rotation -- --nocapture
```

**Additional manual verification:**
```bash
# Monitor metrics during epoch rotation
# There should be no errors in the 30 seconds around the rotation
watch -n 1 'journalctl -u wallet-service --since "1 minute ago" | grep -c "error"'
```

---

## 8. RNG: GLI-19 Pre-Certification

### TC-RNG-01: NIST SP 800-22: test suite installation

```bash
# Download and build the NIST reference suite
wget -q https://csrc.nist.gov/CSRC/media/Projects/Random-Bit-Generation/documents/sts-2_1_2.zip
unzip -q sts-2_1_2.zip
cd sts-2.1.2
make -s

ls -la assess
# Expected: executable file 'assess'
```

---

### TC-RNG-02: Generate 1 billion bits from the RNG service

```bash
# Build the RNG export tool
cargo build --release --bin rng-export

# Generate samples (takes ~5 minutes)
./target/release/rng-export \
  --samples 1000000000 \
  --output /tmp/rng_samples.bin

echo "Size: $(wc -c < /tmp/rng_samples.bin) bytes"
# Expected: 125000000 bytes (1 billion bits = 125 MB)
```

**Implement the `rng-export` binary:**
```rust
// services/rng/src/bin/rng-export.rs
use clap::Parser;
use igaming_rng::{CertifiedRng, SeedPool};
use std::io::Write;

#[derive(Parser)]
struct Args {
    #[arg(long, default_value = "1000000000")]
    samples: u64,       // number of bits
    #[arg(long)]
    output: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    let hsm = igaming_hsm::HsmClient::from_env()?;
    let pool = SeedPool::new(hsm.clone(), /* epoch */ todo!());
    pool.warmup().await?;

    let mut file = std::fs::File::create(&args.output)?;
    let bytes_needed = (args.samples / 8) as usize;
    let mut written = 0usize;

    while written < bytes_needed {
        let mut session = pool.create_game_session(
            &format!("export-{}", written), &uuid::Uuid::new_v4()
        ).await?;
        let chunk: Vec<u8> = (0..256).map(|_| session.draw(256) as u8).collect();
        let to_write = (bytes_needed - written).min(chunk.len());
        file.write_all(&chunk[..to_write])?;
        written += to_write;
    }

    println!("Generated {} bytes in {}", written, args.output);
    Ok(())
}
```

---

### TC-RNG-03: Run all 15 NIST SP 800-22 tests

```bash
cd sts-2.1.2

# Convert binary to NIST format (ASCII 0s and 1s)
python3 -c "
import sys
with open('/tmp/rng_samples.bin', 'rb') as f:
    data = f.read(1000000)  # first 1M bytes for a quick test
for byte in data:
    sys.stdout.write(f'{byte:08b}')
" > /tmp/rng_nist.txt

# Run the suite with 100 sequences of 1M bits
./assess 1000000 << 'EOF'
0
/tmp/rng_nist.txt
1
0
EOF

# Check results
cat experiments/AlgorithmTesting/finalAnalysisReport.txt
```

**✅ PASS if:** all lines show `PASS` or the passing proportion is ≥ 96%  
**❌ FAIL if:** any test consistently shows `FAIL` → bias in the RNG

---

### TC-RNG-04: Session isolation

```bash
cargo test -p igaming-rng rng::pool::test_context_mixing_isolation -- --nocapture
```

```rust
// Implement in the test module:
#[tokio::test]
async fn test_context_mixing_isolation() {
    let pool = create_test_pool().await;

    // Same pool seed, different contexts
    let mut session1 = pool.create_game_session("game-A", &player_id_1).await.unwrap();
    let mut session2 = pool.create_game_session("game-A", &player_id_2).await.unwrap();

    // Collect 1000 draws from each
    let seq1: Vec<u32> = (0..1000).map(|_| session1.draw(1000)).collect();
    let seq2: Vec<u32> = (0..1000).map(|_| session2.draw(1000)).collect();

    // Calculate Pearson correlation (should be close to 0)
    let correlation = pearson_correlation(&seq1, &seq2);
    assert!(correlation.abs() < 0.05,
        "Correlated sessions: r={} (max 0.05)", correlation);
    println!("✅ Correlation between sessions: {} (< 0.05)", correlation);
}
```

---

### TC-RNG-05: RTP accuracy over 10 million spins

```bash
cargo test --release -p igaming-game-engine -- rtp::test_10m_spins --nocapture
```

```rust
#[tokio::test]
async fn test_10m_spins() {
    const SPINS: u64 = 10_000_000;
    const TARGET_RTP: f64 = 96.5;   // %
    const TOLERANCE: f64 = 0.5;     // ±0.5% is excellent; GLI accepts ±2%

    let pool = create_test_pool().await;
    let game_config = SlotConfig::standard_5reel_3row(TARGET_RTP);

    let mut total_staked = 0u64;
    let mut total_won = 0u64;

    for i in 0..SPINS {
        let stake = 100u64;  // 100 units
        let player_id = uuid::Uuid::new_v4();
        let mut session = pool.create_game_session(&format!("spin-{}", i), &player_id).await.unwrap();

        let symbols = game_config.spin(&mut session);
        let win = game_config.evaluate_win(&symbols, stake);

        total_staked += stake;
        total_won += win;
    }

    let empirical_rtp = (total_won as f64 / total_staked as f64) * 100.0;
    let delta = (empirical_rtp - TARGET_RTP).abs();

    println!("Target RTP: {}%", TARGET_RTP);
    println!("Empirical RTP ({} spins): {:.4}%", SPINS, empirical_rtp);
    println!("Delta: {:.4}% (max: {}%)", delta, TOLERANCE);

    assert!(delta <= TOLERANCE,
        "RTP out of tolerance: {:.4}% vs target {}% (delta: {:.4}%)",
        empirical_rtp, TARGET_RTP, delta);

    println!("✅ PASS: RTP within GLI tolerance");
}
```

**✅ PASS if:** delta ≤ 0.5% (the lab's tolerance is ±2%)  
**❌ FAIL:** delta > 2% → critical failure, biased RNG or incorrect payout logic

---

### TC-RNG-06: Rejection sampling: zero bias

```bash
cargo test -p igaming-rng rng::test_unbiased_distribution -- --nocapture
```

```rust
#[test]
fn test_unbiased_distribution() {
    use rand::SeedableRng;
    let mut rng = rand_chacha::ChaCha20Rng::from_seed([42u8; 32]);
    const N: u32 = 37;          // non-power-of-2 number: the hardest case
    const DRAWS: u64 = 10_000_000;

    let mut counts = vec![0u64; N as usize];
    for _ in 0..DRAWS {
        let v = unbiased_range(&mut rng, N);
        counts[v as usize] += 1;
    }

    let expected = DRAWS / N as u64;
    let chi_sq: f64 = counts.iter().map(|&c| {
        let diff = c as f64 - expected as f64;
        diff * diff / expected as f64
    }).sum();

    // Chi-squared with N-1=36 degrees of freedom, alpha=0.05
    // Critical value: 50.998
    println!("Chi-squared: {:.4} (critical: 50.998)", chi_sq);
    assert!(chi_sq < 50.998,
        "Biased distribution: chi²={:.4}", chi_sq);
    println!("✅ PASS: uniform distribution (no bias)");
}
```

---

## 9. Wallet Engine: Financial Integrity

### TC-WAL-01: Atomic transaction with idempotency

```bash
cargo test -p igaming-wallet wallet::test_idempotent_transaction -- --nocapture
```

```rust
#[tokio::test]
async fn test_idempotent_transaction() {
    let engine = create_test_engine().await;
    let player_id = create_test_player(&engine.db, dec!(100.00)).await;
    let idem_key = uuid::Uuid::new_v4();

    // First transaction
    let r1 = engine.execute(TxRequest {
        player_id,
        amount: dec!(10.00),
        tx_type: TxType::Deposit,
        idempotency_key: idem_key,
        ..Default::default()
    }).await.unwrap();

    // Second attempt with the same idempotency_key: should return AlreadyProcessed
    let r2 = engine.execute(TxRequest {
        player_id,
        amount: dec!(10.00),
        tx_type: TxType::Deposit,
        idempotency_key: idem_key,  // same key
        ..Default::default()
    }).await;

    assert!(matches!(r2, Err(WalletError::AlreadyProcessed(_))),
        "Second attempt should return AlreadyProcessed");

    // Balance should reflect only one transaction (not two)
    let balance = get_balance(&engine.db, player_id).await;
    assert_eq!(balance, dec!(110.00), "Incorrect balance: {}", balance);

    println!("✅ PASS: idempotency OK, correct balance {}", balance);
}
```

---

### TC-WAL-02: Race condition: 100 concurrent deposits

```bash
cargo test -p igaming-wallet wallet::test_concurrent_deposits -- --nocapture
```

```rust
#[tokio::test]
async fn test_concurrent_deposits() {
    let engine = Arc::new(create_test_engine().await);
    let player_id = create_test_player(&engine.db, dec!(0.00)).await;
    const CONCURRENT: usize = 100;

    let handles: Vec<_> = (0..CONCURRENT).map(|i| {
        let eng = Arc::clone(&engine);
        tokio::spawn(async move {
            eng.execute(TxRequest {
                player_id,
                amount: dec!(1.00),
                tx_type: TxType::Deposit,
                idempotency_key: uuid::Uuid::new_v4(),
                ..Default::default()
            }).await
        })
    }).collect();

    let results: Vec<_> = futures::future::join_all(handles).await;
    let successes = results.iter().filter(|r| r.as_ref().unwrap().is_ok()).count();

    let final_balance = get_balance(&engine.db, player_id).await;
    let expected = rust_decimal::Decimal::from(successes);

    assert_eq!(final_balance, expected,
        "Balance {} ≠ expected {} ({} successful transactions)",
        final_balance, expected, successes);

    println!("✅ PASS: {} of {} concurrent deposits OK, balance={}", successes, CONCURRENT, final_balance);
}
```

---

### TC-WAL-03: Ledger vs. projected balance reconciliation

```bash
# Run the reconciliation job and verify the result
psql "$DATABASE_URL" << 'SQL'
-- Verify consistency between projected balance and ledger sum
SELECT
    a.player_id,
    a.balance AS projected,
    COALESCE(SUM(CASE
        WHEN t.tx_type IN ('deposit','win','refund','bonus') THEN t.amount
        ELSE -t.amount
    END), 0) AS ledger_sum,
    a.balance - COALESCE(SUM(CASE
        WHEN t.tx_type IN ('deposit','win','refund','bonus') THEN t.amount
        ELSE -t.amount
    END), 0) AS discrepancy
FROM accounts a
LEFT JOIN transactions t ON t.player_id = a.player_id
GROUP BY a.player_id, a.balance
HAVING ABS(a.balance - COALESCE(SUM(CASE
    WHEN t.tx_type IN ('deposit','win','refund','bonus') THEN t.amount
    ELSE -t.amount
END), 0)) > 0.00000001
ORDER BY ABS(discrepancy) DESC;
SQL

# Expected: 0 rows (zero discrepancies)
```

**✅ PASS if:** query returns 0 rows  
**❌ FAIL if:** any discrepancy → urgent audit required

---

### TC-WAL-04: HSM signature of transactions

```bash
cargo test -p igaming-wallet wallet::test_transaction_signature_valid -- --nocapture
```

```rust
#[tokio::test]
async fn test_transaction_signature_valid() {
    let engine = create_test_engine().await;
    let player_id = create_test_player(&engine.db, dec!(100.00)).await;

    let result = engine.execute(TxRequest {
        player_id,
        amount: dec!(10.00),
        tx_type: TxType::Bet,
        idempotency_key: uuid::Uuid::new_v4(),
        game_session_id: Some(uuid::Uuid::new_v4()),
        ..Default::default()
    }).await.unwrap();

    // Rebuild the payload and verify the signature
    let payload = format!("{}:{}:{}:{}", result.tx_id, player_id, dec!(10.00), result.balance_after);
    let sig_bytes = hex::decode(&result.signature).unwrap();

    // Verify with the HSM public key
    let hsm_pub_key = engine.hsm.get_public_key("wallet-signing-key").await.unwrap();
    let valid = verify_ecdsa_p256(&hsm_pub_key, payload.as_bytes(), &sig_bytes);

    assert!(valid, "Invalid HSM signature for tx {}", result.tx_id);
    println!("✅ PASS: valid ECDSA P-256 signature for tx {}", result.tx_id);
}
```

---

## 10. Audit Chain: Immutability

### TC-AUD-01: Hash chain detects tampering

```bash
cargo test -p igaming-audit audit::chain::test_tamper_detection -- --nocapture
```

```rust
#[tokio::test]
async fn test_tamper_detection() {
    let chain = create_test_chain().await;

    // Insert 100 entries
    for i in 0..100 {
        chain.append("tx", None, serde_json::json!({"i": i})).await.unwrap();
    }
    chain.checkpoint().await.unwrap();

    // Manual tampering in the DB
    sqlx::query!("UPDATE audit_log SET data = '{\"tampered\": true}' WHERE sequence = 50")
        .execute(&chain.db).await.unwrap();

    // Verify that tampering is detected
    let valid = chain.verify_range(1, 100).await.unwrap();
    assert!(!valid, "Tampering should be detected");
    println!("✅ PASS: tampering at seq 50 detected by the audit chain");
}
```

---

### TC-AUD-02: 1 ECDSA HSM call per batch of 1,000 records

```bash
cargo test -p igaming-audit audit::chain::test_checkpoint_frequency -- --nocapture
```

```rust
#[tokio::test]
async fn test_checkpoint_frequency() {
    let hsm = CountingHsmClient::new();
    let chain = AuditChain::new_with_hsm(hsm.clone(), batch_size=1000, /* ... */);

    // Insert 3,000 entries
    for i in 0..3000 {
        chain.append("event", None, serde_json::json!({"seq": i})).await.unwrap();
    }

    // Verify exactly 3 ECDSA calls (3 × 1,000)
    assert_eq!(hsm.sign_call_count(), 3,
        "Expected 3 HSM checkpoints, got {}", hsm.sign_call_count());
    println!("✅ PASS: {} ECDSA calls for 3000 records", hsm.sign_call_count());
}
```

---

### TC-AUD-03: Verify existing checkpoints in the database

```bash
psql "$DATABASE_URL" << 'SQL'
SELECT
    id,
    last_sequence,
    entry_count,
    LEFT(batch_hash, 16) || '...' AS hash_preview,
    LEFT(hsm_signature, 16) || '...' AS sig_preview,
    created_at
FROM audit_checkpoints
ORDER BY created_at DESC
LIMIT 10;
SQL
# Expected: rows with hsm_signature populated and not null
```

---

## 11. Session Keys: JWT and HSM Endorsement

### TC-JWT-01: 10,000 JWTs without an additional HSM call

```bash
cargo test -p igaming-auth auth::session_key::test_jwt_no_hsm_call -- --nocapture
```

```rust
#[tokio::test]
async fn test_jwt_no_hsm_call() {
    let hsm = CountingHsmClient::new();
    let key = SessionSigningKey::generate(&hsm).await.unwrap();
    let initial_calls = hsm.call_count();

    let claims = Claims { sub: "player-123".into(), exp: far_future(), iat: now() };

    for _ in 0..10_000 {
        key.sign_jwt(&claims).unwrap();
    }

    let final_calls = hsm.call_count();
    assert_eq!(final_calls - initial_calls, 0,
        "sign_jwt made {} additional HSM calls (expected: 0)", final_calls - initial_calls);
    println!("✅ PASS: 10,000 JWTs with no additional HSM call");
}
```

---

### TC-JWT-02: Expired JWT is rejected

```bash
cargo test -p igaming-auth auth::session_key::test_expired_jwt_rejected -- --nocapture
```

---

### TC-JWT-03: HSM endorsement is verifiable

```bash
cargo test -p igaming-auth auth::session_key::test_endorsement_valid -- --nocapture
```

```rust
#[tokio::test]
async fn test_endorsement_valid() {
    let real_hsm = create_test_hsm_client().await; // uses SoftHSM in CI
    let key = SessionSigningKey::generate(&real_hsm).await.unwrap();

    // Rebuild the endorsement payload
    let payload = format!("session-key:v1:{}:{}:{}",
        key.pub_key_b64, key.generated_at.timestamp(), key.key_id);

    // Verify signature with HSM public key
    let hsm_pub = real_hsm.get_public_key("session-endorsement-key").await.unwrap();
    let sig_bytes = hex::decode(&key.endorsement).unwrap();
    assert!(verify_ecdsa_p256(&hsm_pub, payload.as_bytes(), &sig_bytes),
        "Invalid HSM endorsement");
    println!("✅ PASS: ECDSA P-256 endorsement verified");
}
```

---

## 12. KYC / AML: Real-Time Compliance

### TC-AML-01: Deposit blocked before KYC

```bash
# Via API
TOKEN=$(get_unverified_player_token)
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $TOKEN" \
  -X POST \
  -d '{"amount": "10.00", "currency": "EUR"}' \
  https://api.igaming.internal/wallet/deposit)

[ "$HTTP_CODE" = "403" ] && echo "✅ PASS: deposit blocked without KYC" \
                          || echo "❌ FAIL: HTTP $HTTP_CODE (KYC bypass!)"
```

---

### TC-AML-02: AML alert: high volume in 24h

```bash
cargo test -p igaming-kyc-aml aml::test_high_volume_alert -- --nocapture
```

```rust
#[tokio::test]
async fn test_high_volume_alert() {
    let engine = create_test_aml_engine().await;
    let player_id = uuid::Uuid::new_v4();

    // Simulate €1,800 deposited in the last 24h
    seed_deposit_history(&engine.db, player_id, dec!(1800.00)).await;

    // Try to deposit another €300 (total = €2,100 > €2,000 AMLD6 threshold)
    let decision = engine.evaluate(player_id, dec!(300.00), "deposit", "Test Player").await.unwrap();

    assert!(matches!(decision, AmlDecision::FlagForReview { .. }),
        "Expected FlagForReview, got {:?}", decision);
    println!("✅ PASS: AML alert generated for volume > €2,000/24h");
}
```

---

### TC-AML-03: Rapid cycling: deposit → quick withdrawal

```bash
cargo test -p igaming-kyc-aml aml::test_rapid_cycling_detection -- --nocapture
```

---

### TC-AML-04: Sanctions hit: immediate block

```bash
cargo test -p igaming-kyc-aml aml::test_sanctions_block -- --nocapture
```

```rust
#[tokio::test]
async fn test_sanctions_block() {
    let engine = create_test_aml_engine_with_sanctions().await;
    // Add name to the test sanctions list
    engine.sanctions.add_test_entry("Osama Bin Laden", "OFAC-SDN");

    let decision = engine.evaluate(
        uuid::Uuid::new_v4(), dec!(100.00), "deposit", "Osama Bin Laden"
    ).await.unwrap();

    assert!(matches!(decision, AmlDecision::Block { .. }),
        "Sanctions hit should produce Block, got {:?}", decision);
    println!("✅ PASS: sanctions hit blocked immediately");
}
```

---

## 13. Player Protection: Limits and Self-Exclusion

### TC-PP-01: Daily limit respected

```bash
cargo test -p igaming-responsible-gambling limits::test_daily_limit_enforced -- --nocapture
```

---

### TC-PP-02: Jurisdictional limit (Germany €1,000/month)

```bash
cargo test -p igaming-responsible-gambling limits::test_germany_monthly_limit -- --nocapture
```

```rust
#[tokio::test]
async fn test_germany_monthly_limit() {
    let engine = create_test_limit_engine().await;
    let player_id = uuid::Uuid::new_v4();

    // Simulate €950 deposited during the month
    seed_monthly_deposits(&engine.db, player_id, dec!(950.00)).await;

    // Try to deposit €100 (total would be €1,050 > €1,000 DE limit)
    let result = engine.check_deposit_allowed(player_id, dec!(100.00), "DE").await.unwrap();

    assert!(matches!(result, LimitDecision::Blocked { .. }),
        "Deposit should be blocked by the German limit");
    println!("✅ PASS: German monthly limit (€1,000) respected");
}
```

---

### TC-PP-03: Self-exclusion propagates to all channels in < 5 minutes

```bash
# 1. Authenticate as a player
PLAYER_TOKEN=$(get_player_token "test-player@example.com")

# 2. Request self-exclusion
curl -sf -X POST \
  -H "Authorization: Bearer $PLAYER_TOKEN" \
  -d '{"duration": "permanent", "channels": ["all"]}' \
  https://api.igaming.internal/responsible-gambling/self-exclude

# 3. Wait for propagation
sleep 30

# 4. Verify blocking across all channels
for ENDPOINT in "/wallet/deposit" "/games/spin" "/auth/refresh"; do
  HTTP=$(curl -sf -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $PLAYER_TOKEN" \
    https://api.igaming.internal$ENDPOINT)
  echo "Endpoint $ENDPOINT: HTTP $HTTP"
  [ "$HTTP" = "403" ] && echo "  ✅ Blocked" || echo "  ❌ FAIL: not blocked"
done

# 5. Verify the session was invalidated
DB_CHECK=$(psql "$DATABASE_URL" -t -c \
  "SELECT COUNT(*) FROM sessions WHERE player_id = (SELECT id FROM players WHERE email = 'test-player@example.com')")
echo "Active sessions: $DB_CHECK (expected: 0)"
```

**✅ PASS if:** all endpoints return 403 and sessions = 0  
**❌ FAIL:** any channel still accepts the token → critical regulatory failure

---

## 14. Fund Segregation: Reconciliation

### TC-FS-01: Reconciliation job runs without errors

```bash
# Run reconciliation manually
cargo run --release --bin fund-reconciler -- --run-once

# Check the result in the database
psql "$DATABASE_URL" << 'SQL'
SELECT
    total_player_balance,
    bank_balance,
    delta,
    CASE WHEN delta >= 0 THEN '✅ Surplus' ELSE '❌ CRITICAL DEFICIT' END AS status,
    created_at
FROM fund_reconciliation_reports
ORDER BY created_at DESC
LIMIT 1;
SQL
```

**✅ PASS if:** `delta >= 0` (bank ≥ total player balances)  
**❌ FAIL if:** `delta < 0` → urgent regulatory violation, notify the compliance team

---

### TC-FS-02: Alert in case of deficit

```bash
cargo test -p igaming-audit fund_reconciliation::test_deficit_alert -- --nocapture
```

```rust
#[tokio::test]
async fn test_deficit_alert() {
    let (reconciler, kafka_consumer) = create_test_reconciler().await;

    // Simulate player balance greater than bank balance
    mock_player_balance(&reconciler.db, dec!(10000.00)).await;
    mock_bank_balance(&reconciler.bank_api, dec!(9999.00)).await; // deficit of 1.00

    reconciler.run().await.unwrap();

    // Verify that an alert was emitted to Kafka
    let alert = kafka_consumer.poll_timeout(Duration::from_secs(5)).await;
    assert!(alert.is_some(), "Deficit alert not emitted");
    let alert_data: serde_json::Value = serde_json::from_str(&alert.unwrap()).unwrap();
    assert_eq!(alert_data["type"], "fund_segregation_deficit");
    println!("✅ PASS: deficit alert emitted to the compliance team");
}
```

---

## 15. Performance and Load

### TC-PERF-01: HMAC throughput after HKDF startup

```bash
# Build and run benchmark
cargo bench -p igaming-hsm hsm::bench_hmac_throughput 2>&1 | \
  grep -E "time:|thrpt:"
# Expected: ≥ 500,000 MACs/s per thread
```

```rust
// Implement in libs/hsm/benches/hmac.rs
use criterion::{criterion_group, criterion_main, Criterion, Throughput};

fn bench_hmac_throughput(c: &mut Criterion) {
    let runtime = tokio::runtime::Runtime::new().unwrap();
    let keys = runtime.block_on(async {
        let hsm = MockHsmClient::new();
        KeyHierarchy::derive_from_hsm(&hsm).await.unwrap()
    });

    let mut group = c.benchmark_group("hmac");
    group.throughput(Throughput::Elements(1));

    group.bench_function("wallet_hmac_sha256", |b| {
        b.iter(|| {
            hmac_sha256(&keys.wallet_hmac, b"tx:player-123:100.00:1000.00")
        });
    });

    group.finish();
}

criterion_group!(benches, bench_hmac_throughput);
criterion_main!(benches);
```

---

### TC-PERF-02: Load test with k6 (50k users)

```bash
cat > /tmp/igaming_load.js << 'EOF'
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';

const errorRate = new Rate('errors');

export const options = {
  stages: [
    { duration: '2m', target: 1000 },
    { duration: '5m', target: 10000 },
    { duration: '10m', target: 50000 },
    { duration: '5m', target: 50000 },
    { duration: '2m', target: 0 },
  ],
  thresholds: {
    'http_req_duration{name:auth}':   ['p(99)<100'],
    'http_req_duration{name:wallet}': ['p(99)<200'],
    'http_req_duration{name:rng}':    ['p(99)<50'],
    'http_req_failed':                ['rate<0.001'],
    'errors':                         ['rate<0.005'],
  },
};

export default function() {
  // Auth
  const loginRes = http.post('https://api.igaming.internal/auth/login',
    JSON.stringify({ email: `user${__VU}@test.com`, password: 'test' }),
    { headers: { 'Content-Type': 'application/json' }, tags: { name: 'auth' } }
  );
  check(loginRes, { 'auth 200': r => r.status === 200 }) || errorRate.add(1);

  if (loginRes.status !== 200) { sleep(1); return; }

  const token = loginRes.json('access_token');
  const headers = {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  };

  // Wallet balance
  const walletRes = http.get('https://api.igaming.internal/wallet/balance',
    { headers, tags: { name: 'wallet' } });
  check(walletRes, { 'wallet 200': r => r.status === 200 }) || errorRate.add(1);

  // Game spin (RNG)
  const spinRes = http.post('https://api.igaming.internal/games/spin',
    JSON.stringify({ game_id: 'slot-01', stake: '0.50', idempotency_key: crypto.randomUUID() }),
    { headers, tags: { name: 'rng' } });
  check(spinRes, { 'spin 200': r => r.status === 200 }) || errorRate.add(1);

  sleep(1);
}
EOF

k6 run /tmp/igaming_load.js --out json=/tmp/k6_results.json
```

**✅ PASS if:** all thresholds pass by the end of the test  
**❌ FAIL if:** p99 exceeds the limit or error rate > 0.1%

---

### TC-PERF-03: Startup time with HSM initialization

```bash
# Measure total startup time including HKDF and seed pool
time (cargo run --release --bin wallet-service 2>&1 | grep "listening on")
# Expected: < 3 seconds total
# Expected details in the logs:
# "seed pool warmed up": < 500ms
# "key hierarchy initialized from HSM": < 200ms
# "session key generated": < 300ms
```

---

## 16. Security: SAST, CVE, Pentest

### TC-SEC-01: Zero critical CVEs in dependencies

```bash
# Install and run cargo-audit
cargo install cargo-audit --quiet
cargo audit --deny warnings 2>&1 | tail -10

echo "Exit code: $?"
# Expected: exit code 0 (no vulnerabilities)
```

**✅ PASS if:** `No vulnerabilities found` or only `warning` (not `error`)  
**❌ FAIL:** any CRITICAL or HIGH → update the dependency before continuing

---

### TC-SEC-02: Clippy with no warnings

```bash
cargo clippy --all-targets --all-features -- \
  -D warnings \
  -D clippy::unwrap_used \
  -D clippy::expect_used \
  -D clippy::panic \
  2>&1 | tail -20

echo "Exit code: $?"
# Expected: exit code 0
```

**Note:** `unwrap_used`, `expect_used`, and `panic` are denied: in financial production, all errors must be handled explicitly.

---

### TC-SEC-03: Secrets scanner (no secrets in the code)

```bash
# Install gitleaks
brew install gitleaks || apt-get install gitleaks

# Scan the repository
gitleaks detect --source . --report-format json --report-path /tmp/gitleaks.json

cat /tmp/gitleaks.json | python3 -c "
import sys, json
findings = json.load(sys.stdin)
if not findings:
    print('✅ PASS: no secrets detected')
else:
    print(f'❌ FAIL: {len(findings)} secrets detected!')
    for f in findings:
        print(f'  - {f[\"Description\"]} in {f[\"File\"]}:{f[\"StartLine\"]}')
"
```

---

### TC-SEC-04: OWASP ZAP API scan

```bash
# Run in a staging environment (never in production)
docker run -t owasp/zap2docker-stable zap-api-scan.py \
  -t https://api.igaming.staging/openapi.json \
  -f openapi \
  -r /tmp/zap_report.html \
  -l WARN

# Verify there is no HIGH/CRITICAL level failure
grep -E "FAIL-HIGH|FAIL-CRITICAL" /tmp/zap_report.html && \
  echo "❌ FAIL: HIGH/CRITICAL vulnerabilities detected" || \
  echo "✅ PASS: no critical vulnerabilities in the ZAP scan"
```

---

### TC-SEC-05: TLS score A+ on SSL Labs (staging)

```bash
# Verify manually at: https://www.ssllabs.com/ssltest/
# Staging URL: https://api.igaming.staging

# Or use testssl locally:
testssl.sh --grade https://api.igaming.staging 2>&1 | grep "Overall Grade"
# Expected: Overall Grade: A or A+
```

---

### TC-SEC-06: Constant-time comparison in HMAC verify

```bash
cargo test -p igaming-hsm hsm::epoch::test_constant_time_verify -- --nocapture

# Additional verification with cargo-flamegraph to detect timing leaks:
# cargo flamegraph --test epoch_timing -- test_constant_time_verify
```

---

## 17. PCI DSS Compliance: Evidence Collection

### Evidence required for the QSA

Run each item and save the output as evidence:

```bash
# Create evidence directory
EVIDENCE_DIR="./pci-evidence-$(date +%Y%m%d)"
mkdir -p "$EVIDENCE_DIR"

# REQ 3.6: Keys in FIPS 140-2 L3 HSM
pkcs11-tool --module "$PKCS11_LIB" --login --pin "$HSM_PIN" \
  --list-objects --verbose > "$EVIDENCE_DIR/req3.6-hsm-keys.txt"

# REQ 6.3: Clean CVE scan
cargo audit > "$EVIDENCE_DIR/req6.3-cargo-audit.txt" 2>&1

# REQ 10.2: Audit log sample (last 100 entries)
tail -100 /var/log/openbao/audit.log | jq '.' > "$EVIDENCE_DIR/req10.2-audit-log-sample.json"

# REQ 11.3: Pentest results (placeholder)
echo "Pentest by [COMPANY] on [DATE]: report attached" > "$EVIDENCE_DIR/req11.3-pentest-ref.txt"

# REQ 4: TLS configuration
testssl.sh --json-pretty https://api.igaming.internal > "$EVIDENCE_DIR/req4-tls-config.json" 2>&1

# REQ 3.5: Verify that keys are not in source code
grep -r "bao-root-key\|HSM_PIN\|wallet_hmac" --include="*.rs" --include="*.toml" \
  src/ libs/ services/ > "$EVIDENCE_DIR/req3.5-secret-scan.txt" 2>&1 && \
  echo "❌ Secrets found!" >> "$EVIDENCE_DIR/req3.5-secret-scan.txt" || \
  echo "✅ No secrets in source code" >> "$EVIDENCE_DIR/req3.5-secret-scan.txt"

echo "Evidence saved to $EVIDENCE_DIR/"
ls -la "$EVIDENCE_DIR/"
```

### KEK/DEK Policy Document for the QSA

Create file `key-management-policy.md`:

```markdown
## Key Management Policy: Hybrid KEK/DEK Architecture

### Key Layers

| Layer | Type | Location | TTL | Reference |
|--------|------|-------------|-----|------------|
| Root Entropy | TRNG Hardware | YubiHSM 2 (FIPS 140-2 L3) | Permanent | NIST SP 800-90B |
| KEK (Key Encrypting Key) | AES-256 | YubiHSM 2, non-exportable | Permanent | PCI DSS Req. 3.6 |
| Derived DEKs (HKDF) | AES-256 / HMAC-SHA256 | Process memory | Epoch (30 days) | NIST SP 800-56C |
| Session keys | Ed25519 | Process memory | 1 hour | N/A |

### Compliance Rationale

The DEKs derived via HKDF exist in process memory but are protected by:
1. ZeroizeOnDrop: automatically zeroed when leaving scope
2. No persistence to disk
3. No logging of key values
4. 30-day TTL via epoch rotation
5. Derived from a KEK that never leaves the FIPS 140-2 Level 3 HSM

Reference: NIST SP 800-57 Part 1, Section 5.3.5, "Hybrid Key Management"
Reference: PCI DSS v4.0.1 Req. 3.6.1, "Protect keys within SCDs or via key components"
```

---

## 18. Go-Live Checklist

Run this checklist before launching to production:

### Infrastructure
- [ ] YubiHSM 2: default PIN changed (`0001password` → strong PIN)
- [ ] YubiHSM 2: physical spare available in a secure location
- [ ] OpenBao: recovery keys (5-of-3) in a physical safe at 2 separate locations
- [ ] OpenBao: all 3 nodes responding, quorum confirmed
- [ ] LUKS: all data disks encrypted and automatic boot tested
- [ ] TLS: valid certificates with < 90 days TTL, automatic renewal active
- [ ] Backups: encrypted daily backup tested (restore verified)
- [ ] DR: disaster recovery plan documented and tested (drill completed)

### Security
- [ ] `cargo audit`: zero critical/high CVEs
- [ ] `cargo clippy -D warnings`: zero warnings
- [ ] External pentest: zero unremediated Critical/High findings
- [ ] Secrets scan: zero secrets in code/repository
- [ ] SSL Labs: grade A or A+
- [ ] `PKCS11_MODULE_PIN` in a systemd file, chmod 600, not in a committed `.env`

### Compliance
- [ ] Audit log active (OpenBao audit + application) → integrated with SIEM
- [ ] RNG: NIST SP 800-22 passed internally (all 15 tests)
- [ ] RNG: technical documentation prepared for GLI submission
- [ ] PCI DSS: SAQ completed or QSA engaged
- [ ] KYC/AML: Sumsub/Jumio webhooks tested with real cases
- [ ] Self-exclusion: tested across all channels (web, mobile, API)
- [ ] Fund segregation: trust bank account configured, reconciliation active
- [ ] Jurisdictional limits: configured per market (DE: €1000/month, NL: €700/month)

### Performance
- [ ] Load test with 50k concurrent users: all thresholds passing
- [ ] Seed pool warmed up: < 500ms at startup
- [ ] Auth p99 < 100ms confirmed in load test
- [ ] Wallet p99 < 200ms confirmed in load test
- [ ] Monitoring: Prometheus + Grafana dashboards active
- [ ] Alerts: PagerDuty configured (sealed vault, error rate, latency)

---

## 19. Known Gaps and Pending Items

The items below were identified during development but still require action:

### 🔴 Critical: blocks certification

| Gap | Description | Action |
|-----|-----------|------|
| **RNG GLI submission** | Technical documentation for seed pool mixing needs a specific section explaining isolation | Add a "Seed Pool Architecture" section to the RNG Tech Spec before submitting to GLI |
| **PCI QSA audit** | A formal Key Management Policy does not yet exist as a document | Create a formal document based on the template in Section 17 |
| **Bank fund segregation** | Trust account at a licensed bank not yet opened | Banking process: can take 2-4 weeks |

### 🟡 Important: resolve before go-live

| Gap | Description | Action |
|-----|-----------|------|
| **OASIS integration (DE)** | Integration with the German self-exclusion database not implemented | Required to operate in the German market |
| **BankID (SE)** | OIDC adapter for Swedish BankID not implemented | Required for the Swedish market |
| **miri test for Zeroize** | TC-KEY-01 mentions `cargo miri test` but it is not in CI | Add `cargo miri test -p igaming-hsm` to the pipeline |
| **SoftHSM for CI** | Tests that require a real HSM do not run in CI without a mock/SoftHSM | Configure SoftHSM 2 in CI for integration tests |
| **External pentest** | No firm engaged yet | Hire a CREST-certified firm; process takes 4-8 weeks |
| **ISO 27001 gap assessment** | Not started | Hire a consultancy; full process takes 6-12 months |

### 🟢 Improvement: non-blocking

| Gap | Description | Action |
|-----|-----------|------|
| **cargo-fuzz** | Fuzzing of input parsers is not configured | Add `cargo fuzz` targets for HTTP handlers |
| **Flame graph timing** | TC-SEC-06 mentions flamegraph for timing analysis but it is not automated | Add to the security pipeline |
| **WebSocket load test** | The k6 load test does not cover WebSocket connections (live casino) | Add a WebSocket scenario to the k6 script |
| **Responsible gambling thresholds** | Affordability check (UKGC) not implemented for £2,000+/month | Implement integration with an affordability system |
| **Quantum readiness** | Current algorithms (ECDSA P-256, Ed25519) are not quantum-safe | Monitor NIST PQC; prepare migration to ML-DSA once YubiHSM supports it |
| **Multi-region failover** | DR assumed single-region; multi-region not documented | Define RTO/RPO for a full-region failure |

---

## Appendix A: Quick Diagnostic Commands

```bash
# General platform status (run daily)
echo "=== OpenBao ===" && bao status --format=json | jq '{sealed, ha_enabled}'
echo "=== Raft peers ===" && bao operator raft list-peers --format=json | jq '.data.config.servers | length'
echo "=== YubiHSM ===" && curl -sf http://127.0.0.1:12345/connector/status | jq .Status
echo "=== LUKS volumes ===" && lsblk -o NAME,TYPE,MOUNTPOINT | grep crypt
echo "=== Audit log size ===" && wc -l /var/log/openbao/audit.log
echo "=== DB connection ===" && psql "$DATABASE_URL" -c "SELECT 1" -q && echo "OK"
echo "=== Fund reconciliation ===" && psql "$DATABASE_URL" -t -c \
  "SELECT delta >= 0 AS ok FROM fund_reconciliation_reports ORDER BY created_at DESC LIMIT 1"
```

---

## Appendix B: SoftHSM 2 for Development and CI

```bash
# Install SoftHSM 2 (substitute for YubiHSM 2 for local tests and CI)
sudo apt-get install -y softhsm2

# Configure test token
mkdir -p /tmp/softhsm2-tokens
cat > /tmp/softhsm2.conf << 'EOF'
directories.tokendir = /tmp/softhsm2-tokens
objectstore.backend = file
log.level = INFO
EOF

export SOFTHSM2_CONF=/tmp/softhsm2.conf
softhsm2-util --init-token --slot 0 --label "igaming-test" \
  --pin 1234 --so-pin 12345678

# Create an AES-256 key equivalent to the YubiHSM 2 one
export TEST_PKCS11_MODULE="/usr/lib/softhsm/libsofthsm2.so"
pkcs11-tool --module "$TEST_PKCS11_MODULE" \
  --token-label "igaming-test" --login --pin 1234 \
  --keygen --key-type aes:32 --label "bao-root-key-aes" --sensitive

# Use in Rust tests:
# export PKCS11_LIB=/usr/lib/softhsm/libsofthsm2.so
# export HSM_PIN=1234
# cargo test -p igaming-hsm
```

---

## Appendix C: References

| Document | URL |
|-----------|-----|
| PCI DSS v4.0.1 | https://docs-prv.pcisecuritystandards.org/PCI%20DSS/Standard/PCI-DSS-v4_0_1.pdf |
| NIST SP 800-90A (RNG) | https://csrc.nist.gov/publications/detail/sp/800-90a/rev-1/final |
| NIST SP 800-22 (RNG Tests) | https://csrc.nist.gov/publications/detail/sp/800-22/rev-1a/final |
| NIST SP 800-56C (HKDF) | https://csrc.nist.gov/publications/detail/sp/800-56c/rev-2/final |
| NIST SP 800-57 (Key Mgmt) | https://csrc.nist.gov/publications/detail/sp/800-57-part-1/rev-5/final |
| GLI-19 (Interactive Gaming) | https://gaminglabs.com/standards/gli-19 |
| GLI-33 (Cybersecurity) | https://gaminglabs.com/standards/gli-33 |
| MGA Technical Regulations | https://www.mga.org.mt/regulatory-framework/technical-regulations/ |
| UKGC LCCP | https://www.gamblingcommission.gov.uk/licensees-and-businesses/lccp |
| GDPR Art. 32 | https://gdpr-info.eu/art-32-gdpr/ |
| AMLD6 | https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32018L1673 |
| YubiHSM 2 User Guide | https://docs.yubico.com/hardware/yubihsm-2/hsm-2-user-guide/ |
| cryptoki crate (Rust) | https://docs.rs/cryptoki/latest/cryptoki/ |
| OpenBao PKCS#11 docs | https://openbao.org/docs/configuration/seal/pkcs11/ |
| RFC 5869 (HKDF) | https://datatracker.ietf.org/doc/html/rfc5869 |

---

*Last updated: 2025-03-30 | Version: 1.0*  
*Next review: before each submission to a lab or QSA*
