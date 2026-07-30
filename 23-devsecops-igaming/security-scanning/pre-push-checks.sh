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
# Git Pre-Push Security Hook for iGaming Platform
# =============================================================================
# Chapter 23: DevSecOps - Security Scanning
#
# WHY: Pre-push hooks are the last line of defense before code leaves the
# developer's machine. While pre-commit hooks catch issues in individual
# files, pre-push hooks validate the aggregate of all commits being pushed.
#
# For iGaming platforms, pre-push checks catch:
#   - Secrets that slipped through pre-commit (e.g., added in a fixup commit)
#   - Hardcoded production IPs that could expose infrastructure
#   - Database credentials accidentally included in migrations
#   - Debug flags that disable security controls
#   - Missing compliance tags required by change management
#
# INSTALLATION:
#   cp pre-push-checks.sh .git/hooks/pre-push
#   chmod +x .git/hooks/pre-push
#   (Or use setup-pre-commit.sh which handles this automatically)
#
# USAGE (as standalone):
#   ./pre-push-checks.sh                  # Run all checks
#   ./pre-push-checks.sh --help           # Show this help
#
# EXIT CODES:
#   0 - All checks passed, push allowed
#   1 - Security issue found, push blocked
#
# =============================================================================
set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# Track failures
FAILURES=0
WARNINGS=0

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------
log_info() { echo -e "${BLUE}[PRE-PUSH]${NC} $1"; }
log_success() { echo -e "${GREEN}[PRE-PUSH PASS]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[PRE-PUSH WARN]${NC} $1"; WARNINGS=$((WARNINGS + 1)); }
log_error() { echo -e "${RED}[PRE-PUSH FAIL]${NC} $1" >&2; FAILURES=$((FAILURES + 1)); }

show_help() {
    echo "Usage: $(basename "$0") [OPTIONS]"
    echo ""
    echo "Git pre-push security hook for iGaming platforms."
    echo "Validates all staged commits before pushing to remote."
    echo ""
    echo "Options:"
    echo "  --skip-gitleaks    Skip gitleaks secret scanning"
    echo "  --skip-ip-check    Skip hardcoded IP detection"
    echo "  --skip-debug       Skip debug flag detection"
    echo "  --help             Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  SKIP_PRE_PUSH      Set to 'true' to skip all pre-push checks"
    echo "                     (USE WITH EXTREME CAUTION - for emergencies only)"
    echo ""
}

# Get the range of commits being pushed
# When used as a git hook, stdin provides the ref info
get_commit_range() {
    local range=""

    # When run as a git hook, read from stdin
    if [[ ! -t 0 ]]; then
        while read -r _local_ref local_sha _remote_ref remote_sha; do
            if [[ "${remote_sha}" == "0000000000000000000000000000000000000000" ]]; then
                # New branch: check all commits not in any remote branch
                range="${local_sha}"
            elif [[ "${local_sha}" == "0000000000000000000000000000000000000000" ]]; then
                # Deleting branch: nothing to check
                continue
            else
                range="${remote_sha}..${local_sha}"
            fi
        done
    fi

    # Fallback: check commits not yet pushed
    if [[ -z "${range}" ]]; then
        local remote_branch
        remote_branch=$(git rev-parse --abbrev-ref --symbolic-full-name "@{upstream}" 2>/dev/null || echo "")
        if [[ -n "${remote_branch}" ]]; then
            range="${remote_branch}..HEAD"
        else
            # No upstream: check last 10 commits as safety measure
            range="HEAD~10..HEAD"
        fi
    fi

    echo "${range}"
}

# ---------------------------------------------------------------------------
# Check 1: Gitleaks on Staged Commits
# WHY: Scans the actual diff of commits being pushed, catching secrets
# that may have been introduced in any commit in the push batch.
# This catches secrets missed by pre-commit (e.g., added via git commit
# --no-verify, or introduced in merge commits).
# ---------------------------------------------------------------------------
check_gitleaks() {
    log_info "Running gitleaks on commits being pushed..."

    if ! command -v gitleaks &>/dev/null; then
        log_warn "gitleaks not installed. Install with: ./setup-pre-commit.sh"
        return 0
    fi

    local config_file=""
    if [[ -f "${REPO_ROOT}/.gitleaks.toml" ]]; then
        config_file="${REPO_ROOT}/.gitleaks.toml"
    elif [[ -f "${SCRIPT_DIR}/gitleaks-config.toml" ]]; then
        config_file="${SCRIPT_DIR}/gitleaks-config.toml"
    fi

    local gitleaks_args=("detect" "--source" "${REPO_ROOT}" "--verbose")
    if [[ -n "${config_file}" ]]; then
        gitleaks_args+=("--config" "${config_file}")
    fi

    local commit_range
    commit_range=$(get_commit_range)

    if [[ -n "${commit_range}" ]]; then
        gitleaks_args+=("--log-opts=${commit_range}")
    fi

    local exit_code=0
    gitleaks "${gitleaks_args[@]}" 2>&1 || exit_code=$?

    if [[ ${exit_code} -eq 0 ]]; then
        log_success "No secrets found in commits"
    else
        log_error "SECRETS DETECTED in commits being pushed!"
        log_error "Remove secrets, rotate credentials, then try again."
        log_error "Tip: Use 'git rebase -i' to edit the offending commit."
    fi
}

# ---------------------------------------------------------------------------
# Check 2: Hardcoded Production IPs
# WHY: Hardcoded IP addresses of production servers in code or config
# expose infrastructure to targeted attacks. Attackers can:
#   - Directly probe production databases
#   - Bypass CDN/WAF protections
#   - Map internal network topology
# Production IPs must come from environment variables or secret managers.
# ---------------------------------------------------------------------------
check_hardcoded_ips() {
    log_info "Checking for hardcoded production IP addresses..."

    local commit_range
    commit_range=$(get_commit_range)

    # Get the diff of all commits being pushed
    local diff_content
    diff_content=$(git diff "${commit_range}" 2>/dev/null || git diff HEAD 2>/dev/null || echo "")

    if [[ -z "${diff_content}" ]]; then
        log_success "No diff to check for IPs"
        return 0
    fi

    # Known production IP patterns to check
    # WHY: These patterns cover common production infrastructure:
    #   - RFC 1918 private ranges that indicate internal network exposure
    #   - Common cloud provider metadata IPs
    #   - Specific production IPs (add your own below)
    local ip_patterns=(
        # Cloud provider metadata endpoints (never hardcode these)
        '169\.254\.169\.254'
        # Example production IPs (customize for your platform)
        # Add your production server IPs here:
        # '10\.0\.1\.[0-9]+'     # Production DB subnet
        # '10\.0\.2\.[0-9]+'     # Payment processing subnet
    )

    local found_ips=0
    for pattern in "${ip_patterns[@]}"; do
        local matches
        matches=$(echo "${diff_content}" | grep -n "^+" | grep -oE "${pattern}" 2>/dev/null || true)
        if [[ -n "${matches}" ]]; then
            log_error "Found hardcoded IP matching pattern: ${pattern}"
            found_ips=$((found_ips + 1))
        fi
    done

    # Also check for any IP:port combinations in added lines
    # (common pattern for hardcoded database connections)
    local ip_port_matches
    ip_port_matches=$(echo "${diff_content}" | grep -n "^+" | \
        grep -oE '[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}:[0-9]{4,5}' 2>/dev/null || true)

    if [[ -n "${ip_port_matches}" ]]; then
        # Exclude common safe patterns (localhost, test IPs)
        local filtered
        filtered=$(echo "${ip_port_matches}" | grep -v -E '^(127\.0\.0\.1|0\.0\.0\.0|192\.168\.)' || true)
        if [[ -n "${filtered}" ]]; then
            log_warn "Found IP:port combinations in code (verify these are not production):"
            echo "${filtered}" | while IFS= read -r match; do
                echo "    ${match}"
            done
        fi
    fi

    if [[ ${found_ips} -eq 0 ]]; then
        log_success "No hardcoded production IPs found"
    fi
}

# ---------------------------------------------------------------------------
# Check 3: Database Credentials in Code
# WHY: Database connection strings contain passwords that grant direct
# access to player data, financial records, and game outcomes. Even in
# migration files, hardcoded credentials can leak via git history.
# Connection strings must use environment variables or secret managers.
# ---------------------------------------------------------------------------
check_database_credentials() {
    log_info "Checking for database credentials in code..."

    local commit_range
    commit_range=$(get_commit_range)

    local diff_content
    diff_content=$(git diff "${commit_range}" 2>/dev/null || git diff HEAD 2>/dev/null || echo "")

    if [[ -z "${diff_content}" ]]; then
        log_success "No diff to check for credentials"
        return 0
    fi

    # Only check added lines (lines starting with +)
    local added_lines
    added_lines=$(echo "${diff_content}" | grep "^+" | grep -v "^+++" || true)

    local cred_patterns=(
        # PostgreSQL connection strings with embedded passwords
        'postgres(ql)?://[^:]+:[^@]+@'
        # Redis with password
        'redis://:[^@]+@'
        # MySQL connection strings
        'mysql://[^:]+:[^@]+@'
        # MongoDB with credentials
        'mongodb(\+srv)?://[^:]+:[^@]+@'
        # Generic DSN with password
        'password=[^&\s]{8,}'
    )

    local found_creds=0
    for pattern in "${cred_patterns[@]}"; do
        local matches
        matches=$(echo "${added_lines}" | grep -iE "${pattern}" 2>/dev/null || true)
        if [[ -n "${matches}" ]]; then
            # Filter out common safe patterns
            local filtered
            filtered=$(echo "${matches}" | grep -v -iE '(example|test|fake|dummy|placeholder|localhost|changeme|\$\{|<%=|{{)' || true)
            if [[ -n "${filtered}" ]]; then
                log_error "Possible database credentials found:"
                echo "${filtered}" | head -3 | while IFS= read -r line; do
                    echo "    ${line:0:80}..."
                done
                found_creds=$((found_creds + 1))
            fi
        fi
    done

    if [[ ${found_creds} -eq 0 ]]; then
        log_success "No database credentials found in code"
    fi
}

# ---------------------------------------------------------------------------
# Check 4: Debug Flags Left Enabled
# WHY: Debug flags in iGaming platforms can:
#   - Expose stack traces revealing internal architecture
#   - Enable verbose logging that dumps player data to logs
#   - Disable authentication/authorization for testing
#   - Enable debug endpoints that bypass security controls
#   - Disable rate limiting, allowing abuse
# ---------------------------------------------------------------------------
check_debug_flags() {
    log_info "Checking for debug flags left enabled..."

    local commit_range
    commit_range=$(get_commit_range)

    local diff_content
    diff_content=$(git diff "${commit_range}" 2>/dev/null || git diff HEAD 2>/dev/null || echo "")

    if [[ -z "${diff_content}" ]]; then
        log_success "No diff to check for debug flags"
        return 0
    fi

    local added_lines
    added_lines=$(echo "${diff_content}" | grep "^+" | grep -v "^+++" || true)

    # Debug patterns that should not appear in production code
    local debug_patterns=(
        'DEBUG\s*=\s*True'
        'DEBUG\s*=\s*true'
        'DEBUG\s*=\s*1'
        'TESTING\s*=\s*True'
        'DISABLE_AUTH\s*=\s*True'
        'DISABLE_AUTH\s*=\s*true'
        'SKIP_VERIFICATION\s*=\s*True'
        'SKIP_VERIFICATION\s*=\s*true'
        'DISABLE_RATE_LIMIT'
        'BYPASS_SECURITY'
        'import pdb'
        'pdb\.set_trace'
        'breakpoint()'
        'debugger;'
        'console\.log.*password'
        'console\.log.*secret'
        'console\.log.*token'
        'print.*password'
        'print.*secret'
        'print.*token'
    )

    local found_debug=0
    for pattern in "${debug_patterns[@]}"; do
        local matches
        matches=$(echo "${added_lines}" | grep -iE "${pattern}" 2>/dev/null || true)
        if [[ -n "${matches}" ]]; then
            # Filter out comments and test files
            local filtered
            filtered=$(echo "${matches}" | grep -v -E '^\+\s*(#|//|/\*|\*)' || true)
            if [[ -n "${filtered}" ]]; then
                log_error "Debug flag found: ${pattern}"
                echo "${filtered}" | head -2 | while IFS= read -r line; do
                    echo "    ${line:0:80}"
                done
                found_debug=$((found_debug + 1))
            fi
        fi
    done

    if [[ ${found_debug} -eq 0 ]]; then
        log_success "No debug flags found in code"
    fi
}

# ---------------------------------------------------------------------------
# Check 5: Compliance Tags
# WHY: Gaming regulators require every change to be traceable to a
# ticket/requirement. Compliance tags in commit messages (e.g.,
# "feat(payment): JIRA-123") enable automated audit trail generation.
# Without them, auditors must manually cross-reference changes -- a
# process that can delay license renewal by weeks.
# ---------------------------------------------------------------------------
check_compliance_tags() {
    log_info "Checking for compliance tags in commit messages..."

    local commit_range
    commit_range=$(get_commit_range)

    local commits
    commits=$(git log --oneline "${commit_range}" 2>/dev/null || git log --oneline -5 2>/dev/null || echo "")

    if [[ -z "${commits}" ]]; then
        log_success "No commits to check"
        return 0
    fi

    # Check for conventional commit format
    # WHY: Conventional commits (feat:, fix:, security:) enable automated
    # changelog generation required by regulatory change management.
    local non_conventional=0
    while IFS= read -r commit_line; do
        local message="${commit_line#* }"  # Remove hash prefix
        if ! echo "${message}" | grep -qE '^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|security|compliance)(\(.*\))?:'; then
            log_warn "Non-conventional commit: ${commit_line}"
            non_conventional=$((non_conventional + 1))
        fi
    done <<< "${commits}"

    if [[ ${non_conventional} -eq 0 ]]; then
        log_success "All commits follow conventional format"
    else
        log_warn "${non_conventional} commit(s) don't follow conventional commit format"
        log_warn "Expected format: type(scope): description"
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local skip_gitleaks=false
    local skip_ip_check=false
    local skip_debug=false

    # Check for emergency skip
    if [[ "${SKIP_PRE_PUSH:-false}" == "true" ]]; then
        log_warn "============================================================"
        log_warn "  PRE-PUSH CHECKS SKIPPED (SKIP_PRE_PUSH=true)"
        log_warn "  This should only be used in genuine emergencies."
        log_warn "============================================================"
        exit 0
    fi

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --skip-gitleaks)  skip_gitleaks=true; shift ;;
            --skip-ip-check)  skip_ip_check=true; shift ;;
            --skip-debug)     skip_debug=true; shift ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                # When used as a git hook, extra args may be passed (remote name, URL)
                shift
                ;;
        esac
    done

    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    echo -e "${BOLD}  iGaming Pre-Push Security Checks${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""

    # Run checks
    [[ "${skip_gitleaks}" != "true" ]]  && check_gitleaks
    [[ "${skip_ip_check}" != "true" ]]  && check_hardcoded_ips
    check_database_credentials
    [[ "${skip_debug}" != "true" ]]     && check_debug_flags
    check_compliance_tags

    # Summary
    echo ""
    echo -e "${BOLD}=====================================================================${NC}"
    printf "  Failures: %d  |  Warnings: %d\n" "${FAILURES}" "${WARNINGS}"

    if [[ ${FAILURES} -gt 0 ]]; then
        echo ""
        echo -e "  ${RED}${BOLD}PUSH BLOCKED: ${FAILURES} security issue(s) found.${NC}"
        echo -e "  ${RED}Fix the issues above and try again.${NC}"
        echo ""
        echo "  To skip in emergencies (NOT recommended):"
        echo "    SKIP_PRE_PUSH=true git push"
        echo -e "${BOLD}=====================================================================${NC}"
        echo ""
        exit 1
    fi

    echo ""
    echo -e "  ${GREEN}${BOLD}PUSH ALLOWED: All security checks passed.${NC}"
    echo -e "${BOLD}=====================================================================${NC}"
    echo ""
    exit 0
}

main "$@"
