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

# =============================================================================
# setup-private-ca.sh
#
# Purpose : Bootstrap the two-tier PKI for the AcmeToCasino platform using
#           OpenBao as the CA engine.  The Root CA key material never leaves
#           OpenBao (which is auto-unsealed via YubiHSM 2).  The Intermediate
#           CA signs all operational certificates.
#
# CA hierarchy
#   Root CA  — RSA-4096, 10 years, path=pki
#     └── Intermediate CA — ECDSA P-384, 3 years, path=pki_int
#           ├── mtls-service   ECDSA P-256, 24 h   (inter-service mTLS)
#           ├── k8s-internal   ECDSA P-256, 90 d   (K8s control plane)
#           ├── origin-https   RSA-2048,    1 yr   (Cloudflare Auth. Origin)
#           └── pix-payment    RSA-2048,    1 yr   (BACEN PIX compliance)
#
# Usage
#   export BAO_ADDR=https://vault.internal:8200
#   export BAO_CACERT=/opt/openbao/tls/ca.pem
#   export BAO_TOKEN=<root-or-admin-token>    # or leave unset to be prompted
#   ./setup-private-ca.sh [--dry-run] [--force]
#
# Exit codes
#   0  — success
#   1  — error
#
# Compliance: PCI DSS Req. 3.6/3.7 · FIPS 140-2 Level 3 (via YubiHSM 2 seal)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_NAME="setup-private-ca.sh"
SCRIPT_VERSION="1.0.0"

ROOT_CA_PATH="pki"
INT_CA_PATH="pki_int"

ROOT_CA_TTL="87600h"          # 10 years
INT_CA_TTL="26280h"           # 3 years

ROOT_CA_CN="The Backend of Luck Root CA"
INT_CA_CN="AcmeToCasino Platform CA"
ORG="The Backend of Luck"
ROOT_CA_OU="iGaming Infrastructure"
INT_CA_OU="Casino Platform Operations"
COUNTRY="NL"

ROOT_CERT_OUT="/opt/openbao/tls/root-ca.pem"
CSR_TMP="/tmp/pki_int.$$.csr"
INT_CERT_TMP="/tmp/intermediate.$$.cert.pem"

# Vault/OpenBao base URL defaults — override via env
BAO_ADDR="${BAO_ADDR:-https://vault.internal:8200}"
BAO_CACERT="${BAO_CACERT:-}"

# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------
DRY_RUN=false
FORCE=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        --force)   FORCE=true ;;
        --help|-h)
            sed -n '/^# ====/,/^# ====/p' "$0" | head -40
            exit 0
            ;;
        *)
            echo "Unknown argument: $arg" >&2
            echo "Usage: $SCRIPT_NAME [--dry-run] [--force]" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Colour helpers
#
# We store ANSI codes as variables and expand them via printf '%b' so that
# the format string itself is always a literal (satisfies SC2059).
# ---------------------------------------------------------------------------
_c_reset="\033[0m"
_c_green="\033[0;32m"
_c_yellow="\033[1;33m"
_c_red="\033[0;31m"
_c_cyan="\033[0;36m"
_c_bold="\033[1m"

log_info()  { printf '%b[INFO]%b  %s\n'      "$_c_green"  "$_c_reset" "$*"; }
log_warn()  { printf '%b[WARN]%b  %s\n'      "$_c_yellow" "$_c_reset" "$*" >&2; }
log_error() { printf '%b[ERROR]%b %s\n'      "$_c_red"    "$_c_reset" "$*" >&2; }
log_step()  { printf '\n%b%b==> %s%b\n'      "$_c_bold"   "$_c_cyan"  "$*" "$_c_reset"; }
log_dry()   { printf '%b[DRY-RUN]%b would run: %s\n' "$_c_yellow" "$_c_reset" "$*"; }

die() {
    log_error "$*"
    exit 1
}

# ---------------------------------------------------------------------------
# Dry-run wrapper — prints the command instead of running it
# ---------------------------------------------------------------------------
run() {
    if $DRY_RUN; then
        log_dry "$*"
    else
        "$@"
    fi
}

# ---------------------------------------------------------------------------
# Require commands
# ---------------------------------------------------------------------------
require_cmd() {
    local cmd="$1"
    if ! command -v "$cmd" &>/dev/null; then
        die "Required command not found: $cmd — install it before running this script."
    fi
}

# ---------------------------------------------------------------------------
# Token resolution
# ---------------------------------------------------------------------------
resolve_token() {
    if [[ -n "${BAO_TOKEN:-}" ]]; then
        log_info "Using token from BAO_TOKEN environment variable."
        return
    fi
    if [[ -f "${HOME}/.bao/token" ]]; then
        BAO_TOKEN="$(cat "${HOME}/.bao/token")"
        log_info "Using token from ~/.bao/token."
        export BAO_TOKEN
        return
    fi
    # Interactive fallback (only when not in dry-run and not in CI)
    if $DRY_RUN; then
        BAO_TOKEN="dry-run-placeholder"
        export BAO_TOKEN
        return
    fi
    if [[ -t 0 ]]; then
        printf '%bEnter OpenBao root/admin token: %b' "$_c_yellow" "$_c_reset"
        read -rs BAO_TOKEN
        echo
        export BAO_TOKEN
    else
        die "BAO_TOKEN not set and no TTY available.  Set BAO_TOKEN or run interactively."
    fi
}

# ---------------------------------------------------------------------------
# OpenBao helpers
# ---------------------------------------------------------------------------
bao_is_sealed() {
    local status
    status=$(bao status -format=json 2>/dev/null | jq -r '.sealed // "true"') || true
    [[ "$status" == "true" ]]
}

engine_enabled() {
    local path="$1"
    bao secrets list -format=json 2>/dev/null \
        | jq -e --arg p "${path}/" 'has($p)' &>/dev/null
}

role_exists() {
    local mount="$1" role="$2"
    bao read -format=json "${mount}/roles/${role}" &>/dev/null
}

# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------
verify_root_ca() {
    log_info "Verifying Root CA at ${ROOT_CA_PATH}/cert/ca ..."
    bao read -field=certificate "${ROOT_CA_PATH}/cert/ca" \
        | openssl x509 -noout -subject -issuer -dates
}

verify_intermediate_ca() {
    log_info "Verifying Intermediate CA at ${INT_CA_PATH}/cert/ca ..."
    bao read -field=certificate "${INT_CA_PATH}/cert/ca" \
        | openssl x509 -noout -subject -issuer -dates
}

verify_role() {
    local mount="$1" role="$2"
    if role_exists "$mount" "$role"; then
        log_info "Role exists: ${mount}/roles/${role}"
    else
        die "Role not found after creation: ${mount}/roles/${role}"
    fi
}

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------
cleanup() {
    if [[ -f "$CSR_TMP" ]]; then
        rm -f "$CSR_TMP"
    fi
    if [[ -f "$INT_CERT_TMP" ]]; then
        rm -f "$INT_CERT_TMP"
    fi
}
trap cleanup EXIT

# ===========================================================================
# Pre-flight checks
# ===========================================================================
preflight() {
    log_step "Pre-flight checks"

    require_cmd bao
    require_cmd jq
    require_cmd openssl

    resolve_token

    if $DRY_RUN; then
        log_warn "DRY-RUN mode — no changes will be made to OpenBao."
        return
    fi

    if bao_is_sealed; then
        die "OpenBao is sealed.  Run 'bao operator unseal' or check YubiHSM auto-unseal."
    fi

    # Verify connectivity
    if ! bao status &>/dev/null; then
        die "Cannot reach OpenBao at ${BAO_ADDR}.  Check BAO_ADDR and network."
    fi

    log_info "OpenBao reachable at ${BAO_ADDR} and unsealed."

    # Ensure output directory for root CA cert exists
    local root_cert_dir
    root_cert_dir="$(dirname "$ROOT_CERT_OUT")"
    if [[ ! -d "$root_cert_dir" ]]; then
        log_warn "Output directory ${root_cert_dir} does not exist — creating it."
        mkdir -p "$root_cert_dir"
    fi
}

# ===========================================================================
# STEP 1 — Enable Root CA PKI engine
# ===========================================================================
setup_root_pki_engine() {
    log_step "Step 1: Enable Root CA PKI engine (path=${ROOT_CA_PATH})"

    if engine_enabled "$ROOT_CA_PATH" && ! $FORCE; then
        log_warn "PKI engine already enabled at '${ROOT_CA_PATH}'.  Use --force to re-initialise."
        return
    fi

    if engine_enabled "$ROOT_CA_PATH"; then
        log_warn "--force set: disabling existing engine at '${ROOT_CA_PATH}' ..."
        run bao secrets disable "$ROOT_CA_PATH"
    fi

    # Enable with a conservative default TTL — we override per-cert anyway
    run bao secrets enable -path="${ROOT_CA_PATH}" pki
    run bao secrets tune -max-lease-ttl="${ROOT_CA_TTL}" "${ROOT_CA_PATH}"

    log_info "Root CA PKI engine enabled, max TTL = ${ROOT_CA_TTL}."
}

# ===========================================================================
# STEP 2 — Generate Root CA (internal — key stays inside OpenBao / YubiHSM)
# ===========================================================================
generate_root_ca() {
    log_step "Step 2: Generate Root CA (RSA-4096, ${ROOT_CA_TTL})"

    # Check if Root CA cert already exists (idempotency guard without --force)
    if ! $FORCE && bao read "${ROOT_CA_PATH}/cert/ca" &>/dev/null; then
        log_warn "Root CA cert already present.  Use --force to regenerate."
        return
    fi

    if $DRY_RUN; then
        log_dry "bao write -field=certificate ${ROOT_CA_PATH}/root/generate/internal \
common_name='${ROOT_CA_CN}' organization='${ORG}' ou='${ROOT_CA_OU}' \
country='${COUNTRY}' ttl=${ROOT_CA_TTL} key_type=rsa key_bits=4096 \
> ${ROOT_CERT_OUT}"
        return
    fi

    # The 'internal' type means the private key is generated inside OpenBao
    # and is NEVER returned in the API response — the YubiHSM-sealed storage
    # protects it at rest.
    bao write -field=certificate "${ROOT_CA_PATH}/root/generate/internal" \
        common_name="${ROOT_CA_CN}"             \
        organization="${ORG}"                   \
        ou="${ROOT_CA_OU}"                      \
        country="${COUNTRY}"                    \
        ttl="${ROOT_CA_TTL}"                    \
        key_type=rsa                            \
        key_bits=4096                           \
        > "${ROOT_CERT_OUT}"

    log_info "Root CA certificate written to ${ROOT_CERT_OUT}."
    openssl x509 -noout -subject -dates -in "${ROOT_CERT_OUT}"
}

# ===========================================================================
# STEP 3 — Configure Root CA URLs (CRL / issuer)
# ===========================================================================
configure_root_urls() {
    log_step "Step 3: Configure Root CA issuer and CRL URLs"

    # These URLs are embedded into issued certificates so relying parties
    # can fetch the CRL and verify the issuer.  They must be reachable from
    # everywhere that validates certs — adjust if using an internal PKI proxy.
    run bao write "${ROOT_CA_PATH}/config/urls" \
        issuing_certificates="${BAO_ADDR}/v1/${ROOT_CA_PATH}/ca" \
        crl_distribution_points="${BAO_ADDR}/v1/${ROOT_CA_PATH}/crl"

    log_info "Root CA URLs configured."
}

# ===========================================================================
# STEP 4 — Enable Intermediate CA PKI engine
# ===========================================================================
setup_int_pki_engine() {
    log_step "Step 4: Enable Intermediate CA PKI engine (path=${INT_CA_PATH})"

    if engine_enabled "$INT_CA_PATH" && ! $FORCE; then
        log_warn "PKI engine already enabled at '${INT_CA_PATH}'.  Use --force to re-initialise."
        return
    fi

    if engine_enabled "$INT_CA_PATH"; then
        log_warn "--force set: disabling existing engine at '${INT_CA_PATH}' ..."
        run bao secrets disable "$INT_CA_PATH"
    fi

    run bao secrets enable -path="${INT_CA_PATH}" pki
    run bao secrets tune -max-lease-ttl="${INT_CA_TTL}" "${INT_CA_PATH}"

    log_info "Intermediate CA PKI engine enabled, max TTL = ${INT_CA_TTL}."
}

# ===========================================================================
# STEP 5 — Generate Intermediate CA CSR
# ===========================================================================
generate_intermediate_csr() {
    log_step "Step 5: Generate Intermediate CA CSR (ECDSA P-384)"

    if $DRY_RUN; then
        log_dry "bao write -format=json ${INT_CA_PATH}/intermediate/generate/internal \
common_name='${INT_CA_CN}' ... | jq -r '.data.csr' > ${CSR_TMP}"
        return
    fi

    # ECDSA P-384 is the right trade-off for an intermediate CA:
    #   - Smaller certs than RSA-4096 → lower TLS overhead
    #   - 192-bit security level → well above PCI DSS requirements
    #   - Supported by all modern clients and BACEN's cryptographic guidelines
    bao write -format=json "${INT_CA_PATH}/intermediate/generate/internal" \
        common_name="${INT_CA_CN}"             \
        organization="${ORG}"                  \
        ou="${INT_CA_OU}"                      \
        country="${COUNTRY}"                   \
        key_type=ec                            \
        key_bits=384                           \
        | jq -r '.data.csr'                   \
        > "${CSR_TMP}"

    local csr_size
    csr_size=$(wc -c < "$CSR_TMP")
    if [[ "$csr_size" -lt 100 ]]; then
        die "Generated CSR appears empty (${csr_size} bytes).  Check OpenBao logs."
    fi

    log_info "Intermediate CSR generated (${csr_size} bytes) at ${CSR_TMP}."
}

# ===========================================================================
# STEP 6 — Sign Intermediate CA with Root CA
# ===========================================================================
sign_intermediate() {
    log_step "Step 6: Sign Intermediate CA with Root CA"

    if $DRY_RUN; then
        log_dry "bao write -format=json ${ROOT_CA_PATH}/root/sign-intermediate \
csr=@${CSR_TMP} format=pem_bundle ttl=${INT_CA_TTL} | jq -r '.data.certificate' > ${INT_CERT_TMP}"
        return
    fi

    # The Root CA signs the Intermediate and embeds the issuer/CRL URLs that
    # were configured in Step 3.  The resulting certificate chain is stored
    # temporarily — we import it into pki_int in the next step and then delete
    # the temp file.
    bao write -format=json "${ROOT_CA_PATH}/root/sign-intermediate" \
        csr="@${CSR_TMP}"                     \
        format=pem_bundle                      \
        ttl="${INT_CA_TTL}"                    \
        | jq -r '.data.certificate'            \
        > "${INT_CERT_TMP}"

    local cert_size
    cert_size=$(wc -c < "$INT_CERT_TMP")
    if [[ "$cert_size" -lt 100 ]]; then
        die "Signed certificate appears empty (${cert_size} bytes).  Check OpenBao logs."
    fi

    log_info "Intermediate CA signed by Root CA (${cert_size} bytes)."
    openssl x509 -noout -subject -issuer -dates -in \
        <(grep -m1 -A9999 "BEGIN CERTIFICATE" "$INT_CERT_TMP")
}

# ===========================================================================
# STEP 7 — Import signed Intermediate CA
# ===========================================================================
import_intermediate() {
    log_step "Step 7: Import signed Intermediate CA into ${INT_CA_PATH}"

    if $DRY_RUN; then
        log_dry "bao write ${INT_CA_PATH}/intermediate/set-signed certificate=@${INT_CERT_TMP}"
        return
    fi

    bao write "${INT_CA_PATH}/intermediate/set-signed" \
        certificate="@${INT_CERT_TMP}"

    log_info "Intermediate CA imported."
}

# ===========================================================================
# STEP 8 — Configure Intermediate CA URLs
# ===========================================================================
configure_int_urls() {
    log_step "Step 8: Configure Intermediate CA issuer and CRL URLs"

    run bao write "${INT_CA_PATH}/config/urls" \
        issuing_certificates="${BAO_ADDR}/v1/${INT_CA_PATH}/ca" \
        crl_distribution_points="${BAO_ADDR}/v1/${INT_CA_PATH}/crl"

    log_info "Intermediate CA URLs configured."
}

# ===========================================================================
# STEP 9 — Create PKI roles
# ===========================================================================
create_roles() {
    log_step "Step 9: Create PKI roles on ${INT_CA_PATH}"

    # ------------------------------------------------------------------
    # Role: mtls-service
    # ECDSA P-256 · 24 h TTL · server+client auth
    #
    # Used for inter-service mTLS inside the K8s cluster.  Short TTL (24 h)
    # means compromised certs expire quickly without needing explicit
    # revocation.  cert-manager or vault-agent-injector rotates automatically.
    # ------------------------------------------------------------------
    log_info "Creating role: mtls-service"
    run bao write "${INT_CA_PATH}/roles/mtls-service"            \
        allowed_domains="svc.cluster.local,internal,acmetocasino.local" \
        allow_subdomains=true                                    \
        allow_bare_domains=true                                  \
        max_ttl=24h                                              \
        ttl=24h                                                  \
        key_type=ec                                              \
        key_bits=256                                             \
        require_cn=true                                          \
        server_flag=true                                         \
        client_flag=true

    # ------------------------------------------------------------------
    # Role: k8s-internal
    # ECDSA P-256 · 90 days
    #
    # K8s control plane components (etcd peers, API server, kubelet) and
    # Ingress controllers need longer-lived certs than mTLS services.
    # 90 days aligns with cert-manager's default renewal trigger at 2/3 TTL.
    # ------------------------------------------------------------------
    log_info "Creating role: k8s-internal"
    run bao write "${INT_CA_PATH}/roles/k8s-internal"            \
        allowed_domains="svc.cluster.local,acmetocasino-prod.svc.cluster.local,acmetocasino-staging.svc.cluster.local" \
        allow_subdomains=true                                    \
        max_ttl=2160h                                            \
        ttl=2160h                                                \
        key_type=ec                                              \
        key_bits=256

    # ------------------------------------------------------------------
    # Role: origin-https
    # RSA-2048 · 1 year
    #
    # Cloudflare Authenticated Origin Pulls requires a cert issued by a
    # CA you control.  RSA-2048 is specified because some Cloudflare edge
    # nodes still prefer RSA for origin handshakes.  The cert is installed
    # on the origin Nginx/Caddy and Cloudflare is configured with
    # "Authenticated Origin Pulls" enabled.
    # ------------------------------------------------------------------
    log_info "Creating role: origin-https"
    run bao write "${INT_CA_PATH}/roles/origin-https"            \
        allowed_domains="acmetocasino.com,thebackendofluck.com,portrasdasorte.com.br,cloud-acmetocasino.com" \
        allow_subdomains=true                                    \
        max_ttl=8760h                                            \
        ttl=8760h                                                \
        key_type=rsa                                             \
        key_bits=2048

    # ------------------------------------------------------------------
    # Role: pix-payment
    # RSA-2048 · 1 year · strict CN enforcement
    #
    # BACEN's PIX normative (Resolução BCB nº 1) requires mutual TLS
    # with certificates whose CN/SAN matches the registered ISPB entry.
    # allow_subdomains=false enforces that only the exact registered
    # domains can be issued, preventing accidental misuse.
    # ------------------------------------------------------------------
    log_info "Creating role: pix-payment"
    run bao write "${INT_CA_PATH}/roles/pix-payment"             \
        allowed_domains="pix.acmetocasino.com,payment.acmetocasino.internal" \
        allow_subdomains=false                                   \
        max_ttl=8760h                                            \
        ttl=8760h                                                \
        key_type=rsa                                             \
        key_bits=2048                                            \
        require_cn=true

    log_info "All roles created."
}

# ===========================================================================
# STEP 10 — Verification
# ===========================================================================
verify_ca_setup() {
    log_step "Step 10: Verification"

    if $DRY_RUN; then
        log_dry "bao read ${ROOT_CA_PATH}/cert/ca"
        log_dry "bao read ${INT_CA_PATH}/cert/ca"
        log_dry "bao list ${INT_CA_PATH}/roles"
        log_dry "bao write ${INT_CA_PATH}/issue/mtls-service common_name=test.svc.cluster.local ttl=1h"
        return
    fi

    verify_root_ca
    verify_intermediate_ca

    log_info "Listing roles:"
    bao list "${INT_CA_PATH}/roles"

    log_info "Issuing test certificate via mtls-service role ..."
    local test_output
    test_output=$(bao write -format=json "${INT_CA_PATH}/issue/mtls-service" \
        common_name="setup-test.svc.cluster.local"                           \
        ttl=1h)

    local serial expiry
    serial=$(echo "$test_output" | jq -r '.data.serial_number')
    expiry=$(echo "$test_output" | jq -r '.data.expiration')

    # Format expiry portably: GNU date uses -d, BSD date uses -r
    local expiry_fmt
    expiry_fmt=$(date -d "@${expiry}" 2>/dev/null \
              || date -r "${expiry}"  2>/dev/null \
              || echo "${expiry}")
    log_info "Test cert issued — serial: ${serial}, expires: ${expiry_fmt}"

    # Revoke the test cert immediately; use explicit if to avoid SC2015
    log_info "Revoking test certificate ..."
    if bao write "${INT_CA_PATH}/revoke" serial_number="${serial}" &>/dev/null; then
        log_info "Test cert revoked."
    else
        log_warn "Could not auto-revoke test cert — revoke manually: serial=${serial}"
    fi

    log_info "Verifying CRL is accessible ..."
    local crl_url="${BAO_ADDR}/v1/${INT_CA_PATH}/crl"
    if curl -sf --cacert "${BAO_CACERT:-/dev/null}" -o /dev/null "$crl_url"; then
        log_info "CRL endpoint reachable: ${crl_url}"
    else
        log_warn "CRL endpoint not reachable: ${crl_url}  (may be expected in air-gapped environments)"
    fi
}

# ===========================================================================
# Main
# ===========================================================================
main() {
    printf '\n%b%s v%s%b\n'    "$_c_bold"   "$SCRIPT_NAME" "$SCRIPT_VERSION" "$_c_reset"
    printf '%bCA hierarchy for: AcmeToCasino Platform%b\n' "$_c_cyan" "$_c_reset"
    if $DRY_RUN; then
        printf '%b*** DRY-RUN MODE — no changes will be made ***%b\n\n' "$_c_yellow" "$_c_reset"
    fi

    preflight
    setup_root_pki_engine
    generate_root_ca
    configure_root_urls
    setup_int_pki_engine
    generate_intermediate_csr
    sign_intermediate
    import_intermediate
    configure_int_urls
    create_roles
    verify_ca_setup

    # Remove temp files (trap handles abnormal exits)
    if [[ -f "$CSR_TMP" ]]; then
        rm -f "$CSR_TMP"
    fi
    if [[ -f "$INT_CERT_TMP" ]]; then
        rm -f "$INT_CERT_TMP"
    fi

    printf '\n%b%bCA setup complete.%b\n'    "$_c_green" "$_c_bold" "$_c_reset"
    printf 'Root CA cert   : %s\n' "${ROOT_CERT_OUT}"
    printf 'Root PKI mount : %s\n' "${ROOT_CA_PATH}"
    printf 'Int  PKI mount : %s\n' "${INT_CA_PATH}"
    printf 'Roles          : mtls-service, k8s-internal, origin-https, pix-payment\n\n'
    printf 'Next steps:\n'
    printf '  1. Distribute %s to all servers and K8s as a trusted CA.\n' "${ROOT_CERT_OUT}"
    printf '  2. Configure cert-manager vault-issuer to use %s/issue/<role>.\n' "${INT_CA_PATH}"
    printf '  3. Upload root CA to Cloudflare (Authenticated Origin Pulls).\n'
    printf '  4. Run ./verify-ca.sh to confirm the full chain is healthy.\n\n'
}

main "$@"
