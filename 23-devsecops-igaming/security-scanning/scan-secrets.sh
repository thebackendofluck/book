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
# Standalone Secret Scanning Script for iGaming Platform
# =============================================================================
# Chapter 23: DevSecOps - Security Scanning
#
# WHY: Pre-commit hooks only scan new changes. This script performs
# comprehensive scans of the entire repository including git history.
# Critical for:
#   - Initial onboarding of legacy codebases
#   - Periodic full audits required by gaming regulators (MGA, UKGC)
#   - CI/CD pipeline integration for merge request scanning
#   - Incident response: "did we ever commit credentials?"
#
# USAGE:
#   ./scan-secrets.sh                    # Scan current files
#   ./scan-secrets.sh --history          # Scan full git history
#   ./scan-secrets.sh --branch main      # Scan specific branch
#   ./scan-secrets.sh --ci               # CI mode (JSON output, exit codes)
#   ./scan-secrets.sh --notify           # Send Slack/email on findings
#   ./scan-secrets.sh --help             # Show this help
#
# EXIT CODES:
#   0 - No secrets found
#   1 - Secrets found (findings in report)
#   2 - Scanner error or misconfiguration
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
GITLEAKS_CONFIG="${SCRIPT_DIR}/gitleaks-config.toml"
REPORT_DIR="${REPO_ROOT}/.security-reports"
TIMESTAMP="$(date -u +%Y%m%d_%H%M%S)"
REPORT_JSON="${REPORT_DIR}/secrets-${TIMESTAMP}.json"
REPORT_SARIF="${REPORT_DIR}/secrets-${TIMESTAMP}.sarif"

# Notification settings (override via environment variables)
SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
NOTIFICATION_EMAIL="${NOTIFICATION_EMAIL:-}"
SMTP_SERVER="${SMTP_SERVER:-localhost}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

show_help() {
    echo "Usage: $(basename "$0") [OPTIONS]"
    echo ""
    echo "Comprehensive secret scanning for iGaming platform repositories."
    echo ""
    echo "Options:"
    echo "  --history         Scan full git history (all commits, all branches)"
    echo "  --branch NAME     Scan specific branch history"
    echo "  --since DATE      Scan commits since date (e.g., '2024-01-01')"
    echo "  --ci              CI mode: JSON output, strict exit codes, no color"
    echo "  --notify          Send notifications on findings (requires SLACK_WEBHOOK_URL"
    echo "                    or NOTIFICATION_EMAIL environment variables)"
    echo "  --sarif           Generate SARIF format report (for GitHub Security tab)"
    echo "  --config PATH     Custom gitleaks config file"
    echo "  --output DIR      Custom output directory for reports"
    echo "  --help            Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  SLACK_WEBHOOK_URL     Slack incoming webhook URL for notifications"
    echo "  NOTIFICATION_EMAIL    Email address for findings notification"
    echo "  SMTP_SERVER           SMTP server for email notifications (default: localhost)"
    echo ""
    echo "Exit Codes:"
    echo "  0  No secrets found"
    echo "  1  Secrets found (review report)"
    echo "  2  Scanner error or misconfiguration"
    echo ""
    echo "Examples:"
    echo "  $(basename "$0")                           # Quick scan of current files"
    echo "  $(basename "$0") --history                 # Full history audit"
    echo "  $(basename "$0") --ci --notify             # CI pipeline with alerts"
    echo "  $(basename "$0") --since '2024-06-01'      # Scan recent history"
    echo ""
}

check_gitleaks() {
    if ! command -v gitleaks &>/dev/null; then
        log_error "gitleaks is not installed."
        log_error "Install with: ./setup-pre-commit.sh"
        log_error "Or manually: https://github.com/gitleaks/gitleaks/releases"
        exit 2
    fi
}

ensure_report_dir() {
    mkdir -p "${REPORT_DIR}"
}

# ---------------------------------------------------------------------------
# Scan Functions
# ---------------------------------------------------------------------------

# Scan current working directory (no git history)
scan_current_files() {
    log_info "Scanning current files for secrets..."

    local exit_code=0
    gitleaks detect \
        --config "${GITLEAKS_CONFIG}" \
        --source "${REPO_ROOT}" \
        --report-path "${REPORT_JSON}" \
        --report-format json \
        --no-git \
        --verbose 2>&1 || exit_code=$?

    return ${exit_code}
}

# Scan full git history
scan_git_history() {
    local log_opts="${1:-}"

    if [[ -n "${log_opts}" ]]; then
        log_info "Scanning git history with options: ${log_opts}"
    else
        log_info "Scanning full git history (all commits, all branches)..."
        log_info "This may take several minutes for large repositories."
    fi

    local exit_code=0
    if [[ -n "${log_opts}" ]]; then
        gitleaks detect \
            --config "${GITLEAKS_CONFIG}" \
            --source "${REPO_ROOT}" \
            --report-path "${REPORT_JSON}" \
            --report-format json \
            --log-opts="${log_opts}" \
            --verbose 2>&1 || exit_code=$?
    else
        gitleaks detect \
            --config "${GITLEAKS_CONFIG}" \
            --source "${REPO_ROOT}" \
            --report-path "${REPORT_JSON}" \
            --report-format json \
            --log-opts="--all" \
            --verbose 2>&1 || exit_code=$?
    fi

    return ${exit_code}
}

# Scan specific branch
scan_branch() {
    local branch="$1"
    log_info "Scanning branch: ${branch}"

    local exit_code=0
    gitleaks detect \
        --config "${GITLEAKS_CONFIG}" \
        --source "${REPO_ROOT}" \
        --report-path "${REPORT_JSON}" \
        --report-format json \
        --log-opts="--all ${branch}" \
        --verbose 2>&1 || exit_code=$?

    return ${exit_code}
}

# Generate SARIF report (for GitHub Security tab)
generate_sarif() {
    log_info "Generating SARIF report..."

    gitleaks detect \
        --config "${GITLEAKS_CONFIG}" \
        --source "${REPO_ROOT}" \
        --report-path "${REPORT_SARIF}" \
        --report-format sarif \
        --no-git 2>&1 || true

    if [[ -f "${REPORT_SARIF}" ]]; then
        log_success "SARIF report: ${REPORT_SARIF}"
    fi
}

# ---------------------------------------------------------------------------
# Reporting Functions
# ---------------------------------------------------------------------------

# Parse JSON report and display summary table
display_summary() {
    local report_file="$1"

    if [[ ! -f "${report_file}" ]]; then
        log_info "No report file generated (no findings)"
        return 0
    fi

    local finding_count
    finding_count=$(python3 -c "
import json, sys
try:
    with open('${report_file}') as f:
        data = json.load(f)
    print(len(data) if isinstance(data, list) else 0)
except (json.JSONDecodeError, FileNotFoundError):
    print(0)
" 2>/dev/null || echo "0")

    if [[ "${finding_count}" -eq 0 ]]; then
        return 0
    fi

    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${RED}${BOLD}  SECRET SCANNING FINDINGS: ${finding_count} potential secret(s) detected${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""

    # Display summary table using Python for JSON parsing
    python3 -c "
import json, sys

with open('${report_file}') as f:
    findings = json.load(f)

if not isinstance(findings, list) or not findings:
    sys.exit(0)

# Print table header
print(f'{'Rule':<35} {'File':<45} {'Line':<6} {'Commit':<10}')
print('-' * 96)

# Group by rule
from collections import Counter
rule_counts = Counter(f.get('RuleID', 'unknown') for f in findings)

for finding in findings[:50]:  # Limit to first 50
    rule = finding.get('RuleID', 'unknown')[:34]
    filepath = finding.get('File', 'unknown')
    # Truncate long paths from the left
    if len(filepath) > 44:
        filepath = '...' + filepath[-41:]
    line = str(finding.get('StartLine', '?'))
    commit = finding.get('Commit', '')[:9]
    print(f'{rule:<35} {filepath:<45} {line:<6} {commit:<10}')

if len(findings) > 50:
    print(f'  ... and {len(findings) - 50} more findings')

print()
print('Summary by rule:')
print('-' * 50)
for rule, count in rule_counts.most_common():
    print(f'  {rule:<40} {count:>5}')
print('-' * 50)
print(f'  {'TOTAL':<40} {len(findings):>5}')
" 2>/dev/null || log_warn "Could not parse report for summary table"

    echo ""
    echo -e "${YELLOW}Full report: ${report_file}${NC}"
    echo ""

    return 1
}

# ---------------------------------------------------------------------------
# Notification Functions
# ---------------------------------------------------------------------------

# Send Slack notification
# WHY: Security findings need immediate attention. Slack notifications
# ensure the security team is alerted in real-time, not just when
# someone checks CI logs.
notify_slack() {
    local finding_count="$1"
    local report_file="$2"

    if [[ -z "${SLACK_WEBHOOK_URL}" ]]; then
        return 0
    fi

    log_info "Sending Slack notification..."

    local repo_name
    repo_name=$(basename "${REPO_ROOT}")
    local branch
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")

    local payload
    payload=$(cat <<SLACK_EOF
{
    "blocks": [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "Secret Scanning Alert",
                "emoji": true
            }
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": "*Repository:*\n${repo_name}"},
                {"type": "mrkdwn", "text": "*Branch:*\n${branch}"},
                {"type": "mrkdwn", "text": "*Findings:*\n${finding_count}"},
                {"type": "mrkdwn", "text": "*Time:*\n$(date -u +%Y-%m-%dT%H:%M:%SZ)"}
            ]
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Action Required:* Review findings and rotate any exposed credentials immediately."
            }
        }
    ]
}
SLACK_EOF
)

    if curl -s -X POST -H 'Content-type: application/json' \
        --data "${payload}" "${SLACK_WEBHOOK_URL}" > /dev/null 2>&1; then
        log_success "Slack notification sent"
    else
        log_warn "Failed to send Slack notification"
    fi
}

# Send email notification
notify_email() {
    local finding_count="$1"
    local report_file="$2"

    if [[ -z "${NOTIFICATION_EMAIL}" ]]; then
        return 0
    fi

    log_info "Sending email notification to ${NOTIFICATION_EMAIL}..."

    local repo_name
    repo_name=$(basename "${REPO_ROOT}")
    local subject="[SECURITY] Secret scanning found ${finding_count} issue(s) in ${repo_name}"

    local body
    body="Secret scanning detected ${finding_count} potential secret(s) in repository: ${repo_name}

Time: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Branch: $(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo 'unknown')
Report: ${report_file}

ACTION REQUIRED:
1. Review the findings in the attached report
2. Rotate any confirmed leaked credentials IMMEDIATELY
3. Update .gitleaksignore for confirmed false positives

This is an automated message from the iGaming DevSecOps pipeline."

    if command -v mail &>/dev/null; then
        echo "${body}" | mail -s "${subject}" "${NOTIFICATION_EMAIL}" 2>/dev/null || true
        log_success "Email notification sent"
    elif command -v sendmail &>/dev/null; then
        {
            echo "Subject: ${subject}"
            echo "To: ${NOTIFICATION_EMAIL}"
            echo ""
            echo "${body}"
        } | sendmail "${NOTIFICATION_EMAIL}" 2>/dev/null || true
        log_success "Email notification sent"
    else
        log_warn "No mail command available. Install mailutils for email notifications."
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local scan_mode="current"
    local branch_name=""
    local since_date=""
    local ci_mode=false
    local do_notify=false
    local do_sarif=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --history)
                scan_mode="history"
                shift
                ;;
            --branch)
                scan_mode="branch"
                branch_name="${2:?'--branch requires a branch name'}"
                shift 2
                ;;
            --since)
                scan_mode="since"
                since_date="${2:?'--since requires a date'}"
                shift 2
                ;;
            --ci)
                ci_mode=true
                shift
                ;;
            --notify)
                do_notify=true
                shift
                ;;
            --sarif)
                do_sarif=true
                shift
                ;;
            --config)
                GITLEAKS_CONFIG="${2:?'--config requires a path'}"
                shift 2
                ;;
            --output)
                REPORT_DIR="${2:?'--output requires a directory'}"
                REPORT_JSON="${REPORT_DIR}/secrets-${TIMESTAMP}.json"
                REPORT_SARIF="${REPORT_DIR}/secrets-${TIMESTAMP}.sarif"
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

    # Validate prerequisites
    check_gitleaks

    if [[ ! -f "${GITLEAKS_CONFIG}" ]]; then
        log_error "Gitleaks config not found: ${GITLEAKS_CONFIG}"
        log_error "Run setup-pre-commit.sh first or specify --config PATH"
        exit 2
    fi

    ensure_report_dir

    # Run the appropriate scan
    local scan_exit=0
    case "${scan_mode}" in
        current)
            scan_current_files || scan_exit=$?
            ;;
        history)
            scan_git_history || scan_exit=$?
            ;;
        branch)
            scan_branch "${branch_name}" || scan_exit=$?
            ;;
        since)
            scan_git_history "--since=${since_date}" || scan_exit=$?
            ;;
    esac

    # Generate SARIF if requested
    if [[ "${do_sarif}" == "true" ]]; then
        generate_sarif
    fi

    # Display results
    if [[ ${scan_exit} -eq 0 ]]; then
        echo ""
        log_success "============================================================"
        log_success "  NO SECRETS FOUND - Repository is clean"
        log_success "============================================================"
        echo ""
        log_info "Report: ${REPORT_JSON}"
        exit 0
    fi

    # Findings detected
    display_summary "${REPORT_JSON}" || true

    # Count findings for notifications
    local finding_count
    finding_count=$(python3 -c "
import json
try:
    with open('${REPORT_JSON}') as f:
        data = json.load(f)
    print(len(data) if isinstance(data, list) else 0)
except Exception:
    print(0)
" 2>/dev/null || echo "unknown")

    # Send notifications if requested
    if [[ "${do_notify}" == "true" ]]; then
        notify_slack "${finding_count}" "${REPORT_JSON}"
        notify_email "${finding_count}" "${REPORT_JSON}"
    fi

    # CI mode: explicit exit code
    if [[ "${ci_mode}" == "true" ]]; then
        log_error "CI GATE FAILED: ${finding_count} secret(s) detected"
        exit 1
    fi

    exit 1
}

main "$@"
