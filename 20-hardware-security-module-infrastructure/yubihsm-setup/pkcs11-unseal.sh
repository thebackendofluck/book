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

# pkcs11-unseal.sh — Configure and test YubiHSM PKCS#11 auto-unseal for OpenBao.
#
# This script provisions the YubiHSM 2 for use as a PKCS#11 seal mechanism with
# OpenBao, working around the CKM_AES_GCM compatibility gap (firmware 2.4.x does
# not expose CKM_AES_GCM; use CKM_AES_CBC_PAD instead).
#
# Prerequisites:
#   - yubihsm-shell installed (/usr/bin/yubihsm-shell)
#   - yubihsm2-pkcs11.so installed (/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm2-pkcs11.so)
#   - openbao installed and configured
#   - YubiHSM 2 connected via USB
#
# Usage:
#   sudo ./pkcs11-unseal.sh [--provision] [--test] [--configure-bao]
#
# Options:
#   --provision     Generate AES wrapping key on YubiHSM, export key ID
#   --test          Verify PKCS#11 connectivity and list available mechanisms
#   --configure-bao Write openbao.hcl seal stanza and restart service
#
# Security:
#   - Run as root; script will refuse to run as non-root
#   - Key material never leaves the HSM
#   - All operations logged to /var/log/yubihsm-pkcs11-setup.log
#
# References:
#   Chapter 20 — Hardware Security Module Infrastructure
#   YubiHSM 2 PKCS#11 Guide: https://docs.yubico.com/hardware/yubihsm-2/

set -euo pipefail

readonly SCRIPT_NAME="pkcs11-unseal"
readonly LOG_FILE="/var/log/yubihsm-pkcs11-setup.log"
readonly PKCS11_LIB="/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm2-pkcs11.so"
readonly BAO_CONFIG="/etc/openbao/openbao.hcl"
readonly HSM_AUTHKEY_ID="0x0001"          # default admin auth key
readonly HSM_SEAL_KEY_LABEL="openbao-seal"
readonly HSM_SLOT="0"

# ── Logging ──────────────────────────────────────────────────────────────────
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [$SCRIPT_NAME] $*" | tee -a "$LOG_FILE"; }
die() { log "ERROR: $*"; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must run as root"

# ── Dependency checks ─────────────────────────────────────────────────────────
check_dependencies() {
    local missing=()
    for cmd in yubihsm-shell pkcs11-tool openssl bao; do
        command -v "$cmd" &>/dev/null || missing+=("$cmd")
    done
    [[ ${#missing[@]} -eq 0 ]] || die "Missing tools: ${missing[*]}"
    [[ -f "$PKCS11_LIB" ]] || die "PKCS#11 library not found: $PKCS11_LIB"
    log "Dependency check passed"
}

# ── Provision: generate AES-256 wrapping key on YubiHSM ──────────────────────
provision_seal_key() {
    log "Provisioning AES-256 seal key on YubiHSM..."

    # Read HSM auth password from environment or prompt
    local hsm_password="${YUBIHSM_PASSWORD:-}"
    if [[ -z "$hsm_password" ]]; then
        read -rsp "YubiHSM admin password: " hsm_password; echo
    fi

    # Generate AES-256 key with wrap-data + unwrap-data capabilities
    local key_id
    key_id=$(yubihsm-shell \
        --authkey "$HSM_AUTHKEY_ID" \
        --password "$hsm_password" \
        --algorithm aes256 \
        --capabilities "wrap-data,unwrap-data,export-wrapped" \
        --label "$HSM_SEAL_KEY_LABEL" \
        --action generate-symmetric-key \
        2>&1 | grep -oE 'key: 0x[0-9a-f]+' | head -1 | awk '{print $2}')

    [[ -n "$key_id" ]] || die "Failed to generate seal key"
    log "Seal key provisioned: $key_id (label: $HSM_SEAL_KEY_LABEL)"
    echo "HSM_SEAL_KEY_ID=$key_id" >> /etc/openbao/pkcs11-seal.env
    chmod 600 /etc/openbao/pkcs11-seal.env
}

# ── Test PKCS#11 connectivity and list mechanisms ─────────────────────────────
test_pkcs11() {
    log "Testing PKCS#11 connectivity..."

    # List mechanisms — CKM_AES_CBC_PAD (0x0065) must be present
    log "Available mechanisms:"
    pkcs11-tool \
        --module "$PKCS11_LIB" \
        --list-mechanisms \
        --slot "$HSM_SLOT" \
        2>&1 | tee -a "$LOG_FILE"

    # Check for required mechanism
    if pkcs11-tool --module "$PKCS11_LIB" --list-mechanisms --slot "$HSM_SLOT" 2>&1 \
            | grep -q "AES-CBC-PAD"; then
        log "PASS: CKM_AES_CBC_PAD is available — suitable for OpenBao seal"
    else
        log "WARN: CKM_AES_CBC_PAD not found. Confirm YubiHSM firmware >= 2.4.0"
    fi

    # Note firmware limitation
    log "NOTE: CKM_AES_GCM is NOT supported by YubiHSM 2 firmware 2.x via PKCS#11"
    log "      OpenBao must be configured with mechanism = CKM_AES_CBC_PAD"
    log "      See: scripts/chapter-20/yubihsm-setup/openbao.hcl"

    log "PKCS#11 connectivity test complete"
}

# ── Write OpenBao seal stanza ──────────────────────────────────────────────────
configure_openbao() {
    log "Writing OpenBao PKCS#11 seal stanza to $BAO_CONFIG..."

    [[ -f "$BAO_CONFIG" ]] || die "OpenBao config not found: $BAO_CONFIG"

    # Source the key ID provisioned earlier
    local seal_key_id=""
    if [[ -f /etc/openbao/pkcs11-seal.env ]]; then
        # shellcheck source=/dev/null
        source /etc/openbao/pkcs11-seal.env
        seal_key_id="${HSM_SEAL_KEY_ID:-}"
    fi
    [[ -n "$seal_key_id" ]] || die "No seal key ID found; run --provision first"

    # Append seal block if not already present
    if ! grep -q "pkcs11" "$BAO_CONFIG"; then
        cat >> "$BAO_CONFIG" <<EOF

seal "pkcs11" {
  lib            = "$PKCS11_LIB"
  slot           = "$HSM_SLOT"
  key_label      = "$HSM_SEAL_KEY_LABEL"
  mechanism      = "0x0065"   # CKM_AES_CBC_PAD — YubiHSM 2 compatible
  # Note: CKM_AES_GCM (0x1087) is NOT supported by YubiHSM 2 firmware 2.x
  pin            = "\${YUBIHSM_PIN}"
}
EOF
        log "Seal stanza written"
    else
        log "Seal stanza already present in $BAO_CONFIG — skipping"
    fi

    log "Reloading OpenBao..."
    systemctl reload-or-restart openbao.service || log "WARN: could not reload openbao"
}

# ── Smoke test: seal-wrap round-trip ─────────────────────────────────────────
smoke_test() {
    log "Running seal-wrap smoke test..."

    local test_plain
    test_plain="HSMSEALTEST-$(date +%s)"
    local encrypted decrypted

    encrypted=$(echo "$test_plain" | \
        pkcs11-tool --module "$PKCS11_LIB" \
            --encrypt --mechanism AES-CBC-PAD \
            --id "$(cat /etc/openbao/pkcs11-seal.env | grep HSM_SEAL_KEY_ID | cut -d= -f2)" \
            --slot "$HSM_SLOT" 2>/dev/null | base64)

    decrypted=$(echo "$encrypted" | base64 -d | \
        pkcs11-tool --module "$PKCS11_LIB" \
            --decrypt --mechanism AES-CBC-PAD \
            --id "$(cat /etc/openbao/pkcs11-seal.env | grep HSM_SEAL_KEY_ID | cut -d= -f2)" \
            --slot "$HSM_SLOT" 2>/dev/null)

    if [[ "$decrypted" == "$test_plain" ]]; then
        log "PASS: Seal-wrap smoke test succeeded"
    else
        log "FAIL: Decrypted output does not match plaintext"
        return 1
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    log "Starting YubiHSM PKCS#11 unseal setup"
    check_dependencies

    local do_provision=false do_test=false do_configure=false

    for arg in "$@"; do
        case "$arg" in
            --provision)   do_provision=true ;;
            --test)        do_test=true ;;
            --configure-bao) do_configure=true ;;
            *) die "Unknown option: $arg" ;;
        esac
    done

    # Default: run all steps
    if ! $do_provision && ! $do_test && ! $do_configure; then
        do_provision=true; do_test=true; do_configure=true
    fi

    $do_provision   && provision_seal_key
    $do_test        && test_pkcs11
    $do_configure   && configure_openbao

    log "YubiHSM PKCS#11 unseal setup complete"
}

main "$@"
