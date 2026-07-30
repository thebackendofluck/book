#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Casino Platform Load Test Suite
# Tests nginx 1.28 + varnish 7.6 + casino-service stack on K3s
#
# Prerequisites:
#   - kubectl configured with casino cluster kubeconfig
#   - hey (HTTP load generator): go install github.com/rakyll/hey@latest
#   - jq for JSON parsing
#
# Usage:
#   ./run-load-test.sh                    # Run all tests
#   ./run-load-test.sh --quick            # Quick smoke test (10s each)
#   ./run-load-test.sh --full             # Full benchmark (60s each)
#   ./run-load-test.sh --concurrent 50000 # Custom concurrency
# =============================================================================

set -euo pipefail

NAMESPACE="casino-prod"
DURATION=30
CONCURRENCY=10000
MODE="standard"
RESULTS_DIR="$(dirname "$0")"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
RESULTS_FILE="${RESULTS_DIR}/BENCHMARK_RESULTS_${TIMESTAMP}.md"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --quick) DURATION=10; CONCURRENCY=1000; MODE="quick" ;;
        --full) DURATION=60; CONCURRENCY=50000; MODE="full" ;;
        --concurrent) CONCURRENCY="$2"; shift ;;
        --duration) DURATION="$2"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
    shift
done

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)]${NC} $*"; }
fail() { echo -e "${RED}[$(date +%H:%M:%S)]${NC} $*"; }

# Detect endpoint
detect_endpoint() {
    local nodeport_http
    local nodeport_https
    local node_ip

    node_ip=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
    nodeport_http=$(kubectl get svc nginx-tls -n "$NAMESPACE" -o jsonpath='{.spec.ports[?(@.name=="http")].nodePort}' 2>/dev/null || echo "")
    nodeport_https=$(kubectl get svc nginx-tls -n "$NAMESPACE" -o jsonpath='{.spec.ports[?(@.name=="https")].nodePort}' 2>/dev/null || echo "")

    if [[ -n "$nodeport_https" ]]; then
        ENDPOINT_HTTPS="https://${node_ip}:${nodeport_https}"
        ENDPOINT_HTTP="http://${node_ip}:${nodeport_http}"
    else
        # Try LoadBalancer IP
        local lb_ip
        lb_ip=$(kubectl get svc nginx-tls -n "$NAMESPACE" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")
        if [[ -n "$lb_ip" ]]; then
            ENDPOINT_HTTPS="https://${lb_ip}"
            ENDPOINT_HTTP="http://${lb_ip}"
        else
            fail "Cannot determine endpoint. Check nginx-tls service."
            exit 1
        fi
    fi
}

# Pre-flight checks
preflight() {
    log "Pre-flight checks..."

    # Check tools
    for tool in kubectl hey jq; do
        if ! command -v "$tool" &>/dev/null; then
            fail "Missing: $tool"
            exit 1
        fi
    done

    # Check cluster
    if ! kubectl get ns "$NAMESPACE" &>/dev/null; then
        fail "Namespace $NAMESPACE not found"
        exit 1
    fi

    # Check pods
    local ready
    ready=$(kubectl get pods -n "$NAMESPACE" -l app=nginx-tls -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' | tr ' ' '\n' | grep -c True || echo 0)
    log "nginx-tls pods ready: $ready"

    ready=$(kubectl get pods -n "$NAMESPACE" -l app=varnish -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' | tr ' ' '\n' | grep -c True || echo 0)
    log "varnish pods ready: $ready"

    ready=$(kubectl get pods -n "$NAMESPACE" -l app=casino-service -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].status}' | tr ' ' '\n' | grep -c True || echo 0)
    log "casino-service pods ready: $ready"

    # Check versions
    local nginx_ver varnish_ver
    nginx_ver=$(kubectl get deploy nginx-tls -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].image}')
    varnish_ver=$(kubectl get deploy varnish -n "$NAMESPACE" -o jsonpath='{.spec.template.spec.containers[0].image}')
    log "nginx image: $nginx_ver"
    log "varnish image: $varnish_ver"

    detect_endpoint
    log "HTTP endpoint: $ENDPOINT_HTTP"
    log "HTTPS endpoint: $ENDPOINT_HTTPS"

    # Health check
    if ! curl -sk "${ENDPOINT_HTTP}/health" &>/dev/null; then
        fail "Health check failed on $ENDPOINT_HTTP/health"
        exit 1
    fi
    log "Health check: OK"
}

# Run a single test
# Usage: run_test <name> <url> <concurrency> <duration> [extra hey flags...]
run_test() {
    local name="$1"
    local url="$2"
    local concurrency="$3"
    local duration="$4"
    shift 4

    log "Running: $name (${concurrency}c, ${duration}s)"

    local output
    output=$(hey -z "${duration}s" -c "$concurrency" -disable-keepalive=false \
        -t 30 "$@" "$url" 2>&1)

    local rps latency_avg latency_p99 errors
    rps=$(echo "$output" | grep "Requests/sec" | awk '{print $2}')
    latency_avg=$(echo "$output" | grep "Average" | head -1 | awk '{print $2}')
    latency_p99=$(echo "$output" | grep "99%" | head -1 | awk '{print $2}')
    errors=$(echo "$output" | grep -c "Error\|error" || echo "0")

    echo "| $name | $concurrency | ${rps} | ${latency_avg}s | ${latency_p99}s | $errors |"

    # Return data for summary
    echo "$name,$rps,$latency_avg,$latency_p99,$errors" >> "/tmp/loadtest_${TIMESTAMP}.csv"
}

# Generate report
generate_report() {
    cat > "$RESULTS_FILE" << EOF
# Load Test Results — ${TIMESTAMP}

**Mode:** ${MODE}
**Duration per test:** ${DURATION}s
**Max concurrency:** ${CONCURRENCY}
**Cluster:** K3s casino ($NAMESPACE)

## Stack Versions

| Component | Image |
|---|---|
| nginx | nginx:1.28-alpine |
| varnish | varnish:7.6 |
| casino-service | casino-service:prod |

## Results

| Test | Concurrency | RPS | Avg Latency | P99 Latency | Errors |
|---|---|---|---|---|---|
EOF

    {
        # HTTP health (baseline)
        run_test "HTTP Health" "${ENDPOINT_HTTP}/health" "$CONCURRENCY" "$DURATION"

        # HTTPS health (TLS overhead)
        run_test "HTTPS Health" "${ENDPOINT_HTTPS}/health" "$CONCURRENCY" "$DURATION" "-h2"

        # HTTPS main page through full stack
        run_test "HTTPS Full Stack" "${ENDPOINT_HTTPS}/" "$CONCURRENCY" "$DURATION" "-h2"

        # WebSocket upgrade test (short burst)
        local ws_conc=$((CONCURRENCY / 10))
        run_test "WS Upgrade" "${ENDPOINT_HTTPS}/ws/" "$ws_conc" "$DURATION" \
            -H "Upgrade: websocket" -H "Connection: Upgrade"

        # Ramp up test
        for c in 100 1000 5000 10000; do
            if [[ $c -le $CONCURRENCY ]]; then
                run_test "Ramp ${c}c" "${ENDPOINT_HTTPS}/health" "$c" "10" "-h2"
            fi
        done

        # HPA scaling test (sustained load)
        if [[ "$MODE" == "full" ]]; then
            run_test "HPA Trigger" "${ENDPOINT_HTTPS}/" "$CONCURRENCY" "120" "-h2"

            # Check HPA state after load
            echo ""
            echo "## HPA State After Load"
            echo '```'
            kubectl get hpa -n "$NAMESPACE"
            echo '```'
        fi

        # Pod resource usage
        echo ""
        echo "## Pod Resource Usage"
        echo '```'
        kubectl top pods -n "$NAMESPACE" --sort-by=cpu 2>/dev/null || echo "metrics-server not available"
        echo '```'
    } >> "$RESULTS_FILE"

    log "Results saved to: $RESULTS_FILE"
}

# Main
main() {
    echo "============================================"
    echo "  Casino Platform Load Test Suite"
    echo "  Mode: ${MODE} | Duration: ${DURATION}s | Concurrency: ${CONCURRENCY}"
    echo "============================================"
    echo ""

    preflight
    echo ""
    generate_report

    log "Done. Results: $RESULTS_FILE"
}

main "$@"
