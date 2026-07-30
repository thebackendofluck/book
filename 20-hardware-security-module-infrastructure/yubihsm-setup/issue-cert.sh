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
# issue-cert.sh
#
# Purpose : Issue a certificate from the AcmeToCasino Platform CA via OpenBao.
#           Writes cert.pem, key.pem, and ca-chain.pem to stdout or to a
#           directory supplied via --output.
#
# Usage
#   export BAO_ADDR=https://vault.internal:8200
#   export BAO_CACERT=/opt/openbao/tls/ca.pem
#   export BAO_TOKEN=<token>
#
#   ./issue-cert.sh --role mtls-service \
#                   --cn casino-api.acmetocasino-prod.svc.cluster.local \
#                   --ttl 24h
#
#   ./issue-cert.sh --role origin-https \
#                   --cn new.acmetocasino.com \
#                   --ttl 8760h \
#                   --output /etc/nginx/ssl/
#
#   ./issue-cert.sh --role pix-payment \
#                   --cn pix.acmetocasino.com
#
# Available roles
#   mtls-service   ECDSA P-256 · max 24 h  (inter-service mTLS)
#   k8s-internal   ECDSA P-256 · max 90 d  (K8s control plane / Ingress)
#   origin-https   RSA-2048    · max 1 yr  (Cloudflare Authenticated Origin)
#   pix-payment    RSA-2048    · max 1 yr  (BACEN PIX / payment gateway)
#
# Output files (in --output dir or cwd)
#   cert.pem      — leaf certificate
#   key.pem       — private key  (mode 0600)
#   ca-chain.pem  — issuing CA certificate chain
#
# Exit codes
#   0  — success
#   1  — error
#
# Compliance: PCI DSS Req. 3.6/3.7
# =============================================================================

set -euo pipefail

SCRIPT_NAME="issue-cert.sh"
INT_CA_PATH="pki_int"

# Defaults
BAO_ADDR="${BAO_ADDR:-https://vault.internal:8200}"
BAO_CACERT="${BAO_CACERT:-}"
ROLE=""
CN=""
TTL=""
OUTPUT_DIR=""
ALT_NAMES=""
IP_SANS=""
DRY_RUN=false

# ---------------------------------------------------------------------------
# Colour helpers
#
# Variables hold ANSI codes; we expand them via printf '%b' to keep the
# format string literal and satisfy shellcheck SC2059.
# ---------------------------------------------------------------------------
_c_reset="\033[0m"
_c_green="\033[0;32m"
_c_yellow="\033[1;33m"
_c_red="\033[0;31m"

log_info()  { printf '%b[INFO]%b  %s\n'   "$_c_green"  "$_c_reset" "$*"; }
log_warn()  { printf '%b[WARN]%b  %s\n'   "$_c_yellow" "$_c_reset" "$*" >&2; }
log_error() { printf '%b[ERROR]%b %s\n'   "$_c_red"    "$_c_reset" "$*" >&2; }
die()       { log_error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
${SCRIPT_NAME} — Issue a certificate from the AcmeToCasino Platform CA

USAGE
  $SCRIPT_NAME --role <role> --cn <common-name> [OPTIONS]

REQUIRED
  --role <name>        PKI role: mtls-service | k8s-internal | origin-https | pix-payment
  --cn   <fqdn>        Certificate Common Name (e.g. api.svc.cluster.local)

OPTIONS
  --ttl  <duration>    Validity period (e.g. 24h, 2160h, 8760h). Defaults to role default.
  --alt-names <list>   Comma-separated SANs (e.g. api.internal,api.acmetocasino.local)
  --ip-sans <list>     Comma-separated IP SANs (e.g. 10.0.1.5)
  --output <dir>       Write cert.pem / key.pem / ca-chain.pem here instead of stdout
  --dry-run            Show what would be issued without contacting OpenBao
  --help               Show this help

ENVIRONMENT
  BAO_ADDR             OpenBao address  [default: https://vault.internal:8200]
  BAO_CACERT           Path to CA cert for TLS verification
  BAO_TOKEN            OpenBao token (or set via ~/.bao/token)

EXAMPLES
  # mTLS leaf cert for casino-api service
  $SCRIPT_NAME --role mtls-service --cn casino-api.acmetocasino-prod.svc.cluster.local

  # Cloudflare origin cert with 1-year TTL written to /etc/nginx/ssl/
  $SCRIPT_NAME --role origin-https --cn new.acmetocasino.com --ttl 8760h --output /etc/nginx/ssl/

  # PIX payment cert
  $SCRIPT_NAME --role pix-payment --cn pix.acmetocasino.com
EOF
    exit 0
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --role)       ROLE="$2";       shift 2 ;;
            --cn)         CN="$2";         shift 2 ;;
            --ttl)        TTL="$2";        shift 2 ;;
            --alt-names)  ALT_NAMES="$2";  shift 2 ;;
            --ip-sans)    IP_SANS="$2";    shift 2 ;;
            --output)     OUTPUT_DIR="$2"; shift 2 ;;
            --dry-run)    DRY_RUN=true;    shift   ;;
            --help|-h)    usage ;;
            *)
                die "Unknown argument: $1  (run with --help for usage)"
                ;;
        esac
    done

    [[ -n "$ROLE" ]] || die "--role is required."
    [[ -n "$CN"   ]] || die "--cn is required."
}

# ---------------------------------------------------------------------------
# Role validation
# ---------------------------------------------------------------------------
validate_role() {
    local valid_roles="mtls-service k8s-internal origin-https pix-payment"
    local found=false
    local r
    for r in $valid_roles; do
        if [[ "$ROLE" == "$r" ]]; then
            found=true
            break
        fi
    done
    if ! $found; then
        die "Unknown role '${ROLE}'.  Valid: ${valid_roles}"
    fi
}

# ---------------------------------------------------------------------------
# Token resolution (same pattern as setup-private-ca.sh)
# ---------------------------------------------------------------------------
resolve_token() {
    if [[ -n "${BAO_TOKEN:-}" ]]; then
        return
    fi
    if [[ -f "${HOME}/.bao/token" ]]; then
        BAO_TOKEN="$(cat "${HOME}/.bao/token")"
        export BAO_TOKEN
        return
    fi
    if $DRY_RUN; then
        BAO_TOKEN="dry-run-placeholder"
        export BAO_TOKEN
        return
    fi
    if [[ -t 0 ]]; then
        printf '%bEnter OpenBao token: %b' "$_c_yellow" "$_c_reset"
        read -rs BAO_TOKEN; echo
        export BAO_TOKEN
    else
        die "BAO_TOKEN not set and no TTY available."
    fi
}

# ---------------------------------------------------------------------------
# Build the bao write argument array
#
# We build an array so that each argument is a discrete element — this
# avoids word-splitting (SC2046) that would occur if we used a plain string.
# ---------------------------------------------------------------------------
build_issue_args() {
    ISSUE_ARGS=()
    ISSUE_ARGS+=("common_name=${CN}")
    if [[ -n "$TTL" ]];       then ISSUE_ARGS+=("ttl=${TTL}"); fi
    if [[ -n "$ALT_NAMES" ]]; then ISSUE_ARGS+=("alt_names=${ALT_NAMES}"); fi
    if [[ -n "$IP_SANS" ]];   then ISSUE_ARGS+=("ip_sans=${IP_SANS}"); fi
}

# ---------------------------------------------------------------------------
# Write output files
# ---------------------------------------------------------------------------
write_output() {
    local json="$1"
    local dest="${OUTPUT_DIR:-}"

    local certificate issuing_ca private_key
    certificate=$(printf '%s' "$json" | jq -r '.data.certificate')
    issuing_ca=$(printf '%s'  "$json" | jq -r '.data.issuing_ca')
    private_key=$(printf '%s' "$json" | jq -r '.data.private_key')

    if [[ -n "$dest" ]]; then
        mkdir -p "$dest"
        printf '%s\n' "$certificate"  > "${dest}/cert.pem"
        printf '%s\n' "$issuing_ca"   > "${dest}/ca-chain.pem"
        # Key file: strict permissions before writing
        install -m 600 /dev/null "${dest}/key.pem"
        printf '%s\n' "$private_key"  > "${dest}/key.pem"

        log_info "Certificate written to ${dest}/"
        log_info "  ${dest}/cert.pem"
        log_info "  ${dest}/key.pem        (mode 0600)"
        log_info "  ${dest}/ca-chain.pem"

        # Print cert details for logging
        printf '%s\n' "$certificate" \
            | openssl x509 -noout -subject -issuer -dates -serial
    else
        # Stdout mode — emit all three documents with clear markers
        printf '### cert.pem ###\n%s\n\n'      "$certificate"
        printf '### ca-chain.pem ###\n%s\n\n'  "$issuing_ca"
        printf '### key.pem ###\n%s\n'         "$private_key"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    validate_role
    resolve_token

    # Dry-run: show intent and exit
    if $DRY_RUN; then
        printf '%b[DRY-RUN]%b Would issue:\n' "$_c_yellow" "$_c_reset"
        printf '  Mount  : %s\n' "${INT_CA_PATH}"
        printf '  Role   : %s\n' "${ROLE}"
        printf '  CN     : %s\n' "${CN}"
        if [[ -n "$TTL" ]];        then printf '  TTL    : %s\n' "${TTL}"; fi
        if [[ -n "$ALT_NAMES" ]];  then printf '  SANs   : %s\n' "${ALT_NAMES}"; fi
        if [[ -n "$IP_SANS" ]];    then printf '  IP SANs: %s\n' "${IP_SANS}"; fi
        if [[ -n "$OUTPUT_DIR" ]]; then printf '  Output : %s\n' "${OUTPUT_DIR}"; fi
        exit 0
    fi

    log_info "Issuing cert: role=${ROLE} cn=${CN}"

    build_issue_args

    local result
    result=$(bao write -format=json "${INT_CA_PATH}/issue/${ROLE}" \
        "${ISSUE_ARGS[@]}")

    local serial expiry
    serial=$(printf '%s' "$result" | jq -r '.data.serial_number')
    expiry=$(printf '%s' "$result" | jq -r '.data.expiration')

    local expiry_fmt
    expiry_fmt=$(date -d "@${expiry}" 2>/dev/null \
              || date -r "${expiry}"  2>/dev/null \
              || printf '%s' "${expiry}")

    log_info "Issued — serial: ${serial}"
    log_info "Expires: ${expiry_fmt}"

    write_output "$result"
}

main "$@"
