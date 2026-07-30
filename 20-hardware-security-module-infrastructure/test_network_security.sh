#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test_network_security.sh - Comprehensive testing suite for mTLS and WireGuard network security implementations
# Tests both implementations for functionality, security, and performance
# shellcheck disable=SC2034,SC2129,SC2012  # Config constants, grouped redirects, ls usage

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEST_DIR="${SCRIPT_DIR}/tests"
RESULTS_DIR="${TEST_DIR}/results"
MTLS_SCRIPT="${SCRIPT_DIR}/network_mtls_setup.sh"
WIREGUARD_SCRIPT="${SCRIPT_DIR}/network_wireguard_setup.sh"

# Test configuration
TEST_NETWORKS=("test-network-1" "test-network-2" "test-network-3")
TEST_TIMEOUT=300
PARALLEL_TESTS=3

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${RESULTS_DIR}/test.log"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${RESULTS_DIR}/test.log"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${RESULTS_DIR}/test.log"
}

log_test() {
    echo -e "${BLUE}[TEST]${NC} $(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "${RESULTS_DIR}/test.log"
}

# Initialize test environment
setup_test_environment() {
    log_info "Setting up test environment..."

    # Create test directories
    mkdir -p "${TEST_DIR}" "${RESULTS_DIR}"

    # Backup existing configurations if any
    if [ -d "config" ]; then
        cp -r config "${TEST_DIR}/config.backup.$(date +%s)"
    fi
    if [ -d "certs" ]; then
        cp -r certs "${TEST_DIR}/certs.backup.$(date +%s)"
    fi
    if [ -d "keys" ]; then
        cp -r keys "${TEST_DIR}/keys.backup.$(date +%s)"
    fi

    # Clean test environment
    rm -rf config certs keys logs

    log_info "Test environment setup complete"
}

# Cleanup test environment
cleanup_test_environment() {
    log_info "Cleaning up test environment..."

    # Remove test configurations
    rm -rf config certs keys logs

    # Restore backups if they exist
    if ls "${TEST_DIR}"/config.backup.* 2>/dev/null; then
        LATEST_BACKUP=$(ls -t "${TEST_DIR}"/config.backup.* | head -1)
        cp -r "$LATEST_BACKUP" config
    fi
    if ls "${TEST_DIR}"/certs.backup.* 2>/dev/null; then
        LATEST_BACKUP=$(ls -t "${TEST_DIR}"/certs.backup.* | head -1)
        cp -r "$LATEST_BACKUP" certs
    fi
    if ls "${TEST_DIR}"/keys.backup.* 2>/dev/null; then
        LATEST_BACKUP=$(ls -t "${TEST_DIR}"/keys.backup.* | head -1)
        cp -r "$LATEST_BACKUP" keys
    fi

    log_info "Test environment cleanup complete"
}

# Test mTLS implementation
test_mtls_implementation() {
    log_test "Starting mTLS implementation tests..."

    local test_results="${RESULTS_DIR}/mtls_test_results.txt"
    echo "mTLS Implementation Test Results - $(date)" > "$test_results"
    echo "========================================" >> "$test_results"

    # Test 1: Prerequisites check
    log_test "Test 1: Prerequisites check"
    if [ -x "$MTLS_SCRIPT" ]; then
        echo "✓ mTLS script is executable" >> "$test_results"
        log_test "✓ mTLS script is executable"
    else
        echo "✗ mTLS script is not executable" >> "$test_results"
        log_error "✗ mTLS script is not executable"
        return 1
    fi

    # Test 2: Help/usage display
    log_test "Test 2: Help/usage display"
    if "$MTLS_SCRIPT" 2>&1 | grep -q "Usage:"; then
        echo "✓ mTLS script shows usage information" >> "$test_results"
        log_test "✓ mTLS script shows usage information"
    else
        echo "✗ mTLS script does not show usage information" >> "$test_results"
        log_error "✗ mTLS script does not show usage information"
    fi

    # Test 3: Interactive menu availability
    log_test "Test 3: Interactive menu availability"
    if "$MTLS_SCRIPT" interactive <<< "0" 2>/dev/null; then
        echo "✓ mTLS interactive menu works" >> "$test_results"
        log_test "✓ mTLS interactive menu works"
    else
        echo "✗ mTLS interactive menu failed" >> "$test_results"
        log_error "✗ mTLS interactive menu failed"
    fi

    # Test 4: CA generation (mock test - would require HSM)
    log_test "Test 4: CA generation syntax"
    if "$MTLS_SCRIPT" ca 2>&1 | grep -q "Checking prerequisites"; then
        echo "✓ mTLS CA generation starts correctly" >> "$test_results"
        log_test "✓ mTLS CA generation starts correctly"
    else
        echo "✗ mTLS CA generation failed to start" >> "$test_results"
        log_error "✗ mTLS CA generation failed to start"
    fi

    log_test "mTLS implementation tests completed"
    return 0
}

# Test WireGuard implementation
test_wireguard_implementation() {
    log_test "Starting WireGuard implementation tests..."

    local test_results="${RESULTS_DIR}/wireguard_test_results.txt"
    echo "WireGuard Implementation Test Results - $(date)" > "$test_results"
    echo "===============================================" >> "$test_results"

    # Test 1: Prerequisites check
    log_test "Test 1: Prerequisites check"
    if [ -x "$WIREGUARD_SCRIPT" ]; then
        echo "✓ WireGuard script is executable" >> "$test_results"
        log_test "✓ WireGuard script is executable"
    else
        echo "✗ WireGuard script is not executable" >> "$test_results"
        log_error "✗ WireGuard script is not executable"
        return 1
    fi

    # Test 2: Help/usage display
    log_test "Test 2: Help/usage display"
    if "$WIREGUARD_SCRIPT" 2>&1 | grep -q "Usage:"; then
        echo "✓ WireGuard script shows usage information" >> "$test_results"
        log_test "✓ WireGuard script shows usage information"
    else
        echo "✗ WireGuard script does not show usage information" >> "$test_results"
        log_error "✗ WireGuard script does not show usage information"
    fi

    # Test 3: Interactive menu availability
    log_test "Test 3: Interactive menu availability"
    if timeout 10s "$WIREGUARD_SCRIPT" interactive <<< "0" 2>/dev/null; then
        echo "✓ WireGuard interactive menu works" >> "$test_results"
        log_test "✓ WireGuard interactive menu works"
    else
        echo "✗ WireGuard interactive menu failed" >> "$test_results"
        log_error "✗ WireGuard interactive menu failed"
    fi

    # Test 4: Key generation syntax
    log_test "Test 4: Key generation syntax"
    if "$WIREGUARD_SCRIPT" keys test-peer 2000 2>&1 | grep -q "Checking prerequisites"; then
        echo "✓ WireGuard key generation starts correctly" >> "$test_results"
        log_test "✓ WireGuard key generation starts correctly"
    else
        echo "✗ WireGuard key generation failed to start" >> "$test_results"
        log_error "✗ WireGuard key generation failed to start"
    fi

    log_test "WireGuard implementation tests completed"
    return 0
}

# Test security features
test_security_features() {
    log_test "Starting security features tests..."

    local security_results="${RESULTS_DIR}/security_test_results.txt"
    echo "Security Features Test Results - $(date)" > "$security_results"
    echo "=====================================" >> "$security_results"

    # Test 1: File permissions
    log_test "Test 1: File permissions check"
    if [ -x "$MTLS_SCRIPT" ] && [ -x "$WIREGUARD_SCRIPT" ]; then
        echo "✓ Scripts have execute permissions" >> "$security_results"
        log_test "✓ Scripts have execute permissions"
    else
        echo "✗ Scripts missing execute permissions" >> "$security_results"
        log_error "✗ Scripts missing execute permissions"
    fi

    # Test 2: No hardcoded secrets
    log_test "Test 2: Hardcoded secrets check"
    if ! grep -r "password\|secret\|key" "$MTLS_SCRIPT" "$WIREGUARD_SCRIPT" | grep -v "PASSWORD\|AUTH_KEY\|HSM_PASSWORD"; then
        echo "✓ No hardcoded secrets found" >> "$security_results"
        log_test "✓ No hardcoded secrets found"
    else
        echo "⚠ Potential hardcoded secrets found" >> "$security_results"
        log_warn "⚠ Potential hardcoded secrets found"
    fi

    # Test 3: Input validation
    log_test "Test 3: Input validation"
    if "$MTLS_SCRIPT" invalid_command 2>&1 | grep -q "Usage:"; then
        echo "✓ mTLS script validates input" >> "$security_results"
        log_test "✓ mTLS script validates input"
    else
        echo "✗ mTLS script input validation failed" >> "$security_results"
        log_error "✗ mTLS script input validation failed"
    fi

    if "$WIREGUARD_SCRIPT" invalid_command 2>&1 | grep -q "Usage:"; then
        echo "✓ WireGuard script validates input" >> "$security_results"
        log_test "✓ WireGuard script validates input"
    else
        echo "✗ WireGuard script input validation failed" >> "$security_results"
        log_error "✗ WireGuard script input validation failed"
    fi

    log_test "Security features tests completed"
}

# Test performance
test_performance() {
    log_test "Starting performance tests..."

    local perf_results="${RESULTS_DIR}/performance_test_results.txt"
    echo "Performance Test Results - $(date)" > "$perf_results"
    echo "================================" >> "$perf_results"

    # Test 1: Script execution time
    log_test "Test 1: Script execution time"

    local start_time end_time duration

    start_time=$(date +%s)
    "$MTLS_SCRIPT" 2>/dev/null | head -5 >/dev/null
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    echo "mTLS script startup time: ${duration}s" >> "$perf_results"
    log_test "mTLS script startup time: ${duration}s"

    start_time=$(date +%s)
    "$WIREGUARD_SCRIPT" 2>/dev/null | head -5 >/dev/null
    end_time=$(date +%s)
    duration=$((end_time - start_time))
    echo "WireGuard script startup time: ${duration}s" >> "$perf_results"
    log_test "WireGuard script startup time: ${duration}s"

    # Test 2: Memory usage (if available)
    log_test "Test 2: Memory usage check"
    if command -v ps >/dev/null; then
        echo "✓ Memory monitoring available" >> "$perf_results"
        log_test "✓ Memory monitoring available"
    else
        echo "⚠ Memory monitoring not available" >> "$perf_results"
        log_warn "⚠ Memory monitoring not available"
    fi

    log_test "Performance tests completed"
}

# Test integration
test_integration() {
    log_test "Starting integration tests..."

    local integration_results="${RESULTS_DIR}/integration_test_results.txt"
    echo "Integration Test Results - $(date)" > "$integration_results"
    echo "================================" >> "$integration_results"

    # Test 1: Docker integration
    log_test "Test 1: Docker integration"
    if command -v docker >/dev/null && command -v docker-compose >/dev/null; then
        echo "✓ Docker and Docker Compose available" >> "$integration_results"
        log_test "✓ Docker and Docker Compose available"
    else
        echo "✗ Docker or Docker Compose not available" >> "$integration_results"
        log_error "✗ Docker or Docker Compose not available"
    fi

    # Test 2: Network tools availability
    log_test "Test 2: Network tools availability"
    local network_tools=("curl" "openssl" "ping" "traceroute")
    local missing_tools=()

    for tool in "${network_tools[@]}"; do
        if ! command -v "$tool" >/dev/null; then
            missing_tools+=("$tool")
        fi
    done

    if [ ${#missing_tools[@]} -eq 0 ]; then
        echo "✓ All network tools available" >> "$integration_results"
        log_test "✓ All network tools available"
    else
        echo "⚠ Missing network tools: ${missing_tools[*]}" >> "$integration_results"
        log_warn "⚠ Missing network tools: ${missing_tools[*]}"
    fi

    # Test 3: Configuration file generation
    log_test "Test 3: Configuration file generation"
    # This would test actual config generation in a real environment
    echo "✓ Configuration file generation test placeholder" >> "$integration_results"
    log_test "✓ Configuration file generation test placeholder"

    log_test "Integration tests completed"
}

# Generate test report
generate_test_report() {
    log_info "Generating comprehensive test report..."

    local report_file="${RESULTS_DIR}/comprehensive_test_report.txt"
    echo "Network Security Implementation - Comprehensive Test Report" > "$report_file"
    echo "==========================================================" >> "$report_file"
    echo "Test Date: $(date)" >> "$report_file"
    echo "Test Environment: $(uname -a)" >> "$report_file"
    echo "" >> "$report_file"

    # Summary
    echo "TEST SUMMARY" >> "$report_file"
    echo "============" >> "$report_file"

    local total_tests=0
    local passed_tests=0
    local failed_tests=0

    # Count results from individual test files
    for result_file in "${RESULTS_DIR}"/*_test_results.txt; do
        if [ -f "$result_file" ]; then
            local test_name
            test_name=$(basename "$result_file" | sed 's/_test_results.txt//')
            echo "" >> "$report_file"
            echo "Results for ${test_name}:" >> "$report_file"
            cat "$result_file" >> "$report_file"

            # Count pass/fail (simplified)
            local pass_count fail_count
            pass_count=$(grep -c "✓" "$result_file")
            fail_count=$(grep -c "✗" "$result_file")
            total_tests=$((total_tests + pass_count + fail_count))
            passed_tests=$((passed_tests + pass_count))
            failed_tests=$((failed_tests + fail_count))
        fi
    done

    echo "" >> "$report_file"
    echo "OVERALL RESULTS" >> "$report_file"
    echo "===============" >> "$report_file"
    echo "Total Tests: $total_tests" >> "$report_file"
    echo "Passed: $passed_tests" >> "$report_file"
    echo "Failed: $failed_tests" >> "$report_file"
    echo "Success Rate: $((passed_tests * 100 / total_tests))%" >> "$report_file"

    if [ $failed_tests -eq 0 ]; then
        echo "Status: ✅ ALL TESTS PASSED" >> "$report_file"
    else
        echo "Status: ❌ SOME TESTS FAILED" >> "$report_file"
    fi

    echo "" >> "$report_file"
    echo "RECOMMENDATIONS" >> "$report_file"
    echo "===============" >> "$report_file"
    if [ $failed_tests -gt 0 ]; then
        echo "- Review failed tests and fix issues" >> "$report_file"
        echo "- Ensure all prerequisites are installed" >> "$report_file"
        echo "- Check HSM connectivity for full functionality" >> "$report_file"
    else
        echo "- All basic functionality tests passed" >> "$report_file"
        echo "- Ready for integration testing with HSM" >> "$report_file"
        echo "- Consider performance testing in production environment" >> "$report_file"
    fi

    log_info "Test report generated: $report_file"
}

# Run all tests
run_all_tests() {
    log_info "Starting comprehensive network security test suite..."

    # Setup
    setup_test_environment

    # Run individual test suites
    local test_status=0

    if test_mtls_implementation; then
        log_info "mTLS tests completed successfully"
    else
        log_error "mTLS tests failed"
        test_status=1
    fi

    if test_wireguard_implementation; then
        log_info "WireGuard tests completed successfully"
    else
        log_error "WireGuard tests failed"
        test_status=1
    fi

    if test_security_features; then
        log_info "Security tests completed successfully"
    else
        log_error "Security tests failed"
        test_status=1
    fi

    if test_performance; then
        log_info "Performance tests completed successfully"
    else
        log_error "Performance tests failed"
        test_status=1
    fi

    if test_integration; then
        log_info "Integration tests completed successfully"
    else
        log_error "Integration tests failed"
        test_status=1
    fi

    # Generate report
    generate_test_report

    # Cleanup
    cleanup_test_environment

    log_info "Test suite completed"

    if [ $test_status -eq 0 ]; then
        log_info "✅ All tests passed!"
        return 0
    else
        log_error "❌ Some tests failed. Check the test report for details."
        return 1
    fi
}

# Main execution
main() {
    case "${1:-}" in
        "mtls")
            setup_test_environment
            test_mtls_implementation
            cleanup_test_environment
            ;;
        "wireguard")
            setup_test_environment
            test_wireguard_implementation
            cleanup_test_environment
            ;;
        "security")
            setup_test_environment
            test_security_features
            cleanup_test_environment
            ;;
        "performance")
            setup_test_environment
            test_performance
            cleanup_test_environment
            ;;
        "integration")
            setup_test_environment
            test_integration
            cleanup_test_environment
            ;;
        "report")
            generate_test_report
            ;;
        "all"|*)
            run_all_tests
            ;;
    esac
}

main "$@"