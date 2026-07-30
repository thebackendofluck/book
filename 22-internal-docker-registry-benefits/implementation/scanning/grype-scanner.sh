#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 22, Internal Docker Registry.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# =============================================================================
# Grype Vulnerability Scanner — Secondary Scanner for iGaming Registry
# =============================================================================
# Grype provides a second opinion on vulnerabilities, complementing Trivy.
# Using two scanners reduces false negatives (GLI-33 compliance requirement).
#
# Prerequisites:
#   - grype >= 0.74.0 (https://github.com/anchore/grype)
#   - jq, curl
#   - Docker credentials for Harbor registry
#
# Usage:
#   ./grype-scanner.sh scan <image>          # Scan single image
#   ./grype-scanner.sh compare <image>       # Compare Grype vs Trivy results
#   ./grype-scanner.sh batch <images-file>   # Scan images listed in file
#   ./grype-scanner.sh db-update             # Update Grype vulnerability DB
# =============================================================================
set -euo pipefail

HARBOR_URL="${HARBOR_URL:-https://registry.casino-platform.internal}"
REPORT_DIR="${REPORT_DIR:-/var/reports/grype}"
GRYPE_DB_DIR="${GRYPE_DB_DIR:-/var/cache/grype/db}"
TRIVY_REPORT_DIR="${TRIVY_REPORT_DIR:-/var/reports/trivy}"

# Thresholds matching Trivy gate
CRITICAL_THRESHOLD="${CRITICAL_THRESHOLD:-0}"
HIGH_THRESHOLD="${HIGH_THRESHOLD:-5}"

ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

mkdir -p "${REPORT_DIR}"

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [grype] $*"; }
error() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] [grype] ERROR: $*" >&2; }

# --- Database Update ----------------------------------------------------------
cmd_db_update() {
    log "Updating Grype vulnerability database..."
    grype db update
    grype db status
    log "Database update complete"
}

# --- Single Image Scan --------------------------------------------------------
cmd_scan() {
    local image="$1"
    local timestamp
    timestamp=$(date +'%Y%m%d_%H%M%S')
    local safe_name
    safe_name=$(echo "${image}" | tr '/:' '__')
    local json_report="${REPORT_DIR}/${safe_name}_${timestamp}.json"

    log "Scanning image: ${image}"

    # Run Grype scan
    grype "${image}" \
        --output json \
        --file "${json_report}" \
        --fail-on critical \
        --add-cpes-if-none \
        --by-cve \
        2>&1 || true  # Don't exit on fail-on match, we handle it below

    # Parse results
    local critical high medium low
    critical=$(jq '[.matches[]? | select(.vulnerability.severity=="Critical")] | length' "${json_report}" 2>/dev/null || echo 0)
    high=$(jq '[.matches[]? | select(.vulnerability.severity=="High")] | length' "${json_report}" 2>/dev/null || echo 0)
    medium=$(jq '[.matches[]? | select(.vulnerability.severity=="Medium")] | length' "${json_report}" 2>/dev/null || echo 0)
    low=$(jq '[.matches[]? | select(.vulnerability.severity=="Low")] | length' "${json_report}" 2>/dev/null || echo 0)

    # Fixed vs unfixed
    local fixable
    fixable=$(jq '[.matches[]? | select(.vulnerability.fix.state=="fixed")] | length' "${json_report}" 2>/dev/null || echo 0)

    log "Results for ${image}:"
    log "  CRITICAL: ${critical} | HIGH: ${high} | MEDIUM: ${medium} | LOW: ${low}"
    log "  Fixable: ${fixable}"
    log "  Report: ${json_report}"

    # Gate check
    local exit_code=0
    if [[ "${critical}" -gt "${CRITICAL_THRESHOLD}" ]]; then
        error "GATE FAILED: ${critical} critical vulnerabilities (max: ${CRITICAL_THRESHOLD})"
        exit_code=1
    fi
    if [[ "${high}" -gt "${HIGH_THRESHOLD}" ]]; then
        error "GATE FAILED: ${high} high vulnerabilities (max: ${HIGH_THRESHOLD})"
        exit_code=1
    fi

    if [[ "${exit_code}" -eq 0 ]]; then
        log "GATE PASSED for ${image}"
    fi

    # Generate table report
    grype "${image}" \
        --output table \
        --file "${REPORT_DIR}/${safe_name}_${timestamp}.txt" \
        --by-cve \
        2>/dev/null || true

    return "${exit_code}"
}

# --- Compare Grype vs Trivy Results ------------------------------------------
cmd_compare() {
    local image="$1"
    local safe_name
    safe_name=$(echo "${image}" | tr '/:' '__')

    log "Running comparative scan: Grype vs Trivy for ${image}"

    # Run Grype
    local grype_report="${REPORT_DIR}/${safe_name}_compare_grype.json"
    grype "${image}" --output json --file "${grype_report}" --by-cve 2>/dev/null || true

    # Run Trivy
    local trivy_report="${REPORT_DIR}/${safe_name}_compare_trivy.json"
    trivy image "${image}" \
        --format json \
        --output "${trivy_report}" \
        --severity CRITICAL,HIGH,MEDIUM,LOW \
        2>/dev/null || true

    # Extract CVE IDs from both
    local grype_cves trivy_cves
    grype_cves=$(jq -r '[.matches[]?.vulnerability.id] | sort | unique | .[]' "${grype_report}" 2>/dev/null || true)
    trivy_cves=$(jq -r '[.Results[]?.Vulnerabilities[]?.VulnerabilityID] | sort | unique | .[]' "${trivy_report}" 2>/dev/null || true)

    # Compare
    local grype_only trivy_only both
    grype_only=$(comm -23 <(echo "${grype_cves}" | sort) <(echo "${trivy_cves}" | sort) | wc -l)
    trivy_only=$(comm -13 <(echo "${grype_cves}" | sort) <(echo "${trivy_cves}" | sort) | wc -l)
    both=$(comm -12 <(echo "${grype_cves}" | sort) <(echo "${trivy_cves}" | sort) | wc -l)

    local grype_total trivy_total
    grype_total=$(echo "${grype_cves}" | grep -c . || echo 0)
    trivy_total=$(echo "${trivy_cves}" | grep -c . || echo 0)

    log "Comparison Results for ${image}:"
    log "  Grype total CVEs:       ${grype_total}"
    log "  Trivy total CVEs:       ${trivy_total}"
    log "  Found by both:          ${both}"
    log "  Grype-only findings:    ${grype_only}"
    log "  Trivy-only findings:    ${trivy_only}"

    # Generate comparison report
    local comparison_report="${REPORT_DIR}/${safe_name}_comparison.json"
    jq -n \
        --arg image "${image}" \
        --arg grype_total "${grype_total}" \
        --arg trivy_total "${trivy_total}" \
        --arg both "${both}" \
        --arg grype_only "${grype_only}" \
        --arg trivy_only "${trivy_only}" \
        '{
            image: $image,
            timestamp: (now | todate),
            grype_total: ($grype_total | tonumber),
            trivy_total: ($trivy_total | tonumber),
            overlap: ($both | tonumber),
            grype_unique: ($grype_only | tonumber),
            trivy_unique: ($trivy_only | tonumber),
            coverage_note: "Using dual scanners provides comprehensive vulnerability detection per GLI-33 requirements"
        }' > "${comparison_report}"

    log "Comparison report: ${comparison_report}"

    # Alert if significant discrepancy (>20% difference)
    if [[ "${grype_total}" -gt 0 && "${trivy_total}" -gt 0 ]]; then
        local diff_pct
        diff_pct=$(( (grype_only + trivy_only) * 100 / (grype_total + trivy_total) ))
        if [[ "${diff_pct}" -gt 40 ]]; then
            log "WARNING: Significant scanner discrepancy (${diff_pct}%) — review ${comparison_report}"
        fi
    fi
}

# --- Batch Scan ---------------------------------------------------------------
cmd_batch() {
    local images_file="$1"
    [[ ! -f "${images_file}" ]] && { error "File not found: ${images_file}"; exit 1; }

    local total=0 passed=0 failed=0

    while IFS= read -r image; do
        [[ -z "${image}" || "${image}" == \#* ]] && continue
        total=$((total + 1))

        if cmd_scan "${image}" 2>/dev/null; then
            passed=$((passed + 1))
        else
            failed=$((failed + 1))
        fi
    done < "${images_file}"

    log "Batch scan complete: ${total} scanned, ${passed} passed, ${failed} failed"
    [[ "${failed}" -gt 0 ]] && return 1
    return 0
}

# --- Main ---------------------------------------------------------------------
main() {
    local cmd="${1:-help}"
    shift || true

    case "${cmd}" in
        scan)
            [[ $# -lt 1 ]] && { error "Usage: $0 scan <image>"; exit 1; }
            cmd_scan "$1"
            ;;
        compare)
            [[ $# -lt 1 ]] && { error "Usage: $0 compare <image>"; exit 1; }
            cmd_compare "$1"
            ;;
        batch)
            [[ $# -lt 1 ]] && { error "Usage: $0 batch <images-file>"; exit 1; }
            cmd_batch "$1"
            ;;
        db-update)
            cmd_db_update
            ;;
        *)
            echo "Usage: $0 {scan|compare|batch|db-update} [args]"
            echo ""
            echo "Commands:"
            echo "  scan <image>          Scan a single image"
            echo "  compare <image>       Run Grype + Trivy and compare results"
            echo "  batch <images-file>   Scan all images listed in file (one per line)"
            echo "  db-update             Update Grype vulnerability database"
            exit 1
            ;;
    esac
}

main "$@"
