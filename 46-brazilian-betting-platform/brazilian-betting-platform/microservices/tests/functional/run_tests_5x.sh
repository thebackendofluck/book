#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 46, Building a Brazilian Betting Platform.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# run_tests_5x.sh — Brazilian Betting Platform Integration Test Runner
# =============================================================================
# Starts the integration docker-compose stack, waits for all services to
# become healthy, runs pytest 5 times, prints a results summary table, and
# exits non-zero if ANY run fails.
#
# Usage:
#   cd microservices/
#   bash tests/functional/run_tests_5x.sh [pytest-extra-args...]
#
# Environment (all optional):
#   COMPOSE_FILE        path to integration compose file
#                       (default: ./docker-compose.integration.yml)
#   HEALTH_TIMEOUT      seconds to wait for services (default: 180)
#   PYTEST_ARGS         extra args forwarded to pytest
#   SKIP_BUILD          set to 1 to skip docker compose build
#   KEEP_UP             set to 1 to leave containers running after tests
#   RUNS                number of test runs (default: 5)
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# docker-compose.integration.yml lives one level above tests/functional/
MICROSERVICES_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-${MICROSERVICES_DIR}/docker-compose.integration.yml}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-180}"
RUNS="${RUNS:-5}"
KEEP_UP="${KEEP_UP:-0}"
SKIP_BUILD="${SKIP_BUILD:-0}"

# Colours
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# Result arrays
declare -a RUN_STATUS
declare -a RUN_DURATION
declare -a RUN_PASSED
declare -a RUN_FAILED
declare -a RUN_ERRORS

# ---------------------------------------------------------------------------
# Cleanup on exit
# ---------------------------------------------------------------------------

cleanup() {
  if [[ "${KEEP_UP}" != "1" ]]; then
    echo -e "\n${YELLOW}[cleanup] Stopping integration stack...${RESET}"
    docker compose -f "${COMPOSE_FILE}" down --volumes --remove-orphans \
      2>/dev/null || true
  else
    echo -e "\n${YELLOW}[cleanup] KEEP_UP=1 — leaving containers running.${RESET}"
  fi
}

trap cleanup EXIT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

log_info()  { echo -e "${CYAN}[INFO]  $*${RESET}"; }
log_ok()    { echo -e "${GREEN}[OK]    $*${RESET}"; }
log_warn()  { echo -e "${YELLOW}[WARN]  $*${RESET}"; }
log_error() { echo -e "${RED}[ERROR] $*${RESET}"; }

require_cmd() {
  if ! command -v "$1" &>/dev/null; then
    log_error "Required command not found: $1"
    exit 1
  fi
}

# ---------------------------------------------------------------------------
# Preflight checks
# ---------------------------------------------------------------------------

require_cmd docker
require_cmd python3
require_cmd pytest

log_info "Compose file:     ${COMPOSE_FILE}"
log_info "Microservices dir: ${MICROSERVICES_DIR}"
log_info "Runs:             ${RUNS}"
log_info "Health timeout:   ${HEALTH_TIMEOUT}s"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  log_error "docker-compose.integration.yml not found at ${COMPOSE_FILE}"
  exit 1
fi

# ---------------------------------------------------------------------------
# Start stack
# ---------------------------------------------------------------------------

log_info "Pulling base images..."
docker compose -f "${COMPOSE_FILE}" pull --quiet 2>/dev/null || \
  log_warn "Image pull had warnings (may be fine if images are local)"

if [[ "${SKIP_BUILD}" != "1" ]]; then
  log_info "Building microservice images..."
  docker compose -f "${COMPOSE_FILE}" build --parallel 2>&1 | tail -20
fi

log_info "Starting integration stack..."
docker compose -f "${COMPOSE_FILE}" up -d --remove-orphans

# ---------------------------------------------------------------------------
# Wait for all services to be healthy
# ---------------------------------------------------------------------------

SERVICES=(
  "pam|http://127.0.0.1:8010/health"
  "responsible-gaming|http://127.0.0.1:8020/health"
  "betting-engine|http://127.0.0.1:8080/health"
  "wallet|http://127.0.0.1:8081/health"
  "settlement|http://127.0.0.1:8082/health"
  "odds-feed|http://127.0.0.1:8083/health"
  "bonus-engine|http://127.0.0.1:8030/health"
  "casino-aggregation|http://127.0.0.1:8040/health"
)

log_info "Waiting up to ${HEALTH_TIMEOUT}s for all services to be healthy..."

DEADLINE=$(( $(date +%s) + HEALTH_TIMEOUT ))
ALL_HEALTHY=0

while (( $(date +%s) < DEADLINE )); do
  healthy_count=0
  for entry in "${SERVICES[@]}"; do
    svc="${entry%%|*}"
    url="${entry##*|}"
    if curl -sf --max-time 3 "${url}" >/dev/null 2>&1; then
      (( healthy_count++ )) || true
    fi
  done

  if (( healthy_count == ${#SERVICES[@]} )); then
    ALL_HEALTHY=1
    break
  fi

  remaining=$(( DEADLINE - $(date +%s) ))
  printf "\r${YELLOW}[health] %d/%d healthy — %ds remaining...${RESET}   " \
    "${healthy_count}" "${#SERVICES[@]}" "${remaining}"
  sleep 3
done

echo ""  # newline after progress

if (( ALL_HEALTHY == 0 )); then
  log_error "Not all services became healthy within ${HEALTH_TIMEOUT}s."
  log_error "Container status:"
  docker compose -f "${COMPOSE_FILE}" ps
  log_error "Recent logs:"
  docker compose -f "${COMPOSE_FILE}" logs --tail=30
  exit 1
fi

log_ok "All ${#SERVICES[@]} services are healthy."

# ---------------------------------------------------------------------------
# Run pytest 5 times
# ---------------------------------------------------------------------------

RESULTS_DIR="${SCRIPT_DIR}/results"
mkdir -p "${RESULTS_DIR}"

EXTRA_PYTEST_ARGS="${PYTEST_ARGS:-}"
# Append any args passed directly to this script
if [[ $# -gt 0 ]]; then
  EXTRA_PYTEST_ARGS="${EXTRA_PYTEST_ARGS} $*"
fi

OVERALL_PASS=0

for (( run=1; run<=RUNS; run++ )); do
  log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log_info "Run ${run} of ${RUNS}"
  log_info "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  REPORT_XML="${RESULTS_DIR}/run_${run}_report.xml"
  REPORT_LOG="${RESULTS_DIR}/run_${run}_output.txt"

  RUN_START=$(date +%s%N)

  set +e
  pytest \
    "${SCRIPT_DIR}" \
    --asyncio-mode=auto \
    --tb=short \
    --junit-xml="${REPORT_XML}" \
    --timeout=120 \
    -q \
    ${EXTRA_PYTEST_ARGS} \
    2>&1 | tee "${REPORT_LOG}"
  EXIT_CODE=$?
  set -e

  RUN_END=$(date +%s%N)
  RUN_DURATION_MS=$(( (RUN_END - RUN_START) / 1000000 ))

  # Parse pytest summary from XML
  if [[ -f "${REPORT_XML}" ]]; then
    PASSED=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('${REPORT_XML}')
root = tree.getroot()
suite = root if root.tag == 'testsuite' else root.find('testsuite')
print(suite.get('tests', '0')) if suite is not None else print('0')
" 2>/dev/null || echo "0")
    FAILED=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('${REPORT_XML}')
root = tree.getroot()
suite = root if root.tag == 'testsuite' else root.find('testsuite')
f = int(suite.get('failures', 0)) + int(suite.get('errors', 0)) if suite is not None else 0
print(f)
" 2>/dev/null || echo "0")
    ERRORS=$(python3 -c "
import xml.etree.ElementTree as ET
tree = ET.parse('${REPORT_XML}')
root = tree.getroot()
suite = root if root.tag == 'testsuite' else root.find('testsuite')
print(suite.get('errors', '0')) if suite is not None else print('0')
" 2>/dev/null || echo "0")
  else
    PASSED="?"
    FAILED="?"
    ERRORS="?"
  fi

  RUN_STATUS+=( $EXIT_CODE )
  RUN_DURATION+=( $RUN_DURATION_MS )
  RUN_PASSED+=( "$PASSED" )
  RUN_FAILED+=( "$FAILED" )
  RUN_ERRORS+=( "$ERRORS" )

  if [[ $EXIT_CODE -eq 0 ]]; then
    log_ok "Run ${run} PASSED (${RUN_DURATION_MS}ms)"
  else
    log_error "Run ${run} FAILED (exit ${EXIT_CODE}, ${RUN_DURATION_MS}ms)"
    OVERALL_PASS=1
  fi
done

# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD}  INTEGRATION TEST SUMMARY — Brazilian Betting Platform${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
printf "${BOLD}  %-6s %-10s %-8s %-8s %-8s %-10s${RESET}\n" \
  "Run" "Result" "Tests" "Passed" "Failed" "Duration"
echo -e "  ──────────────────────────────────────────────────────────"

for (( run=1; run<=RUNS; run++ )); do
  idx=$(( run - 1 ))
  status="${RUN_STATUS[$idx]}"
  dur="${RUN_DURATION[$idx]}"
  passed="${RUN_PASSED[$idx]}"
  failed="${RUN_FAILED[$idx]}"

  dur_s=$(python3 -c "print(f'{${dur}/1000:.1f}s')" 2>/dev/null || echo "${dur}ms")

  if [[ "$status" -eq 0 ]]; then
    result_str="${GREEN}PASS${RESET}"
  else
    result_str="${RED}FAIL${RESET}"
  fi

  printf "  %-6s " "${run}"
  echo -e "${result_str}       ${passed:-?}        ${passed:-?}       ${failed:-?}        ${dur_s}"
done

echo -e "  ──────────────────────────────────────────────────────────"

if [[ $OVERALL_PASS -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}  OVERALL: ALL ${RUNS} RUNS PASSED${RESET}"
else
  echo -e "${RED}${BOLD}  OVERALL: ONE OR MORE RUNS FAILED${RESET}"
fi
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo "  Reports saved to: ${RESULTS_DIR}/"
echo ""

exit $OVERALL_PASS
