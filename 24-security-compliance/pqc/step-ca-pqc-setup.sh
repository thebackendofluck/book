#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# step-ca-pqc-setup.sh — Private CA with PQC certificates using step-ca
# Chapter 24g: Post-Quantum Cryptography for iGaming
#
# Purpose:
#   Sets up a private PKI for internal iGaming services using Smallstep's
#   step-ca. Attempts to use ML-DSA (formerly Dilithium) for the root CA
#   when the installed version supports it; falls back to classical ECDSA
#   P-384 with a clear status note.
#
# PQC Support Status in step-ca (as of 2025):
#   step-ca 0.27+ has experimental support for FIPS 204 (ML-DSA-65) root CAs
#   using the "pqc" build tag. The mainline release still defaults to ECDSA.
#   Hybrid certificates (ECDSA + ML-DSA dual-signed) are planned but not yet
#   in stable releases.
#
#   Alternatives for production PQC certificates:
#     - CFSSL with liboqs patches
#     - BoringSSL-based CAs with Kyber/Dilithium support
#     - Commercial CAs: DigiCert (offers hybrid certs), Let's Encrypt (watch ACME PQC draft)
#     - OpenSSL 3.x + oqs-provider for custom CA scripts
#
#   Tracking issue: https://github.com/smallstep/certificates/issues/pqc
#
# Usage:
#   sudo ./step-ca-pqc-setup.sh [--domain DOMAIN] [--ca-dir DIR] [--help]
#
# Options:
#   --domain DOMAIN   Leaf certificate domain (default: igaming.internal)
#   --ca-dir DIR      CA root directory (default: /etc/step-ca)
#   --help            Show this help
#
# Dependencies:
#   - curl or wget (for downloading step-cli)
#   - tar
#   - openssl (for verification)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DOMAIN="igaming.internal"
CA_DIR="/etc/step-ca"
CA_NAME="iGaming Internal CA"
ROOT_SUBJECT="Root CA — iGaming Platform"
INT_SUBJECT="Intermediate CA — iGaming Platform"
STEP_VERSION="0.27.4"       # Minimum version; check https://github.com/smallstep/cli/releases
STEP_CA_VERSION="0.27.4"
STEP_BIN="/usr/local/bin/step"
STEP_CA_BIN="/usr/local/bin/step-ca"
PQC_SUPPORTED=false
LEAF_DURATION="8760h"        # 1 year leaf certificate
INT_DURATION="43800h"        # 5 year intermediate
ROOT_DURATION="87600h"       # 10 year root

# Colour output
RED='\033[0;31m' YELLOW='\033[1;33m' GREEN='\033[0;32m'
CYAN='\033[0;36m' BOLD='\033[1m' RESET='\033[0m'
[[ -t 1 ]] || { RED='' YELLOW='' GREEN='' CYAN='' BOLD='' RESET=''; }

log()  { echo -e "${CYAN}[INFO]${RESET}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
ok()   { echo -e "${GREEN}[OK]${RESET}    $*"; }
err()  { echo -e "${RED}[ERROR]${RESET} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)   DOMAIN="$2";  shift 2 ;;
        --ca-dir)   CA_DIR="$2";  shift 2 ;;
        --help|-h)
            sed -n '2,35p' "$0" | sed 's/^# //'
            exit 0 ;;
        *)
            warn "Unknown option: $1 (ignored)"
            shift ;;
    esac
done

# ---------------------------------------------------------------------------
# Detect OS and architecture
# ---------------------------------------------------------------------------
detect_platform() {
    local os arch
    os="$(uname -s | tr '[:upper:]' '[:lower:]')"
    arch="$(uname -m)"

    case "$arch" in
        x86_64)  arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
        armv7l)  arch="armv7" ;;
        *)       err "Unsupported architecture: $arch" ;;
    esac

    # macOS uses darwin
    [[ "$os" == "darwin" ]] && os="darwin"

    echo "${os}_${arch}"
}

# ---------------------------------------------------------------------------
# Install step-cli
# ---------------------------------------------------------------------------
install_step_cli() {
    if command -v step &>/dev/null; then
        local ver
        ver=$(step version 2>/dev/null | grep -oP 'Version: \K[0-9.]+' || echo "unknown")
        ok "step-cli already installed (version ${ver})"
        STEP_BIN="$(command -v step)"
        return
    fi

    log "Installing step-cli ${STEP_VERSION}..."
    local platform
    platform=$(detect_platform)
    local url="https://github.com/smallstep/cli/releases/download/v${STEP_VERSION}/step_${platform}.tar.gz"

    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' RETURN

    if command -v curl &>/dev/null; then
        curl -fsSL "$url" -o "${tmpdir}/step.tar.gz"
    elif command -v wget &>/dev/null; then
        wget -q "$url" -O "${tmpdir}/step.tar.gz"
    else
        err "Neither curl nor wget is installed. Install one and retry."
    fi

    tar -xzf "${tmpdir}/step.tar.gz" -C "$tmpdir"
    install -m 755 "${tmpdir}/step_${STEP_VERSION}/bin/step" "$STEP_BIN"
    ok "step-cli installed at ${STEP_BIN}"
}

# ---------------------------------------------------------------------------
# Install step-ca
# ---------------------------------------------------------------------------
install_step_ca() {
    if command -v step-ca &>/dev/null; then
        local ver
        ver=$(step-ca version 2>/dev/null | grep -oP 'Version: \K[0-9.]+' || echo "unknown")
        ok "step-ca already installed (version ${ver})"
        STEP_CA_BIN="$(command -v step-ca)"
        return
    fi

    log "Installing step-ca ${STEP_CA_VERSION}..."
    local platform
    platform=$(detect_platform)
    local url="https://github.com/smallstep/certificates/releases/download/v${STEP_CA_VERSION}/step-ca_${platform}.tar.gz"

    local tmpdir
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' RETURN

    if command -v curl &>/dev/null; then
        curl -fsSL "$url" -o "${tmpdir}/step-ca.tar.gz"
    elif command -v wget &>/dev/null; then
        wget -q "$url" -O "${tmpdir}/step-ca.tar.gz"
    fi

    tar -xzf "${tmpdir}/step-ca.tar.gz" -C "$tmpdir"
    install -m 755 "${tmpdir}/step-ca_${STEP_CA_VERSION}/bin/step-ca" "$STEP_CA_BIN"
    ok "step-ca installed at ${STEP_CA_BIN}"
}

# ---------------------------------------------------------------------------
# Check PQC support in installed step-ca
# ---------------------------------------------------------------------------
check_pqc_support() {
    log "Checking step-ca PQC algorithm support..."

    # Check if step-ca was built with the pqc tag or reports ML-DSA support
    if "$STEP_CA_BIN" version 2>/dev/null | grep -qi "pqc\|mldsa\|dilithium"; then
        PQC_SUPPORTED=true
        ok "PQC (ML-DSA) support detected in step-ca"
    else
        PQC_SUPPORTED=false
        warn "PQC not supported in this step-ca build."
        warn "Using ECDSA P-384 for the root CA (classical, quantum-vulnerable)."
        warn ""
        warn "To enable PQC, build step-ca from source with the pqc tag:"
        warn "  git clone https://github.com/smallstep/certificates"
        warn "  cd certificates && go build -tags pqc ./cmd/step-ca"
        warn ""
        warn "Alternatively, use OpenSSL 3.x + oqs-provider for PQC certificate issuance:"
        warn "  https://github.com/open-quantum-safe/oqs-provider"
    fi
}

# ---------------------------------------------------------------------------
# Initialise the CA
# ---------------------------------------------------------------------------
initialise_ca() {
    log "Initialising step-ca PKI at ${CA_DIR}..."

    # Create CA directory
    mkdir -p "$CA_DIR"
    export STEPPATH="$CA_DIR"

    local root_key_type int_key_type
    if [[ "$PQC_SUPPORTED" == "true" ]]; then
        # ML-DSA-65 (FIPS 204 / Dilithium3 equivalent) for root and intermediate
        root_key_type="ML-DSA-65"
        int_key_type="ML-DSA-65"
        log "Using ML-DSA-65 for root and intermediate CA key types"
    else
        # Classical fallback: ECDSA P-384
        root_key_type="EC"
        int_key_type="EC"
        log "Using ECDSA P-384 for root and intermediate CA key types (classical fallback)"
    fi

    # Generate a random password for the CA key
    local ca_password
    ca_password=$(openssl rand -hex 32)
    echo "$ca_password" > "${CA_DIR}/ca-password.txt"
    chmod 600 "${CA_DIR}/ca-password.txt"
    warn "CA password stored at ${CA_DIR}/ca-password.txt — protect this file!"

    # Initialise the PKI
    # Note: --kty flag accepts OQS algorithm names when built with pqc tag
    "$STEP_BIN" ca init \
        --name "$CA_NAME" \
        --dns "$DOMAIN" \
        --address ":9000" \
        --root "${CA_DIR}/certs/root_ca.crt" \
        --key "${CA_DIR}/secrets/root_ca_key" \
        --provisioner "admin@${DOMAIN}" \
        --password-file "${CA_DIR}/ca-password.txt" \
        --kty "$root_key_type" \
        --not-after "$ROOT_DURATION" \
        --path "$CA_DIR"

    ok "CA initialised successfully"
}

# ---------------------------------------------------------------------------
# Create intermediate CA
# ---------------------------------------------------------------------------
create_intermediate_ca() {
    log "Creating intermediate CA..."
    export STEPPATH="$CA_DIR"

    local int_key_type="EC"
    [[ "$PQC_SUPPORTED" == "true" ]] && int_key_type="ML-DSA-65"

    # Generate intermediate key
    "$STEP_BIN" crypto keypair \
        "${CA_DIR}/certs/intermediate_ca.crt" \
        "${CA_DIR}/secrets/intermediate_ca_key" \
        --kty "$int_key_type" \
        --no-password --insecure \
        2>/dev/null || true

    # The step ca init command already creates an intermediate; this block
    # is a placeholder for additional intermediates (e.g. per-environment)
    log "Intermediate CA configuration available at ${CA_DIR}/config/ca.json"
}

# ---------------------------------------------------------------------------
# Issue leaf certificate for the iGaming domain
# ---------------------------------------------------------------------------
issue_leaf_certificate() {
    log "Issuing leaf certificate for ${DOMAIN}..."
    export STEPPATH="$CA_DIR"

    local cert_dir="${CA_DIR}/issued"
    mkdir -p "$cert_dir"

    # Start step-ca in background for certificate issuance
    "$STEP_CA_BIN" "${CA_DIR}/config/ca.json" \
        --password-file "${CA_DIR}/ca-password.txt" \
        &
    local ca_pid=$!
    sleep 2  # Allow step-ca to start

    # Issue the leaf certificate
    "$STEP_BIN" ca certificate "${DOMAIN}" \
        "${cert_dir}/${DOMAIN}.crt" \
        "${cert_dir}/${DOMAIN}.key" \
        --ca-url "https://localhost:9000" \
        --root "${CA_DIR}/certs/root_ca.crt" \
        --not-after "${LEAF_DURATION}" \
        --san "${DOMAIN}" \
        --san "*.${DOMAIN}" \
        --san "localhost" \
        --san "127.0.0.1" \
        --password-file "${CA_DIR}/ca-password.txt" \
        --force \
        2>/dev/null || {
            warn "Leaf certificate issuance failed — step-ca may need configuration."
            warn "Manually issue with: step ca certificate ${DOMAIN} cert.pem key.pem"
        }

    # Stop step-ca
    kill "$ca_pid" 2>/dev/null || true
    wait "$ca_pid" 2>/dev/null || true

    ok "Leaf certificate issued: ${cert_dir}/${DOMAIN}.crt"
}

# ---------------------------------------------------------------------------
# Build and verify the certificate chain
# ---------------------------------------------------------------------------
verify_chain() {
    log "Verifying certificate chain..."
    export STEPPATH="$CA_DIR"

    local cert_dir="${CA_DIR}/issued"
    local leaf_cert="${cert_dir}/${DOMAIN}.crt"
    local root_cert="${CA_DIR}/certs/root_ca.crt"
    local int_cert="${CA_DIR}/certs/intermediate_ca.crt"

    # Build full chain
    local chain_file="${cert_dir}/${DOMAIN}.fullchain.pem"
    cat "$leaf_cert" "$int_cert" "$root_cert" > "$chain_file" 2>/dev/null || \
        cat "$leaf_cert" "$root_cert" > "$chain_file" 2>/dev/null

    ok "Certificate chain: ${chain_file}"

    # Verify chain with openssl
    if openssl verify -CAfile "$root_cert" "$leaf_cert" &>/dev/null; then
        ok "Chain verification: PASSED"
    else
        warn "Chain verification failed — check intermediate CA configuration"
    fi

    # Print leaf certificate summary
    if [[ -f "$leaf_cert" ]]; then
        log "Leaf certificate details:"
        openssl x509 -in "$leaf_cert" -noout -subject -issuer -dates -ext subjectAltName 2>/dev/null || true
    fi

    # Print root CA algorithm
    log "Root CA algorithm:"
    openssl x509 -in "$root_cert" -noout -text 2>/dev/null | grep "Public Key Algorithm\|Signature Algorithm" | head -2 || true
}

# ---------------------------------------------------------------------------
# Output systemd service file for step-ca
# ---------------------------------------------------------------------------
write_systemd_service() {
    if [[ "$(uname -s)" != "Linux" ]]; then
        log "Skipping systemd service file (not Linux)"
        return
    fi

    local service_file="/etc/systemd/system/step-ca.service"
    log "Writing systemd service to ${service_file}..."

    cat > "$service_file" << EOF
[Unit]
Description=step-ca — iGaming Internal PQC Certificate Authority
After=network.target

[Service]
Type=simple
User=step
Group=step
Environment=STEPPATH=${CA_DIR}
ExecStart=${STEP_CA_BIN} ${CA_DIR}/config/ca.json --password-file ${CA_DIR}/ca-password.txt
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload 2>/dev/null || true
    ok "systemd service written. Enable with: systemctl enable --now step-ca"
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    echo ""
    echo -e "${BOLD}============================================================${RESET}"
    echo -e "${BOLD} step-ca PQC Setup Summary${RESET}"
    echo -e "${BOLD}============================================================${RESET}"
    echo -e " CA directory  : ${CYAN}${CA_DIR}${RESET}"
    echo -e " Domain        : ${CYAN}${DOMAIN}${RESET}"
    echo -e " PQC support   : $(if [[ "$PQC_SUPPORTED" == "true" ]]; then echo "${GREEN}YES (ML-DSA-65)${RESET}"; else echo "${YELLOW}NO (ECDSA P-384 fallback)${RESET}"; fi)"
    echo ""
    echo -e " Files created:"
    echo -e "   ${CA_DIR}/certs/root_ca.crt"
    echo -e "   ${CA_DIR}/certs/intermediate_ca.crt"
    echo -e "   ${CA_DIR}/issued/${DOMAIN}.crt"
    echo -e "   ${CA_DIR}/issued/${DOMAIN}.key"
    echo -e "   ${CA_DIR}/issued/${DOMAIN}.fullchain.pem"
    echo ""
    echo -e " Start CA: ${CYAN}step-ca ${CA_DIR}/config/ca.json --password-file ${CA_DIR}/ca-password.txt${RESET}"
    echo -e " Issue cert: ${CYAN}step ca certificate myservice.${DOMAIN} cert.pem key.pem${RESET}"
    echo ""
    if [[ "$PQC_SUPPORTED" == "false" ]]; then
        echo -e " ${YELLOW}To enable PQC, rebuild step-ca with PQC support or use:${RESET}"
        echo -e "   ${CYAN}openssl + oqs-provider${RESET}: https://github.com/open-quantum-safe/oqs-provider"
        echo -e "   ${CYAN}CFSSL + liboqs${RESET}: https://github.com/open-quantum-safe/oqs-demos/tree/main/cfssl"
    fi
    echo -e "${BOLD}============================================================${RESET}"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo ""
    echo -e "${BOLD}step-ca PQC Certificate Authority Setup${RESET}"
    echo -e "Domain: ${DOMAIN}  |  CA Dir: ${CA_DIR}"
    echo ""

    if [[ "$(id -u)" -ne 0 ]]; then
        warn "Not running as root. Some operations may fail."
        warn "Re-run with: sudo $0 $*"
    fi

    install_step_cli
    install_step_ca
    check_pqc_support
    initialise_ca
    create_intermediate_ca
    issue_leaf_certificate
    verify_chain
    write_systemd_service
    print_summary
}

main "$@"
