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

# setup-yubihsm-connector.sh
# Install YubiHSM 2 SDK on Ubuntu 24.04, configure yubihsm-connector,
# create AES-256 wrap key (non-exportable), and verify HSM connectivity.
#
# Target: ops-host hypervisor bao-01 node (10.0.0.11)
# Compliance: PCI DSS Req. 3.6/3.7 · FIPS 140-2 Level 3

set -euo pipefail

LOG_FILE="/var/log/yubihsm-setup.log"
PKCS11_LIB="/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so"
CONNECTOR_URL="http://127.0.0.1:12345"
YUBIHSM_CONF="/etc/yubihsm_pkcs11.conf"

# --- Logging helpers -----------------------------------------------------------
log()  { echo "[$(date -Is)] INFO  $*" | tee -a "$LOG_FILE"; }
warn() { echo "[$(date -Is)] WARN  $*" | tee -a "$LOG_FILE"; }
die()  { echo "[$(date -Is)] ERROR $*" | tee -a "$LOG_FILE"; exit 1; }

# --- Idempotency guard ---------------------------------------------------------
check_already_done() {
    if systemctl is-active --quiet yubihsm-connector 2>/dev/null; then
        log "yubihsm-connector already running — checking wrap key..."
        if pkcs11-tool --module "$PKCS11_LIB" \
               --login --pin "${HSM_PIN:-0001password}" \
               --list-objects 2>/dev/null | grep -q "bao-root-key-aes"; then
            log "Wrap key already present. Run with --force to re-provision."
            [[ "${1:-}" == "--force" ]] || exit 0
        fi
    fi
}

# --- Preflight checks ---------------------------------------------------------
preflight() {
    log "=== Preflight checks ==="
    [[ $EUID -eq 0 ]] || die "Must run as root"
    [[ -n "${HSM_PIN:-}" ]] || die "HSM_PIN environment variable not set. Export before running."
    [[ "$HSM_PIN" != "0001password" ]] || \
        warn "Using default YubiHSM PIN — change before production!"

    # Verify YubiHSM 2 is physically connected
    if ! lsusb 2>/dev/null | grep -qi "yubico"; then
        die "YubiHSM 2 not detected via USB. Check hardware connection."
    fi
    log "YubiHSM 2 detected via USB"
}

# --- Install Yubico SDK -------------------------------------------------------
install_sdk() {
    log "=== Installing YubiHSM 2 SDK ==="

    if dpkg -l yubihsm-connector &>/dev/null; then
        log "YubiHSM SDK already installed — skipping apt install"
        return 0
    fi

    # Add Yubico APT repository
    apt-get install -y gnupg curl wget lsb-release 2>/dev/null

    wget -qO- https://developers.yubico.com/Software_Projects/Software_Distribution/yubihsm2-sdk.asc \
        | gpg --dearmor \
        | tee /etc/apt/trusted.gpg.d/yubico.gpg > /dev/null

    # Ubuntu 24.04 (noble) falls back to bookworm-compatible packages
    local CODENAME
    CODENAME=$(lsb_release -cs 2>/dev/null || echo "bookworm")
    # Map ubuntu codenames to debian equivalents for Yubico repo
    case "$CODENAME" in
        noble|jammy|focal) CODENAME="bookworm" ;;
    esac

    echo "deb https://developers.yubico.com/Software_Projects/Software_Distribution/debian ${CODENAME} main" \
        | tee /etc/apt/sources.list.d/yubico.list

    apt-get update -qq
    apt-get install -y yubihsm-connector yubihsm-pkcs11 opensc yubihsm-shell
    log "YubiHSM SDK installed successfully"
}

# --- Write PKCS#11 config -----------------------------------------------------
write_pkcs11_conf() {
    log "=== Writing PKCS#11 configuration ==="
    if [[ -f "$YUBIHSM_CONF" ]]; then
        log "$YUBIHSM_CONF already exists — backing up"
        cp "$YUBIHSM_CONF" "${YUBIHSM_CONF}.bak.$(date +%s)"
    fi

    cat > "$YUBIHSM_CONF" << 'EOF'
# YubiHSM PKCS#11 configuration
# connector: yubihsm-connector listens on localhost only (PCI DSS HSM zone)
connector = http://127.0.0.1:12345

# debug_log: set to 1 for verbose PKCS#11 debugging (disable in production)
debug_log = 0

# cacert: TLS CA cert for HTTPS connector (optional, leave empty for HTTP localhost)
# cacert =

# cert / key: mutual TLS to connector (optional)
# cert =
# key =
EOF
    chmod 644 "$YUBIHSM_CONF"
    log "Written $YUBIHSM_CONF"
}

# --- Configure and start connector service ------------------------------------
configure_connector() {
    log "=== Configuring yubihsm-connector service ==="

    # Write connector config (bind localhost only — HSM zone is air-gapped)
    mkdir -p /etc/yubihsm-connector
    cat > /etc/yubihsm-connector/yubihsm-connector.conf << 'EOF'
# yubihsm-connector config
# Listen only on localhost — connector must NOT be reachable from outside
# PCI DSS Req. 1: HSM zone network isolation
listen = 127.0.0.1:12345

# Log to syslog for audit trail
log = syslog

# Device serial (leave empty to use first detected device)
# serial =
EOF

    # Override systemd unit to restrict to localhost
    mkdir -p /etc/systemd/system/yubihsm-connector.service.d
    cat > /etc/systemd/system/yubihsm-connector.service.d/override.conf << 'EOF'
[Service]
# Hardening
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
NoNewPrivileges=yes
RestrictAddressFamilies=AF_INET AF_UNIX
# PCI DSS: service runs as dedicated user
User=yubihsm
Group=yubihsm
EOF

    # Create dedicated service user if not present
    if ! id -u yubihsm &>/dev/null; then
        useradd --system --no-create-home --shell /usr/sbin/nologin yubihsm
        log "Created yubihsm system user"
    fi

    # Add yubihsm user to plugdev for USB access
    usermod -aG plugdev yubihsm 2>/dev/null || true

    systemctl daemon-reload
    systemctl enable --now yubihsm-connector

    # Wait up to 10s for connector to be ready
    local i=0
    until curl -sf "${CONNECTOR_URL}/connector/status" | grep -q '"status":"OK"' 2>/dev/null; do
        sleep 1
        (( i++ ))
        [[ $i -lt 10 ]] || die "yubihsm-connector did not start within 10s"
    done
    log "yubihsm-connector is running and responding at ${CONNECTOR_URL}"
}

# --- Create AES-256 wrap key --------------------------------------------------
create_wrap_key() {
    log "=== Creating AES-256 wrap key in YubiHSM 2 ==="
    log "WARNING: This wrap key is the root of trust. It is NEVER extractable."
    log "         Perform this step ONCE. Destroying it requires physical HSM destruction."

    # Check if key already exists
    if pkcs11-tool --module "$PKCS11_LIB" \
           --login --pin "${HSM_PIN}" \
           --list-objects 2>/dev/null | grep -q "bao-root-key-aes"; then
        log "Wrap key 'bao-root-key-aes' already exists — skipping creation"
        return 0
    fi

    # Generate AES-256 (32 bytes) wrap key — non-exportable, usage: wrap
    # slot 0 = first HSM, ID 01, label bao-root-key-aes
    pkcs11-tool \
        --module "$PKCS11_LIB" \
        --login \
        --pin "${HSM_PIN}" \
        --keygen \
        --key-type aes:32 \
        --label "bao-root-key-aes" \
        --id 01 \
        -y secrkey \
        --sensitive \
        --usage-wrap

    log "AES-256 wrap key 'bao-root-key-aes' created successfully"

    # Create ECDSA P-256 signing key for audit chain and JWT endorsement
    if ! pkcs11-tool --module "$PKCS11_LIB" \
           --login --pin "${HSM_PIN}" \
           --list-objects 2>/dev/null | grep -q "session-endorsement-key"; then
        pkcs11-tool \
            --module "$PKCS11_LIB" \
            --login \
            --pin "${HSM_PIN}" \
            --keypairgen \
            --key-type EC:prime256v1 \
            --label "session-endorsement-key" \
            --id 02 \
            --sensitive
        log "ECDSA P-256 session endorsement key created"
    fi

    # Create Ed25519 signing key for JWT signing (if supported by firmware)
    if ! pkcs11-tool --module "$PKCS11_LIB" \
           --login --pin "${HSM_PIN}" \
           --list-objects 2>/dev/null | grep -q "jwt-signing-key"; then
        pkcs11-tool \
            --module "$PKCS11_LIB" \
            --login \
            --pin "${HSM_PIN}" \
            --keypairgen \
            --key-type EC:ed25519 \
            --label "jwt-signing-key" \
            --id 03 \
            --sensitive 2>/dev/null || \
        log "Ed25519 not supported in this firmware version — ECDSA P-256 will be used for JWT"
    fi
}

# --- Verify connectivity and key attributes -----------------------------------
verify_setup() {
    log "=== Verifying HSM setup ==="

    # List all objects
    log "Objects in YubiHSM 2:"
    pkcs11-tool \
        --module "$PKCS11_LIB" \
        --login \
        --pin "${HSM_PIN}" \
        --list-objects 2>&1 | tee -a "$LOG_FILE"

    # Verify wrap key is non-extractable (critical security property)
    local key_attrs
    key_attrs=$(pkcs11-tool \
        --module "$PKCS11_LIB" \
        --login \
        --pin "${HSM_PIN}" \
        --read-object --type secrkey --label "bao-root-key-aes" 2>&1 || true)

    if echo "$key_attrs" | grep -qi "CKA_EXTRACTABLE.*false\|never extractable"; then
        log "VERIFIED: bao-root-key-aes is non-extractable"
    else
        log "NOTE: Verify key extractability attribute in HSM management console"
    fi

    # Test TRNG (random number generation)
    log "Testing TRNG — generating 32 bytes:"
    pkcs11-tool \
        --module "$PKCS11_LIB" \
        --login \
        --pin "${HSM_PIN}" \
        --generate-random 32 2>/dev/null | xxd | tee -a "$LOG_FILE" \
        || warn "TRNG test failed — check HSM connectivity"

    log "=== YubiHSM 2 setup verification complete ==="
}

# --- Security hardening reminder ----------------------------------------------
print_security_reminders() {
    cat << 'EOF'

==========================================================================
SECURITY REMINDERS — Complete before production deployment
==========================================================================
1. CHANGE THE DEFAULT PIN:
   yubihsm-shell --connector http://127.0.0.1:12345 \
     --action change-authentication-key \
     --authkey 1 \
     --new-password <new-pin>

2. Store the YubiHSM device serial number in your asset register.

3. Configure PCI DSS network rules (HSM zone — no direct internet):
   nft add rule ip filter INPUT  \
     ip saddr != { 10.3.0.1, 10.3.0.2, 10.3.0.3 } tcp dport 12345 drop

4. The HSM PIN must be stored in a systemd environment override (chmod 600):
   /etc/systemd/system/openbao.service.d/hsm.conf
   Environment="BAO_HSM_PIN=<your-pin>"

5. Backup the HSM device firmware attestation certificate.

6. Document the HSM serial and key IDs in the PCI DSS key inventory.
==========================================================================
EOF
}

# --- Main entrypoint ----------------------------------------------------------
main() {
    exec > >(tee -a "$LOG_FILE") 2>&1
    log "=== YubiHSM 2 Setup Start ($(date)) ==="

    check_already_done "${1:-}"
    preflight
    install_sdk
    write_pkcs11_conf
    configure_connector
    create_wrap_key
    verify_setup
    print_security_reminders

    log "=== YubiHSM 2 Setup Complete ==="
}

main "$@"
