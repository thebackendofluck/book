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
# k6 iGaming Load Test Runner
# =============================================================================
# Runs one of four test scenarios against a configurable target URL.
# Supports local k6, k6 Cloud, and Docker execution modes.
#
# Usage:
#   ./run.sh [SCENARIO] [OPTIONS]
#
# Scenarios:
#   sustained     Steady-state production load (default)
#   peak          World Cup final simulation
#   spike         Goal / match-start surge
#   soak          4-hour stability run
#
# Options:
#   -u URL           Target base URL (default: https://new.acmetocasino.com)
#   -p PROFILE       Test profile: smoke|load|stress|spike|soak
#   -s SCALE         VU scale multiplier (default: 1)
#   -o OUTPUT        Output format: influxdb|json|cloud (default: none)
#   -t INFLUX_URL    InfluxDB URL for --out influxdb
#   -c               Run via k6 Cloud (requires K6_CLOUD_TOKEN env var)
#   -d               Run via Docker (no local k6 install required)
#   -h               Show this help
#
# Environment variables (override CLI options):
#   BASE_URL         Platform API base URL
#   WS_URL           WebSocket base URL
#   TEST_PROFILE     smoke|load|stress|spike|soak
#   VU_SCALE         Multiplier applied to all VU counts
#   K6_CLOUD_TOKEN   k6 Cloud API token (required for -c)
#   INFLUXDB_URL     InfluxDB URL (e.g. http://localhost:8086/k6)
#
# Examples:
#   ./run.sh sustained -p smoke
#   ./run.sh peak -u https://staging.acmetocasino.com -s 0.1
#   ./run.sh spike -u https://staging.acmetocasino.com -p stress
#   ./run.sh soak -u https://staging.acmetocasino.com -s 0.5 \
#            -o influxdb -t http://localhost:8086/k6
#   ./run.sh sustained -c   # k6 Cloud
#   ./run.sh peak -d        # Docker
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

SCENARIO="${1:-sustained}"
shift || true

BASE_URL="${BASE_URL:-https://new.acmetocasino.com}"
WS_URL="${WS_URL:-wss://new.acmetocasino.com}"
TEST_PROFILE="${TEST_PROFILE:-load}"
VU_SCALE="${VU_SCALE:-1}"
OUTPUT_MODE=""
INFLUX_URL="${INFLUXDB_URL:-http://localhost:8086/k6}"
USE_CLOUD=false
USE_DOCKER=false
EXTRA_ARGS=()

# ---------------------------------------------------------------------------
# Parse CLI options
# ---------------------------------------------------------------------------

while getopts "u:p:s:o:t:cdh" opt; do
  case $opt in
    u) BASE_URL="$OPTARG"     ;;
    p) TEST_PROFILE="$OPTARG" ;;
    s) VU_SCALE="$OPTARG"     ;;
    o) OUTPUT_MODE="$OPTARG"  ;;
    t) INFLUX_URL="$OPTARG"   ;;
    c) USE_CLOUD=true          ;;
    d) USE_DOCKER=true         ;;
    h) sed -n '2,60p' "$0"; exit 0 ;;
    *) echo "Unknown option -$OPTARG" >&2; exit 1 ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve scenario path
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$SCENARIO" in
  sustained|load)   SCRIPT="scenarios/sustained-load.js" ;;
  peak|world-cup)   SCRIPT="scenarios/peak-traffic.js"   ;;
  spike|goal)       SCRIPT="scenarios/spike-test.js"     ;;
  soak|stability)   SCRIPT="scenarios/soak-test.js"      ;;
  *)
    echo "ERROR: Unknown scenario '$SCENARIO'" >&2
    echo "Valid scenarios: sustained, peak, spike, soak" >&2
    exit 1
    ;;
esac

SCRIPT_PATH="${SCRIPT_DIR}/${SCRIPT}"

if [[ ! -f "$SCRIPT_PATH" ]]; then
  echo "ERROR: Script not found: $SCRIPT_PATH" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Build environment args
# ---------------------------------------------------------------------------

ENV_ARGS=(
  "--env" "BASE_URL=${BASE_URL}"
  "--env" "WS_URL=${WS_URL}"
  "--env" "TEST_PROFILE=${TEST_PROFILE}"
  "--env" "VU_SCALE=${VU_SCALE}"
)

# ---------------------------------------------------------------------------
# Build output args
# ---------------------------------------------------------------------------

OUT_ARGS=()
case "$OUTPUT_MODE" in
  influxdb)  OUT_ARGS=("--out" "influxdb=${INFLUX_URL}") ;;
  json)      OUT_ARGS=("--out" "json=results-${SCENARIO}-$(date +%Y%m%d-%H%M%S).json") ;;
  cloud)     OUT_ARGS=("--out" "cloud") ;;
  "")        ;;  # no output destination
  *)         echo "WARN: Unknown output mode '$OUTPUT_MODE', ignored" ;;
esac

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

echo "============================================================"
echo "  k6 iGaming Load Test Runner"
echo "============================================================"
echo "  Scenario:    ${SCENARIO}  (${SCRIPT})"
echo "  Target:      ${BASE_URL}"
echo "  Profile:     ${TEST_PROFILE}"
echo "  VU scale:    ${VU_SCALE}x"
echo "  Output:      ${OUTPUT_MODE:-terminal only}"
echo "  Mode:        $( $USE_CLOUD && echo 'k6 Cloud' || $USE_DOCKER && echo 'Docker' || echo 'Local k6' )"
echo "============================================================"

# Warn about high VU counts
vu_numeric=$(echo "$VU_SCALE" | awk '{print int($1)}')
if [[ "$vu_numeric" -ge 10 ]]; then
  echo ""
  echo "WARNING: VU_SCALE=${VU_SCALE} will generate high load."
  echo "  - Ensure target environment can handle this traffic."
  echo "  - For VU_SCALE >= 100, use distributed k6 execution."
  echo "  - Monitor target servers during the test."
  echo ""
  read -r -p "Continue? [y/N] " confirm
  case "$confirm" in
    [yY][eE][sS]|[yY]) ;;
    *) echo "Aborted."; exit 0 ;;
  esac
fi

# Cloud mode: verify token
if $USE_CLOUD; then
  if [[ -z "${K6_CLOUD_TOKEN:-}" ]]; then
    echo "ERROR: K6_CLOUD_TOKEN must be set for cloud execution." >&2
    echo "  Export it: export K6_CLOUD_TOKEN=<your-token>" >&2
    exit 1
  fi
  EXTRA_ARGS+=("--token" "$K6_CLOUD_TOKEN")
fi

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

if $USE_DOCKER; then
  # Docker mode — mount the scripts directory into the container
  echo "Running via Docker (grafana/k6)..."
  docker run --rm \
    -v "${SCRIPT_DIR}:/scripts" \
    -e "BASE_URL=${BASE_URL}" \
    -e "WS_URL=${WS_URL}" \
    -e "TEST_PROFILE=${TEST_PROFILE}" \
    -e "VU_SCALE=${VU_SCALE}" \
    grafana/k6:latest run \
    "${ENV_ARGS[@]}" \
    "${OUT_ARGS[@]}" \
    "${EXTRA_ARGS[@]}" \
    "/scripts/${SCRIPT}"

elif $USE_CLOUD; then
  # k6 Cloud
  echo "Uploading and running on k6 Cloud..."
  k6 cloud \
    "${ENV_ARGS[@]}" \
    "${OUT_ARGS[@]}" \
    "${EXTRA_ARGS[@]}" \
    "$SCRIPT_PATH"

else
  # Local k6
  if ! command -v k6 &>/dev/null; then
    echo "ERROR: k6 not found in PATH." >&2
    echo "  Install: https://k6.io/docs/getting-started/installation/" >&2
    echo "  Or use -d flag to run via Docker." >&2
    exit 1
  fi

  k6_version=$(k6 version 2>&1 | head -1)
  echo "Using: ${k6_version}"
  echo ""

  k6 run \
    "${ENV_ARGS[@]}" \
    "${OUT_ARGS[@]}" \
    "${EXTRA_ARGS[@]}" \
    "$SCRIPT_PATH"
fi

exit_code=$?

echo ""
echo "============================================================"
if [[ $exit_code -eq 0 ]]; then
  echo "  Test PASSED — all thresholds met"
else
  echo "  Test FAILED — one or more thresholds breached (exit ${exit_code})"
  echo "  Check the threshold summary above for details."
fi
echo "============================================================"

exit $exit_code
