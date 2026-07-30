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

# setup-luks-encryption.sh
# Configure LUKS2 AES-XTS-512 on data volumes with OpenBao Transit key wrapping.
# The LUKS plaintext key NEVER touches disk — only the ciphertext is stored.
# Auto-unlock script is embedded into initramfs for boot-time unlock.
#
# Usage (run on each VM that has a data volume to encrypt):
#   VM_NAME=vm-db-01 LUKS_DEV=/dev/vdb BAO_ADDR=https://bao-01:8200 \
#     ROLE_ID=<role-id> SECRET_ID=<secret-id> bash setup-luks-encryption.sh
#
# Compliance: PCI DSS Req. 3 (AES-256), GDPR Art. 32, ISO 27001 A.10.1

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
VM_NAME="${VM_NAME:-vm-db-01}"
LUKS_DEV="${LUKS_DEV:-/dev/vdb}"
MAPPER_NAME="${MAPPER_NAME:-data_crypt}"
BAO_ADDR="${BAO_ADDR:-https://bao-01:8200}"
BAO_CA="${BAO_CA:-/etc/openbao/ca.crt}"
ROLE_ID="${ROLE_ID:-}"
SECRET_ID_FILE="${SECRET_ID_FILE:-/etc/openbao/secret-id}"
UNLOCK_SCRIPT="/usr/local/sbin/bao-luks-unlock"
LOG_FILE="/var/log/luks-setup.log"

# ── Logging helpers ────────────────────────────────────────────────────────────
log()  { echo "[$(date -Is)] INFO  $*" | tee -a "$LOG_FILE"; }
warn() { echo "[$(date -Is)] WARN  $*" | tee -a "$LOG_FILE"; }
die()  { echo "[$(date -Is)] ERROR $*" | tee -a "$LOG_FILE"; exit 1; }

# Zero-out sensitive variables on exit
cleanup() {
    unset LUKS_KEY WRAPPED TOKEN PLAINTEXT_B64 SECRET_ID
}
trap cleanup EXIT

# ── Preflight ──────────────────────────────────────────────────────────────────
preflight() {
    log "=== Preflight checks ==="
    [[ $EUID -eq 0 ]] || die "Must run as root"
    [[ -n "$VM_NAME" ]]  || die "VM_NAME not set"
    [[ -n "$LUKS_DEV" ]] || die "LUKS_DEV not set"
    [[ -n "$ROLE_ID" ]]  || die "ROLE_ID not set"
    [[ -f "$BAO_CA" ]]   || die "BAO_CA not found: $BAO_CA"

    command -v cryptsetup >/dev/null 2>&1 || apt-get install -y cryptsetup
    command -v curl       >/dev/null 2>&1 || apt-get install -y curl
    command -v python3    >/dev/null 2>&1 || apt-get install -y python3

    # Verify device exists
    [[ -b "$LUKS_DEV" ]] || die "Block device not found: $LUKS_DEV"

    # Warn if device already has LUKS
    if cryptsetup isLuks "$LUKS_DEV" 2>/dev/null; then
        warn "$LUKS_DEV already has a LUKS header — use --re-encrypt to re-provision"
        [[ "${1:-}" == "--re-encrypt" ]] || exit 0
    fi

    # Verify OpenBao connectivity
    curl -sf --cacert "$BAO_CA" --max-time 5 \
        "${BAO_ADDR}/v1/sys/health" >/dev/null 2>&1 \
        || die "OpenBao not reachable at $BAO_ADDR (check BAO_ADDR and BAO_CA)"

    log "All preflight checks passed"
}

# ── Store Secret-ID on disk ───────────────────────────────────────────────────
store_secret_id() {
    log "=== Storing AppRole Secret-ID ==="
    [[ -n "${SECRET_ID:-}" ]] || die "SECRET_ID not set"

    mkdir -p "$(dirname "$SECRET_ID_FILE")"
    printf '%s' "$SECRET_ID" > "$SECRET_ID_FILE"
    chmod 400 "$SECRET_ID_FILE"
    chown root:root "$SECRET_ID_FILE"
    log "Secret-ID stored at $SECRET_ID_FILE (chmod 400)"
}

# ── Provision LUKS2 volume ────────────────────────────────────────────────────
provision_luks() {
    log "=== Provisioning LUKS2 on $LUKS_DEV ==="

    # Step 1: Generate LUKS key in memory only (64 bytes → base64 → ~88 chars)
    LUKS_KEY=$(openssl rand -base64 48)
    log "LUKS key generated in memory (never written to disk)"

    # Step 2: Format with LUKS2 AES-XTS-512 (above PCI DSS AES-128 minimum)
    # AES-XTS-512 = two 256-bit keys for XTS mode — actual encryption: AES-256
    # argon2id PBKDF with 256MB memory — resistant to GPU attacks
    log "Formatting $LUKS_DEV with LUKS2 AES-XTS-512..."
    echo -n "$LUKS_KEY" | cryptsetup luksFormat \
        --type luks2 \
        --cipher aes-xts-plain64 \
        --key-size 512 \
        --hash sha512 \
        --pbkdf argon2id \
        --pbkdf-memory 262144 \
        --pbkdf-parallel 4 \
        --key-file - \
        --batch-mode \
        "$LUKS_DEV"
    log "LUKS2 formatted: $LUKS_DEV"

    # Step 3: Authenticate with OpenBao AppRole
    log "Authenticating with OpenBao AppRole..."
    local secret_id
    secret_id=$(cat "$SECRET_ID_FILE")

    TOKEN=$(curl -sf --cacert "$BAO_CA" \
        --max-time 10 \
        -X POST \
        -H "Content-Type: application/json" \
        -d "{\"role_id\":\"${ROLE_ID}\",\"secret_id\":\"${secret_id}\"}" \
        "${BAO_ADDR}/v1/auth/approle/login" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['auth']['client_token'])") \
        || die "AppRole login failed"
    log "AppRole token obtained (TTL: 5m)"

    # Step 4: Wrap LUKS key via Transit (plaintext must be base64 to Transit API)
    log "Wrapping LUKS key via OpenBao Transit..."
    PLAINTEXT_B64=$(echo -n "$LUKS_KEY" | base64 -w0)
    WRAPPED=$(curl -sf --cacert "$BAO_CA" \
        --max-time 10 \
        -H "X-Vault-Token: $TOKEN" \
        -H "Content-Type: application/json" \
        -X POST \
        -d "{\"plaintext\":\"${PLAINTEXT_B64}\"}" \
        "${BAO_ADDR}/v1/transit/encrypt/${VM_NAME}" \
        | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['ciphertext'])") \
        || die "Transit encrypt failed"
    log "LUKS key wrapped by OpenBao Transit (ciphertext: ${WRAPPED:0:30}...)"

    # Step 5: Store ciphertext as LUKS2 token in disk header (safe to store)
    log "Storing ciphertext in LUKS2 header token..."
    local token_json
    token_json="{\"type\":\"openbao-transit\",\"vm\":\"${VM_NAME}\",\"bao_addr\":\"${BAO_ADDR}\",\"ciphertext\":\"${WRAPPED}\"}"
    echo "$token_json" | cryptsetup token import "$LUKS_DEV" --token-id 0
    log "Ciphertext stored in LUKS2 header (token slot 0)"

    # Step 6: Revoke token immediately — VM retains no OpenBao credential
    curl -sf --cacert "$BAO_CA" \
        -H "X-Vault-Token: $TOKEN" \
        -X POST \
        "${BAO_ADDR}/v1/auth/token/revoke-self" >/dev/null 2>&1 || true
    log "AppRole token revoked"

    # Step 7: Clear plaintext from memory (bash variables)
    unset LUKS_KEY WRAPPED TOKEN PLAINTEXT_B64
    log "Plaintext key zeroed from memory"

    # Verify LUKS header
    log "LUKS2 header dump:"
    cryptsetup luksDump "$LUKS_DEV" | grep -E "Version|Cipher|UUID|Tokens" | tee -a "$LOG_FILE"
    log "Token slot 0 content:"
    cryptsetup token export --token-id 0 "$LUKS_DEV" | python3 -m json.tool 2>/dev/null | tee -a "$LOG_FILE" || true
}

# ── Install auto-unlock script ────────────────────────────────────────────────
install_unlock_script() {
    log "=== Installing LUKS unlock script ==="

    cat > "$UNLOCK_SCRIPT" << 'SCRIPT_EOF'
#!/bin/bash
# bao-luks-unlock — embedded in initramfs
# Runs before pivot_root to unlock LUKS volume via OpenBao Transit
set -euo pipefail

# ── Hardcoded per-VM config (set during provisioning) ────────────────────────
BAO_ADDR="${BAO_ADDR:-https://bao-01:8200}"
BAO_CA="/etc/openbao/ca.crt"
ROLE_ID_FILE="/etc/openbao/role-id"
SECRET_ID_FILE="/etc/openbao/secret-id"
TRANSIT_KEY="${VM_NAME:-vm-db-01}"
LUKS_DEV="${LUKS_DEV:-/dev/vdb}"
MAPPER_NAME="${MAPPER_NAME:-data_crypt}"
MAX_RETRIES=10
RETRY_DELAY=3

log() { echo "[bao-unlock $(date +%T)] $*" >&2; }
die() { log "FATAL: $*"; exit 1; }

cleanup() { unset PLAINTEXT TOKEN CIPHERTEXT; }
trap cleanup EXIT

# ── Wait for network ──────────────────────────────────────────────────────────
log "Waiting for OpenBao connectivity..."
for i in $(seq 1 $MAX_RETRIES); do
    if curl -sf --cacert "$BAO_CA" --max-time 3 \
            "${BAO_ADDR}/v1/sys/health" >/dev/null 2>&1; then
        log "OpenBao reachable"
        break
    fi
    [[ $i -eq $MAX_RETRIES ]] && die "OpenBao unreachable after ${MAX_RETRIES} retries"
    log "Retry $i/$MAX_RETRIES — waiting ${RETRY_DELAY}s..."
    sleep $RETRY_DELAY
done

# ── Read ciphertext from LUKS2 token header ───────────────────────────────────
log "Reading ciphertext from LUKS2 header..."
CIPHERTEXT=$(cryptsetup token export --token-id 0 "$LUKS_DEV" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['ciphertext'])") \
    || die "Failed to read LUKS token"

# ── AppRole login ─────────────────────────────────────────────────────────────
log "Authenticating with OpenBao AppRole..."
ROLE_ID=$(cat "$ROLE_ID_FILE")  || die "Cannot read role-id"
SECRET_ID=$(cat "$SECRET_ID_FILE") || die "Cannot read secret-id"

TOKEN=$(curl -sf --cacert "$BAO_CA" \
    --max-time 10 \
    -H "Content-Type: application/json" \
    -X POST \
    -d "{\"role_id\":\"${ROLE_ID}\",\"secret_id\":\"${SECRET_ID}\"}" \
    "${BAO_ADDR}/v1/auth/approle/login" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['auth']['client_token'])") \
    || die "AppRole login failed"
log "Token obtained (TTL 5m)"

# ── Decrypt LUKS key via Transit ──────────────────────────────────────────────
log "Decrypting LUKS key via Transit..."
PLAINTEXT=$(curl -sf --cacert "$BAO_CA" \
    --max-time 10 \
    -H "X-Vault-Token: $TOKEN" \
    -H "Content-Type: application/json" \
    -X POST \
    -d "{\"ciphertext\":\"${CIPHERTEXT}\"}" \
    "${BAO_ADDR}/v1/transit/decrypt/${TRANSIT_KEY}" \
    | python3 -c "
import sys, json, base64
d = json.load(sys.stdin)
print(base64.b64decode(d['data']['plaintext']).decode())
") || die "Transit decrypt failed"

# ── Open LUKS device ──────────────────────────────────────────────────────────
log "Opening LUKS device $LUKS_DEV -> /dev/mapper/$MAPPER_NAME ..."
echo -n "$PLAINTEXT" | cryptsetup luksOpen \
    --key-file - \
    "$LUKS_DEV" "$MAPPER_NAME" \
    || die "cryptsetup luksOpen failed"

log "LUKS opened: /dev/mapper/$MAPPER_NAME"

# ── Revoke token immediately ──────────────────────────────────────────────────
curl -sf --cacert "$BAO_CA" \
    -H "X-Vault-Token: $TOKEN" \
    -X POST \
    "${BAO_ADDR}/v1/auth/token/revoke-self" >/dev/null 2>&1 || true

# Zero sensitive vars
unset PLAINTEXT TOKEN CIPHERTEXT ROLE_ID SECRET_ID
log "Unlock complete — credentials zeroed from memory"
SCRIPT_EOF

    chmod 755 "$UNLOCK_SCRIPT"
    log "Unlock script installed: $UNLOCK_SCRIPT"
}

# ── Embed config into initramfs ───────────────────────────────────────────────
configure_initramfs() {
    log "=== Configuring initramfs ==="

    # Store role-id (not secret, can be on disk)
    mkdir -p /etc/openbao
    printf '%s' "$ROLE_ID" > /etc/openbao/role-id
    chmod 444 /etc/openbao/role-id

    # Copy CA cert
    cp "$BAO_CA" /etc/openbao/ca.crt
    chmod 444 /etc/openbao/ca.crt

    # Create initramfs hook (Debian/Ubuntu mkinitramfs)
    mkdir -p /etc/initramfs-tools/hooks
    cat > /etc/initramfs-tools/hooks/openbao-luks << HOOK_EOF
#!/bin/sh
# initramfs hook: embed bao-luks-unlock and dependencies
PREREQ=""
prereqs() { echo "\$PREREQ"; }
case \$1 in prereqs) prereqs; exit 0;; esac

. /usr/share/initramfs-tools/hook-functions

# Embed unlock script
copy_exec /usr/local/sbin/bao-luks-unlock /usr/local/sbin/bao-luks-unlock

# Embed configs
copy_file config /etc/openbao/ca.crt
copy_file config /etc/openbao/role-id
copy_file config /etc/openbao/secret-id

# Embed required binaries
copy_exec /usr/bin/curl
copy_exec /usr/bin/python3

# Embed cryptsetup
copy_exec /sbin/cryptsetup
copy_exec /sbin/dmsetup
HOOK_EOF
    chmod 755 /etc/initramfs-tools/hooks/openbao-luks

    # Create initramfs script to run at premount (before filesystems mount)
    mkdir -p /etc/initramfs-tools/scripts/local-premount
    cat > /etc/initramfs-tools/scripts/local-premount/openbao-luks << SCRIPT2_EOF
#!/bin/sh
# initramfs premount script: run bao-luks-unlock before mounting filesystems
PREREQ="networking"
prereqs() { echo "\$PREREQ"; }
case \$1 in prereqs) prereqs; exit 0;; esac

BAO_ADDR="${BAO_ADDR}" VM_NAME="${VM_NAME}" LUKS_DEV="${LUKS_DEV}" \
    MAPPER_NAME="${MAPPER_NAME}" /usr/local/sbin/bao-luks-unlock \
    || echo "[bao-unlock] WARNING: unlock failed — manual intervention required"
SCRIPT2_EOF
    chmod 755 /etc/initramfs-tools/scripts/local-premount/openbao-luks

    # Also create dracut hook for RHEL/Rocky/Fedora systems
    if command -v dracut &>/dev/null; then
        mkdir -p /etc/dracut.conf.d
        cat > /etc/dracut.conf.d/openbao-luks.conf << DRACUT_EOF
install_items+=" ${UNLOCK_SCRIPT} "
install_items+=" /etc/openbao/ca.crt "
install_items+=" /etc/openbao/role-id "
install_items+=" /etc/openbao/secret-id "
install_items+=" /usr/bin/curl "
install_items+=" /usr/bin/python3 "
install_items+=" /sbin/cryptsetup "
add_dracut_modules+=" network "
add_drivers+=" virtio_net e1000e virtio_blk "
DRACUT_EOF
        log "dracut config written: /etc/dracut.conf.d/openbao-luks.conf"
    fi

    # Configure crypttab to use _netdev (wait for network before mount)
    local uuid
    uuid=$(blkid -s UUID -o value "$LUKS_DEV" 2>/dev/null || echo "")
    if [[ -n "$uuid" ]]; then
        if ! grep -q "$MAPPER_NAME" /etc/crypttab 2>/dev/null; then
            echo "${MAPPER_NAME} UUID=${uuid} none luks,_netdev,nofail" \
                | tee -a /etc/crypttab
            log "Added to /etc/crypttab: ${MAPPER_NAME} UUID=${uuid}"
        fi
    fi

    # Configure fstab
    if ! grep -q "/dev/mapper/${MAPPER_NAME}" /etc/fstab 2>/dev/null; then
        echo "/dev/mapper/${MAPPER_NAME} /data ext4 defaults,_netdev,nofail 0 2" \
            | tee -a /etc/fstab
        mkdir -p /data
        log "Added to /etc/fstab: /dev/mapper/${MAPPER_NAME} -> /data"
    fi

    # Regenerate initramfs
    log "Regenerating initramfs..."
    if command -v update-initramfs &>/dev/null; then
        update-initramfs -u -k "$(uname -r)"
        log "initramfs updated (Debian/Ubuntu)"
    elif command -v dracut &>/dev/null; then
        dracut --force --verbose "/boot/initramfs-$(uname -r).img" "$(uname -r)"
        log "initramfs updated (dracut)"
    else
        warn "Could not update initramfs — do it manually"
    fi
}

# ── Verify encryption ─────────────────────────────────────────────────────────
verify_encryption() {
    log "=== Verifying encryption setup ==="

    # Verify LUKS header exists
    cryptsetup isLuks "$LUKS_DEV" || die "$LUKS_DEV is not a LUKS device"
    log "LUKS header verified on $LUKS_DEV"

    # Verify cipher strength
    local cipher
    cipher=$(cryptsetup luksDump "$LUKS_DEV" | grep "Cipher:" | awk '{print $2}')
    log "Cipher: $cipher"
    echo "$cipher" | grep -q "aes-xts" || warn "Unexpected cipher: $cipher (expected aes-xts-plain64)"

    # Verify key size (512 = 256-bit effective for XTS)
    local keysize
    keysize=$(cryptsetup luksDump "$LUKS_DEV" | grep -i "MK bits:" | awk '{print $3}')
    log "Key size: ${keysize} bits"
    [[ "$keysize" == "512" ]] || warn "Key size is ${keysize}, expected 512 (AES-XTS-256)"

    # Verify LUKS token exists
    if cryptsetup token export --token-id 0 "$LUKS_DEV" &>/dev/null; then
        log "LUKS2 token slot 0: present (ciphertext stored)"
    else
        warn "LUKS2 token slot 0: not found — provisioning may have failed"
    fi

    log "=== Encryption verification complete ==="
}

# ── Test unlock cycle ─────────────────────────────────────────────────────────
test_unlock_cycle() {
    log "=== Testing unlock cycle ==="

    # Temporarily unlock to verify
    BAO_ADDR="$BAO_ADDR" VM_NAME="$VM_NAME" LUKS_DEV="$LUKS_DEV" \
        MAPPER_NAME="${MAPPER_NAME}-test" \
        "$UNLOCK_SCRIPT" 2>&1 | tee -a "$LOG_FILE" || {
        warn "Unlock test failed — check OpenBao connectivity and AppRole credentials"
        return 1
    }

    # Close the test mapping
    cryptsetup close "${MAPPER_NAME}-test" 2>/dev/null || true
    log "Unlock cycle test: PASSED"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    exec > >(tee -a "$LOG_FILE") 2>&1
    log "=== LUKS Encryption Setup Start (VM: ${VM_NAME}, Dev: ${LUKS_DEV}, $(date)) ==="

    preflight "${1:-}"

    # Read SECRET_ID from env or prompt
    if [[ -z "${SECRET_ID:-}" ]]; then
        [[ -f "$SECRET_ID_FILE" ]] || die "SECRET_ID not set and $SECRET_ID_FILE not found"
    else
        store_secret_id
    fi

    provision_luks
    install_unlock_script
    configure_initramfs
    verify_encryption

    if [[ "${1:-}" == "--test" ]]; then
        test_unlock_cycle
    fi

    log ""
    log "=== LUKS setup complete for ${VM_NAME} ==="
    log "Reboot the VM to verify auto-unlock via initramfs"
}

main "$@"
