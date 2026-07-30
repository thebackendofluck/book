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

# setup-softhsm-ci.sh
# Sets up SoftHSM2 as a YubiHSM substitute for CI/automated testing.
# Installs softhsm2, configures token directory, initialises the CI token,
# and provisions the AES-256 wrap key and ECDSA P-256 audit signing key.
#
# Usage: sudo bash setup-softhsm-ci.sh
# Requirements: Debian/Ubuntu, opensc (pkcs11-tool)

set -euo pipefail

SOFTHSM_LIB="/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so"
TOKEN_DIR="/opt/softhsm2-tokens"
CONF_FILE="/etc/softhsm2.conf"
TOKEN_LABEL="igaming-ci"
TOKEN_PIN="1234"
TOKEN_SO_PIN="12345678"

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# ── 1. Install packages ───────────────────────────────────────────────────────
log "Installing softhsm2 and opensc..."
apt-get install -y softhsm2 opensc

# ── 2. Token directory ────────────────────────────────────────────────────────
log "Creating token directory: ${TOKEN_DIR}"
mkdir -p "${TOKEN_DIR}"

# Allow the calling user (or CI runner) to own the token dir
if [ -n "${SUDO_USER:-}" ]; then
    chown -R "${SUDO_USER}:${SUDO_USER}" "${TOKEN_DIR}"
fi

# ── 3. Write global config ────────────────────────────────────────────────────
log "Writing ${CONF_FILE}"
cat > "${CONF_FILE}" << EOF
directories.tokendir = ${TOKEN_DIR}
objectstore.backend = file
log.level = INFO
EOF

export SOFTHSM2_CONF="${CONF_FILE}"

# ── 4. Initialise token ───────────────────────────────────────────────────────
# Re-init is idempotent if we delete existing token first
log "Initialising token '${TOKEN_LABEL}'..."
if softhsm2-util --show-slots 2>/dev/null | grep -q "${TOKEN_LABEL}"; then
    log "Token already exists; skipping init"
else
    softhsm2-util \
        --init-token --slot 0 \
        --label  "${TOKEN_LABEL}" \
        --pin    "${TOKEN_PIN}" \
        --so-pin "${TOKEN_SO_PIN}"
fi

# ── 5. AES-256 wrap key (YubiHSM wrap key equivalent) ────────────────────────
log "Creating AES-256 key: bao-root-key-aes"
pkcs11-tool --module "${SOFTHSM_LIB}" \
    --token-label "${TOKEN_LABEL}" \
    --login --pin "${TOKEN_PIN}" \
    --keygen \
    --key-type  aes:32 \
    --label     "bao-root-key-aes" \
    --sensitive \
    2>&1 | grep -v "^warning:"

# ── 6. ECDSA P-256 signing keypair ────────────────────────────────────────────
log "Creating ECDSA P-256 keypair: audit-signing-key"
pkcs11-tool --module "${SOFTHSM_LIB}" \
    --token-label "${TOKEN_LABEL}" \
    --login --pin "${TOKEN_PIN}" \
    --keypairgen \
    --key-type EC:prime256v1 \
    --label    "audit-signing-key" \
    2>&1 | grep -v "^warning:"

# ── 7. Verify ─────────────────────────────────────────────────────────────────
log "Listing objects in token..."
pkcs11-tool --module "${SOFTHSM_LIB}" \
    --token-label "${TOKEN_LABEL}" \
    --login --pin "${TOKEN_PIN}" \
    --list-objects \
    2>&1 | grep -v "^warning:"

log "SoftHSM2 CI setup complete."
log "  Config  : ${CONF_FILE}"
log "  Tokens  : ${TOKEN_DIR}"
log "  Library : ${SOFTHSM_LIB}"
log "  Token   : ${TOKEN_LABEL} (PIN: ${TOKEN_PIN})"
