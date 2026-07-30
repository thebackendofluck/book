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
# verify-ca.sh
#
# Purpose : End-to-end health check for the AcmeToCasino two-tier PKI.
#           Verifies every component of the CA chain and reports a clear
#           PASS / FAIL for each check.
#
# Checks performed
#   1.  OpenBao seal status
#   2.  YubiHSM connector reachability
#   3.  Root CA existence and validity window
#   4.  Intermediate CA existence, validity, and chain to Root
#   5.  Role configuration (all four expected roles present)
#   6.  Issue a test certificate via each role and verify its chain
#   7.  CRL accessibility for both Root and Intermediate
#   8.  Revoke the test certificates (cleanup)
#
# Usage
#   export BAO_ADDR=https://vault.internal:8200
#   export BAO_CACERT=/opt/openbao/tls/ca.pem
#   export BAO_TOKEN=<token>
#   ./verify-ca.sh [--skip-issue] [--json]
#
# Exit codes
#   0  — all checks passed
#   1  — one or more checks failed
#
# Compliance: PCI DSS Req. 3.6/3.7 · FIPS 140-2 Level 3
# =============================================================================

set -euo pipefail

SCRIPT_NAME="verify-ca.sh"
SCRIPT_VERSION="1.0.0"

ROOT_CA_PATH="pki"
INT_CA_PATH="pki_int"

# Minimum days of validity remaining before we flag expiry warnings
ROOT_WARN_DAYS=365
INT_WARN_DAYS=90

# YubiHSM connector default address (override via env)
YUBIHSM_CONNECTOR_URL="${YUBIHSM_CONNECTOR_URL:-http://127.0.0.1:12345}"

# OpenBao settings
BAO_ADDR="${BAO_ADDR:-https://vault.internal:8200}"
BAO_CACERT="${BAO_CACERT:-}"

# CLI flags
SKIP_ISSUE=false
JSON_OUTPUT=false

# Counters
CHECKS_PASSED=0
CHECKS_FAILED=0
CHECKS_WARNED=0

# Temp files for issued certs during this run
ISSUED_CERTS=()

# ---------------------------------------------------------------------------
# Colour helpers
#
# Variables hold ANSI codes; expanded via printf '%b' to satisfy SC2059.
# ---------------------------------------------------------------------------
_c_reset="\033[0m"
_c_green="\033[0;32m"
_c_yellow="\033[1;33m"
_c_red="\033[0;31m"
_c_cyan="\033[0;36m"
_c_bold="\033[1m"

log_info()  { printf '%b[INFO]%b  %s\n'    "$_c_green"  "$_c_reset" "$*"; }
log_warn()  { printf '%b[WARN]%b  %s\n'    "$_c_yellow" "$_c_reset" "$*" >&2; }
log_error() { printf '%b[ERROR]%b %s\n'    "$_c_red"    "$_c_reset" "$*" >&2; }
log_step()  { printf '\n%b%b==> %s%b\n'    "$_c_bold"   "$_c_cyan"  "$*" "$_c_reset"; }

pass() {
    CHECKS_PASSED=$((CHECKS_PASSED + 1))
    printf '  %b[PASS]%b %s\n' "$_c_green"  "$_c_reset" "$*"
}

fail() {
    CHECKS_FAILED=$((CHECKS_FAILED + 1))
    printf '  %b[FAIL]%b %s\n' "$_c_red"    "$_c_reset" "$*" >&2
}

warn() {
    CHECKS_WARNED=$((CHECKS_WARNED + 1))
    printf '  %b[WARN]%b %s\n' "$_c_yellow" "$_c_reset" "$*"
}

die() { log_error "$*"; exit 1; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-issue) SKIP_ISSUE=true; shift ;;
            --json)       JSON_OUTPUT=true; shift ;;
            --help|-h)
                cat <<EOF
${SCRIPT_NAME} v${SCRIPT_VERSION}

USAGE: $SCRIPT_NAME [--skip-issue] [--json]

  --skip-issue   Skip the test certificate issuance checks (faster, less thorough)
  --json         Emit a JSON summary to stdout at the end (in addition to human output)
  --help         Show this help

ENVIRONMENT
  BAO_ADDR              OpenBao address        [default: https://vault.internal:8200]
  BAO_CACERT            Path to OpenBao CA cert
  BAO_TOKEN             OpenBao token
  YUBIHSM_CONNECTOR_URL YubiHSM connector URL  [default: http://127.0.0.1:12345]
EOF
                exit 0
                ;;
            *) die "Unknown argument: $1" ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Token resolution
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
    if [[ -t 0 ]]; then
        printf '%bEnter OpenBao token: %b' "$_c_yellow" "$_c_reset"
        read -rs BAO_TOKEN; echo
        export BAO_TOKEN
    else
        die "BAO_TOKEN not set and no TTY available."
    fi
}

# ---------------------------------------------------------------------------
# Cleanup on exit — revoke any issued test certs
# ---------------------------------------------------------------------------
cleanup() {
    if [[ ${#ISSUED_CERTS[@]} -gt 0 ]]; then
        log_info "Cleaning up ${#ISSUED_CERTS[@]} test certificate(s) ..."
        local serial
        for serial in "${ISSUED_CERTS[@]}"; do
            if bao write "${INT_CA_PATH}/revoke" serial_number="$serial" &>/dev/null; then
                log_info "  Revoked: ${serial}"
            else
                log_warn "  Could not revoke: ${serial}  (revoke manually)"
            fi
        done
    fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Days-remaining helper (portable: Linux and macOS)
# ---------------------------------------------------------------------------
days_until() {
    local expiry_date="$1"
    local now_epoch expiry_epoch
    now_epoch=$(date +%s)
    # Try GNU date, then BSD date
    expiry_epoch=$(date -d "$expiry_date" +%s 2>/dev/null \
                   || date -j -f "%b %d %T %Y %Z" "$expiry_date" +%s 2>/dev/null \
                   || echo 0)
    echo $(( (expiry_epoch - now_epoch) / 86400 ))
}

# ---------------------------------------------------------------------------
# CHECK 1 — OpenBao seal status
# ---------------------------------------------------------------------------
check_seal_status() {
    log_step "Check 1: OpenBao seal status"

    local status_json
    if ! status_json=$(bao status -format=json 2>/dev/null); then
        fail "Cannot reach OpenBao at ${BAO_ADDR}"
        return
    fi

    local sealed initialized
    sealed=$(printf '%s' "$status_json"      | jq -r '.sealed')
    initialized=$(printf '%s' "$status_json" | jq -r '.initialized')

    if [[ "$initialized" != "true" ]]; then
        fail "OpenBao is not initialized."
        return
    fi
    pass "OpenBao is initialized."

    if [[ "$sealed" == "true" ]]; then
        fail "OpenBao is SEALED — auto-unseal via YubiHSM may have failed."
    else
        pass "OpenBao is unsealed."
    fi

    local ha_enabled
    ha_enabled=$(printf '%s' "$status_json" | jq -r '.ha_enabled // false')
    if [[ "$ha_enabled" == "true" ]]; then
        local leader_cluster
        leader_cluster=$(printf '%s' "$status_json" | jq -r '.leader_cluster_address // "unknown"')
        pass "HA mode active — leader: ${leader_cluster}"
    else
        warn "HA mode not detected — acceptable for single-node setups."
    fi
}

# ---------------------------------------------------------------------------
# CHECK 2 — YubiHSM connector reachability
# ---------------------------------------------------------------------------
check_yubihsm_connectivity() {
    log_step "Check 2: YubiHSM connector reachability"

    if ! command -v curl &>/dev/null; then
        warn "curl not available — skipping YubiHSM connector check."
        return
    fi

    # The yubihsm-connector exposes a /connector/status endpoint
    local status_url="${YUBIHSM_CONNECTOR_URL}/connector/status"
    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
        --connect-timeout 3 "$status_url" 2>/dev/null || echo "000")

    if [[ "$http_code" == "200" ]]; then
        pass "YubiHSM connector reachable at ${YUBIHSM_CONNECTOR_URL} (HTTP 200)."
    elif [[ "$http_code" == "000" ]]; then
        fail "YubiHSM connector unreachable at ${YUBIHSM_CONNECTOR_URL} — check if yubihsm-connector.service is running."
    else
        warn "YubiHSM connector returned HTTP ${http_code} — check connector configuration."
    fi

    # If pkcs11-tool is available, attempt a device-level ping
    if command -v pkcs11-tool &>/dev/null; then
        local pkcs11_lib="/usr/lib/x86_64-linux-gnu/pkcs11/yubihsm_pkcs11.so"
        if [[ -f "$pkcs11_lib" ]]; then
            if pkcs11-tool --module "$pkcs11_lib" --list-slots &>/dev/null; then
                pass "pkcs11-tool lists YubiHSM slots successfully."
            else
                warn "pkcs11-tool could not list slots — HSM device may not be inserted."
            fi
        else
            warn "YubiHSM PKCS#11 library not found at ${pkcs11_lib} — skipping slot check."
        fi
    fi
}

# ---------------------------------------------------------------------------
# CHECK 3 — Root CA validity
# ---------------------------------------------------------------------------
check_root_ca() {
    log_step "Check 3: Root CA (${ROOT_CA_PATH})"

    # Confirm the PKI engine is mounted
    if ! bao secrets list -format=json 2>/dev/null \
            | jq -e --arg p "${ROOT_CA_PATH}/" 'has($p)' &>/dev/null; then
        fail "PKI engine not mounted at '${ROOT_CA_PATH}'.  Run setup-private-ca.sh first."
        return
    fi
    pass "PKI engine mounted at '${ROOT_CA_PATH}'."

    local root_cert
    if ! root_cert=$(bao read -field=certificate "${ROOT_CA_PATH}/cert/ca" 2>/dev/null); then
        fail "Root CA certificate not found."
        return
    fi
    pass "Root CA certificate present."

    # Parse cert details
    local subject not_after
    subject=$(printf '%s\n' "$root_cert"   | openssl x509 -noout -subject  2>/dev/null | sed 's/subject=//')
    not_after=$(printf '%s\n' "$root_cert" | openssl x509 -noout -enddate  2>/dev/null | sed 's/notAfter=//')

    log_info "  Subject : ${subject}"
    log_info "  Expires : ${not_after}"

    local days_left
    days_left=$(days_until "$not_after")
    if [[ "$days_left" -lt "$ROOT_WARN_DAYS" ]]; then
        warn "Root CA expires in ${days_left} days (threshold: ${ROOT_WARN_DAYS}d)."
    else
        pass "Root CA valid for ${days_left} more days."
    fi

    # Verify it is self-signed (subject == issuer for root CA)
    local issuer
    issuer=$(printf '%s\n' "$root_cert" | openssl x509 -noout -issuer 2>/dev/null | sed 's/issuer=//')
    if [[ "$subject" == "$issuer" ]]; then
        pass "Root CA is self-signed."
    else
        fail "Root CA issuer != subject — chain may be corrupted.  Subject: ${subject}  Issuer: ${issuer}"
    fi

    # Key type and size
    local key_info
    key_info=$(printf '%s\n' "$root_cert" | openssl x509 -noout -text 2>/dev/null \
        | grep -E "Public Key Algorithm|RSA Public-Key|Public-Key" | head -2 | tr '\n' ' ')
    log_info "  Key     : ${key_info}"
}

# ---------------------------------------------------------------------------
# CHECK 4 — Intermediate CA validity and chain
# ---------------------------------------------------------------------------
check_intermediate_ca() {
    log_step "Check 4: Intermediate CA (${INT_CA_PATH})"

    if ! bao secrets list -format=json 2>/dev/null \
            | jq -e --arg p "${INT_CA_PATH}/" 'has($p)' &>/dev/null; then
        fail "PKI engine not mounted at '${INT_CA_PATH}'.  Run setup-private-ca.sh first."
        return
    fi
    pass "PKI engine mounted at '${INT_CA_PATH}'."

    local int_cert
    if ! int_cert=$(bao read -field=certificate "${INT_CA_PATH}/cert/ca" 2>/dev/null); then
        fail "Intermediate CA certificate not found."
        return
    fi
    pass "Intermediate CA certificate present."

    local subject not_after issuer
    subject=$(printf '%s\n' "$int_cert"   | openssl x509 -noout -subject  2>/dev/null | sed 's/subject=//')
    not_after=$(printf '%s\n' "$int_cert" | openssl x509 -noout -enddate  2>/dev/null | sed 's/notAfter=//')
    issuer=$(printf '%s\n' "$int_cert"    | openssl x509 -noout -issuer   2>/dev/null | sed 's/issuer=//')

    log_info "  Subject : ${subject}"
    log_info "  Issuer  : ${issuer}"
    log_info "  Expires : ${not_after}"

    local days_left
    days_left=$(days_until "$not_after")
    if [[ "$days_left" -lt "$INT_WARN_DAYS" ]]; then
        warn "Intermediate CA expires in ${days_left} days (threshold: ${INT_WARN_DAYS}d)."
    else
        pass "Intermediate CA valid for ${days_left} more days."
    fi

    # Verify chain: intermediate signed by root
    local root_cert
    root_cert=$(bao read -field=certificate "${ROOT_CA_PATH}/cert/ca" 2>/dev/null || true)

    if [[ -n "$root_cert" ]]; then
        local tmp_root tmp_int
        tmp_root=$(mktemp /tmp/root-ca-verify.XXXXXX.pem)
        tmp_int=$(mktemp /tmp/int-ca-verify.XXXXXX.pem)
        printf '%s\n' "$root_cert" > "$tmp_root"
        printf '%s\n' "$int_cert"  > "$tmp_int"

        if openssl verify -CAfile "$tmp_root" "$tmp_int" &>/dev/null; then
            pass "Intermediate CA chain verifies against Root CA."
        else
            fail "Intermediate CA chain FAILED verification against Root CA."
        fi
        rm -f "$tmp_root" "$tmp_int"
    else
        warn "Could not retrieve Root CA cert to verify chain."
    fi
}

# ---------------------------------------------------------------------------
# CHECK 5 — Role configuration
# ---------------------------------------------------------------------------
check_roles() {
    log_step "Check 5: PKI role configuration"

    local expected_roles=("mtls-service" "k8s-internal" "origin-https" "pix-payment")

    local listed_roles
    listed_roles=$(bao list -format=json "${INT_CA_PATH}/roles" 2>/dev/null \
        | jq -r '.[]' 2>/dev/null || true)

    local role
    for role in "${expected_roles[@]}"; do
        if printf '%s\n' "$listed_roles" | grep -qxF "$role"; then
            pass "Role present: ${role}"
            # Spot-check key role attributes
            local role_json max_ttl key_type
            role_json=$(bao read -format=json "${INT_CA_PATH}/roles/${role}" 2>/dev/null || true)
            if [[ -n "$role_json" ]]; then
                max_ttl=$(printf '%s' "$role_json" | jq -r '.data.max_ttl // "unknown"')
                key_type=$(printf '%s' "$role_json" | jq -r '.data.key_type // "unknown"')
                log_info "    max_ttl=${max_ttl}  key_type=${key_type}"
            fi
        else
            fail "Role missing: ${role}"
        fi
    done
}

# ---------------------------------------------------------------------------
# CHECK 6 — Issue a test certificate per role and verify chain
# ---------------------------------------------------------------------------
check_cert_issuance() {
    log_step "Check 6: Test certificate issuance"

    if $SKIP_ISSUE; then
        warn "Skipping cert issuance checks (--skip-issue)."
        return
    fi

    # Map each role to a valid test CN using parallel arrays
    local roles=("mtls-service" "k8s-internal" "origin-https" "pix-payment")
    local cns=(
        "verify-test.svc.cluster.local"
        "verify-test.acmetocasino-prod.svc.cluster.local"
        "verify-test.acmetocasino.com"
        "pix.acmetocasino.com"
    )

    local root_cert
    root_cert=$(bao read -field=certificate "${ROOT_CA_PATH}/cert/ca" 2>/dev/null || true)

    local i
    for i in "${!roles[@]}"; do
        local role="${roles[$i]}"
        local test_cn="${cns[$i]}"
        log_info "  Issuing test cert: role=${role}  cn=${test_cn}"

        local result serial
        if ! result=$(bao write -format=json "${INT_CA_PATH}/issue/${role}" \
                        common_name="$test_cn" ttl=1h 2>/dev/null); then
            fail "Failed to issue cert for role: ${role}  cn: ${test_cn}"
            continue
        fi

        serial=$(printf '%s' "$result" | jq -r '.data.serial_number')
        ISSUED_CERTS+=("$serial")

        local leaf_cert issuing_ca_cert
        leaf_cert=$(printf '%s' "$result"       | jq -r '.data.certificate')
        issuing_ca_cert=$(printf '%s' "$result"  | jq -r '.data.issuing_ca')

        # Write to temp files for openssl verify
        local tmp_leaf tmp_chain tmp_root
        tmp_leaf=$(mktemp /tmp/leaf-verify.XXXXXX.pem)
        tmp_chain=$(mktemp /tmp/chain-verify.XXXXXX.pem)
        tmp_root=$(mktemp /tmp/root-verify.XXXXXX.pem)

        printf '%s\n' "$leaf_cert"       > "$tmp_leaf"
        printf '%s\n' "$issuing_ca_cert" > "$tmp_chain"
        if [[ -n "$root_cert" ]]; then
            printf '%s\n' "$root_cert" > "$tmp_root"
        fi

        # Append intermediate to chain for full-chain verify
        if [[ -n "$root_cert" ]]; then
            cat "$tmp_root" >> "$tmp_chain"
            if openssl verify -CAfile "$tmp_chain" "$tmp_leaf" &>/dev/null; then
                pass "Chain verifies for role: ${role}  serial: ${serial}"
            else
                fail "Chain verification FAILED for role: ${role}  serial: ${serial}"
            fi
        else
            warn "Cannot verify full chain for ${role} — Root CA cert unavailable."
        fi

        # Check CN matches
        local issued_cn
        issued_cn=$(printf '%s\n' "$leaf_cert" \
            | openssl x509 -noout -subject 2>/dev/null \
            | sed 's/.*CN\s*=\s*//' | sed 's/,.*//')
        if [[ "$issued_cn" == "$test_cn" ]]; then
            pass "CN matches for role: ${role} (${issued_cn})"
        else
            fail "CN mismatch for role: ${role}  expected: ${test_cn}  got: ${issued_cn}"
        fi

        rm -f "$tmp_leaf" "$tmp_chain" "$tmp_root"
    done
}

# ---------------------------------------------------------------------------
# CHECK 7 — CRL accessibility
# ---------------------------------------------------------------------------
check_crl() {
    log_step "Check 7: CRL accessibility"

    if ! command -v curl &>/dev/null; then
        warn "curl not available — skipping CRL checks."
        return
    fi

    local endpoints=(
        "${BAO_ADDR}/v1/${ROOT_CA_PATH}/crl"
        "${BAO_ADDR}/v1/${INT_CA_PATH}/crl"
    )

    local url
    for url in "${endpoints[@]}"; do
        local cacert_arg=()
        if [[ -n "$BAO_CACERT" ]]; then
            cacert_arg=(--cacert "$BAO_CACERT")
        fi

        local http_code
        http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
            --connect-timeout 5 "${cacert_arg[@]}" "$url" 2>/dev/null || echo "000")

        if [[ "$http_code" == "200" ]]; then
            pass "CRL endpoint reachable: ${url}"
        elif [[ "$http_code" == "000" ]]; then
            warn "CRL endpoint not reachable: ${url}  (acceptable in air-gapped setups)"
        else
            fail "CRL endpoint returned HTTP ${http_code}: ${url}"
        fi
    done
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    local exit_code=0

    printf '\n%b%s v%s — Summary%b\n' "$_c_bold" "$SCRIPT_NAME" "$SCRIPT_VERSION" "$_c_reset"
    printf '%-10s %b%d%b\n' "PASSED:"  "$_c_green"  "$CHECKS_PASSED"  "$_c_reset"
    printf '%-10s %b%d%b\n' "WARNED:"  "$_c_yellow" "$CHECKS_WARNED"  "$_c_reset"
    printf '%-10s %b%d%b\n' "FAILED:"  "$_c_red"    "$CHECKS_FAILED"  "$_c_reset"

    if [[ "$CHECKS_FAILED" -gt 0 ]]; then
        printf '\n%b%bRESULT: FAIL%b — %d check(s) failed.\n' \
            "$_c_red" "$_c_bold" "$_c_reset" "$CHECKS_FAILED"
        exit_code=1
    else
        printf '\n%b%bRESULT: PASS%b — CA chain is healthy.\n' \
            "$_c_green" "$_c_bold" "$_c_reset"
    fi

    if $JSON_OUTPUT; then
        local result_str
        if [[ "$exit_code" -eq 0 ]]; then
            result_str="PASS"
        else
            result_str="FAIL"
        fi
        printf '\n{"script":"%s","version":"%s","passed":%d,"warned":%d,"failed":%d,"result":"%s"}\n' \
            "$SCRIPT_NAME" "$SCRIPT_VERSION" \
            "$CHECKS_PASSED" "$CHECKS_WARNED" "$CHECKS_FAILED" \
            "$result_str"
    fi

    return "$exit_code"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    resolve_token

    printf '\n%b%b%s v%s%b\n' "$_c_bold" "$_c_cyan" "$SCRIPT_NAME" "$SCRIPT_VERSION" "$_c_reset"
    printf '%bAcmeToCasino Platform CA — health verification%b\n' "$_c_cyan" "$_c_reset"
    printf 'BAO_ADDR: %s\n' "$BAO_ADDR"
    if $SKIP_ISSUE; then
        printf '%bTest cert issuance skipped.%b\n' "$_c_yellow" "$_c_reset"
    fi

    check_seal_status
    check_yubihsm_connectivity
    check_root_ca
    check_intermediate_ca
    check_roles
    check_cert_issuance
    check_crl

    print_summary
}

main "$@"
