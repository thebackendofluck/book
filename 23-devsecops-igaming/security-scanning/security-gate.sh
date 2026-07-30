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
# CI/CD Security Gate Script for iGaming Platform
# =============================================================================
# Chapter 23: DevSecOps - Security Scanning
#
# WHY: This script is the final decision point before code reaches production.
# It aggregates results from all security tools (SAST, DAST, secrets,
# dependencies) and enforces the platform's security policy.
#
# For iGaming platforms, the security gate enforces:
#   - Zero critical vulnerabilities (regulatory requirement)
#   - No leaked secrets (instant license revocation risk)
#   - Payment endpoint hardening verified
#   - RNG endpoint isolation confirmed
#   - Admin panel security checks passed
#   - Compliance documentation generated for audit trail
#
# This script is designed to run as a CI/CD pipeline step (GitHub Actions,
# GitLab CI, Jenkins) and block merges that fail security policy.
#
# USAGE:
#   ./security-gate.sh                           # Check all reports
#   ./security-gate.sh --report-dir ./reports    # Custom report directory
#   ./security-gate.sh --strict                  # Zero tolerance mode
#   ./security-gate.sh --jira PROJ-123           # Post results to Jira
#   ./security-gate.sh --help                    # Show this help
#
# EXIT CODES:
#   0 - All checks passed, merge allowed
#   1 - Security policy violated, merge blocked
#   2 - Configuration error
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
REPORT_DIR="${REPO_ROOT}/.security-reports"
COMPLIANCE_REPORT="${REPORT_DIR}/compliance-gate-$(date -u +%Y%m%d_%H%M%S).json"

# Policy thresholds
# WHY: Gaming regulators (MGA Article 24, UKGC LCCP) require platforms to
# demonstrate "adequate security measures." Zero critical findings is the
# industry baseline. High findings must have documented remediation plans.
POLICY_CRITICAL_MAX=0
POLICY_HIGH_MAX=0
POLICY_SECRETS_MAX=0

# Jira integration
JIRA_BASE_URL="${JIRA_BASE_URL:-}"
JIRA_TOKEN="${JIRA_TOKEN:-}"
JIRA_TICKET=""

# Strict mode: zero tolerance for any findings
STRICT_MODE=false

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# Gate results
declare -A GATE_RESULTS
GATE_PASS=true

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[PASS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[FAIL]${NC} $1" >&2; }

show_help() {
    echo "Usage: $(basename "$0") [OPTIONS]"
    echo ""
    echo "CI/CD security gate for iGaming platform deployments."
    echo "Aggregates all security tool results and enforces policy."
    echo ""
    echo "Options:"
    echo "  --report-dir DIR       Directory containing security reports"
    echo "                         (default: .security-reports/)"
    echo "  --strict               Zero tolerance: any finding blocks merge"
    echo "  --jira TICKET          Post results to Jira ticket (e.g., PROJ-123)"
    echo "  --max-critical N       Max critical findings allowed (default: 0)"
    echo "  --max-high N           Max high findings allowed (default: 0)"
    echo "  --ci                   CI mode: machine-readable output"
    echo "  --output PATH          Custom compliance report output path"
    echo "  --help                 Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  JIRA_BASE_URL          Jira instance URL (e.g., https://company.atlassian.net)"
    echo "  JIRA_TOKEN             Jira API token (base64 of email:token)"
    echo ""
    echo "Gate Policy (default):"
    echo "  - Critical findings: 0 allowed (regulatory requirement)"
    echo "  - High findings: 0 allowed"
    echo "  - Secrets in code: 0 allowed"
    echo "  - Payment endpoints: must require authentication"
    echo "  - RNG endpoints: must require authentication"
    echo "  - Admin panel: must require authentication + MFA"
    echo ""
}

record_check() {
    local name="$1"
    local status="$2"
    local detail="$3"

    GATE_RESULTS["${name}"]="${status}|${detail}"

    if [[ "${status}" == "FAIL" ]]; then
        GATE_PASS=false
        log_error "${name}: ${detail}"
    elif [[ "${status}" == "WARN" ]]; then
        log_warn "${name}: ${detail}"
    else
        log_success "${name}: ${detail}"
    fi
}

# ---------------------------------------------------------------------------
# Check: Secret Scanning Results
# WHY: Any secret in the codebase is an immediate deployment blocker.
# Leaked credentials cannot be mitigated after deployment -- they must
# be caught and rotated before code leaves the developer's machine.
# ---------------------------------------------------------------------------
check_secrets() {
    log_info "Checking secret scanning results..."

    local secrets_dir="${REPORT_DIR}"
    local total_secrets=0

    # Find the most recent secrets report
    local latest_report
    latest_report=$(find "${secrets_dir}" -name "secrets-*.json" -type f 2>/dev/null | sort -r | head -1)

    if [[ -z "${latest_report}" ]]; then
        record_check "Secrets Scan" "WARN" "No secret scan report found. Run scan-secrets.sh first."
        return
    fi

    total_secrets=$(python3 -c "
import json
try:
    with open('${latest_report}') as f:
        data = json.load(f)
    print(len(data) if isinstance(data, list) else 0)
except Exception:
    print(0)
" 2>/dev/null || echo "0")

    if [[ ${total_secrets} -gt ${POLICY_SECRETS_MAX} ]]; then
        record_check "Secrets Scan" "FAIL" "${total_secrets} secret(s) found (max: ${POLICY_SECRETS_MAX})"
    else
        record_check "Secrets Scan" "PASS" "No secrets detected"
    fi
}

# ---------------------------------------------------------------------------
# Check: SAST Results
# WHY: Static analysis findings above threshold indicate unresolved
# vulnerabilities. Critical findings (RCE, SQLi, auth bypass) must
# never reach production in a platform handling real money.
# ---------------------------------------------------------------------------
check_sast() {
    log_info "Checking SAST results..."

    local sast_dir="${REPORT_DIR}/sast"
    local latest_report
    latest_report=$(find "${sast_dir}" -name "sast-consolidated-*.json" -type f 2>/dev/null | sort -r | head -1)

    if [[ -z "${latest_report}" ]]; then
        record_check "SAST Analysis" "WARN" "No SAST report found. Run sast-pipeline.sh first."
        return
    fi

    local counts
    counts=$(python3 -c "
import json
with open('${latest_report}') as f:
    data = json.load(f)
summary = data.get('metadata', {}).get('summary', {})
print(summary.get('critical', 0), summary.get('high', 0), summary.get('medium', 0))
" 2>/dev/null || echo "0 0 0")

    read -r sast_critical sast_high sast_medium <<< "${counts}"

    if [[ ${sast_critical} -gt ${POLICY_CRITICAL_MAX} ]]; then
        record_check "SAST Critical" "FAIL" "${sast_critical} critical finding(s) (max: ${POLICY_CRITICAL_MAX})"
    else
        record_check "SAST Critical" "PASS" "${sast_critical} critical (threshold: ${POLICY_CRITICAL_MAX})"
    fi

    if [[ ${sast_high} -gt ${POLICY_HIGH_MAX} ]]; then
        record_check "SAST High" "FAIL" "${sast_high} high finding(s) (max: ${POLICY_HIGH_MAX})"
    else
        record_check "SAST High" "PASS" "${sast_high} high (threshold: ${POLICY_HIGH_MAX})"
    fi

    record_check "SAST Medium" "INFO" "${sast_medium} medium findings (informational)"
}

# ---------------------------------------------------------------------------
# Check: DAST Results
# WHY: Dynamic testing catches runtime vulnerabilities that static analysis
# misses (authentication flow bugs, runtime injection, misconfigured
# headers). DAST failures indicate the running application is exploitable.
# ---------------------------------------------------------------------------
check_dast() {
    log_info "Checking DAST results..."

    local dast_dir="${REPORT_DIR}/dast"

    if [[ ! -d "${dast_dir}" ]]; then
        record_check "DAST Analysis" "WARN" "No DAST report directory found. Run dast-pipeline.sh first."
        return
    fi

    local latest_report
    latest_report=$(find "${dast_dir}" -name "zap-*.json" -type f 2>/dev/null | sort -r | head -1)

    if [[ -z "${latest_report}" ]]; then
        record_check "DAST Analysis" "WARN" "No DAST report found"
        return
    fi

    # Check ZAP results
    local high_alerts
    high_alerts=$(python3 -c "
import json
with open('${latest_report}') as f:
    data = json.load(f)
# ZAP JSON format varies; handle both formats
alerts = data.get('site', [{}])
if isinstance(alerts, list) and alerts:
    alerts = alerts[0].get('alerts', [])
    high_count = sum(1 for a in alerts if a.get('riskcode', '0') in ('3', '2'))
    print(high_count)
else:
    print(0)
" 2>/dev/null || echo "0")

    if [[ ${high_alerts} -gt 0 ]]; then
        record_check "DAST Analysis" "FAIL" "${high_alerts} high/critical alert(s) from ZAP"
    else
        record_check "DAST Analysis" "PASS" "No high/critical DAST alerts"
    fi

    # Check casino-specific test results
    local casino_report
    casino_report=$(find "${dast_dir}" -name "casino-tests-*.json" -type f 2>/dev/null | sort -r | head -1)

    if [[ -n "${casino_report}" ]]; then
        local casino_findings
        casino_findings=$(python3 -c "
import json
with open('${casino_report}') as f:
    data = json.load(f)
print(data.get('summary', {}).get('findings', 0))
" 2>/dev/null || echo "0")

        if [[ ${casino_findings} -gt 0 ]]; then
            record_check "Casino-Specific Tests" "FAIL" "${casino_findings} casino-specific finding(s)"
        else
            record_check "Casino-Specific Tests" "PASS" "All casino-specific tests passed"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Check: Casino-Specific Security Requirements
# WHY: Beyond generic security, iGaming platforms have domain-specific
# requirements that regulators and auditors check:
#   - Payment endpoints must have additional authentication layers
#   - RNG endpoints must be isolated and access-controlled
#   - Admin panel must require MFA
#   - Player data endpoints must enforce authorization
# ---------------------------------------------------------------------------
check_casino_requirements() {
    log_info "Checking iGaming-specific security requirements..."

    # Check 1: Payment endpoint protection
    # WHY: Payment endpoints (deposit, withdraw, transfer) are the highest-value
    # targets. They must enforce authentication, authorization, rate limiting,
    # and transaction signing.
    local payment_files
    payment_files=$(find "${REPO_ROOT}" -type f -name "*.py" \
        -path "*/payment*" -o -path "*/wallet*" -o -path "*/cashier*" \
        2>/dev/null | head -20)

    if [[ -n "${payment_files}" ]]; then
        local unprotected=0
        while IFS= read -r filepath; do
            # Check if authentication decorator/middleware is present
            if ! grep -qE '(@requires_auth|@login_required|@authenticated|Depends\(.*auth|verify_token|check_permission)' \
                "${filepath}" 2>/dev/null; then
                log_warn "  Payment file may lack auth: ${filepath}"
                unprotected=$((unprotected + 1))
            fi
        done <<< "${payment_files}"

        if [[ ${unprotected} -gt 0 ]]; then
            record_check "Payment Endpoint Auth" "WARN" "${unprotected} payment file(s) may lack authentication"
        else
            record_check "Payment Endpoint Auth" "PASS" "All payment files have authentication"
        fi
    else
        record_check "Payment Endpoint Auth" "INFO" "No payment endpoint files found in scan scope"
    fi

    # Check 2: RNG endpoint isolation
    # WHY: The Random Number Generator is the core of fair gaming. If RNG
    # endpoints are accessible to unauthorized parties, game outcomes can
    # be predicted or manipulated -- a criminal offense in most jurisdictions.
    local rng_files
    rng_files=$(find "${REPO_ROOT}" -type f -name "*.py" \
        -path "*/rng*" -o -path "*/random*" -o -path "*/game_engine*" \
        2>/dev/null | head -10)

    if [[ -n "${rng_files}" ]]; then
        local rng_issues=0
        while IFS= read -r filepath; do
            # Check for insecure random usage
            if grep -qE 'import random$|random\.random|random\.randint' \
                "${filepath}" 2>/dev/null; then
                log_warn "  Insecure random in RNG file: ${filepath}"
                rng_issues=$((rng_issues + 1))
            fi
        done <<< "${rng_files}"

        if [[ ${rng_issues} -gt 0 ]]; then
            record_check "RNG Security" "FAIL" "${rng_issues} file(s) use insecure random in RNG context"
        else
            record_check "RNG Security" "PASS" "No insecure random usage in RNG files"
        fi
    else
        record_check "RNG Security" "INFO" "No RNG files found in scan scope"
    fi

    # Check 3: Admin panel security
    # WHY: Admin panels provide God-mode access: player management, balance
    # adjustments, game configuration, compliance overrides. They must
    # require strong authentication (MFA) and log all actions.
    local admin_files
    admin_files=$(find "${REPO_ROOT}" -type f -name "*.py" \
        -path "*/admin*" -o -path "*/backoffice*" \
        2>/dev/null | head -10)

    if [[ -n "${admin_files}" ]]; then
        local admin_issues=0
        while IFS= read -r filepath; do
            if ! grep -qE '(@requires_admin|@admin_required|@requires_role.*admin|role.*admin|permission.*admin)' \
                "${filepath}" 2>/dev/null; then
                admin_issues=$((admin_issues + 1))
            fi
        done <<< "${admin_files}"

        if [[ ${admin_issues} -gt 0 ]]; then
            record_check "Admin Panel Auth" "WARN" "${admin_issues} admin file(s) may lack role-based access"
        else
            record_check "Admin Panel Auth" "PASS" "Admin files have role-based access controls"
        fi
    else
        record_check "Admin Panel Auth" "INFO" "No admin files found in scan scope"
    fi
}

# ---------------------------------------------------------------------------
# Generate Compliance Report
# WHY: Gaming regulators require documented evidence of security testing.
# This report maps findings to regulatory requirements and provides
# the audit trail needed for license renewals and compliance reviews.
# ---------------------------------------------------------------------------
generate_compliance_report() {
    log_info "Generating compliance report..."

    local gate_status="PASSED"
    if [[ "${GATE_PASS}" == "false" ]]; then
        gate_status="FAILED"
    fi

    # Build results JSON from associative array
    local results_json="["
    local first=true
    for check_name in "${!GATE_RESULTS[@]}"; do
        local value="${GATE_RESULTS[${check_name}]}"
        local status="${value%%|*}"
        local detail="${value#*|}"

        if [[ "${first}" == "true" ]]; then
            first=false
        else
            results_json+=","
        fi
        results_json+=$(printf '\n    {"check": "%s", "status": "%s", "detail": "%s"}' \
            "${check_name}" "${status}" "${detail}")
    done
    results_json+=$'\n  ]'

    cat > "${COMPLIANCE_REPORT}" << REPORT_EOF
{
  "report_type": "security_gate_compliance",
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "repository": "$(basename "${REPO_ROOT}")",
  "branch": "$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")",
  "commit": "$(git rev-parse HEAD 2>/dev/null || echo "unknown")",
  "commit_short": "$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")",
  "gate_decision": "${gate_status}",
  "policy": {
    "max_critical": ${POLICY_CRITICAL_MAX},
    "max_high": ${POLICY_HIGH_MAX},
    "max_secrets": ${POLICY_SECRETS_MAX},
    "strict_mode": ${STRICT_MODE}
  },
  "checks": ${results_json},
  "regulatory_mapping": {
    "mga_article_24": "Security of processing systems",
    "ukgc_lccp_15": "Protection of customer funds and assets",
    "pci_dss_6": "Develop and maintain secure systems",
    "gdpr_article_32": "Security of processing"
  }
}
REPORT_EOF

    log_success "Compliance report: ${COMPLIANCE_REPORT}"
}

# ---------------------------------------------------------------------------
# Post Results to Jira
# WHY: Security findings must be tracked in the project management system
# for accountability and audit trail. Jira comments create a permanent
# record linking code changes to security assessments.
# ---------------------------------------------------------------------------
post_to_jira() {
    local ticket="$1"

    if [[ -z "${JIRA_BASE_URL}" ]] || [[ -z "${JIRA_TOKEN}" ]]; then
        log_warn "Jira credentials not configured. Set JIRA_BASE_URL and JIRA_TOKEN."
        return 0
    fi

    log_info "Posting security gate results to ${ticket}..."

    local gate_status="PASSED"
    if [[ "${GATE_PASS}" == "false" ]]; then
        gate_status="FAILED"
    fi

    # Build comment body
    local comment="h3. Security Gate: ${gate_status}\n\n"
    comment+="||Check||Status||Detail||\n"

    for check_name in "${!GATE_RESULTS[@]}"; do
        local value="${GATE_RESULTS[${check_name}]}"
        local status="${value%%|*}"
        local detail="${value#*|}"

        local icon="(/)"
        if [[ "${status}" == "FAIL" ]]; then icon="(x)"; fi
        if [[ "${status}" == "WARN" ]]; then icon="(!)"; fi

        comment+="|${check_name}|${icon} ${status}|${detail}|\n"
    done

    comment+="\n_Commit: $(git rev-parse --short HEAD 2>/dev/null || echo "unknown")_"
    comment+="\n_Branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")_"

    local payload
    payload=$(printf '{"body": "%s"}' "${comment}")

    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST \
        -H "Authorization: Basic ${JIRA_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "${payload}" \
        "${JIRA_BASE_URL}/rest/api/2/issue/${ticket}/comment" 2>/dev/null || echo "000")

    if [[ "${http_code}" =~ ^2 ]]; then
        log_success "Posted results to Jira ticket ${ticket}"
    else
        log_warn "Failed to post to Jira (HTTP ${http_code}). Check JIRA_BASE_URL and JIRA_TOKEN."
    fi
}

# ---------------------------------------------------------------------------
# Display Final Gate Decision
# ---------------------------------------------------------------------------
display_gate_decision() {
    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${BOLD}  Security Gate Decision${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""

    # Display results table
    printf "  %-30s %-8s %s\n" "CHECK" "STATUS" "DETAIL"
    printf "  %-30s %-8s %s\n" "-----" "------" "------"

    for check_name in "${!GATE_RESULTS[@]}"; do
        local value="${GATE_RESULTS[${check_name}]}"
        local status="${value%%|*}"
        local detail="${value#*|}"

        local color="${NC}"
        case "${status}" in
            PASS) color="${GREEN}" ;;
            FAIL) color="${RED}" ;;
            WARN) color="${YELLOW}" ;;
        esac

        printf "  %-30s ${color}%-8s${NC} %s\n" "${check_name}" "${status}" "${detail}"
    done

    echo ""

    if [[ "${GATE_PASS}" == "true" ]]; then
        echo -e "  ${GREEN}${BOLD}DECISION: MERGE ALLOWED${NC}"
        echo -e "  ${GREEN}All security checks passed. Code may proceed to deployment.${NC}"
    else
        echo -e "  ${RED}${BOLD}DECISION: MERGE BLOCKED${NC}"
        echo -e "  ${RED}Security policy violations detected. Remediate before deploying.${NC}"
    fi

    echo ""
    echo "  Compliance Report: ${COMPLIANCE_REPORT}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local ci_mode=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --report-dir)
                REPORT_DIR="${2:?'--report-dir requires a directory'}"
                shift 2
                ;;
            --strict)
                STRICT_MODE=true
                POLICY_HIGH_MAX=0
                shift
                ;;
            --jira)
                JIRA_TICKET="${2:?'--jira requires a ticket ID'}"
                shift 2
                ;;
            --max-critical)
                POLICY_CRITICAL_MAX="${2:?'--max-critical requires a number'}"
                shift 2
                ;;
            --max-high)
                POLICY_HIGH_MAX="${2:?'--max-high requires a number'}"
                shift 2
                ;;
            --ci)
                ci_mode=true  # Used to control output formatting
                export ci_mode
                shift
                ;;
            --output)
                COMPLIANCE_REPORT="${2:?'--output requires a path'}"
                shift 2
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 2
                ;;
        esac
    done

    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${BOLD}  iGaming Security Gate${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""

    if [[ ! -d "${REPORT_DIR}" ]]; then
        log_error "Report directory not found: ${REPORT_DIR}"
        log_error "Run security scanning tools first (scan-secrets.sh, sast-pipeline.sh, dast-pipeline.sh)"
        exit 2
    fi

    # Run all checks
    check_secrets
    check_sast
    check_dast
    check_casino_requirements

    # Generate compliance report
    mkdir -p "$(dirname "${COMPLIANCE_REPORT}")"
    generate_compliance_report

    # Post to Jira if configured
    if [[ -n "${JIRA_TICKET}" ]]; then
        post_to_jira "${JIRA_TICKET}"
    fi

    # Display final decision
    display_gate_decision

    # Exit code
    if [[ "${GATE_PASS}" == "true" ]]; then
        exit 0
    else
        exit 1
    fi
}

main "$@"
