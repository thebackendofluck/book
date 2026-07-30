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

# shellcheck disable=SC2034,SC2155
# =============================================================================
# Compliance Gate for CI/CD Pipelines
# Blocks deployment if PCI DSS or ISO 27001 compliance checks fail.
#
# Designed as a pipeline stage that runs after build/test and before deploy.
# Integrates with Jenkins, GitLab CI, GitHub Actions, and standalone execution.
#
# Usage:
#   ./compliance-gate.sh --environment staging --frameworks pci,iso27001
#   ./compliance-gate.sh --environment production --fail-on warn
#   ./compliance-gate.sh --environment production --dry-run
#
# Exit Codes:
#   0 - All checks passed, deployment may proceed
#   1 - Compliance failures detected, deployment blocked
#   2 - Configuration or runtime error
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPORTS_DIR="${COMPLIANCE_REPORTS_DIR:-/opt/acme-casino/compliance-reports}"
ENVIRONMENT="${ENVIRONMENT:-staging}"
FRAMEWORKS="${FRAMEWORKS:-pci,iso27001}"
FAIL_ON="${FAIL_ON:-fail}"          # fail | warn (whether warnings also block)
DRY_RUN="${DRY_RUN:-false}"
SLACK_WEBHOOK="${SLACK_WEBHOOK_URL:-}"

# Pipeline detection
PIPELINE_NAME="${CI_PIPELINE_NAME:-${BUILD_TAG:-${GITHUB_WORKFLOW:-manual}}}"
PIPELINE_URL="${CI_PIPELINE_URL:-${BUILD_URL:-${GITHUB_SERVER_URL:-}/${GITHUB_REPOSITORY:-}/actions/runs/${GITHUB_RUN_ID:-}}}"
COMMIT_SHA="${CI_COMMIT_SHA:-${GIT_COMMIT:-${GITHUB_SHA:-$(git rev-parse HEAD 2>/dev/null || echo unknown)}}}"
DEPLOYER="${GITLAB_USER_LOGIN:-${BUILD_USER:-${GITHUB_ACTOR:-$(whoami)}}}"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { echo -e "${BLUE}[GATE]${NC} $(date -u +%H:%M:%S) $*"; }
pass()  { echo -e "${GREEN}[PASS]${NC} $*"; }
fail()  { echo -e "${RED}[FAIL]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
fatal() { echo -e "${RED}[FATAL]${NC} $*"; exit 2; }

# ---------------------------------------------------------------------------
# Pre-flight Checks
# ---------------------------------------------------------------------------
preflight() {
    log "Compliance Gate v1.0 — iGaming DevSecOps Pipeline"
    log "Environment: $ENVIRONMENT"
    log "Frameworks:  $FRAMEWORKS"
    log "Pipeline:    $PIPELINE_NAME"
    log "Commit:      ${COMMIT_SHA:0:12}"
    log "Deployer:    $DEPLOYER"
    log "Fail on:     $FAIL_ON"
    log "Dry run:     $DRY_RUN"
    echo ""

    # Ensure report directory exists
    mkdir -p "$REPORTS_DIR"

    # Check Python is available
    if ! command -v python3 &>/dev/null; then
        fatal "Python 3 is required for compliance checks"
    fi

    # Check compliance scripts exist
    if [[ ! -f "${SCRIPT_DIR}/pci-dss-checks.py" ]]; then
        fatal "PCI DSS check script not found at ${SCRIPT_DIR}/pci-dss-checks.py"
    fi
    if [[ ! -f "${SCRIPT_DIR}/iso27001-checks.py" ]]; then
        fatal "ISO 27001 check script not found at ${SCRIPT_DIR}/iso27001-checks.py"
    fi
}

# ---------------------------------------------------------------------------
# Run PCI DSS Checks
# ---------------------------------------------------------------------------
run_pci_checks() {
    local report_file="${REPORTS_DIR}/pci-dss-${ENVIRONMENT}-$(date -u +%Y%m%d-%H%M%S).json"

    log "Running PCI DSS v4.0 compliance checks..."
    log "Report: $report_file"

    local pci_exit=0
    python3 "${SCRIPT_DIR}/pci-dss-checks.py" \
        --target "$ENVIRONMENT" \
        --output "$report_file" \
        --quiet \
        2>&1 || pci_exit=$?

    if [[ ! -f "$report_file" ]]; then
        fail "PCI DSS report was not generated"
        return 1
    fi

    # Parse results
    local total passed failed warnings score
    total=$(jq '.total_checks' "$report_file")
    passed=$(jq '.passed' "$report_file")
    failed=$(jq '.failed' "$report_file")
    warnings=$(jq '.warnings' "$report_file")
    score=$(jq '.compliance_score' "$report_file")

    echo ""
    echo "  PCI DSS v4.0 Results:"
    echo "  ─────────────────────"
    echo "  Score:    ${score}%"
    echo "  Passed:   $passed / $total"
    echo "  Failed:   $failed"
    echo "  Warnings: $warnings"
    echo ""

    # Show failed checks
    if [[ "$failed" -gt 0 ]]; then
        echo "  Failed Controls:"
        jq -r '.checks[] | select(.status == "FAIL") | "    \(.requirement): \(.title)"' "$report_file"
        echo ""
    fi

    # Determine gate decision
    if [[ "$failed" -gt 0 ]]; then
        fail "PCI DSS: $failed compliance failures detected"
        return 1
    elif [[ "$warnings" -gt 0 && "$FAIL_ON" == "warn" ]]; then
        fail "PCI DSS: $warnings warnings (fail-on-warn enabled)"
        return 1
    elif [[ "$warnings" -gt 0 ]]; then
        warn "PCI DSS: $warnings warnings (non-blocking)"
        return 0
    else
        pass "PCI DSS: All checks passed (score: ${score}%)"
        return 0
    fi
}

# ---------------------------------------------------------------------------
# Run ISO 27001 Checks
# ---------------------------------------------------------------------------
run_iso27001_checks() {
    local report_file="${REPORTS_DIR}/iso27001-${ENVIRONMENT}-$(date -u +%Y%m%d-%H%M%S).json"

    log "Running ISO 27001:2022 compliance checks..."
    log "Report: $report_file"

    local iso_exit=0
    python3 "${SCRIPT_DIR}/iso27001-checks.py" \
        --target "$ENVIRONMENT" \
        --output "$report_file" \
        --quiet \
        2>&1 || iso_exit=$?

    if [[ ! -f "$report_file" ]]; then
        fail "ISO 27001 report was not generated"
        return 1
    fi

    local total passed failed warnings score
    total=$(jq '.total_checks' "$report_file")
    passed=$(jq '.passed' "$report_file")
    failed=$(jq '.failed' "$report_file")
    warnings=$(jq '.warnings' "$report_file")
    score=$(jq '.compliance_score' "$report_file")

    echo ""
    echo "  ISO 27001:2022 Results:"
    echo "  ───────────────────────"
    echo "  Score:    ${score}%"
    echo "  Passed:   $passed / $total"
    echo "  Failed:   $failed"
    echo "  Warnings: $warnings"
    echo ""

    if [[ "$failed" -gt 0 ]]; then
        echo "  Failed Controls:"
        jq -r '.checks[] | select(.status == "FAIL") | "    \(.control_id): \(.title)"' "$report_file"
        echo ""
    fi

    if [[ "$failed" -gt 0 ]]; then
        fail "ISO 27001: $failed compliance failures detected"
        return 1
    elif [[ "$warnings" -gt 0 && "$FAIL_ON" == "warn" ]]; then
        fail "ISO 27001: $warnings warnings (fail-on-warn enabled)"
        return 1
    elif [[ "$warnings" -gt 0 ]]; then
        warn "ISO 27001: $warnings warnings (non-blocking)"
        return 0
    else
        pass "ISO 27001: All checks passed (score: ${score}%)"
        return 0
    fi
}

# ---------------------------------------------------------------------------
# Additional CI/CD Security Checks
# ---------------------------------------------------------------------------
run_cicd_security_checks() {
    log "Running CI/CD pipeline security checks..."

    local failures=0

    # Check 1: No secrets in environment variables
    local secret_patterns="(password|secret|api_key|token|private_key)="
    if env | grep -iE "$secret_patterns" | grep -vE "^(SLACK_WEBHOOK|JIRA_TOKEN|PAGERDUTY|DEPENDENCY_TRACK)" >/dev/null 2>&1; then
        fail "Potential secrets detected in environment variables"
        ((failures++))
    else
        pass "No exposed secrets in environment variables"
    fi

    # Check 2: Docker socket not mounted in CI (unless explicitly required)
    if [[ -S /var/run/docker.sock ]] && [[ "$ENVIRONMENT" == "production" ]]; then
        warn "Docker socket mounted — ensure this is intentional for production deployments"
    fi

    # Check 3: Image being deployed uses a pinned digest
    if [[ -n "${DEPLOY_IMAGE:-}" ]]; then
        if [[ "$DEPLOY_IMAGE" == *":latest"* ]] || [[ "$DEPLOY_IMAGE" != *"@sha256:"* && "$DEPLOY_IMAGE" != *":"* ]]; then
            fail "Deployment image uses :latest or unpinned tag: $DEPLOY_IMAGE"
            ((failures++))
        else
            pass "Deployment image uses pinned tag: $DEPLOY_IMAGE"
        fi
    fi

    # Check 4: Verify deployment is from protected branch (production only)
    if [[ "$ENVIRONMENT" == "production" ]]; then
        local branch="${CI_COMMIT_BRANCH:-${GIT_BRANCH:-${GITHUB_REF_NAME:-unknown}}}"
        if [[ "$branch" != "main" && "$branch" != "master" && "$branch" != "release/"* ]]; then
            fail "Production deployment from non-protected branch: $branch"
            ((failures++))
        else
            pass "Deploying from protected branch: $branch"
        fi
    fi

    echo ""
    if [[ "$failures" -gt 0 ]]; then
        fail "CI/CD security: $failures issues detected"
        return 1
    else
        pass "CI/CD security: All checks passed"
        return 0
    fi
}

# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------
notify_result() {
    local status="$1"  # passed | blocked
    local details="$2"

    if [[ -z "$SLACK_WEBHOOK" ]]; then
        return 0
    fi

    local emoji color
    if [[ "$status" == "passed" ]]; then
        emoji=":white_check_mark:"
        color="good"
    else
        emoji=":no_entry:"
        color="danger"
    fi

    local message="${emoji} *Compliance Gate — ${ENVIRONMENT^^}*
Commit: \`${COMMIT_SHA:0:12}\`
Deployer: ${DEPLOYER}
Pipeline: ${PIPELINE_NAME}
Status: *${status^^}*
${details}"

    curl -sS -X POST "$SLACK_WEBHOOK" \
        -H 'Content-Type: application/json' \
        -d "{\"text\": \"${message}\"}" \
        >/dev/null 2>&1 || true
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --environment|-e) ENVIRONMENT="$2";   shift 2;;
            --frameworks|-f)  FRAMEWORKS="$2";    shift 2;;
            --fail-on)        FAIL_ON="$2";       shift 2;;
            --dry-run)        DRY_RUN="true";     shift;;
            --reports-dir)    REPORTS_DIR="$2";    shift 2;;
            --help|-h)
                echo "Usage: $0 [--environment <env>] [--frameworks <pci,iso27001>] [--fail-on <fail|warn>] [--dry-run]"
                exit 0
                ;;
            *) fatal "Unknown argument: $1";;
        esac
    done
}

main() {
    parse_args "$@"
    preflight

    local gate_result=0
    local gate_details=""
    IFS=',' read -ra fw_list <<< "$FRAMEWORKS"

    echo "═══════════════════════════════════════════════════════════════════"
    echo "  COMPLIANCE GATE — ${ENVIRONMENT^^}"
    echo "═══════════════════════════════════════════════════════════════════"
    echo ""

    # Run framework-specific checks
    for fw in "${fw_list[@]}"; do
        case "$fw" in
            pci|pci-dss|pcidss)
                if ! run_pci_checks; then
                    gate_result=1
                    gate_details="${gate_details}\nPCI DSS: FAILED"
                else
                    gate_details="${gate_details}\nPCI DSS: PASSED"
                fi
                ;;
            iso|iso27001)
                if ! run_iso27001_checks; then
                    gate_result=1
                    gate_details="${gate_details}\nISO 27001: FAILED"
                else
                    gate_details="${gate_details}\nISO 27001: PASSED"
                fi
                ;;
            *)
                warn "Unknown framework: $fw"
                ;;
        esac
    done

    # Always run CI/CD security checks
    if ! run_cicd_security_checks; then
        gate_result=1
        gate_details="${gate_details}\nCI/CD Security: FAILED"
    else
        gate_details="${gate_details}\nCI/CD Security: PASSED"
    fi

    # Final verdict
    echo ""
    echo "═══════════════════════════════════════════════════════════════════"
    if [[ "$DRY_RUN" == "true" ]]; then
        warn "DRY RUN — gate result would be: $([ $gate_result -eq 0 ] && echo PASS || echo BLOCK)"
        echo "═══════════════════════════════════════════════════════════════════"
        exit 0
    fi

    if [[ "$gate_result" -eq 0 ]]; then
        pass "COMPLIANCE GATE: PASSED — Deployment may proceed"
        notify_result "passed" "$gate_details"
    else
        fail "COMPLIANCE GATE: BLOCKED — Deployment denied"
        echo ""
        echo "  Deployment to ${ENVIRONMENT} has been blocked due to compliance failures."
        echo "  Review the reports in: ${REPORTS_DIR}"
        echo "  Contact: security-team@acme-casino.io"
        echo ""
        notify_result "blocked" "$gate_details"
    fi
    echo "═══════════════════════════════════════════════════════════════════"

    exit "$gate_result"
}

main "$@"
