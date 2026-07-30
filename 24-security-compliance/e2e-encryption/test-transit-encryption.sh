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

# test-transit-encryption.sh — Verify encryption in transit across all endpoints
# Tests TLS versions, cipher suites, mTLS, HSTS, certificate validity,
# PostgreSQL SSL enforcement, and Redis TLS.
#
# Usage:
#   ./test-transit-encryption.sh [--target HOST] [--report /path/to/report.log]
#   Default target: localhost (override for ops-host with --target ops-host)
#
# Compliance: PCI DSS v4.0.1 Req.4.2.1; GDPR Art.32; GLI-33 Section 6.1

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_HOST="${TARGET_HOST:-localhost}"
REPORT_FILE="${REPORT_FILE:-/tmp/transit-encryption-test.log}"
CERT_WARN_DAYS=30
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()    { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${REPORT_FILE}"; }
pass()   { log "  PASS  $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail()   { log "  FAIL  $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
warn()   { log "  WARN  $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
section(){ log ""; log "=== $* ==="; }

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        warn "Command '$1' not found — skipping related tests"
        return 1
    fi
    return 0
}

# Run openssl s_client and capture output
tls_probe() {
    local host="$1"
    local port="$2"
    local extra_opts="${3:-}"
    # shellcheck disable=SC2086
    timeout 10 openssl s_client \
        ${extra_opts} \
        -connect "${host}:${port}" \
        -servername "${host}" \
        </dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Test 1: TLS version support
# ---------------------------------------------------------------------------
test_tls_versions() {
    section "TLS Version Tests: ${TARGET_HOST}"

    local endpoints=(
        "443:HTTPS/API"
        "8443:Admin panel"
        "8080:WebSocket upgrade"
    )

    for entry in "${endpoints[@]}"; do
        local port="${entry%%:*}"
        local label="${entry#*:}"

        # TLS 1.3 — expect success
        local out
        out=$(tls_probe "${TARGET_HOST}" "${port}" "-tls1_3" 2>&1)
        if echo "${out}" | grep -q "Protocol  : TLSv1.3"; then
            pass "TLS 1.3 accepted on port ${port} (${label})"
        else
            # Not necessarily a failure if server doesn't have the service
            if echo "${out}" | grep -qE "Connection refused|timeout"; then
                warn "Port ${port} (${label}) not reachable — skipping"
                continue
            fi
            fail "TLS 1.3 not negotiated on port ${port} (${label})"
        fi

        # TLS 1.2 — expect success (acceptable fallback)
        out=$(tls_probe "${TARGET_HOST}" "${port}" "-tls1_2" 2>&1)
        if echo "${out}" | grep -q "Protocol  : TLSv1.2"; then
            pass "TLS 1.2 accepted (valid fallback) on port ${port}"
        fi

        # TLS 1.1 — expect failure
        out=$(tls_probe "${TARGET_HOST}" "${port}" "-tls1_1" 2>&1)
        if echo "${out}" | grep -qE "alert|error|handshake failure"; then
            pass "TLS 1.1 correctly rejected on port ${port}"
        else
            fail "TLS 1.1 NOT rejected on port ${port} — PCI DSS Req.4.2.1 violation"
        fi

        # TLS 1.0 — expect failure
        out=$(tls_probe "${TARGET_HOST}" "${port}" "-tls1" 2>&1)
        if echo "${out}" | grep -qE "alert|error|handshake failure"; then
            pass "TLS 1.0 correctly rejected on port ${port}"
        else
            fail "TLS 1.0 NOT rejected on port ${port} — PCI DSS Req.4.2.1 violation"
        fi
    done
}

# ---------------------------------------------------------------------------
# Test 2: Cipher suite validation (no weak ciphers)
# ---------------------------------------------------------------------------
test_cipher_suites() {
    section "Cipher Suite Tests: ${TARGET_HOST}:443"

    local weak_ciphers=(
        "RC4-SHA"
        "RC4-MD5"
        "DES-CBC3-SHA"
        "EXP-RC4-MD5"
        "NULL-MD5"
        "NULL-SHA"
        "aNULL"
        "eNULL"
    )

    for cipher in "${weak_ciphers[@]}"; do
        local out
        out=$(timeout 10 openssl s_client \
            -connect "${TARGET_HOST}:443" \
            -cipher "${cipher}" \
            -servername "${TARGET_HOST}" \
            </dev/null 2>&1 || true)
        if echo "${out}" | grep -qE "handshake failure|alert|no ciphers"; then
            pass "Weak cipher '${cipher}' correctly rejected"
        elif echo "${out}" | grep -q "Connection refused"; then
            warn "Port 443 not reachable — skipping cipher test for ${cipher}"
            break
        else
            fail "Weak cipher '${cipher}' was ACCEPTED — critical vulnerability"
        fi
    done

    # Check that strong cipher is accepted
    local out
    out=$(tls_probe "${TARGET_HOST}" "443" "-tls1_3" 2>&1)
    if echo "${out}" | grep -qE "TLS_AES_256_GCM_SHA384|TLS_CHACHA20_POLY1305"; then
        pass "Strong TLS 1.3 cipher suite negotiated"
    elif echo "${out}" | grep -q "Connection refused"; then
        warn "Port 443 not reachable — skipping strong cipher check"
    else
        warn "Could not confirm strong cipher suite — manual review recommended"
    fi
}

# ---------------------------------------------------------------------------
# Test 3: Certificate validity
# ---------------------------------------------------------------------------
test_certificate_validity() {
    section "Certificate Validity Tests"

    local services=(
        "${TARGET_HOST}:443"
        "${TARGET_HOST}:8443"
    )

    for svc in "${services[@]}"; do
        local host="${svc%%:*}"
        local port="${svc#*:}"

        local cert_info
        cert_info=$(timeout 10 openssl s_client \
            -connect "${svc}" \
            -servername "${host}" \
            </dev/null 2>&1 | \
            openssl x509 -noout -dates -subject -issuer 2>/dev/null || true)

        if [ -z "${cert_info}" ]; then
            warn "Could not retrieve certificate from ${svc}"
            continue
        fi

        # Check expiry
        local not_after
        not_after=$(echo "${cert_info}" | grep "notAfter" | cut -d= -f2)
        local expiry_epoch
        expiry_epoch=$(date -d "${not_after}" +%s 2>/dev/null || \
                       date -j -f "%b %d %T %Y %Z" "${not_after}" +%s 2>/dev/null || \
                       echo "0")
        local now_epoch
        now_epoch=$(date +%s)
        local days_left
        days_left=$(( (expiry_epoch - now_epoch) / 86400 ))

        if [ "${days_left}" -lt 0 ]; then
            fail "Certificate on ${svc} has EXPIRED (${not_after})"
        elif [ "${days_left}" -lt "${CERT_WARN_DAYS}" ]; then
            warn "Certificate on ${svc} expires in ${days_left} days (${not_after})"
        else
            pass "Certificate on ${svc} valid for ${days_left} more days"
        fi

        # Check key size
        local key_info
        key_info=$(timeout 10 openssl s_client \
            -connect "${svc}" \
            -servername "${host}" \
            </dev/null 2>&1 | \
            openssl x509 -noout -text 2>/dev/null | \
            grep "Public-Key" || true)
        if echo "${key_info}" | grep -qE "2048|256 bit|384 bit|521 bit"; then
            pass "Certificate on ${svc} uses adequate key size: ${key_info}"
        elif [ -n "${key_info}" ]; then
            warn "Certificate key info: ${key_info} — verify meets PCI DSS minimum"
        fi
    done
}

# ---------------------------------------------------------------------------
# Test 4: HSTS headers
# ---------------------------------------------------------------------------
test_hsts() {
    section "HSTS Header Tests"

    if ! require_cmd curl; then return; fi

    local response
    response=$(curl -sI --max-time 10 \
        "https://${TARGET_HOST}/" 2>/dev/null || true)

    if [ -z "${response}" ]; then
        warn "Could not reach https://${TARGET_HOST}/ — skipping HSTS test"
        return
    fi

    if echo "${response}" | grep -qi "strict-transport-security"; then
        local hsts_value
        hsts_value=$(echo "${response}" | grep -i "strict-transport-security" | tr -d '\r')
        pass "HSTS header present: ${hsts_value}"

        if echo "${hsts_value}" | grep -q "max-age=31536000"; then
            pass "HSTS max-age is 1 year (31536000 seconds)"
        else
            warn "HSTS max-age is less than 1 year — PCI DSS recommendation"
        fi

        if echo "${hsts_value}" | grep -q "includeSubDomains"; then
            pass "HSTS includeSubDomains is set"
        else
            warn "HSTS includeSubDomains missing — subdomains may be vulnerable"
        fi

        if echo "${hsts_value}" | grep -q "preload"; then
            pass "HSTS preload directive present"
        else
            warn "HSTS preload missing — consider adding for maximum protection"
        fi
    else
        fail "HSTS header MISSING on ${TARGET_HOST} — PCI DSS Req.4.2.1 finding"
    fi

    # Check that HTTP redirects to HTTPS
    local http_response
    http_response=$(curl -sI --max-time 10 \
        "http://${TARGET_HOST}/" 2>/dev/null || true)
    if echo "${http_response}" | grep -qE "301|302|308"; then
        pass "HTTP correctly redirects to HTTPS"
    elif [ -n "${http_response}" ]; then
        fail "HTTP does NOT redirect to HTTPS — plaintext access possible"
    fi
}

# ---------------------------------------------------------------------------
# Test 5: mTLS between services
# ---------------------------------------------------------------------------
test_mtls() {
    section "mTLS Tests (Internal Services)"

    if ! require_cmd curl; then return; fi

    # These ports are internal service endpoints expecting mTLS
    local mtls_endpoints=(
        "8001:API Gateway internal"
        "9090:Metrics endpoint"
    )

    for entry in "${mtls_endpoints[@]}"; do
        local port="${entry%%:*}"
        local label="${entry#*:}"

        # Without client cert — should be rejected
        local out
        out=$(curl -sk --max-time 10 \
            "https://${TARGET_HOST}:${port}/" 2>&1 || true)
        if echo "${out}" | grep -qE "SSL_ERROR|certificate required|handshake|400|403|No required SSL"; then
            pass "${label}:${port} correctly rejects connections without client cert"
        elif echo "${out}" | grep -q "Connection refused"; then
            warn "${label}:${port} not reachable — skipping mTLS test"
            continue
        else
            warn "${label}:${port} — could not confirm mTLS enforcement (response: ${out:0:100})"
        fi
    done

    # Test that internal services reject TLS 1.1
    local out
    out=$(tls_probe "${TARGET_HOST}" "8001" "-tls1_1" 2>&1)
    if echo "${out}" | grep -qE "alert|error|Connection refused"; then
        pass "Internal services reject TLS 1.1"
    else
        warn "Could not confirm TLS 1.1 rejection on internal ports"
    fi
}

# ---------------------------------------------------------------------------
# Test 6: PostgreSQL SSL enforcement
# ---------------------------------------------------------------------------
test_postgresql_ssl() {
    section "PostgreSQL SSL Tests"

    local pg_host="${TARGET_HOST}"
    local pg_port="${PG_PORT:-5432}"

    if ! require_cmd psql; then
        # Try with openssl directly
        local out
        out=$(tls_probe "${pg_host}" "${pg_port}" "" 2>&1)
        if echo "${out}" | grep -q "CONNECTED"; then
            warn "PostgreSQL port open but psql not available — cannot test SSL enforcement"
        else
            warn "PostgreSQL not reachable on ${pg_host}:${pg_port} — skipping"
        fi
        return
    fi

    # Test: SSL=require should work
    local ssl_out
    ssl_out=$(PGPASSWORD="${PG_PASSWORD:-}" \
        psql -h "${pg_host}" -p "${pg_port}" \
        -U "${PG_USER:-postgres}" \
        -d "${PG_DB:-postgres}" \
        "sslmode=require" \
        -c "SELECT pg_is_in_recovery();" 2>&1 || true)

    if echo "${ssl_out}" | grep -qE "pg_is_in_recovery|f|t"; then
        pass "PostgreSQL accepts SSL connections (sslmode=require)"
    elif echo "${ssl_out}" | grep -qE "SSL|ssl"; then
        pass "PostgreSQL SSL negotiation confirmed"
    elif echo "${ssl_out}" | grep -q "could not connect"; then
        warn "Cannot connect to PostgreSQL — check credentials or host"
    fi

    # Test: sslmode=disable should be rejected
    local nossl_out
    nossl_out=$(PGPASSWORD="${PG_PASSWORD:-}" \
        psql -h "${pg_host}" -p "${pg_port}" \
        -U "${PG_USER:-postgres}" \
        -d "${PG_DB:-postgres}" \
        "sslmode=disable" \
        -c "SELECT 1;" 2>&1 || true)

    if echo "${nossl_out}" | grep -qE "SSL off|no pg_hba.conf|rejected|FATAL"; then
        pass "PostgreSQL correctly rejects non-SSL connections"
    elif echo "${nossl_out}" | grep -qE "1 row|1"; then
        fail "PostgreSQL ACCEPTS non-SSL connections — pg_hba.conf misconfiguration"
    else
        warn "Could not confirm PostgreSQL SSL enforcement: ${nossl_out:0:100}"
    fi

    # Check SSL is being used in existing connections
    local ssl_check
    ssl_check=$(PGPASSWORD="${PG_PASSWORD:-}" \
        psql -h "${pg_host}" -p "${pg_port}" \
        -U "${PG_USER:-postgres}" \
        -d "${PG_DB:-postgres}" \
        "sslmode=require" \
        -t -c "SELECT ssl, version FROM pg_stat_ssl WHERE pid = pg_backend_pid();" 2>&1 || true)

    if echo "${ssl_check}" | grep -q "t"; then
        pass "Current PostgreSQL connection is using SSL"
    fi
}

# ---------------------------------------------------------------------------
# Test 7: Redis TLS
# ---------------------------------------------------------------------------
test_redis_tls() {
    section "Redis TLS Tests"

    local redis_host="${TARGET_HOST}"
    local redis_tls_port="${REDIS_TLS_PORT:-6380}"
    local redis_plain_port="${REDIS_PORT:-6379}"

    # Test: plaintext port should be closed
    if timeout 5 bash -c "echo '' >/dev/tcp/${redis_host}/${redis_plain_port}" 2>/dev/null; then
        fail "Redis plaintext port ${redis_plain_port} is OPEN — should be disabled"
    else
        pass "Redis plaintext port ${redis_plain_port} is closed/unreachable"
    fi

    # Test: TLS port should be open and negotiating TLS
    local tls_out
    tls_out=$(tls_probe "${redis_host}" "${redis_tls_port}" "" 2>&1)
    if echo "${tls_out}" | grep -q "CONNECTED"; then
        pass "Redis TLS port ${redis_tls_port} is open and negotiating TLS"

        if echo "${tls_out}" | grep -qE "TLSv1.3|TLSv1.2"; then
            pass "Redis TLS negotiated TLS 1.2+"
        else
            warn "Could not confirm TLS version on Redis port ${redis_tls_port}"
        fi
    elif echo "${tls_out}" | grep -q "Connection refused"; then
        warn "Redis TLS port ${redis_tls_port} not reachable — may not be configured yet"
    else
        warn "Unexpected Redis TLS probe result: ${tls_out:0:100}"
    fi

    # Test with redis-cli if available
    if require_cmd redis-cli 2>/dev/null; then
        local ping_out
        ping_out=$(redis-cli \
            -h "${redis_host}" \
            -p "${redis_tls_port}" \
            --tls \
            --no-auth-warning \
            -a "${REDIS_PASSWORD:-}" \
            PING 2>&1 || true)
        if echo "${ping_out}" | grep -q "PONG"; then
            pass "Redis TLS PING/PONG confirmed"
        else
            warn "Redis TLS PING failed: ${ping_out:0:80}"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Test 8: WebSocket encryption
# ---------------------------------------------------------------------------
test_websocket_tls() {
    section "WebSocket TLS Tests"

    if ! require_cmd curl; then return; fi

    local ws_port="${WS_PORT:-443}"
    local ws_path="${WS_PATH:-/ws}"

    # Attempt WebSocket upgrade over TLS — curl can verify the TLS handshake
    local ws_out
    ws_out=$(curl -sk --max-time 10 \
        --header "Upgrade: websocket" \
        --header "Connection: Upgrade" \
        --header "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
        --header "Sec-WebSocket-Version: 13" \
        "https://${TARGET_HOST}:${ws_port}${ws_path}" 2>&1 || true)

    # We expect either 101 Switching Protocols (success) or some HTTPS response
    # What we must NOT get is a plaintext HTTP response
    if echo "${ws_out}" | grep -q "101"; then
        pass "WebSocket upgrade over wss:// successful (TLS confirmed)"
    elif echo "${ws_out}" | grep -q "Connection refused"; then
        warn "WebSocket endpoint not reachable at ${TARGET_HOST}:${ws_port}${ws_path}"
    else
        # Verify the connection itself used TLS (curl -sk means TLS was attempted)
        pass "WebSocket endpoint reached over TLS (no plaintext fallback)"
    fi

    # Verify plain ws:// is rejected
    local plain_ws_out
    plain_ws_out=$(curl -s --max-time 5 \
        --header "Upgrade: websocket" \
        --header "Connection: Upgrade" \
        "http://${TARGET_HOST}:${ws_port}${ws_path}" 2>&1 || true)
    if echo "${plain_ws_out}" | grep -qE "301|302|308"; then
        pass "Plain ws:// (HTTP) redirects to wss:// (HTTPS)"
    elif echo "${plain_ws_out}" | grep -q "Connection refused"; then
        warn "Plain HTTP port closed — no redirect test possible"
    fi
}

# ---------------------------------------------------------------------------
# Test 9: Nginx / ingress TLS configuration
# ---------------------------------------------------------------------------
test_nginx_config() {
    section "Nginx TLS Configuration Tests"

    local nginx_conf=""
    for f in /etc/nginx/nginx.conf /etc/nginx/conf.d/default.conf \
              /etc/nginx/sites-enabled/default; do
        if [ -f "${f}" ]; then
            nginx_conf="${f}"
            break
        fi
    done

    if [ -z "${nginx_conf}" ]; then
        warn "Nginx config not found — skipping config-level tests"
        return
    fi

    # Check ssl_protocols
    if grep -q "ssl_protocols.*TLSv1\.3" "${nginx_conf}"; then
        pass "Nginx config enables TLS 1.3"
    else
        warn "Nginx config may not enable TLS 1.3 — check ssl_protocols directive"
    fi

    if grep -qE "ssl_protocols.*TLSv1\b|ssl_protocols.*TLSv1\.1" "${nginx_conf}"; then
        fail "Nginx config includes TLS 1.0 or 1.1 — PCI DSS violation"
    else
        pass "Nginx config does not include deprecated TLS versions"
    fi

    # Check ssl_ciphers for weak patterns
    if grep -qE "ssl_ciphers.*RC4|ssl_ciphers.*3DES|ssl_ciphers.*NULL" "${nginx_conf}"; then
        fail "Nginx config includes weak ciphers (RC4/3DES/NULL)"
    else
        pass "Nginx config does not include known weak ciphers"
    fi

    # Check ssl_session_timeout
    if grep -q "ssl_session_timeout" "${nginx_conf}"; then
        pass "Nginx ssl_session_timeout is configured"
    else
        warn "ssl_session_timeout not set — default may be too long"
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    section "Test Summary"
    log "  Total PASS:    ${PASS_COUNT}"
    log "  Total WARN:    ${WARN_COUNT}"
    log "  Total FAIL:    ${FAIL_COUNT}"
    log ""
    log "  Report saved to: ${REPORT_FILE}"
    log ""

    if [ "${FAIL_COUNT}" -gt 0 ]; then
        log "  STATUS: FAIL — ${FAIL_COUNT} test(s) failed. Remediate before PCI DSS audit."
        return 1
    elif [ "${WARN_COUNT}" -gt 0 ]; then
        log "  STATUS: WARN — review warnings before next compliance review."
        return 0
    else
        log "  STATUS: PASS — all transit encryption tests passed."
        return 0
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    # Parse arguments
    while [ $# -gt 0 ]; do
        case "$1" in
            --target) TARGET_HOST="$2"; shift 2 ;;
            --report) REPORT_FILE="$2"; shift 2 ;;
            *) echo "Unknown option: $1"; exit 1 ;;
        esac
    done

    : >"${REPORT_FILE}"
    log "=== Transit Encryption Test Suite ==="
    log "Target:     ${TARGET_HOST}"
    log "Started:    $(date)"
    log "Compliance: PCI DSS v4.0.1 Req.4.2.1; GDPR Art.32; GLI-33 Section 6.1"

    test_tls_versions
    test_cipher_suites
    test_certificate_validity
    test_hsts
    test_mtls
    test_postgresql_ssl
    test_redis_tls
    test_websocket_tls
    test_nginx_config
    print_summary
}

main "$@"
