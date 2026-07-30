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

# shellcheck disable=SC2034,SC2129
# =============================================================================
# Performance Regression CI/CD Gate for Casino Platforms
# =============================================================================
# Runs performance benchmarks as part of the CI/CD pipeline and fails the build
# if regressions exceed defined thresholds.
#
# Features:
#   - API response time regression detection
#   - Load test baseline comparison
#   - Memory/CPU regression checking
#   - Database query performance gates
#   - WebSocket latency gates
#   - Generates HTML + JSON reports
#   - Integrates with GitHub Actions, GitLab CI, Jenkins
#
# Usage:
#   ./perf_regression_gate.sh --env staging --baseline latest
#   ./perf_regression_gate.sh --env staging --baseline baseline_v2.4.json --threshold 15
#
# Exit codes:
#   0 = All performance gates passed
#   1 = Performance regressions detected (build should fail)
#   2 = Configuration or setup error
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${PROJECT_ROOT:-$(git rev-parse --show-toplevel 2>/dev/null || echo "$SCRIPT_DIR/../..")}"

# Defaults
ENV="${ENV:-staging}"
BASELINE_FILE="${BASELINE_FILE:-latest}"
REGRESSION_THRESHOLD_PCT="${REGRESSION_THRESHOLD_PCT:-15}"  # Max allowed regression %
API_SLO_P95_MS="${API_SLO_P95_MS:-500}"
GAME_ROUND_SLO_P95_MS="${GAME_ROUND_SLO_P95_MS:-300}"
DB_QUERY_SLO_P95_MS="${DB_QUERY_SLO_P95_MS:-50}"
WS_LATENCY_SLO_P95_MS="${WS_LATENCY_SLO_P95_MS:-100}"
LOAD_TEST_DURATION="${LOAD_TEST_DURATION:-120}"  # seconds
LOAD_TEST_USERS="${LOAD_TEST_USERS:-100}"
REPORT_DIR="${REPORT_DIR:-/tmp/perf-regression-reports}"
BASELINE_STORE="${BASELINE_STORE:-s3://casino-perf-baselines}"

# Casino platform URLs
STAGING_API_URL="${STAGING_API_URL:-https://staging-api.casino.example.com}"
STAGING_WS_URL="${STAGING_WS_URL:-wss://staging-ws.casino.example.com}"

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Argument Parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --env)        ENV="$2"; shift 2;;
        --baseline)   BASELINE_FILE="$2"; shift 2;;
        --threshold)  REGRESSION_THRESHOLD_PCT="$2"; shift 2;;
        --users)      LOAD_TEST_USERS="$2"; shift 2;;
        --duration)   LOAD_TEST_DURATION="$2"; shift 2;;
        --report-dir) REPORT_DIR="$2"; shift 2;;
        --api-url)    STAGING_API_URL="$2"; shift 2;;
        --skip-load)  SKIP_LOAD_TEST=true; shift;;
        --skip-db)    SKIP_DB_TEST=true; shift;;
        --help)
            echo "Usage: $0 [options]"
            echo "  --env <staging|production>  Target environment (default: staging)"
            echo "  --baseline <file|latest>    Baseline file to compare against"
            echo "  --threshold <percent>       Max regression threshold (default: 15)"
            echo "  --users <count>             Concurrent users for load test (default: 100)"
            echo "  --duration <seconds>        Load test duration (default: 120)"
            echo "  --report-dir <path>         Report output directory"
            echo "  --api-url <url>             API URL to test"
            echo "  --skip-load                 Skip load testing"
            echo "  --skip-db                   Skip database tests"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 2;;
    esac
done

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
mkdir -p "$REPORT_DIR"
TIMESTAMP=$(date -u +%Y%m%d_%H%M%S)
RESULTS_FILE="$REPORT_DIR/perf_results_${TIMESTAMP}.json"
COMPARISON_FILE="$REPORT_DIR/perf_comparison_${TIMESTAMP}.json"
HTML_REPORT="$REPORT_DIR/perf_report_${TIMESTAMP}.html"
GATE_RESULT="PASS"
FAILURES=()

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $1"; GATE_RESULT="FAIL"; FAILURES+=("$1"); }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

echo "============================================================"
echo "  Casino Performance Regression Gate"
echo "  Environment:  $ENV"
echo "  Threshold:    ${REGRESSION_THRESHOLD_PCT}%"
echo "  Timestamp:    $TIMESTAMP"
echo "  Commit:       $(git rev-parse --short HEAD 2>/dev/null || echo 'N/A')"
echo "============================================================"
echo ""

# ---------------------------------------------------------------------------
# Check Prerequisites
# ---------------------------------------------------------------------------
check_tool() {
    if ! command -v "$1" &>/dev/null; then
        log_warn "Tool not found: $1 — installing..."
        case "$1" in
            k6)
                if [[ "$(uname)" == "Linux" ]]; then
                    curl -sL https://github.com/grafana/k6/releases/download/v0.49.0/k6-v0.49.0-linux-amd64.tar.gz \
                        | tar xz -C /tmp && sudo mv /tmp/k6-v0.49.0-linux-amd64/k6 /usr/local/bin/
                else
                    brew install k6 2>/dev/null || true
                fi
                ;;
            jq) sudo apt-get install -y jq 2>/dev/null || brew install jq 2>/dev/null || true;;
            curl) sudo apt-get install -y curl 2>/dev/null || true;;
        esac

        if ! command -v "$1" &>/dev/null; then
            log_fail "Required tool not available: $1"
            exit 2
        fi
    fi
}

check_tool curl
check_tool jq

# ---------------------------------------------------------------------------
# Fetch Baseline
# ---------------------------------------------------------------------------
fetch_baseline() {
    local baseline_path="$REPORT_DIR/baseline.json"

    if [[ "$BASELINE_FILE" == "latest" ]]; then
        log_info "Fetching latest baseline from artifact store..."
        if command -v aws &>/dev/null; then
            aws s3 cp "${BASELINE_STORE}/${ENV}/latest.json" "$baseline_path" 2>/dev/null || true
        fi
        if [[ ! -f "$baseline_path" ]]; then
            log_warn "No baseline found — this run will establish the baseline"
            echo '{"metrics": {}}' > "$baseline_path"
        fi
    elif [[ -f "$BASELINE_FILE" ]]; then
        cp "$BASELINE_FILE" "$baseline_path"
    else
        log_warn "Baseline file not found: $BASELINE_FILE — using empty baseline"
        echo '{"metrics": {}}' > "$baseline_path"
    fi

    echo "$baseline_path"
}

BASELINE_PATH=$(fetch_baseline)
log_info "Baseline loaded from: $BASELINE_PATH"

# ---------------------------------------------------------------------------
# Gate 1: API Response Time Tests
# ---------------------------------------------------------------------------
run_api_tests() {
    log_info "Gate 1: API Response Time Tests"
    local results_api="$REPORT_DIR/api_results.json"

    # Define critical casino endpoints
    local endpoints=(
        "GET /api/v1/lobby/games lobby_games 200"
        "GET /api/v1/player/profile player_profile 150"
        "GET /api/v1/wallet/balance player_balance 100"
        "GET /api/v1/promotions/active bonus_list 250"
        "GET /api/v1/player/history?limit=50 game_history 300"
        "GET /api/v1/tournaments/leaderboard/weekly leaderboard 350"
    )

    echo '{"api_results": []}' > "$results_api"
    local iterations=20

    for endpoint_def in "${endpoints[@]}"; do
        read -r method path name slo_ms <<< "$endpoint_def"
        local latencies=()
        local errors=0

        for ((i=1; i<=iterations; i++)); do
            local start_ns
            start_ns=$(date +%s%N)
            local http_code
            http_code=$(curl -s -o /dev/null -w "%{http_code}" \
                -X "$method" \
                -H "Authorization: Bearer $AUTH_TOKEN" \
                -H "Content-Type: application/json" \
                --connect-timeout 5 \
                --max-time 10 \
                "${STAGING_API_URL}${path}" 2>/dev/null || echo "000")
            local end_ns
            end_ns=$(date +%s%N)

            local duration_ms=$(( (end_ns - start_ns) / 1000000 ))
            latencies+=("$duration_ms")

            if [[ "$http_code" == "000" ]] || [[ "$http_code" -ge 500 ]]; then
                ((errors++))
            fi
        done

        # Calculate P95
        local sorted_latencies
        sorted_latencies=$(printf '%s\n' "${latencies[@]}" | sort -n)
        local p95_index=$(( (iterations * 95) / 100 ))
        local p95_ms
        p95_ms=$(echo "$sorted_latencies" | sed -n "${p95_index}p")

        local mean_ms=0
        local sum=0
        for lat in "${latencies[@]}"; do sum=$((sum + lat)); done
        mean_ms=$((sum / iterations))

        # Compare against SLO
        if [[ "$p95_ms" -gt "$slo_ms" ]]; then
            log_fail "API $name: P95=${p95_ms}ms exceeds SLO=${slo_ms}ms"
        else
            log_pass "API $name: P95=${p95_ms}ms (SLO=${slo_ms}ms)"
        fi

        # Compare against baseline
        local baseline_p95
        baseline_p95=$(jq -r ".metrics.api_${name}_p95_ms // 0" "$BASELINE_PATH")
        if [[ "$baseline_p95" != "0" ]] && [[ "$baseline_p95" != "null" ]]; then
            local regression_pct=$(( (p95_ms - baseline_p95) * 100 / baseline_p95 ))
            if [[ "$regression_pct" -gt "$REGRESSION_THRESHOLD_PCT" ]]; then
                log_fail "API $name: ${regression_pct}% regression (baseline=${baseline_p95}ms, current=${p95_ms}ms)"
            elif [[ "$regression_pct" -gt 0 ]]; then
                log_warn "API $name: ${regression_pct}% slower (baseline=${baseline_p95}ms)"
            fi
        fi

        # Record results
        jq --arg name "$name" --arg p95 "$p95_ms" --arg mean "$mean_ms" \
           --arg errors "$errors" --arg slo "$slo_ms" \
           '.api_results += [{"name": $name, "p95_ms": ($p95|tonumber), "mean_ms": ($mean|tonumber), "errors": ($errors|tonumber), "slo_ms": ($slo|tonumber)}]' \
           "$results_api" > "$results_api.tmp" && mv "$results_api.tmp" "$results_api"
    done

    echo ""
}

# ---------------------------------------------------------------------------
# Gate 2: Load Test
# ---------------------------------------------------------------------------
run_load_test() {
    if [[ "${SKIP_LOAD_TEST:-false}" == "true" ]]; then
        log_info "Gate 2: Load Test (SKIPPED)"
        return
    fi

    log_info "Gate 2: Load Test (${LOAD_TEST_USERS} users, ${LOAD_TEST_DURATION}s)"

    if ! command -v k6 &>/dev/null; then
        log_warn "k6 not available — generating load test script for manual execution"
        cat > "$REPORT_DIR/casino_load_test.js" << 'K6SCRIPT'
import http from 'k6/http';
import ws from 'k6/ws';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom casino metrics
const betLatency = new Trend('casino_bet_latency', true);
const lobbyLatency = new Trend('casino_lobby_latency', true);
const errorRate = new Rate('casino_error_rate');

export const options = {
  stages: [
    { duration: '30s', target: __ENV.USERS || 100 },   // Ramp up
    { duration: '60s', target: __ENV.USERS || 100 },   // Sustained load
    { duration: '30s', target: __ENV.USERS * 2 || 200 }, // Peak load
    { duration: '30s', target: 0 },                      // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<500', 'p(99)<1000'],
    casino_bet_latency: ['p(95)<300'],
    casino_lobby_latency: ['p(95)<200'],
    casino_error_rate: ['rate<0.01'],
    http_req_failed: ['rate<0.01'],
  },
};

const BASE_URL = __ENV.API_URL || 'https://staging-api.casino.example.com';
const AUTH_TOKEN = __ENV.AUTH_TOKEN || 'load-test-token';

const headers = {
  'Authorization': `Bearer ${AUTH_TOKEN}`,
  'Content-Type': 'application/json',
};

export default function () {
  // Simulate player session: lobby -> select game -> place bets
  const scenario = Math.random();

  if (scenario < 0.4) {
    // 40%: Browse lobby
    const lobbyStart = Date.now();
    const lobbyRes = http.get(`${BASE_URL}/api/v1/lobby/games`, { headers });
    lobbyLatency.add(Date.now() - lobbyStart);
    check(lobbyRes, { 'lobby 200': (r) => r.status === 200 });
    errorRate.add(lobbyRes.status >= 500);

  } else if (scenario < 0.75) {
    // 35%: Place a bet
    const betStart = Date.now();
    const betRes = http.post(`${BASE_URL}/api/v1/games/slots/bet`, JSON.stringify({
      game_id: 'starburst-xxxtreme',
      stake_cents: Math.floor(Math.random() * 500) + 50,
      currency: 'EUR',
      lines: 20,
    }), { headers });
    betLatency.add(Date.now() - betStart);
    check(betRes, { 'bet 200': (r) => r.status === 200 || r.status === 201 });
    errorRate.add(betRes.status >= 500);

  } else if (scenario < 0.9) {
    // 15%: Check balance
    const balRes = http.get(`${BASE_URL}/api/v1/wallet/balance`, { headers });
    check(balRes, { 'balance 200': (r) => r.status === 200 });
    errorRate.add(balRes.status >= 500);

  } else {
    // 10%: View game history
    const histRes = http.get(`${BASE_URL}/api/v1/player/history?limit=20`, { headers });
    check(histRes, { 'history 200': (r) => r.status === 200 });
    errorRate.add(histRes.status >= 500);
  }

  sleep(Math.random() * 2 + 0.5); // 0.5-2.5s think time
}
K6SCRIPT
        log_info "Load test script written to: $REPORT_DIR/casino_load_test.js"
        log_info "Run manually: k6 run --env API_URL=$STAGING_API_URL --env USERS=$LOAD_TEST_USERS $REPORT_DIR/casino_load_test.js"
        return
    fi

    # Run k6 load test
    k6 run \
        --env "API_URL=$STAGING_API_URL" \
        --env "USERS=$LOAD_TEST_USERS" \
        --env "AUTH_TOKEN=${AUTH_TOKEN:-load-test-token}" \
        --out "json=$REPORT_DIR/k6_results_${TIMESTAMP}.json" \
        --summary-export "$REPORT_DIR/k6_summary_${TIMESTAMP}.json" \
        "$REPORT_DIR/casino_load_test.js" 2>&1 | tee "$REPORT_DIR/k6_output.log"

    local k6_exit=$?
    if [[ $k6_exit -ne 0 ]]; then
        log_fail "Load test thresholds violated (k6 exit code: $k6_exit)"
    else
        log_pass "Load test passed all thresholds"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Gate 3: Database Query Performance
# ---------------------------------------------------------------------------
run_db_tests() {
    if [[ "${SKIP_DB_TEST:-false}" == "true" ]]; then
        log_info "Gate 3: Database Query Performance (SKIPPED)"
        return
    fi

    log_info "Gate 3: Database Query Performance"

    local db_host="${DB_HOST:-staging-db.casino.internal}"
    local db_name="${DB_NAME:-casino_staging}"
    local db_user="${DB_USER:-benchmark_reader}"

    if ! command -v psql &>/dev/null; then
        log_warn "psql not available — skipping database performance tests"
        return
    fi

    # Critical queries to benchmark
    declare -A queries=(
        ["player_lookup"]="SELECT id, username, status FROM players WHERE id = 1"
        ["balance_check"]="SELECT balance_cents FROM wallets WHERE player_id = 1"
        ["recent_bets"]="SELECT * FROM game_rounds WHERE player_id = 1 ORDER BY created_at DESC LIMIT 50"
        ["active_sessions"]="SELECT COUNT(*) FROM player_sessions WHERE expires_at > NOW()"
        ["leaderboard"]="SELECT p.username, SUM(gr.payout-gr.stake) as net FROM game_rounds gr JOIN players p ON gr.player_id=p.id WHERE gr.created_at >= NOW()-INTERVAL '7d' GROUP BY p.username ORDER BY net DESC LIMIT 100"
    )

    for query_name in "${!queries[@]}"; do
        local query="${queries[$query_name]}"
        local total_ms=0
        local iterations=10

        for ((i=1; i<=iterations; i++)); do
            local timing
            timing=$(PGPASSWORD="${DB_PASSWORD:-}" psql -h "$db_host" -U "$db_user" -d "$db_name" \
                -c "\\timing on" -c "$query" 2>/dev/null | grep "Time:" | awk '{print $2}' || echo "9999")
            total_ms=$(echo "$total_ms + $timing" | bc 2>/dev/null || echo "$total_ms")
        done

        local avg_ms
        avg_ms=$(echo "scale=2; $total_ms / $iterations" | bc 2>/dev/null || echo "0")

        if (( $(echo "$avg_ms > $DB_QUERY_SLO_P95_MS" | bc -l 2>/dev/null || echo 0) )); then
            log_fail "DB $query_name: avg=${avg_ms}ms exceeds SLO=${DB_QUERY_SLO_P95_MS}ms"
        else
            log_pass "DB $query_name: avg=${avg_ms}ms (SLO=${DB_QUERY_SLO_P95_MS}ms)"
        fi
    done
    echo ""
}

# ---------------------------------------------------------------------------
# Gate 4: WebSocket Latency
# ---------------------------------------------------------------------------
run_ws_tests() {
    log_info "Gate 4: WebSocket Latency"

    if ! command -v websocat &>/dev/null; then
        log_warn "websocat not available — using curl for basic WS health check"
        local ws_health
        ws_health=$(curl -s -o /dev/null -w "%{http_code}" \
            "${STAGING_WS_URL/wss:/https:}/health" 2>/dev/null || echo "000")
        if [[ "$ws_health" == "200" ]]; then
            log_pass "WebSocket health endpoint reachable"
        else
            log_warn "WebSocket health check returned: $ws_health"
        fi
        return
    fi

    local iterations=20
    local latencies=()

    for ((i=1; i<=iterations; i++)); do
        local start_ns
        start_ns=$(date +%s%N)
        echo '{"type":"ping","id":"'$i'"}' | timeout 5 websocat -1 "$STAGING_WS_URL" 2>/dev/null
        local end_ns
        end_ns=$(date +%s%N)
        local duration_ms=$(( (end_ns - start_ns) / 1000000 ))
        latencies+=("$duration_ms")
    done

    if [[ ${#latencies[@]} -gt 0 ]]; then
        local sorted
        sorted=$(printf '%s\n' "${latencies[@]}" | sort -n)
        local p95_index=$(( (${#latencies[@]} * 95) / 100 ))
        local ws_p95
        ws_p95=$(echo "$sorted" | sed -n "${p95_index}p")

        if [[ "$ws_p95" -gt "$WS_LATENCY_SLO_P95_MS" ]]; then
            log_fail "WebSocket P95: ${ws_p95}ms exceeds SLO=${WS_LATENCY_SLO_P95_MS}ms"
        else
            log_pass "WebSocket P95: ${ws_p95}ms (SLO=${WS_LATENCY_SLO_P95_MS}ms)"
        fi
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Gate 5: Memory and Resource Checks
# ---------------------------------------------------------------------------
run_resource_checks() {
    log_info "Gate 5: Resource Usage Checks"

    if command -v kubectl &>/dev/null; then
        # Check pod resource usage
        local high_mem_pods
        high_mem_pods=$(kubectl top pods -n casino-platform --no-headers 2>/dev/null \
            | awk '$3 ~ /[0-9]+Mi/ {gsub(/Mi/,"",$3); if($3 > 1500) print $1, $3"Mi"}' || true)

        if [[ -n "$high_mem_pods" ]]; then
            log_warn "High memory pods detected:"
            echo "$high_mem_pods" | while read -r line; do echo "    $line"; done
        else
            log_pass "Pod memory usage within limits"
        fi

        # Check for OOMKilled containers
        local oom_pods
        oom_pods=$(kubectl get pods -n casino-platform -o json 2>/dev/null \
            | jq -r '.items[].status.containerStatuses[]? | select(.lastState.terminated.reason == "OOMKilled") | .name' || true)

        if [[ -n "$oom_pods" ]]; then
            log_fail "OOMKilled containers detected: $oom_pods"
        else
            log_pass "No OOMKilled containers"
        fi
    else
        log_warn "kubectl not available — skipping resource checks"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# Generate Report
# ---------------------------------------------------------------------------
generate_report() {
    log_info "Generating performance report..."

    cat > "$HTML_REPORT" << 'HTMLEOF'
<!DOCTYPE html>
<html>
<head>
<title>Casino Performance Regression Report</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 20px; background: #1a1a2e; color: #e0e0e0; }
h1 { color: #00d4ff; border-bottom: 2px solid #00d4ff; padding-bottom: 10px; }
h2 { color: #7b68ee; }
.pass { color: #00ff88; font-weight: bold; }
.fail { color: #ff4444; font-weight: bold; }
.warn { color: #ffaa00; font-weight: bold; }
table { border-collapse: collapse; width: 100%; margin: 15px 0; }
th, td { padding: 10px 15px; text-align: left; border: 1px solid #333; }
th { background: #16213e; color: #00d4ff; }
tr:nth-child(even) { background: #0f3460; }
.summary-box { display: inline-block; padding: 20px; margin: 10px; background: #16213e; border-radius: 8px; min-width: 150px; text-align: center; }
.summary-value { font-size: 2em; font-weight: bold; }
</style>
</head>
<body>
HTMLEOF

    echo "<h1>Casino Performance Regression Report</h1>" >> "$HTML_REPORT"
    echo "<p>Generated: $(date -u +'%Y-%m-%d %H:%M:%S UTC') | Commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'N/A') | Env: $ENV</p>" >> "$HTML_REPORT"

    # Summary boxes
    local total_gates=5
    local failed_count=${#FAILURES[@]}
    local passed_count=$((total_gates - failed_count))

    echo "<div>" >> "$HTML_REPORT"
    echo "<div class='summary-box'><div class='summary-value pass'>$passed_count</div>Gates Passed</div>" >> "$HTML_REPORT"
    echo "<div class='summary-box'><div class='summary-value fail'>$failed_count</div>Gates Failed</div>" >> "$HTML_REPORT"
    echo "<div class='summary-box'><div class='summary-value'>${REGRESSION_THRESHOLD_PCT}%</div>Threshold</div>" >> "$HTML_REPORT"
    echo "</div>" >> "$HTML_REPORT"

    if [[ "$GATE_RESULT" == "FAIL" ]]; then
        echo "<h2 class='fail'>RESULT: BUILD FAILED</h2>" >> "$HTML_REPORT"
        echo "<h3>Failures:</h3><ul>" >> "$HTML_REPORT"
        for failure in "${FAILURES[@]}"; do
            echo "<li class='fail'>$failure</li>" >> "$HTML_REPORT"
        done
        echo "</ul>" >> "$HTML_REPORT"
    else
        echo "<h2 class='pass'>RESULT: ALL GATES PASSED</h2>" >> "$HTML_REPORT"
    fi

    echo "</body></html>" >> "$HTML_REPORT"
    log_info "HTML report: $HTML_REPORT"
}

# ---------------------------------------------------------------------------
# Save New Baseline
# ---------------------------------------------------------------------------
save_baseline() {
    if [[ "$GATE_RESULT" == "PASS" ]]; then
        log_info "Saving current results as new baseline..."
        local new_baseline="$REPORT_DIR/baseline_${TIMESTAMP}.json"

        # Combine all results into baseline
        if [[ -f "$REPORT_DIR/api_results.json" ]]; then
            local metrics="{}"
            while IFS= read -r line; do
                local name
                name=$(echo "$line" | jq -r '.name')
                local p95
                p95=$(echo "$line" | jq -r '.p95_ms')
                metrics=$(echo "$metrics" | jq --arg k "api_${name}_p95_ms" --arg v "$p95" '. + {($k): ($v|tonumber)}')
            done < <(jq -c '.api_results[]' "$REPORT_DIR/api_results.json" 2>/dev/null)

            jq -n --argjson metrics "$metrics" \
                '{timestamp: now, commit: "'"$(git rev-parse --short HEAD 2>/dev/null || echo 'N/A')"'", env: "'"$ENV"'", metrics: $metrics}' \
                > "$new_baseline"
        fi

        # Upload to S3 if aws cli available
        if command -v aws &>/dev/null; then
            aws s3 cp "$new_baseline" "${BASELINE_STORE}/${ENV}/latest.json" 2>/dev/null || true
            aws s3 cp "$new_baseline" "${BASELINE_STORE}/${ENV}/baseline_${TIMESTAMP}.json" 2>/dev/null || true
            log_info "Baseline uploaded to S3"
        fi
    else
        log_warn "Baseline NOT updated due to gate failures"
    fi
}

# ---------------------------------------------------------------------------
# Main Execution
# ---------------------------------------------------------------------------
AUTH_TOKEN="${AUTH_TOKEN:-staging-perf-test-token}"

run_api_tests
run_load_test
run_db_tests
run_ws_tests
run_resource_checks

generate_report
save_baseline

# Final result
echo ""
echo "============================================================"
if [[ "$GATE_RESULT" == "PASS" ]]; then
    echo -e "  ${GREEN}PERFORMANCE GATE: PASSED${NC}"
    echo "  All ${#FAILURES[@]} failures out of 5 gates"
else
    echo -e "  ${RED}PERFORMANCE GATE: FAILED${NC}"
    echo "  ${#FAILURES[@]} failure(s) detected:"
    for failure in "${FAILURES[@]}"; do
        echo "    - $failure"
    done
fi
echo "  Report: $HTML_REPORT"
echo "============================================================"

[[ "$GATE_RESULT" == "PASS" ]] && exit 0 || exit 1
