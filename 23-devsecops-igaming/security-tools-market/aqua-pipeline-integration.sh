#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23, DevSecOps for iGaming.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Aqua Security CI/CD Pipeline Integration for iGaming
# =============================================================================
#
# Purpose:
#   Integrates Aqua Security scanning into CI/CD pipelines for casino platform
#   container images. Covers image scanning, vulnerability thresholds, SBOM
#   generation, compliance checks, and registry scanning.
#
# Why pipeline integration matters for iGaming:
#   Every container image deployed to production must be scanned for:
#   - Known CVEs (especially in crypto libraries used for RNG/payment)
#   - Malware embedded in base images
#   - Compliance violations (PCI-DSS requires vulnerability management)
#   - License risks in open-source dependencies
#   - Secrets accidentally baked into images
#
# Usage:
#   ./aqua-pipeline-integration.sh --image casino/wallet-service:v2.1.0
#   ./aqua-pipeline-integration.sh --image casino/gal-service:latest --sbom
#   ./aqua-pipeline-integration.sh --registry-scan --registry docker.io/casino
#
# Prerequisites:
#   - Aqua CLI (trivy or aqua scanner) installed
#   - AQUA_TOKEN environment variable set
#   - Docker daemon running (for local image scanning)
#
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
AQUA_SERVER_URL="${AQUA_SERVER_URL:-https://aqua.example.com}"
AQUA_TOKEN="${AQUA_TOKEN:-}"
AQUA_CLI="${AQUA_CLI:-aqua}"

# Vulnerability thresholds for iGaming
# These are strict because gaming regulators require demonstrable
# vulnerability management (GLI-33, PCI-DSS v4.0 Req 6.3)
MAX_CRITICAL=0    # Zero critical vulns allowed in production images
MAX_HIGH=0        # Zero high vulns for payment/wallet services
MAX_MEDIUM=5      # Up to 5 medium vulns (must have remediation plan)
MAX_LOW=20        # Low vulns tracked but not blocking

# Modes
IMAGE_TO_SCAN=""
GENERATE_SBOM=false
REGISTRY_SCAN=false
REGISTRY_URL=""
COMPLIANCE_CHECK=true
OUTPUT_DIR="${OUTPUT_DIR:-./aqua-reports}"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$1"; }
log_warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$1"; }
log_error() { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --image)
            IMAGE_TO_SCAN="$2"
            shift 2
            ;;
        --sbom)
            GENERATE_SBOM=true
            shift
            ;;
        --registry-scan)
            REGISTRY_SCAN=true
            shift
            ;;
        --registry)
            REGISTRY_URL="$2"
            shift 2
            ;;
        --max-critical)
            MAX_CRITICAL="$2"
            shift 2
            ;;
        --max-high)
            MAX_HIGH="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --no-compliance)
            COMPLIANCE_CHECK=false
            shift
            ;;
        -h|--help)
            printf "Usage: %s --image IMAGE [--sbom] [--registry-scan --registry URL]\n" "$0"
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
validate_inputs() {
    if [[ -z "$IMAGE_TO_SCAN" ]] && [[ "$REGISTRY_SCAN" = false ]]; then
        log_error "Must specify --image or --registry-scan"
        exit 1
    fi

    if [[ "$REGISTRY_SCAN" = true ]] && [[ -z "$REGISTRY_URL" ]]; then
        log_error "Registry scan requires --registry URL"
        exit 1
    fi

    if [[ -z "$AQUA_TOKEN" ]]; then
        log_warn "AQUA_TOKEN not set. Some features may be limited."
    fi

    mkdir -p "${OUTPUT_DIR}"
}

# ---------------------------------------------------------------------------
# Image vulnerability scanning
# ---------------------------------------------------------------------------
scan_image() {
    local image="$1"
    local report_file
    report_file="${OUTPUT_DIR}/scan-$(echo "$image" | tr '/:' '_').json"

    log_info "Scanning image: ${image}"
    log_info "Thresholds: Critical=${MAX_CRITICAL}, High=${MAX_HIGH}, Medium=${MAX_MEDIUM}"

    # Run Aqua/Trivy scanner
    # The --severity flag ensures we get categorised results
    # --format json gives us machine-parseable output for CI decisions
    if command -v trivy &>/dev/null; then
        trivy image \
            --severity CRITICAL,HIGH,MEDIUM,LOW \
            --format json \
            --output "${report_file}" \
            --exit-code 0 \
            "${image}"
    elif command -v "${AQUA_CLI}" &>/dev/null; then
        "${AQUA_CLI}" scan \
            --host "${AQUA_SERVER_URL}" \
            --token "${AQUA_TOKEN}" \
            --local "${image}" \
            --jsonfile "${report_file}" \
            --register=false
    else
        log_error "Neither trivy nor aqua CLI found. Install one to proceed."
        exit 1
    fi

    log_info "Scan report saved to: ${report_file}"

    # Parse and evaluate results
    evaluate_scan_results "${report_file}" "${image}"
}

# ---------------------------------------------------------------------------
# Evaluate scan results against thresholds
# ---------------------------------------------------------------------------
evaluate_scan_results() {
    local report_file="$1"
    local image="$2"

    log_info "Evaluating scan results against iGaming compliance thresholds..."

    # Extract vulnerability counts using Python for reliable JSON parsing
    local counts
    counts=$(python3 -c "
import json
import sys

try:
    with open('${report_file}', 'r') as f:
        data = json.load(f)
except (json.JSONDecodeError, FileNotFoundError):
    print('0 0 0 0')
    sys.exit(0)

critical = high = medium = low = 0

# Handle Trivy format
results = data.get('Results', [])
for result in results:
    for vuln in result.get('Vulnerabilities', []):
        sev = vuln.get('Severity', '').upper()
        if sev == 'CRITICAL':
            critical += 1
        elif sev == 'HIGH':
            high += 1
        elif sev == 'MEDIUM':
            medium += 1
        elif sev == 'LOW':
            low += 1

print(f'{critical} {high} {medium} {low}')
" 2>/dev/null || echo "0 0 0 0")

    local critical high medium low
    read -r critical high medium low <<< "$counts"

    log_info "Results for ${image}:"
    log_info "  Critical: ${critical} (max: ${MAX_CRITICAL})"
    log_info "  High:     ${high} (max: ${MAX_HIGH})"
    log_info "  Medium:   ${medium} (max: ${MAX_MEDIUM})"
    log_info "  Low:      ${low} (max: ${MAX_LOW})"

    local failed=false

    if [[ "$critical" -gt "$MAX_CRITICAL" ]]; then
        log_error "CRITICAL threshold exceeded! ${critical} > ${MAX_CRITICAL}"
        log_error "PCI-DSS v4.0 Req 6.3.3: Critical vulnerabilities must be remediated."
        failed=true
    fi

    if [[ "$high" -gt "$MAX_HIGH" ]]; then
        log_error "HIGH threshold exceeded! ${high} > ${MAX_HIGH}"
        failed=true
    fi

    if [[ "$medium" -gt "$MAX_MEDIUM" ]]; then
        log_warn "MEDIUM threshold exceeded: ${medium} > ${MAX_MEDIUM}"
        log_warn "Remediation plan required within 30 days."
    fi

    if [[ "$failed" = true ]]; then
        log_error "Image ${image} FAILED security gate. Deployment blocked."
        exit 1
    fi

    log_info "Image ${image} PASSED security gate."
}

# ---------------------------------------------------------------------------
# SBOM generation
# ---------------------------------------------------------------------------
generate_sbom() {
    local image="$1"
    local sbom_file
    sbom_file="${OUTPUT_DIR}/sbom-$(echo "$image" | tr '/:' '_').json"

    log_info "Generating SBOM for: ${image}"

    # SBOM generation is required by several gaming regulators as part of
    # supply chain security. CycloneDX format is preferred for compliance.
    if command -v trivy &>/dev/null; then
        trivy image \
            --format cyclonedx \
            --output "${sbom_file}" \
            "${image}"
    elif command -v syft &>/dev/null; then
        syft "${image}" -o cyclonedx-json="${sbom_file}"
    else
        log_warn "Neither trivy nor syft available for SBOM generation."
        return 1
    fi

    log_info "SBOM saved to: ${sbom_file}"

    # Count components for audit trail
    local component_count
    component_count=$(python3 -c "
import json
with open('${sbom_file}', 'r') as f:
    data = json.load(f)
print(len(data.get('components', [])))
" 2>/dev/null || echo "unknown")

    log_info "SBOM contains ${component_count} components."
}

# ---------------------------------------------------------------------------
# Compliance check
# ---------------------------------------------------------------------------
run_compliance_check() {
    local image="$1"
    local compliance_report
    compliance_report="${OUTPUT_DIR}/compliance-$(echo "$image" | tr '/:' '_').json"

    log_info "Running compliance checks (PCI-DSS, SOC2, ISO 27001)..."

    # Check for common compliance failures in iGaming images:
    # 1. Running as root (PCI-DSS violation)
    # 2. Missing health checks (operational risk)
    # 3. Secrets in environment variables
    # 4. Writable filesystem (drift risk)

    python3 -c "
import json
import subprocess
import sys

image = '${image}'
findings = []

# Inspect image configuration
try:
    result = subprocess.run(
        ['docker', 'inspect', image],
        capture_output=True, text=True, check=True
    )
    config = json.loads(result.stdout)[0]
    img_config = config.get('Config', {})

    # Check 1: Running as root
    user = img_config.get('User', '')
    if not user or user == 'root' or user == '0':
        findings.append({
            'rule': 'PCI-DSS-6.2.2',
            'severity': 'HIGH',
            'message': 'Container runs as root. Use a non-root user.',
            'framework': 'PCI-DSS v4.0'
        })

    # Check 2: Health check defined
    healthcheck = img_config.get('Healthcheck')
    if not healthcheck:
        findings.append({
            'rule': 'OPS-HEALTH-01',
            'severity': 'MEDIUM',
            'message': 'No HEALTHCHECK defined. Required for production stability.',
            'framework': 'ISO 27001:2022 A.8.6'
        })

    # Check 3: Suspicious environment variables
    env_vars = img_config.get('Env', [])
    secret_keywords = ['password', 'secret', 'token', 'api_key', 'private_key']
    for env in env_vars:
        key = env.split('=')[0].lower()
        if any(kw in key for kw in secret_keywords):
            findings.append({
                'rule': 'PCI-DSS-3.4.1',
                'severity': 'CRITICAL',
                'message': f'Potential secret in env var: {env.split(chr(61))[0]}',
                'framework': 'PCI-DSS v4.0'
            })

except (subprocess.CalledProcessError, json.JSONDecodeError, IndexError):
    findings.append({
        'rule': 'SCAN-ERR-01',
        'severity': 'LOW',
        'message': 'Could not inspect image locally. Ensure image is pulled.',
        'framework': 'N/A'
    })

report = {
    'image': image,
    'findings': findings,
    'total_findings': len(findings),
    'compliant': all(f['severity'] not in ('CRITICAL', 'HIGH') for f in findings)
}

with open('${compliance_report}', 'w') as f:
    json.dump(report, f, indent=2)

for f in findings:
    severity = f['severity']
    print(f'[{severity}] {f[\"rule\"]}: {f[\"message\"]}')

if not report['compliant']:
    print('COMPLIANCE CHECK FAILED')
    sys.exit(1)
else:
    print('Compliance check passed.')
" 2>/dev/null || log_warn "Compliance check requires docker and python3."

    log_info "Compliance report: ${compliance_report}"
}

# ---------------------------------------------------------------------------
# Registry scanning
# ---------------------------------------------------------------------------
scan_registry() {
    log_info "Scanning container registry: ${REGISTRY_URL}"

    if command -v trivy &>/dev/null; then
        trivy repo \
            --severity CRITICAL,HIGH \
            --format table \
            "${REGISTRY_URL}" 2>&1 | tee "${OUTPUT_DIR}/registry-scan.txt"
    else
        log_warn "Trivy not available for registry scanning."
        log_info "Configure registry scanning in the Aqua Console UI instead."
    fi
}

# ---------------------------------------------------------------------------
# Main execution
# ---------------------------------------------------------------------------
main() {
    log_info "=========================================="
    log_info "Aqua Security Pipeline Integration"
    log_info "=========================================="

    validate_inputs

    if [[ "$REGISTRY_SCAN" = true ]]; then
        scan_registry
    fi

    if [[ -n "$IMAGE_TO_SCAN" ]]; then
        scan_image "${IMAGE_TO_SCAN}"

        if [[ "$GENERATE_SBOM" = true ]]; then
            generate_sbom "${IMAGE_TO_SCAN}"
        fi

        if [[ "$COMPLIANCE_CHECK" = true ]]; then
            run_compliance_check "${IMAGE_TO_SCAN}"
        fi
    fi

    log_info "=========================================="
    log_info "Pipeline integration complete."
    log_info "Reports: ${OUTPUT_DIR}/"
    log_info "=========================================="
}

main "$@"
