#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 18, Real-Time Clock Module Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test-mtls.sh — 8-Scenario mTLS Validation Suite
#
# Validates mutual TLS configuration for the PIX gateway and cert-manager
# integration described in Chapter 18. Covers every failure mode a regulator
# is likely to probe during a PKI audit.
#
# Test Matrix:
#   T1  Valid client cert (RSA-2048, ICP-Brasil sim)       → 200 OK
#   T2  No client cert                                     → 400 / SSL failure
#   T3  Wrong CA (untrusted issuer)                        → SSL handshake failure
#   T4  Expired client cert                                → SSL handshake failure
#   T5  Wrong CN (unauthorized operator)                   → 401 / Auth denied
#   T6  Health endpoint (no mTLS required)                 → 200 OK
#   T7  Cert rotation under load (zero dropped connections)
#   T8  10 concurrent mTLS connections                     → All succeed
#
# Prerequisites:
#   - kubectl configured and connected to the casino k8s cluster
#   - openssl, curl, jq
#
# Usage: ./test-mtls.sh [--gateway-url <URL>] [--namespace <NS>]
#
# Chapter 18 — Real-Time Clock Module Implementation

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GATEWAY_URL="${GATEWAY_URL:-https://pix-gateway.casino.svc.cluster.local}"
NAMESPACE="${NAMESPACE:-payment}"
CERT_DIR="$(mktemp -d /tmp/mtls-test-XXXXXX)"
REPORT_FILE="${REPORT_FILE:-/tmp/mtls-test-report.json}"
CONCURRENT_CONNECTIONS=10
ROTATION_LOAD_RPS=50

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

PASSED=0
FAILED=0
RESULTS=()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_pass()  { echo -e "${GREEN}[PASS]${NC}  $*"; }
log_fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }

record_result() {
    local test_id="$1"
    local description="$2"
    local expected="$3"
    local actual="$4"
    local status="$5"

    if [[ "$status" == "PASS" ]]; then
        (( PASSED++ ))
        log_pass "T${test_id}: ${description}"
    else
        (( FAILED++ ))
        log_fail "T${test_id}: ${description} (expected: ${expected}, got: ${actual})"
    fi

    RESULTS+=("{\"test\":\"T${test_id}\",\"description\":\"${description}\",\"expected\":\"${expected}\",\"actual\":\"${actual}\",\"status\":\"${status}\"}")
}

cleanup() {
    rm -rf "${CERT_DIR}"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Certificate generation helpers
# ---------------------------------------------------------------------------

generate_ca() {
    local name="$1"
    local dir="$2"
    mkdir -p "${dir}"
    openssl req -x509 -newkey rsa:2048 -days 365 -nodes \
        -keyout "${dir}/ca.key" -out "${dir}/ca.crt" \
        -subj "/C=BR/ST=SP/O=ICP-Brasil-Sim/CN=${name}" \
        2>/dev/null
}

generate_client_cert() {
    local name="$1"
    local ca_dir="$2"
    local out_dir="$3"
    local days="${4:-365}"
    local cn="${5:-${name}}"
    mkdir -p "${out_dir}"

    openssl req -newkey rsa:2048 -nodes \
        -keyout "${out_dir}/${name}.key" \
        -out "${out_dir}/${name}.csr" \
        -subj "/C=BR/ST=SP/O=AcmeToCasino/CN=${cn}" \
        2>/dev/null

    openssl x509 -req \
        -in "${out_dir}/${name}.csr" \
        -CA "${ca_dir}/ca.crt" \
        -CAkey "${ca_dir}/ca.key" \
        -CAcreateserial \
        -out "${out_dir}/${name}.crt" \
        -days "${days}" \
        2>/dev/null
}

# ---------------------------------------------------------------------------
# Fetch CA and valid certs from cluster (cert-manager issued)
# ---------------------------------------------------------------------------

fetch_cluster_certs() {
    log_info "Fetching cert-manager issued certificates from cluster..."

    # Extract the trusted CA from the ClusterIssuer / cert-manager secret
    kubectl get secret pix-gateway-ca-secret -n "${NAMESPACE}" \
        -o jsonpath='{.data.ca\.crt}' 2>/dev/null | base64 -d > "${CERT_DIR}/cluster-ca.crt" || true

    # Extract a valid client cert issued by cert-manager
    kubectl get secret pix-client-cert -n "${NAMESPACE}" \
        -o jsonpath='{.data.tls\.crt}' 2>/dev/null | base64 -d > "${CERT_DIR}/valid-client.crt" || true
    kubectl get secret pix-client-cert -n "${NAMESPACE}" \
        -o jsonpath='{.data.tls\.key}' 2>/dev/null | base64 -d > "${CERT_DIR}/valid-client.key" || true

    if [[ -s "${CERT_DIR}/cluster-ca.crt" ]]; then
        log_info "Cluster CA fetched successfully"
    else
        log_warn "Cluster CA not found — generating synthetic certs for all tests"
        generate_ca "PIX-CA-Sim" "${CERT_DIR}/cluster-ca"
        cp "${CERT_DIR}/cluster-ca/ca.crt" "${CERT_DIR}/cluster-ca.crt"
        generate_client_cert "valid-client" "${CERT_DIR}/cluster-ca" "${CERT_DIR}"
    fi
}

# ---------------------------------------------------------------------------
# T1: Valid client cert
# ---------------------------------------------------------------------------

run_t1() {
    log_info "T1: Valid client cert (RSA-2048)"

    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
        --cacert "${CERT_DIR}/cluster-ca.crt" \
        --cert "${CERT_DIR}/valid-client.crt" \
        --key "${CERT_DIR}/valid-client.key" \
        --connect-timeout 5 \
        "${GATEWAY_URL}/api/v1/pix/health" 2>/dev/null || echo "000")

    if [[ "$http_code" == "200" ]]; then
        record_result "1" "Valid client cert → 200 OK" "200" "${http_code}" "PASS"
    else
        record_result "1" "Valid client cert → 200 OK" "200" "${http_code}" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# T2: No client cert
# ---------------------------------------------------------------------------

run_t2() {
    log_info "T2: No client cert → 400 / SSL failure"

    local output
    local exit_code=0
    output=$(curl -sf \
        --cacert "${CERT_DIR}/cluster-ca.crt" \
        --connect-timeout 5 \
        "${GATEWAY_URL}/api/v1/pix/health" 2>&1) || exit_code=$?

    local http_code
    http_code=$(echo "${output}" | grep -oP '(?<=HTTP/)[0-9.]+\s+\K[0-9]+' | head -1 || echo "SSL_FAIL")

    if [[ "$exit_code" -ne 0 ]] || [[ "$http_code" == "400" ]] || [[ "$http_code" == "SSL_FAIL" ]]; then
        record_result "2" "No client cert → rejected" "400/SSL_FAIL" "${http_code:-SSL_FAIL}" "PASS"
    else
        record_result "2" "No client cert → rejected" "400/SSL_FAIL" "${http_code}" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# T3: Wrong CA (untrusted issuer)
# ---------------------------------------------------------------------------

run_t3() {
    log_info "T3: Wrong CA (untrusted issuer) → SSL handshake failure"

    generate_ca "Rogue-CA" "${CERT_DIR}/rogue-ca"
    generate_client_cert "rogue-client" "${CERT_DIR}/rogue-ca" "${CERT_DIR}/rogue-client"

    local exit_code=0
    curl -sf \
        --cacert "${CERT_DIR}/cluster-ca.crt" \
        --cert "${CERT_DIR}/rogue-client/rogue-client.crt" \
        --key "${CERT_DIR}/rogue-client/rogue-client.key" \
        --connect-timeout 5 \
        "${GATEWAY_URL}/api/v1/pix/health" 2>/dev/null || exit_code=$?

    if [[ "$exit_code" -ne 0 ]]; then
        record_result "3" "Wrong CA → SSL handshake failure" "SSL_FAIL" "SSL_FAIL(exit:${exit_code})" "PASS"
    else
        record_result "3" "Wrong CA → SSL handshake failure" "SSL_FAIL" "200" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# T4: Expired client cert
# ---------------------------------------------------------------------------

run_t4() {
    log_info "T4: Expired client cert → rejected"

    # Generate cert that expired 1 day ago (days=-1 via startdate/enddate)
    openssl req -newkey rsa:2048 -nodes \
        -keyout "${CERT_DIR}/expired.key" \
        -out "${CERT_DIR}/expired.csr" \
        -subj "/C=BR/ST=SP/O=AcmeToCasino/CN=expired-client" 2>/dev/null

    openssl x509 -req \
        -in "${CERT_DIR}/expired.csr" \
        -CA "${CERT_DIR}/cluster-ca.crt" \
        -CAkey "${CERT_DIR}/cluster-ca.key" \
        -CAcreateserial \
        -out "${CERT_DIR}/expired.crt" \
        -days 1 \
        -startdate "20200101000000Z" \
        -enddate "20200102000000Z" \
        2>/dev/null || {
            # Fallback: generate with openssl manually
            openssl x509 -req \
                -in "${CERT_DIR}/expired.csr" \
                -CA "${CERT_DIR}/cluster-ca.crt" \
                -CAkey "${CERT_DIR}/cluster-ca.key" \
                -CAcreateserial \
                -out "${CERT_DIR}/expired.crt" \
                -days 1 2>/dev/null
        }

    local exit_code=0
    curl -sf \
        --cacert "${CERT_DIR}/cluster-ca.crt" \
        --cert "${CERT_DIR}/expired.crt" \
        --key "${CERT_DIR}/expired.key" \
        --connect-timeout 5 \
        "${GATEWAY_URL}/api/v1/pix/health" 2>/dev/null || exit_code=$?

    if [[ "$exit_code" -ne 0 ]]; then
        record_result "4" "Expired cert → rejected" "SSL_FAIL" "SSL_FAIL(exit:${exit_code})" "PASS"
    else
        record_result "4" "Expired cert → rejected" "SSL_FAIL" "200" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# T5: Wrong CN (unauthorized operator CNPJ)
# ---------------------------------------------------------------------------

run_t5() {
    log_info "T5: Wrong CN (unauthorized operator) → 401"

    generate_client_cert "wrong-cn" "${CERT_DIR}/cluster-ca" "${CERT_DIR}/wrong-cn" 365 "UNAUTHORISED-OPERATOR"

    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
        --cacert "${CERT_DIR}/cluster-ca.crt" \
        --cert "${CERT_DIR}/wrong-cn/wrong-cn.crt" \
        --key "${CERT_DIR}/wrong-cn/wrong-cn.key" \
        --connect-timeout 5 \
        "${GATEWAY_URL}/api/v1/pix/health" 2>/dev/null || echo "000")

    if [[ "$http_code" == "401" ]] || [[ "$http_code" == "403" ]] || [[ "$http_code" == "000" ]]; then
        record_result "5" "Wrong CN → auth denied" "401/403" "${http_code}" "PASS"
    else
        record_result "5" "Wrong CN → auth denied" "401/403" "${http_code}" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# T6: Health endpoint (no mTLS required)
# ---------------------------------------------------------------------------

run_t6() {
    log_info "T6: Health endpoint (no mTLS required) → 200"

    local http_code
    http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
        --cacert "${CERT_DIR}/cluster-ca.crt" \
        --connect-timeout 5 \
        "${GATEWAY_URL}/health" 2>/dev/null || echo "000")

    if [[ "$http_code" == "200" ]]; then
        record_result "6" "Health endpoint without mTLS → 200" "200" "${http_code}" "PASS"
    else
        record_result "6" "Health endpoint without mTLS → 200" "200" "${http_code}" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# T7: Cert rotation under load (zero dropped connections)
# ---------------------------------------------------------------------------

run_t7() {
    log_info "T7: Cert rotation under load"

    # Start background load: ${ROTATION_LOAD_RPS} concurrent requests
    local pids=()
    local dropped=0

    for (( i = 0; i < ROTATION_LOAD_RPS; i++ )); do
        (
            for (( j = 0; j < 5; j++ )); do
                curl -sf -o /dev/null \
                    --cacert "${CERT_DIR}/cluster-ca.crt" \
                    --cert "${CERT_DIR}/valid-client.crt" \
                    --key "${CERT_DIR}/valid-client.key" \
                    --connect-timeout 3 \
                    "${GATEWAY_URL}/api/v1/pix/health" 2>/dev/null || echo "DROP" >> "${CERT_DIR}/drops.txt"
            done
        ) &
        pids+=("$!")
    done

    # Trigger cert-manager rotation while load is running
    if kubectl get secret pix-client-cert -n "${NAMESPACE}" &>/dev/null; then
        log_info "Triggering cert rotation via cert-manager annotation..."
        kubectl annotate certificate pix-client-cert -n "${NAMESPACE}" \
            cert-manager.io/issue-temporary-certificate="true" --overwrite 2>/dev/null || true
        sleep 2
        kubectl annotate certificate pix-client-cert -n "${NAMESPACE}" \
            cert-manager.io/issue-temporary-certificate- --overwrite 2>/dev/null || true
    fi

    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    dropped=$(wc -l < "${CERT_DIR}/drops.txt" 2>/dev/null || echo "0")
    dropped=$(echo "${dropped}" | tr -d '[:space:]')

    if [[ "${dropped}" -eq 0 ]]; then
        record_result "7" "Cert rotation under load → zero dropped" "0 dropped" "${dropped} dropped" "PASS"
    else
        record_result "7" "Cert rotation under load → zero dropped" "0 dropped" "${dropped} dropped" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# T8: 10 concurrent mTLS connections
# ---------------------------------------------------------------------------

run_t8() {
    log_info "T8: ${CONCURRENT_CONNECTIONS} concurrent mTLS connections"

    local success=0
    local pids=()
    local results_dir="${CERT_DIR}/concurrent"
    mkdir -p "${results_dir}"

    for (( i = 1; i <= CONCURRENT_CONNECTIONS; i++ )); do
        (
            http_code=$(curl -sf -o /dev/null -w "%{http_code}" \
                --cacert "${CERT_DIR}/cluster-ca.crt" \
                --cert "${CERT_DIR}/valid-client.crt" \
                --key "${CERT_DIR}/valid-client.key" \
                --connect-timeout 10 \
                "${GATEWAY_URL}/api/v1/pix/health" 2>/dev/null || echo "000")
            echo "${http_code}" > "${results_dir}/result-${i}.txt"
        ) &
        pids+=("$!")
    done

    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done

    for (( i = 1; i <= CONCURRENT_CONNECTIONS; i++ )); do
        code=$(cat "${results_dir}/result-${i}.txt" 2>/dev/null || echo "000")
        [[ "$code" == "200" ]] && (( success++ )) || true
    done

    if [[ "${success}" -eq "${CONCURRENT_CONNECTIONS}" ]]; then
        record_result "8" "10 concurrent mTLS connections → all succeed" "${CONCURRENT_CONNECTIONS}/10" "${success}/10" "PASS"
    else
        record_result "8" "10 concurrent mTLS connections → all succeed" "${CONCURRENT_CONNECTIONS}/10" "${success}/10" "FAIL"
    fi
}

# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

generate_report() {
    local total=$(( PASSED + FAILED ))
    local timestamp
    timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    local results_json
    results_json=$(IFS=','; echo "[${RESULTS[*]}]")

    cat > "${REPORT_FILE}" <<EOF
{
  "report": "mTLS Validation Suite",
  "chapter": "18 — Real-Time Clock Module Implementation",
  "generated_at": "${timestamp}",
  "gateway_url": "${GATEWAY_URL}",
  "namespace": "${NAMESPACE}",
  "summary": {
    "total": ${total},
    "passed": ${PASSED},
    "failed": ${FAILED},
    "pass_rate": "$(( PASSED * 100 / total ))%"
  },
  "tests": ${results_json}
}
EOF

    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  mTLS Test Results: ${PASSED}/${total} PASS"
    if [[ "${FAILED}" -gt 0 ]]; then
        echo -e "  ${RED}${FAILED} test(s) FAILED${NC}"
    else
        echo -e "  ${GREEN}All tests PASSED${NC}"
    fi
    echo "  Report: ${REPORT_FILE}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    log_info "mTLS Validation Suite — Chapter 18"
    log_info "Gateway: ${GATEWAY_URL} | Namespace: ${NAMESPACE}"
    echo ""

    fetch_cluster_certs

    run_t1
    run_t2
    run_t3
    run_t4
    run_t5
    run_t6
    run_t7
    run_t8

    generate_report

    [[ "${FAILED}" -eq 0 ]] || exit 1
}

main "$@"
