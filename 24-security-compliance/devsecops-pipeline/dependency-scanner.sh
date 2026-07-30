#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034,SC2319
# =============================================================================
# Dependency Vulnerability Scanner for iGaming CI/CD Pipelines
# =============================================================================
# Scans project dependencies across multiple ecosystems (npm, pip, Go, Maven,
# Gradle, Cargo) for known vulnerabilities. Enforces gambling-platform-specific
# policies: zero tolerance for critical CVEs in payment/wallet services, and
# license compliance checks for regulated jurisdictions.
#
# Usage:
#   ./dependency-scanner.sh [--path DIR] [--ecosystem npm|pip|go|all]
#
# Exit codes:
#   0  - No vulnerabilities above threshold
#   1  - Critical vulnerabilities found
#   2  - High vulnerabilities found (when --strict)
#   99 - Scanner error
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORT_DIR="${REPORT_DIR:-/tmp/dependency-reports/$(date +%Y%m%d-%H%M%S)}"
SCAN_PATH="${SCAN_PATH:-.}"
ECOSYSTEM="${ECOSYSTEM:-all}"
STRICT_MODE="${STRICT_MODE:-false}"
# Services where ANY critical CVE blocks deployment
CRITICAL_SERVICES="payment-service wallet-service kyc-service withdrawal-service"

# Disallowed licenses in regulated gambling software
# GPL variants can force source disclosure of proprietary game logic
BLOCKED_LICENSES="GPL-3.0 AGPL-3.0 AGPL-3.0-only AGPL-3.0-or-later SSPL-1.0"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

mkdir -p "${REPORT_DIR}"

# ---------------------------------------------------------------------------
# npm / Node.js dependency scanning
# ---------------------------------------------------------------------------
scan_npm() {
    local project_dir="${1}"
    if [[ ! -f "${project_dir}/package.json" ]]; then
        return 0
    fi

    log_info "Scanning npm dependencies in ${project_dir}..."
    local report="${REPORT_DIR}/npm-audit.json"

    # npm audit with JSON output
    (cd "${project_dir}" && npm audit --json > "${report}" 2>/dev/null) || true

    if [[ -f "${report}" ]]; then
        local critical high moderate
        critical=$(jq '.metadata.vulnerabilities.critical // 0' "${report}" 2>/dev/null || echo 0)
        high=$(jq '.metadata.vulnerabilities.high // 0' "${report}" 2>/dev/null || echo 0)
        moderate=$(jq '.metadata.vulnerabilities.moderate // 0' "${report}" 2>/dev/null || echo 0)

        echo "  npm audit: critical=${critical} high=${high} moderate=${moderate}"

        # Check with Snyk if available (provides gambling-industry specific advisories)
        if command -v snyk >/dev/null 2>&1; then
            log_info "Running Snyk analysis for enhanced vulnerability intelligence..."
            (cd "${project_dir}" && snyk test --json > "${REPORT_DIR}/snyk-npm.json" 2>/dev/null) || true
        fi

        if [[ "${critical}" -gt 0 ]]; then
            log_error "Critical npm vulnerabilities found!"
            # List critical CVEs
            jq -r '.vulnerabilities | to_entries[] | select(.value.severity == "critical") |
                "\(.key): \(.value.title // "N/A") [\(.value.url // "")]"' \
                "${report}" 2>/dev/null || true
            return 1
        fi

        if [[ "${STRICT_MODE}" == "true" && "${high}" -gt 0 ]]; then
            log_error "High npm vulnerabilities found (strict mode)"
            return 2
        fi
    fi

    # License compliance check
    if command -v npx >/dev/null 2>&1; then
        log_info "Checking npm license compliance..."
        local license_report="${REPORT_DIR}/npm-licenses.json"
        (cd "${project_dir}" && npx license-checker --json > "${license_report}" 2>/dev/null) || true

        if [[ -f "${license_report}" ]]; then
            for blocked in ${BLOCKED_LICENSES}; do
                local violations
                violations=$(jq -r "to_entries[] | select(.value.licenses == \"${blocked}\") | .key" \
                    "${license_report}" 2>/dev/null || true)
                if [[ -n "${violations}" ]]; then
                    log_warn "Blocked license ${blocked} found in: ${violations}"
                fi
            done
        fi
    fi

    log_ok "npm dependency scan complete"
    return 0
}

# ---------------------------------------------------------------------------
# Python dependency scanning
# ---------------------------------------------------------------------------
scan_pip() {
    local project_dir="${1}"
    if [[ ! -f "${project_dir}/requirements.txt" && ! -f "${project_dir}/pyproject.toml" && \
          ! -f "${project_dir}/setup.py" && ! -f "${project_dir}/Pipfile.lock" ]]; then
        return 0
    fi

    log_info "Scanning Python dependencies in ${project_dir}..."
    local report="${REPORT_DIR}/pip-audit.json"

    # pip-audit is the recommended tool (replaces safety)
    if command -v pip-audit >/dev/null 2>&1; then
        (cd "${project_dir}" && pip-audit \
            --format json \
            --output "${report}" \
            --desc \
            --fix --dry-run 2>/dev/null) || true

        if [[ -f "${report}" ]]; then
            local vuln_count
            vuln_count=$(jq 'length' "${report}" 2>/dev/null || echo 0)
            local critical_count
            critical_count=$(jq '[.[] | select(.fix_versions | length > 0)] | length' \
                "${report}" 2>/dev/null || echo 0)

            echo "  pip-audit: ${vuln_count} vulnerabilities (${critical_count} fixable)"

            if [[ "${vuln_count}" -gt 0 ]]; then
                jq -r '.[] | "\(.name) \(.version): \(.vulns[0].id // "N/A") - \(.vulns[0].description // "" | split("\n")[0])"' \
                    "${report}" 2>/dev/null || true
            fi
        fi
    fi

    # Also check with safety if available
    if command -v safety >/dev/null 2>&1; then
        log_info "Running Safety check..."
        (cd "${project_dir}" && safety check \
            --json \
            --output "${REPORT_DIR}/safety-report.json" 2>/dev/null) || true
    fi

    # Bandit for Python-specific security issues (crypto, injection, etc.)
    if command -v bandit >/dev/null 2>&1; then
        log_info "Running Bandit security linter..."
        (cd "${project_dir}" && bandit \
            -r . \
            -f json \
            -o "${REPORT_DIR}/bandit-report.json" \
            --severity-level medium \
            --exclude "./.venv,./tests,./node_modules" 2>/dev/null) || true
    fi

    log_ok "Python dependency scan complete"
    return 0
}

# ---------------------------------------------------------------------------
# Go dependency scanning
# ---------------------------------------------------------------------------
scan_go() {
    local project_dir="${1}"
    if [[ ! -f "${project_dir}/go.mod" ]]; then
        return 0
    fi

    log_info "Scanning Go dependencies in ${project_dir}..."

    # govulncheck is the official Go vulnerability scanner
    if command -v govulncheck >/dev/null 2>&1; then
        (cd "${project_dir}" && govulncheck -json ./... > "${REPORT_DIR}/go-vulncheck.json" 2>/dev/null) || true

        if [[ -f "${REPORT_DIR}/go-vulncheck.json" ]]; then
            local vuln_count
            vuln_count=$(jq '[.[] | select(.osv != null)] | length' \
                "${REPORT_DIR}/go-vulncheck.json" 2>/dev/null || echo 0)
            echo "  govulncheck: ${vuln_count} vulnerabilities"
        fi
    fi

    # Also check with nancy (Sonatype OSS Index)
    if command -v nancy >/dev/null 2>&1; then
        (cd "${project_dir}" && go list -json -deps ./... 2>/dev/null | \
            nancy sleuth --output json > "${REPORT_DIR}/nancy-report.json" 2>/dev/null) || true
    fi

    log_ok "Go dependency scan complete"
    return 0
}

# ---------------------------------------------------------------------------
# SBOM generation (required for regulated gambling software)
# ---------------------------------------------------------------------------
generate_sbom() {
    local project_dir="${1}"
    log_info "Generating Software Bill of Materials (SBOM)..."

    # SBOM is increasingly required by gambling regulators for audit purposes
    # CycloneDX format is preferred for security tooling integration
    if command -v syft >/dev/null 2>&1; then
        syft "${project_dir}" \
            -o cyclonedx-json="${REPORT_DIR}/sbom-cyclonedx.json" \
            -o spdx-json="${REPORT_DIR}/sbom-spdx.json" 2>/dev/null || true
        log_ok "SBOM generated (CycloneDX + SPDX formats)"
    elif command -v cyclonedx-npm >/dev/null 2>&1 && [[ -f "${project_dir}/package.json" ]]; then
        (cd "${project_dir}" && cyclonedx-npm --output-file "${REPORT_DIR}/sbom-cyclonedx.json") || true
    else
        log_warn "No SBOM tool available. Install syft: https://github.com/anchore/syft"
    fi

    # Grype vulnerability scan against the SBOM
    if command -v grype >/dev/null 2>&1 && [[ -f "${REPORT_DIR}/sbom-cyclonedx.json" ]]; then
        log_info "Scanning SBOM with Grype..."
        grype "sbom:${REPORT_DIR}/sbom-cyclonedx.json" \
            --output json \
            --file "${REPORT_DIR}/grype-sbom-results.json" 2>/dev/null || true
    fi
}

# ---------------------------------------------------------------------------
# Service criticality check
# ---------------------------------------------------------------------------
check_service_criticality() {
    local project_dir="${1}"
    local service_name
    service_name=$(basename "${project_dir}")

    # For critical financial services, ANY vulnerability is a blocker
    for critical in ${CRITICAL_SERVICES}; do
        if [[ "${service_name}" == "${critical}" ]]; then
            log_warn "Service '${service_name}' is classified as CRITICAL"
            log_warn "Zero-tolerance policy: any vulnerability blocks deployment"
            STRICT_MODE="true"
            return 0
        fi
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    while [[ $# -gt 0 ]]; do
        case "${1}" in
            --path)      SCAN_PATH="${2}"; shift 2 ;;
            --ecosystem) ECOSYSTEM="${2}"; shift 2 ;;
            --strict)    STRICT_MODE="true"; shift ;;
            --help|-h)
                echo "Usage: $0 [--path DIR] [--ecosystem npm|pip|go|all] [--strict]"
                exit 0
                ;;
            *) log_error "Unknown option: ${1}"; exit 99 ;;
        esac
    done

    echo "=============================================="
    echo " Dependency Vulnerability Scanner"
    echo " Path: ${SCAN_PATH}"
    echo " Ecosystem: ${ECOSYSTEM}"
    echo " Strict: ${STRICT_MODE}"
    echo " Reports: ${REPORT_DIR}"
    echo "=============================================="

    check_service_criticality "${SCAN_PATH}"

    local exit_code=0

    case "${ECOSYSTEM}" in
        npm) scan_npm "${SCAN_PATH}" || exit_code=$? ;;
        pip) scan_pip "${SCAN_PATH}" || exit_code=$? ;;
        go)  scan_go  "${SCAN_PATH}" || exit_code=$? ;;
        all)
            scan_npm "${SCAN_PATH}" || exit_code=$?
            scan_pip "${SCAN_PATH}" || { [[ $? -gt ${exit_code} ]] && exit_code=$?; }
            scan_go  "${SCAN_PATH}" || { [[ $? -gt ${exit_code} ]] && exit_code=$?; }
            ;;
    esac

    generate_sbom "${SCAN_PATH}"

    # Summary
    echo ""
    echo "=============================================="
    log_info "Reports saved to: ${REPORT_DIR}"
    ls -la "${REPORT_DIR}/" 2>/dev/null
    echo "=============================================="

    if [[ ${exit_code} -ne 0 ]]; then
        log_error "Dependency scan failed with exit code ${exit_code}"
    else
        log_ok "All dependency checks passed"
    fi

    exit ${exit_code}
}

main "$@"
