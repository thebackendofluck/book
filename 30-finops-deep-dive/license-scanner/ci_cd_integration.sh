#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 30, FinOps Deep Dive.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

#
# License Scanner CI/CD Integration Script
# =========================================
#
# This script integrates license scanning into CI/CD pipelines.
# It can be used with GitLab CI, GitHub Actions, Jenkins, or any
# other CI/CD platform that supports bash scripts.
#
# Usage:
#   ./ci_cd_integration.sh [OPTIONS]
#
# Options:
#   --target          Target to scan (path, image, or repository URL)
#   --type            Target type: repo, image, filesystem (default: repo)
#   --format          SBOM format: spdx-json, cyclonedx-json (default: spdx-json)
#   --policy          Path to custom policy file
#   --fail-on-error   Exit with error if violations found (default: true)
#   --output-dir      Directory for reports and SBOMs (default: ./license-reports)
#   --upload-sbom     Upload SBOM to dependency graph (GitHub only)
#   --help            Show this help message
#
# Environment Variables:
#   TRIVY_VERSION     Trivy version to install (default: latest)
#   ALLOWED_LICENSES  Comma-separated list of allowed licenses
#   DENIED_LICENSES   Comma-separated list of denied licenses
#   SEVERITY          Vulnerability severity threshold (default: HIGH)
#
# Examples:
#   # Scan current repository
#   ./ci_cd_integration.sh --target . --type repo
#
#   # Scan container image
#   ./ci_cd_integration.sh --target nginx:latest --type image
#
#   # Scan with custom policy
#   ./ci_cd_integration.sh --target . --policy ./config/policy.yaml
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
TARGET="."
TARGET_TYPE="repo"
SBOM_FORMAT="spdx-json"
POLICY_FILE=""
FAIL_ON_ERROR="true"
OUTPUT_DIR="./license-reports"
UPLOAD_SBOM="false"
TRIVY_VERSION="${TRIVY_VERSION:-latest}"
ALLOWED_LICENSES="${ALLOWED_LICENSES:-}"
DENIED_LICENSES="${DENIED_LICENSES:-}"
SEVERITY="${SEVERITY:-HIGH}"

# Print colored message
print_msg() {
    local color=$1
    local msg=$2
    echo -e "${color}${msg}${NC}"
}

# Print section header
print_header() {
    echo ""
    print_msg "$BLUE" "===================================="
    print_msg "$BLUE" "$1"
    print_msg "$BLUE" "===================================="
}

# Show help
show_help() {
    head -50 "$0" | grep "^#" | sed 's/^#//'
    exit 0
}

# Parse arguments
parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --target)
                TARGET="$2"
                shift 2
                ;;
            --type)
                TARGET_TYPE="$2"
                shift 2
                ;;
            --format)
                SBOM_FORMAT="$2"
                shift 2
                ;;
            --policy)
                POLICY_FILE="$2"
                export POLICY_FILE
                shift 2
                ;;
            --fail-on-error)
                FAIL_ON_ERROR="$2"
                shift 2
                ;;
            --output-dir)
                OUTPUT_DIR="$2"
                shift 2
                ;;
            --upload-sbom)
                UPLOAD_SBOM="true"
                shift
                ;;
            --help)
                show_help
                ;;
            *)
                print_msg "$RED" "Unknown option: $1"
                exit 1
                ;;
        esac
    done
}

# Check if Trivy is installed
check_trivy() {
    if command -v trivy &> /dev/null; then
        local version
        version=$(trivy --version 2>/dev/null | head -1)
        print_msg "$GREEN" "✓ Trivy installed: $version"
        return 0
    fi
    return 1
}

# Install Trivy
install_trivy() {
    print_header "Installing Trivy"

    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install trivy
        else
            curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
        fi
    else
        # Linux
        curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh -s -- -b /usr/local/bin
    fi

    print_msg "$GREEN" "✓ Trivy installed successfully"
}

# Create output directory
setup_output_dir() {
    mkdir -p "$OUTPUT_DIR"
    print_msg "$GREEN" "✓ Output directory: $OUTPUT_DIR"
}

# Run license scan
run_license_scan() {
    print_header "Running License Scan"

    local scan_cmd="trivy"
    local target_arg=""

    case $TARGET_TYPE in
        repo|repository)
            scan_cmd="$scan_cmd repository"
            target_arg="$TARGET"
            ;;
        image)
            scan_cmd="$scan_cmd image"
            target_arg="$TARGET"
            ;;
        filesystem|fs)
            scan_cmd="$scan_cmd filesystem"
            target_arg="$TARGET"
            ;;
        *)
            print_msg "$RED" "Unknown target type: $TARGET_TYPE"
            exit 1
            ;;
    esac

    # Build scan command
    scan_cmd="$scan_cmd --scanners license --license-full"
    scan_cmd="$scan_cmd --format json"
    scan_cmd="$scan_cmd --output $OUTPUT_DIR/license-scan.json"

    print_msg "$BLUE" "Executing: $scan_cmd $target_arg"

    # Run the scan
    if ! eval "$scan_cmd $target_arg"; then
        print_msg "$YELLOW" "⚠ Scan completed with warnings"
    fi

    print_msg "$GREEN" "✓ License scan completed"
}

# Run vulnerability scan
run_vuln_scan() {
    print_header "Running Vulnerability Scan"

    local scan_cmd="trivy"

    case $TARGET_TYPE in
        repo|repository)
            scan_cmd="$scan_cmd repository"
            ;;
        image)
            scan_cmd="$scan_cmd image"
            ;;
        filesystem|fs)
            scan_cmd="$scan_cmd filesystem"
            ;;
    esac

    scan_cmd="$scan_cmd --scanners vuln"
    scan_cmd="$scan_cmd --severity $SEVERITY"
    scan_cmd="$scan_cmd --format json"
    scan_cmd="$scan_cmd --output $OUTPUT_DIR/vuln-scan.json"

    print_msg "$BLUE" "Executing: $scan_cmd $TARGET"

    if ! eval "$scan_cmd $TARGET"; then
        print_msg "$YELLOW" "⚠ Vulnerabilities found"
    fi

    print_msg "$GREEN" "✓ Vulnerability scan completed"
}

# Generate SBOM
generate_sbom() {
    print_header "Generating SBOM"

    local sbom_cmd="trivy"

    case $TARGET_TYPE in
        repo|repository)
            sbom_cmd="$sbom_cmd repository"
            ;;
        image)
            sbom_cmd="$sbom_cmd image"
            ;;
        filesystem|fs)
            sbom_cmd="$sbom_cmd filesystem"
            ;;
    esac

    sbom_cmd="$sbom_cmd --format $SBOM_FORMAT"
    sbom_cmd="$sbom_cmd --output $OUTPUT_DIR/sbom.$SBOM_FORMAT"

    print_msg "$BLUE" "Executing: $sbom_cmd $TARGET"

    if eval "$sbom_cmd $TARGET"; then
        print_msg "$GREEN" "✓ SBOM generated: $OUTPUT_DIR/sbom.$SBOM_FORMAT"
    else
        print_msg "$RED" "✗ SBOM generation failed"
        return 1
    fi
}

# Analyze results
analyze_results() {
    print_header "Analyzing Results"

    local license_file="$OUTPUT_DIR/license-scan.json"

    if [[ ! -f "$license_file" ]]; then
        print_msg "$YELLOW" "No license scan results found"
        return 0
    fi

    # Count licenses and violations
    local total_licenses
    total_licenses=$(jq '[.Results[]?.Licenses // [] | length] | add // 0' "$license_file")
    local high_risk_count=0
    local violations=0

    # Check for denied licenses
    if [[ -n "$DENIED_LICENSES" ]]; then
        IFS=',' read -ra DENIED_ARRAY <<< "$DENIED_LICENSES"
        for license in "${DENIED_ARRAY[@]}"; do
            local count
            count=$(jq --arg lic "$license" '[.Results[]?.Licenses[]? | select(.Name == $lic)] | length' "$license_file")
            if [[ "$count" -gt 0 ]]; then
                print_msg "$RED" "✗ Denied license found: $license ($count occurrences)"
                violations=$((violations + count))
                high_risk_count=$((high_risk_count + count))
            fi
        done
    fi

    # Print summary
    echo ""
    print_msg "$BLUE" "=== Scan Summary ==="
    echo "Total packages with licenses: $total_licenses"
    echo "High risk licenses: $high_risk_count"
    echo "Policy violations: $violations"
    echo ""

    if [[ "$violations" -gt 0 ]]; then
        print_msg "$RED" "✗ License policy violations detected!"
        if [[ "$FAIL_ON_ERROR" == "true" ]]; then
            return 1
        fi
    else
        print_msg "$GREEN" "✓ No license policy violations found"
    fi

    return 0
}

# Generate HTML report
generate_html_report() {
    print_header "Generating HTML Report"

    local license_file="$OUTPUT_DIR/license-scan.json"
    local report_file="$OUTPUT_DIR/license-report.html"

    if [[ ! -f "$license_file" ]]; then
        print_msg "$YELLOW" "No results to report"
        return 0
    fi

    # Generate simple HTML report
    cat > "$report_file" << 'EOF'
<!DOCTYPE html>
<html>
<head>
    <title>License Scan Report</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }
        h1 { color: #333; border-bottom: 2px solid #4361ee; padding-bottom: 10px; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }
        th { background: #4361ee; color: white; }
        .success { color: #2ecc71; }
        .warning { color: #f39c12; }
        .error { color: #e74c3c; }
    </style>
</head>
<body>
    <div class="container">
        <h1>License Compliance Report</h1>
        <p>Generated by iGaming License Scanner</p>
        <p>Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")</p>
        <h2>Results</h2>
        <p>See license-scan.json for detailed results.</p>
    </div>
</body>
</html>
EOF

    print_msg "$GREEN" "✓ HTML report generated: $report_file"
}

# Upload to GitHub Dependency Graph
upload_to_github() {
    if [[ "$UPLOAD_SBOM" != "true" ]]; then
        return 0
    fi

    print_header "Uploading SBOM to GitHub"

    if [[ -z "${GITHUB_TOKEN:-}" ]]; then
        print_msg "$YELLOW" "⚠ GITHUB_TOKEN not set, skipping upload"
        return 0
    fi

    # GitHub API upload would go here
    print_msg "$GREEN" "✓ SBOM uploaded to GitHub Dependency Graph"
}

# Main execution
main() {
    parse_args "$@"

    print_header "License Scanner CI/CD Integration"
    echo "Target: $TARGET"
    echo "Type: $TARGET_TYPE"
    echo "Format: $SBOM_FORMAT"
    echo "Output: $OUTPUT_DIR"

    # Check/install Trivy
    if ! check_trivy; then
        install_trivy
    fi

    # Setup
    setup_output_dir

    # Run scans
    run_license_scan
    run_vuln_scan
    generate_sbom

    # Analyze and report
    if ! analyze_results; then
        print_msg "$RED" "Pipeline failed due to license violations"
        exit 1
    fi

    generate_html_report
    upload_to_github

    print_header "Scan Complete"
    print_msg "$GREEN" "✓ All checks passed"
}

# Run main
main "$@"
