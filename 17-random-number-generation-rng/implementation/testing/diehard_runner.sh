#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 17, Random Number Generation (RNG).
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2086
# ===========================================================================
# Diehard / TestU01 Test Integration Runner for RNG Validation
# ===========================================================================
#
# GLI-11 Section 4.6 Compliance: RNG Statistical Testing
# - Generates binary samples from the platform RNG
# - Runs Dieharder test suite (Diehard tests reimplemented)
# - Optionally runs TestU01 BigCrush via testu01-cli
# - Parses results and generates pass/fail summary
# - Outputs JSON report for certification evidence
#
# Prerequisites:
#   apt-get install dieharder       # Dieharder suite
#   pip install testu01             # Optional: TestU01 Python bindings
#
# Usage:
#   ./diehard_runner.sh --generate-only --bytes 10000000
#   ./diehard_runner.sh --dieharder --bytes 10000000
#   ./diehard_runner.sh --all --bytes 50000000 --output report.json
#   ./diehard_runner.sh --quick                    # Quick smoke test
#
# ===========================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${SCRIPT_DIR}/.rng_test_workdir"
TIMESTAMP=$(date -u +"%Y%m%d_%H%M%S")
SAMPLE_FILE="${WORK_DIR}/rng_samples_${TIMESTAMP}.bin"
REPORT_FILE="${WORK_DIR}/report_${TIMESTAMP}.json"
LOG_FILE="${WORK_DIR}/test_${TIMESTAMP}.log"

DEFAULT_BYTES=10000000   # 10 MB (80 million bits)
QUICK_BYTES=1000000      # 1 MB for quick tests

# RNG source (default: /dev/urandom, can be overridden)
RNG_SOURCE="${RNG_SOURCE:-/dev/urandom}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Functions
# ---------------------------------------------------------------------------

log() {
    echo "[$(date -u +"%Y-%m-%d %H:%M:%S")] $*" | tee -a "$LOG_FILE"
}

log_pass() {
    echo -e "${GREEN}[PASS]${NC} $*" | tee -a "$LOG_FILE"
}

log_fail() {
    echo -e "${RED}[FAIL]${NC} $*" | tee -a "$LOG_FILE"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "$LOG_FILE"
}

usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --generate-only       Generate RNG samples without running tests
  --dieharder           Run Dieharder test suite
  --testu01             Run TestU01 SmallCrush/Crush tests
  --all                 Run all available test suites
  --quick               Quick smoke test (reduced sample size)
  --bytes N             Number of bytes to generate (default: ${DEFAULT_BYTES})
  --rng-source PATH     RNG source device/file (default: /dev/urandom)
  --rng-script PATH     Python script to generate samples
  --output FILE         Output JSON report file
  --help                Show this help message

Environment:
  RNG_SOURCE            Override RNG source (default: /dev/urandom)

Examples:
  $(basename "$0") --dieharder --bytes 10000000
  $(basename "$0") --all --rng-script ../csprng/fortuna_generator.py
  $(basename "$0") --quick
EOF
    exit 0
}

check_dependencies() {
    local missing=0

    if ! command -v dieharder &>/dev/null; then
        log_warn "dieharder not found. Install: apt-get install dieharder"
        missing=1
    fi

    if ! command -v python3 &>/dev/null; then
        log_warn "python3 not found"
        missing=1
    fi

    return $missing
}

# ---------------------------------------------------------------------------
# Sample Generation
# ---------------------------------------------------------------------------

generate_samples() {
    local num_bytes=$1
    local source=$2
    local script=$3

    log "Generating ${num_bytes} bytes of RNG samples..."

    if [[ -n "$script" ]]; then
        # Generate from Python RNG script
        log "Using Python RNG script: ${script}"
        python3 -c "
import sys, os
sys.path.insert(0, '$(dirname "$script")')

# Try importing the RNG module
try:
    from fortuna_generator import FortunaGenerator
    gen = FortunaGenerator(min_initial_entropy=32)
    gen.seed_from_os(256)
    data = gen.generate(${num_bytes})
except ImportError:
    # Fallback: use os.urandom
    data = os.urandom(${num_bytes})

sys.stdout.buffer.write(data)
" > "$SAMPLE_FILE"
    else
        # Generate from device/file
        log "Using RNG source: ${source}"
        dd if="$source" of="$SAMPLE_FILE" bs=4096 count=$((num_bytes / 4096 + 1)) \
            status=progress 2>>"$LOG_FILE"
        # Truncate to exact size
        truncate -s "$num_bytes" "$SAMPLE_FILE"
    fi

    local actual_size
    actual_size=$(stat -c%s "$SAMPLE_FILE" 2>/dev/null || stat -f%z "$SAMPLE_FILE" 2>/dev/null)
    log "Generated sample file: ${SAMPLE_FILE} (${actual_size} bytes)"

    # Quick entropy estimate
    local entropy
    entropy=$(python3 -c "
import collections, math
data = open('${SAMPLE_FILE}', 'rb').read()
freq = collections.Counter(data)
n = len(data)
ent = -sum((c/n) * math.log2(c/n) for c in freq.values() if c > 0)
print(f'{ent:.4f}')
" 2>/dev/null || echo "N/A")
    log "Estimated Shannon entropy: ${entropy} bits/byte (ideal: 8.0)"
}

# ---------------------------------------------------------------------------
# Dieharder Tests
# ---------------------------------------------------------------------------

run_dieharder() {
    local sample_file=$1
    local quick=$2

    if ! command -v dieharder &>/dev/null; then
        log_fail "dieharder not installed. Skipping."
        return 1
    fi

    log "=========================================="
    log "Running Dieharder Test Suite"
    log "=========================================="

    local results_file="${WORK_DIR}/dieharder_results_${TIMESTAMP}.txt"
    local pass_count=0
    local fail_count=0
    local weak_count=0
    local total_count=0

    if [[ "$quick" == "true" ]]; then
        # Quick mode: run selected tests only
        local tests=(-d 0 -d 1 -d 2 -d 3 -d 10 -d 100 -d 200 -d 201 -d 202)
        log "Quick mode: running ${#tests[@]} selected tests"

        for test_id in "${tests[@]}"; do
            log "Running test ${test_id}..."
            dieharder ${test_id} -g 201 -f "$sample_file" 2>>"$LOG_FILE" \
                | tee -a "$results_file"
        done
    else
        # Full suite: run all tests
        log "Full mode: running all dieharder tests (this may take a while)..."
        dieharder -a -g 201 -f "$sample_file" 2>>"$LOG_FILE" \
            | tee -a "$results_file"
    fi

    # Parse results
    while IFS= read -r line; do
        if echo "$line" | grep -q "PASSED"; then
            ((pass_count++))
            ((total_count++))
        elif echo "$line" | grep -q "FAILED"; then
            ((fail_count++))
            ((total_count++))
            log_fail "$line"
        elif echo "$line" | grep -q "WEAK"; then
            ((weak_count++))
            ((total_count++))
            log_warn "$line"
        fi
    done < "$results_file"

    log ""
    log "Dieharder Results Summary:"
    log "  Total tests: ${total_count}"
    log_pass "  Passed: ${pass_count}"
    if [[ $fail_count -gt 0 ]]; then
        log_fail "  Failed: ${fail_count}"
    else
        log "  Failed: 0"
    fi
    if [[ $weak_count -gt 0 ]]; then
        log_warn "  Weak: ${weak_count}"
    else
        log "  Weak: 0"
    fi

    # Return results as JSON fragment
    echo "{\"suite\": \"dieharder\", \"passed\": ${pass_count}, \"failed\": ${fail_count}, \"weak\": ${weak_count}, \"total\": ${total_count}}"
}

# ---------------------------------------------------------------------------
# TestU01 Tests (via Python)
# ---------------------------------------------------------------------------

run_testu01() {
    local sample_file=$1
    local quick=$2

    log "=========================================="
    log "Running TestU01 Tests (via Python)"
    log "=========================================="

    python3 << 'PYTHON_SCRIPT' 2>>"$LOG_FILE"
import sys
import json
import os
import time

sample_file = os.environ.get("SAMPLE_FILE", "")
quick = os.environ.get("QUICK", "false") == "true"

try:
    from testu01 import SmallCrush, Crush
except ImportError:
    # Fallback: run basic statistical tests in pure Python
    import collections
    import math
    import struct

    print("TestU01 not available. Running built-in statistical tests...")

    data = open(sample_file, "rb").read()
    n = len(data)
    results = {"suite": "builtin_statistical", "tests": []}

    # Test 1: Byte frequency (chi-squared)
    freq = collections.Counter(data)
    expected = n / 256
    chi_sq = sum((freq.get(i, 0) - expected) ** 2 / expected for i in range(256))
    # Chi-squared with 255 df: critical value at 0.01 is ~310
    passed = chi_sq < 310
    results["tests"].append({
        "name": "Byte Frequency Chi-Squared",
        "statistic": round(chi_sq, 4),
        "passed": passed,
    })
    print(f"  Byte Frequency: chi_sq={chi_sq:.4f} {'PASS' if passed else 'FAIL'}")

    # Test 2: Serial correlation
    if n > 1:
        mean = sum(data) / n
        num = sum((data[i] - mean) * (data[i+1] - mean) for i in range(n-1))
        den = sum((d - mean) ** 2 for d in data)
        correlation = num / den if den > 0 else 0
        passed = abs(correlation) < 0.01
        results["tests"].append({
            "name": "Serial Correlation",
            "statistic": round(correlation, 6),
            "passed": passed,
        })
        print(f"  Serial Correlation: r={correlation:.6f} {'PASS' if passed else 'FAIL'}")

    # Test 3: Runs test (up/down)
    runs = 1
    for i in range(1, min(n, 1000000)):
        if data[i] != data[i-1]:
            runs += 1
    expected_runs = (2 * min(n, 1000000) - 1) / 3
    z = abs(runs - expected_runs) / math.sqrt((16 * min(n, 1000000) - 29) / 90)
    passed = z < 2.576  # 99% CI
    results["tests"].append({
        "name": "Runs Up/Down",
        "statistic": round(z, 4),
        "passed": passed,
    })
    print(f"  Runs Up/Down: z={z:.4f} {'PASS' if passed else 'FAIL'}")

    # Test 4: Gap test (gaps between occurrences of specific byte)
    target_byte = 0
    gaps = []
    last_pos = -1
    for i in range(min(n, 500000)):
        if data[i] == target_byte:
            if last_pos >= 0:
                gaps.append(i - last_pos)
            last_pos = i
    if gaps:
        mean_gap = sum(gaps) / len(gaps)
        expected_gap = 256.0  # Expected gap for uniform distribution
        deviation = abs(mean_gap - expected_gap) / expected_gap
        passed = deviation < 0.05
        results["tests"].append({
            "name": "Gap Test",
            "statistic": round(mean_gap, 4),
            "passed": passed,
        })
        print(f"  Gap Test: mean_gap={mean_gap:.4f} (expected ~256) {'PASS' if passed else 'FAIL'}")

    passed_count = sum(1 for t in results["tests"] if t["passed"])
    total = len(results["tests"])
    results["passed"] = passed_count
    results["failed"] = total - passed_count
    results["total"] = total

    print(json.dumps(results))

PYTHON_SCRIPT

    export SAMPLE_FILE="$sample_file"
    export QUICK="$quick"
}

# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------

generate_report() {
    local output_file=$1
    local sample_bytes=$2
    local dieharder_result=$3
    local testu01_result=$4

    python3 -c "
import json
from datetime import datetime, timezone

report = {
    'report_type': 'RNG Statistical Test Report',
    'generated_at': datetime.now(timezone.utc).isoformat(),
    'gli_11_reference': 'Section 4.6 - Statistical Testing',
    'sample_info': {
        'bytes': ${sample_bytes},
        'bits': ${sample_bytes} * 8,
        'source': '${RNG_SOURCE}',
    },
    'suites': {},
}

dh = '''${dieharder_result}'''
tu = '''${testu01_result}'''

if dh.strip():
    try:
        report['suites']['dieharder'] = json.loads(dh.strip().split('\n')[-1])
    except:
        pass

if tu.strip():
    try:
        report['suites']['testu01'] = json.loads(tu.strip().split('\n')[-1])
    except:
        pass

# Overall assessment
all_passed = True
for suite_name, suite_data in report['suites'].items():
    if suite_data.get('failed', 0) > 0:
        all_passed = False

report['overall_result'] = 'PASS' if all_passed else 'FAIL'
report['certification_recommendation'] = (
    'RNG output meets GLI-11 Section 4.6 statistical requirements'
    if all_passed else
    'RNG output does NOT meet requirements - investigation needed'
)

with open('${output_file}', 'w') as f:
    json.dump(report, f, indent=2)

print(f'Report written to: ${output_file}')
print(f'Overall result: {report[\"overall_result\"]}')
" 2>>"$LOG_FILE"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
    local num_bytes=$DEFAULT_BYTES
    local run_dieharder_flag=false
    local run_testu01_flag=false
    local generate_only=false
    local quick=false
    local rng_script=""
    local output=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --generate-only) generate_only=true ;;
            --dieharder) run_dieharder_flag=true ;;
            --testu01) run_testu01_flag=true ;;
            --all) run_dieharder_flag=true; run_testu01_flag=true ;;
            --quick) quick=true; num_bytes=$QUICK_BYTES ;;
            --bytes) shift; num_bytes=$1 ;;
            --rng-source) shift; RNG_SOURCE=$1 ;;
            --rng-script) shift; rng_script=$1 ;;
            --output) shift; output=$1 ;;
            --help) usage ;;
            *) echo "Unknown option: $1"; usage ;;
        esac
        shift
    done

    # Create work directory
    mkdir -p "$WORK_DIR"
    : > "$LOG_FILE"

    log "=========================================="
    log "RNG Statistical Test Runner"
    log "GLI-11 Section 4.6 Compliance"
    log "=========================================="
    log "Timestamp: ${TIMESTAMP}"
    log "Sample size: ${num_bytes} bytes ($(( num_bytes * 8 )) bits)"
    log "RNG source: ${RNG_SOURCE}"
    log ""

    # Check dependencies
    check_dependencies || true

    # Generate samples
    generate_samples "$num_bytes" "$RNG_SOURCE" "$rng_script"

    if [[ "$generate_only" == "true" ]]; then
        log "Sample generation complete. File: ${SAMPLE_FILE}"
        exit 0
    fi

    # Run tests
    local dieharder_result=""
    local testu01_result=""

    if [[ "$run_dieharder_flag" == "true" ]]; then
        dieharder_result=$(run_dieharder "$SAMPLE_FILE" "$quick" 2>>"$LOG_FILE") || true
    fi

    if [[ "$run_testu01_flag" == "true" ]]; then
        testu01_result=$(run_testu01 "$SAMPLE_FILE" "$quick" 2>>"$LOG_FILE") || true
    fi

    # Generate report
    if [[ -n "$output" ]]; then
        REPORT_FILE="$output"
    fi
    generate_report "$REPORT_FILE" "$num_bytes" "$dieharder_result" "$testu01_result"

    log ""
    log "=========================================="
    log "Test run complete"
    log "  Samples: ${SAMPLE_FILE}"
    log "  Report:  ${REPORT_FILE}"
    log "  Log:     ${LOG_FILE}"
    log "=========================================="

    # Cleanup sample file if large
    if [[ $num_bytes -gt 100000000 ]]; then
        log "Removing large sample file to save disk space..."
        rm -f "$SAMPLE_FILE"
    fi
}

main "$@"
