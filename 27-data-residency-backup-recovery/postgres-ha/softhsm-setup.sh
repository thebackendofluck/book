#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# SoftHSM2 setup for PostgreSQL HA encryption key management
# Simulates YubiHSM/hardware HSM via PKCS#11 (SoftHSM2).
# In production: replace softhsm2 with actual YubiHSM2 SDK.
#
# Usage (run on ops-host host or inside a container):
#   sudo ./softhsm-setup.sh [--init | --fetch | --rotate]

set -euo pipefail

TOKEN_LABEL="casino-db"
SO_PIN="${SO_PIN:?export SO_PIN}"
USER_PIN="${USER_PIN:?export USER_PIN}"
KEY_LABEL="pg-tde-master-key"
SOFTHSM_CONF="${HOME}/.config/softhsm2/softhsm2.conf"
SOFTHSM_DB="${HOME}/.local/share/softhsm2/tokens"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${YELLOW}[HSM]${NC} $*"; }
pass()  { echo -e "${GREEN}[OK]${NC} $*"; }
fail()  { echo -e "${RED}[ERR]${NC} $*"; exit 1; }

# ─── Install SoftHSM2 ──────────────────────────────────────────────────────
install_softhsm() {
    if command -v softhsm2-util &>/dev/null; then
        pass "softhsm2 already installed: $(softhsm2-util --version 2>&1 | head -1)"
        return
    fi
    info "Installing softhsm2..."
    if command -v apt-get &>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y softhsm2 opensc gnutls-bin libgnutls-openssl27
    elif command -v yum &>/dev/null; then
        sudo yum install -y softhsm opensc
    else
        fail "Unsupported package manager. Install softhsm2 manually."
    fi
    pass "softhsm2 installed"
}

# ─── Configure SoftHSM2 ───────────────────────────────────────────────────
configure_softhsm() {
    mkdir -p "$(dirname $SOFTHSM_CONF)" "$SOFTHSM_DB"
    if [ ! -f "$SOFTHSM_CONF" ]; then
        cat > "$SOFTHSM_CONF" <<EOF
# SoftHSM2 config — casino-db token
directories.tokendir = ${SOFTHSM_DB}
objectstore.backend = file
log.level = INFO
slots.removable = false
EOF
        pass "SoftHSM2 config written to $SOFTHSM_CONF"
    else
        info "SoftHSM2 config already exists at $SOFTHSM_CONF"
    fi
    export SOFTHSM2_CONF="$SOFTHSM_CONF"
}

# ─── Initialise token ─────────────────────────────────────────────────────
init_token() {
    export SOFTHSM2_CONF="$SOFTHSM_CONF"
    EXISTING=$(softhsm2-util --show-slots 2>/dev/null | grep -c "$TOKEN_LABEL" || true)
    if [ "$EXISTING" -gt 0 ]; then
        info "Token '$TOKEN_LABEL' already initialised."
        return
    fi
    info "Initialising token: $TOKEN_LABEL"
    softhsm2-util --init-token \
        --free \
        --label "$TOKEN_LABEL" \
        --so-pin "$SO_PIN" \
        --pin "$USER_PIN"
    pass "Token '$TOKEN_LABEL' initialised"
}

# ─── Generate AES-256 key for PostgreSQL TDE ──────────────────────────────
generate_key() {
    export SOFTHSM2_CONF="$SOFTHSM_CONF"
    SLOT=$(softhsm2-util --show-slots 2>/dev/null | grep -B5 "$TOKEN_LABEL" | grep '^Slot' | awk '{print $2}' | head -1)
    if [ -z "$SLOT" ]; then
        fail "Could not find slot for token '$TOKEN_LABEL'"
    fi
    info "Generating AES-256 master key in slot $SLOT..."

    # Use pkcs11-tool to generate an AES key stored in the HSM
    if command -v pkcs11-tool &>/dev/null; then
        pkcs11-tool \
            --module /usr/lib/softhsm/libsofthsm2.so \
            --login --pin "$USER_PIN" \
            --slot "$SLOT" \
            --keygen \
            --key-type AES:32 \
            --label "$KEY_LABEL" \
            --id 01 \
            --usage-decrypt --usage-encrypt 2>&1 || true
        pass "AES-256 key '$KEY_LABEL' generated in token"
    else
        info "pkcs11-tool not available — generating key via openssl and wrapping it"
        KEY_HEX=$(openssl rand -hex 32)
        echo "$KEY_HEX" > /tmp/pg-tde-key.hex
        chmod 600 /tmp/pg-tde-key.hex
        pass "AES-256 key stored at /tmp/pg-tde-key.hex (development only)"
        info "In production: use pkcs11-tool to store key inside HSM, never in filesystem"
    fi
}

# ─── Fetch key (simulate what PostgreSQL would do at startup) ─────────────
fetch_key() {
    export SOFTHSM2_CONF="$SOFTHSM_CONF"
    info "Fetching encryption key from SoftHSM2 token '$TOKEN_LABEL'..."

    SLOT=$(softhsm2-util --show-slots 2>/dev/null | grep -B5 "$TOKEN_LABEL" | grep '^Slot' | awk '{print $2}' | head -1)
    if [ -z "$SLOT" ]; then
        fail "Token not initialised. Run: $0 --init"
    fi

    if command -v pkcs11-tool &>/dev/null; then
        pkcs11-tool \
            --module /usr/lib/softhsm/libsofthsm2.so \
            --login --pin "$USER_PIN" \
            --slot "$SLOT" \
            --list-objects 2>&1 | grep -A3 "$KEY_LABEL"
        pass "Key '$KEY_LABEL' found in HSM slot $SLOT — PostgreSQL can fetch via PKCS#11"
    else
        if [ -f /tmp/pg-tde-key.hex ]; then
            pass "Key available at /tmp/pg-tde-key.hex (development fallback)"
        else
            fail "Key not found. Run: $0 --init first"
        fi
    fi
}

# ─── Show token info ──────────────────────────────────────────────────────
show_info() {
    export SOFTHSM2_CONF="$SOFTHSM_CONF"
    info "=== SoftHSM2 Token Status ==="
    softhsm2-util --show-slots 2>&1
    echo ""
    info "=== PKCS#11 module ==="
    ls -la /usr/lib/softhsm/libsofthsm2.so 2>/dev/null || echo "libsofthsm2.so not found in /usr/lib/softhsm"
    echo ""
    info "=== PostgreSQL PKCS#11 integration note ==="
    cat <<'EOF'
To use this HSM with PostgreSQL for TDE (Transparent Data Encryption):

1. With pg_tde extension (Percona, PostgreSQL 17+):
   LOAD 'pg_tde';
   SELECT pg_tde_add_key_provider_file('hsm-pkcs11','/tmp/pg-tde-key.hex');
   SELECT pg_tde_set_principal_key('casino-master-key','hsm-pkcs11');

2. With pgcrypto + PKCS#11 wrapper (PostgreSQL 16):
   -- Store encrypted column data using pgp_sym_encrypt with key from HSM
   SELECT pgp_sym_encrypt(sensitive_data, pg_read_binary_file('/run/secrets/pg_key')::text)
   FROM player_transactions;

3. In production with real YubiHSM2:
   - Replace libsofthsm2.so with yubihsm_pkcs11.so
   - Same PKCS#11 API — no application code changes needed
EOF
}

# ─── Main ─────────────────────────────────────────────────────────────────
MODE="${1:---info}"
install_softhsm
configure_softhsm

case "$MODE" in
    --init)   init_token; generate_key; show_info ;;
    --fetch)  fetch_key ;;
    --rotate)
        info "Key rotation: generating new AES-256 key..."
        generate_key
        pass "Key rotated. Re-encrypt table data with new key."
        ;;
    --info|*) show_info ;;
esac
