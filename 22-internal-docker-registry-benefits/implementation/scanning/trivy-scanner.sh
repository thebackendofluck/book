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
# Trivy Vulnerability Scanner Pipeline for iGaming Docker Registry
# =============================================================================
# Scans container images for OS/library vulnerabilities and misconfigurations.
# Integrates with Harbor registry and CI/CD pipelines.
#
# Prerequisites:
#   - trivy >= 0.50.0 (https://github.com/aquasecurity/trivy)
#   - jq, curl
#   - Docker credentials configured for Harbor
#
# Usage:
#   ./trivy-scanner.sh scan <image>           # Scan single image
#   ./trivy-scanner.sh scan-all <project>     # Scan all images in Harbor project
#   ./trivy-scanner.sh gate <image>           # Scan with pass/fail gate
#   ./trivy-scanner.sh report <image>         # Generate HTML/JSON report
#   ./trivy-scanner.sh db-update              # Update vulnerability database
# =============================================================================
set -euo pipefail

# --- Configuration -----------------------------------------------------------
HARBOR_URL="${HARBOR_URL:-https://registry.casino-platform.internal}"
HARBOR_USER="${HARBOR_USER:-admin}"
HARBOR_PASS="${HARBOR_PASS:?HARBOR_PASS must be set}"
REPORT_DIR="${REPORT_DIR:-/var/reports/trivy}"
TRIVY_CACHE="${TRIVY_CACHE:-/var/cache/trivy}"

# Severity thresholds for gate check
CRITICAL_THRESHOLD="${CRITICAL_THRESHOLD:-0}"    # Zero critical vulns allowed
HIGH_THRESHOLD="${HIGH_THRESHOLD:-5}"            # Max 5 high vulns
MEDIUM_THRESHOLD="${MEDIUM_THRESHOLD:-20}"       # Max 20 medium vulns

# GLI-33 / ISO 27001 compliance — all severities must be tracked
SCAN_SEVERITY="CRITICAL,HIGH,MEDIUM,LOW"

# Slack/webhook for alerts
ALERT_WEBHOOK="${ALERT_WEBHOOK:-}"

mkdir -p "${REPORT_DIR}" "${TRIVY_CACHE}"

# --- Logging -----------------------------------------------------------------
log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }
error() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2; }

# --- Alert Function ----------------------------------------------------------
send_alert() {
    local severity="$1" image="$2" message="$3"
    if [[ -n "${ALERT_WEBHOOK}" ]]; then
        curl -sf -X POST "${ALERT_WEBHOOK}" \
            -H "Content-Type: application/json" \
            -d "{
                \"text\": \":warning: *Registry Security Alert*\",
                \"blocks\": [{
                    \"type\": \"section\",
                    \"text\": {
                        \"type\": \"mrkdwn\",
                        \"text\": \"*Severity:* ${severity}\n*Image:* \`${image}\`\n*Details:* ${message}\"
                    }
                }]
            }" || error "Failed to send alert"
    fi
}

# --- Database Update ----------------------------------------------------------
cmd_db_update() {
    log "Updating Trivy vulnerability database..."
    trivy image --download-db-only \
        --cache-dir "${TRIVY_CACHE}" \
        --db-repository ghcr.io/aquasecurity/trivy-db
    trivy image --download-java-db-only \
        --cache-dir "${TRIVY_CACHE}" \
        --java-db-repository ghcr.io/aquasecurity/trivy-java-db
    log "Database updated successfully"
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

    # Full vulnerability scan
    trivy image \
        --cache-dir "${TRIVY_CACHE}" \
        --severity "${SCAN_SEVERITY}" \
        --vuln-type os,library \
        --format json \
        --output "${json_report}" \
        --ignore-unfixed \
        --scanners vuln,secret,misconfig \
        "${image}" 2>&1

    # Parse results
    local critical high medium low
    critical=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' "${json_report}" 2>/dev/null || echo 0)
    high=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length' "${json_report}" 2>/dev/null || echo 0)
    medium=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="MEDIUM")] | length' "${json_report}" 2>/dev/null || echo 0)
    low=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="LOW")] | length' "${json_report}" 2>/dev/null || echo 0)

    # Check for leaked secrets
    local secrets
    secrets=$(jq '[.Results[]?.Secrets[]?] | length' "${json_report}" 2>/dev/null || echo 0)

    log "Results for ${image}:"
    log "  CRITICAL: ${critical} | HIGH: ${high} | MEDIUM: ${medium} | LOW: ${low}"
    log "  Secrets detected: ${secrets}"
    log "  Report: ${json_report}"

    # Alert on critical findings
    if [[ "${critical}" -gt 0 ]]; then
        send_alert "CRITICAL" "${image}" "${critical} critical vulnerabilities found"
    fi
    if [[ "${secrets}" -gt 0 ]]; then
        send_alert "CRITICAL" "${image}" "${secrets} leaked secrets detected — immediate remediation required"
    fi

    echo "${json_report}"
}

# --- Gate Check (CI/CD) -------------------------------------------------------
cmd_gate() {
    local image="$1"
    local json_report
    json_report=$(cmd_scan "${image}")

    local critical high medium
    critical=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="CRITICAL")] | length' "${json_report}" 2>/dev/null || echo 0)
    high=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="HIGH")] | length' "${json_report}" 2>/dev/null || echo 0)
    medium=$(jq '[.Results[]?.Vulnerabilities[]? | select(.Severity=="MEDIUM")] | length' "${json_report}" 2>/dev/null || echo 0)

    local passed=true
    if [[ "${critical}" -gt "${CRITICAL_THRESHOLD}" ]]; then
        error "GATE FAILED: ${critical} critical vulnerabilities (threshold: ${CRITICAL_THRESHOLD})"
        passed=false
    fi
    if [[ "${high}" -gt "${HIGH_THRESHOLD}" ]]; then
        error "GATE FAILED: ${high} high vulnerabilities (threshold: ${HIGH_THRESHOLD})"
        passed=false
    fi
    if [[ "${medium}" -gt "${MEDIUM_THRESHOLD}" ]]; then
        error "GATE FAILED: ${medium} medium vulnerabilities (threshold: ${MEDIUM_THRESHOLD})"
        passed=false
    fi

    if [[ "${passed}" == "true" ]]; then
        log "GATE PASSED: Image ${image} meets security thresholds"
        return 0
    else
        send_alert "HIGH" "${image}" "Security gate FAILED — deployment blocked"
        return 1
    fi
}

# --- Scan All Images in a Harbor Project --------------------------------------
cmd_scan_all() {
    local project="$1"
    local page=1
    local page_size=100
    local total_scanned=0
    local total_failed=0

    log "Scanning all images in project: ${project}"

    while true; do
        local repos
        repos=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
            "${HARBOR_URL}/api/v2.0/projects/${project}/repositories?page=${page}&page_size=${page_size}" \
            2>/dev/null)

        local count
        count=$(echo "${repos}" | jq 'length' 2>/dev/null || echo 0)
        [[ "${count}" -eq 0 ]] && break

        echo "${repos}" | jq -r '.[].name' | while read -r repo; do
            # Get latest tag
            local tags
            tags=$(curl -sk -u "${HARBOR_USER}:${HARBOR_PASS}" \
                "${HARBOR_URL}/api/v2.0/projects/${project}/repositories/${repo##*/}/artifacts?page=1&page_size=1&with_tag=true" \
                2>/dev/null)

            local tag
            tag=$(echo "${tags}" | jq -r '.[0].tags[0].name // "latest"' 2>/dev/null)
            local full_image="${HARBOR_URL#https://}/${repo}:${tag}"

            if cmd_gate "${full_image}" 2>/dev/null; then
                total_scanned=$((total_scanned + 1))
            else
                total_scanned=$((total_scanned + 1))
                total_failed=$((total_failed + 1))
            fi
        done

        page=$((page + 1))
    done

    log "Scan complete: ${total_scanned} images scanned, ${total_failed} failed gate check"
}

# --- HTML Report Generation ---------------------------------------------------
cmd_report() {
    local image="$1"
    local timestamp
    timestamp=$(date +'%Y%m%d_%H%M%S')
    local safe_name
    safe_name=$(echo "${image}" | tr '/:' '__')

    log "Generating reports for: ${image}"

    # JSON report
    trivy image \
        --cache-dir "${TRIVY_CACHE}" \
        --severity "${SCAN_SEVERITY}" \
        --format json \
        --output "${REPORT_DIR}/${safe_name}_${timestamp}.json" \
        --scanners vuln,secret,misconfig \
        "${image}"

    # HTML report
    trivy image \
        --cache-dir "${TRIVY_CACHE}" \
        --severity "${SCAN_SEVERITY}" \
        --format template \
        --template "@/usr/local/share/trivy/templates/html.tpl" \
        --output "${REPORT_DIR}/${safe_name}_${timestamp}.html" \
        --scanners vuln,secret,misconfig \
        "${image}"

    # SARIF report (for GitHub/GitLab integration)
    trivy image \
        --cache-dir "${TRIVY_CACHE}" \
        --severity "${SCAN_SEVERITY}" \
        --format sarif \
        --output "${REPORT_DIR}/${safe_name}_${timestamp}.sarif" \
        --scanners vuln,secret,misconfig \
        "${image}"

    log "Reports generated in ${REPORT_DIR}/"
    log "  JSON:  ${safe_name}_${timestamp}.json"
    log "  HTML:  ${safe_name}_${timestamp}.html"
    log "  SARIF: ${safe_name}_${timestamp}.sarif"
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
        scan-all)
            [[ $# -lt 1 ]] && { error "Usage: $0 scan-all <project>"; exit 1; }
            cmd_scan_all "$1"
            ;;
        gate)
            [[ $# -lt 1 ]] && { error "Usage: $0 gate <image>"; exit 1; }
            cmd_gate "$1"
            ;;
        report)
            [[ $# -lt 1 ]] && { error "Usage: $0 report <image>"; exit 1; }
            cmd_report "$1"
            ;;
        db-update)
            cmd_db_update
            ;;
        *)
            echo "Usage: $0 {scan|scan-all|gate|report|db-update} [args]"
            echo ""
            echo "Commands:"
            echo "  scan <image>        Scan a single image for vulnerabilities"
            echo "  scan-all <project>  Scan all images in a Harbor project"
            echo "  gate <image>        Scan with pass/fail threshold gate (CI/CD)"
            echo "  report <image>      Generate HTML/JSON/SARIF reports"
            echo "  db-update           Update Trivy vulnerability database"
            exit 1
            ;;
    esac
}

main "$@"
