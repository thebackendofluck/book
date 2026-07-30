#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 25, GLI-GSF Compliance Framework.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034
###############################################################################
# mfa-audit.sh - MFA Coverage Audit for OGIS-2 Compliance
#
# OGIS-2 mandates 100% MFA coverage on all administrative accounts.
# Hardware tokens are required for critical roles, software tokens for
# standard privileged users. No exceptions.
#
# This script audits MFA status across:
#   - AWS IAM users and roles
#   - Okta user accounts (via API)
#   - Local system accounts (PAM)
#   - Service accounts with interactive login capability
#
# The ISF will test by attempting access without MFA, and it must fail.
#
# Usage:
#   ./mfa-audit.sh                    # Full audit (all providers)
#   ./mfa-audit.sh --aws-only         # AWS IAM audit only
#   ./mfa-audit.sh --okta-only        # Okta audit only
#   ./mfa-audit.sh --output json      # JSON output for evidence package
#   ./mfa-audit.sh --critical-only    # Only check critical/admin roles
#
# Requirements:
#   - AWS CLI v2 configured with appropriate permissions
#   - jq for JSON parsing
#   - curl for Okta API calls
#
# Environment Variables:
#   OKTA_DOMAIN     - Okta tenant domain (e.g., acmetocasino.okta.com)
#   OKTA_API_TOKEN  - Okta API token for user enumeration
#
# Exit Codes:
#   0 - All accounts have MFA enabled (OGIS-2 compliant)
#   1 - One or more accounts lack MFA (OGIS-2 non-compliant)
#   2 - Script error (missing dependencies, API failures)
###############################################################################

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VERSION="1.0.0"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE_STAMP=$(date +"%Y-%m-%d")
OUTPUT_FORMAT="${OUTPUT_FORMAT:-text}"
REPORT_DIR="./mfa-audit-reports"

# ANSI colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# Counters
TOTAL_ACCOUNTS=0
MFA_ENABLED=0
MFA_DISABLED=0
CRITICAL_NO_MFA=0

# Results array (for JSON output)
declare -a RESULTS=()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_pass()  { echo -e "${GREEN}[PASS]${NC}  $*"; }
log_fail()  { echo -e "${RED}[FAIL]${NC}  $*"; }

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
check_dependencies() {
    local missing=()

    command -v jq >/dev/null 2>&1 || missing+=("jq")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing dependencies: ${missing[*]}"
        log_error "Install with: apt-get install ${missing[*]}"
        exit 2
    fi
}

# ---------------------------------------------------------------------------
# AWS IAM MFA Audit
# ---------------------------------------------------------------------------
audit_aws_iam() {
    log_info "=== AWS IAM MFA Audit ==="

    if ! command -v aws >/dev/null 2>&1; then
        log_warn "AWS CLI not found. Skipping AWS IAM audit."
        return
    fi

    # Check AWS credentials
    if ! aws sts get-caller-identity >/dev/null 2>&1; then
        log_warn "AWS credentials not configured. Skipping AWS IAM audit."
        return
    fi

    local account_id
    account_id=$(aws sts get-caller-identity --query "Account" --output text 2>/dev/null)
    log_info "AWS Account: ${account_id}"

    # List all IAM users
    local users
    users=$(aws iam list-users --query "Users[].UserName" --output text 2>/dev/null)

    if [[ -z "$users" ]]; then
        log_warn "No IAM users found or insufficient permissions."
        return
    fi

    log_info "Checking IAM users for MFA devices..."
    echo ""
    printf "  %-30s %-12s %-15s %-10s\n" "USER" "MFA STATUS" "MFA TYPE" "CONSOLE"
    printf "  %-30s %-12s %-15s %-10s\n" "----" "----------" "--------" "-------"

    for user in $users; do
        ((TOTAL_ACCOUNTS++))

        # Check for MFA devices
        local mfa_devices
        mfa_devices=$(aws iam list-mfa-devices --user-name "$user" \
            --query "MFADevices[].SerialNumber" --output text 2>/dev/null)

        # Check for virtual MFA
        local virtual_mfa
        virtual_mfa=$(aws iam list-virtual-mfa-devices \
            --query "VirtualMFADevices[?User.UserName=='${user}'].SerialNumber" \
            --output text 2>/dev/null)

        # Check console access
        local has_console="no"
        if aws iam get-login-profile --user-name "$user" >/dev/null 2>&1; then
            has_console="yes"
        fi

        # Determine MFA status and type
        local mfa_status="DISABLED"
        local mfa_type="none"

        if [[ -n "$mfa_devices" && "$mfa_devices" != "None" ]]; then
            mfa_status="ENABLED"
            ((MFA_ENABLED++))

            if echo "$mfa_devices" | grep -q "arn:aws:iam::.*:mfa/"; then
                mfa_type="virtual"
            else
                mfa_type="hardware"
            fi
        else
            ((MFA_DISABLED++))
        fi

        # Check if user has admin policies
        local is_admin=false
        local policies
        policies=$(aws iam list-attached-user-policies --user-name "$user" \
            --query "AttachedPolicies[].PolicyName" --output text 2>/dev/null)

        if echo "$policies" | grep -qi "admin\|full.*access\|power.*user"; then
            is_admin=true
        fi

        # Also check group policies
        local groups
        groups=$(aws iam list-groups-for-user --user-name "$user" \
            --query "Groups[].GroupName" --output text 2>/dev/null)

        for group in $groups; do
            local group_policies
            group_policies=$(aws iam list-attached-group-policies --group-name "$group" \
                --query "AttachedPolicies[].PolicyName" --output text 2>/dev/null)
            if echo "$group_policies" | grep -qi "admin\|full.*access"; then
                is_admin=true
            fi
        done

        # Log result
        if [[ "$mfa_status" == "ENABLED" ]]; then
            printf "  ${GREEN}%-30s %-12s %-15s %-10s${NC}\n" \
                "$user" "$mfa_status" "$mfa_type" "$has_console"
        else
            printf "  ${RED}%-30s %-12s %-15s %-10s${NC}\n" \
                "$user" "$mfa_status" "$mfa_type" "$has_console"

            if [[ "$is_admin" == true ]]; then
                ((CRITICAL_NO_MFA++))
                log_fail "  CRITICAL: Admin user '$user' has NO MFA!"
            elif [[ "$has_console" == "yes" ]]; then
                log_fail "  Console user '$user' has NO MFA (OGIS-2 violation)"
            fi
        fi

        # Store result for JSON output
        RESULTS+=("{\"provider\":\"aws-iam\",\"account\":\"$user\",\"mfa_enabled\":$([ "$mfa_status" == "ENABLED" ] && echo true || echo false),\"mfa_type\":\"$mfa_type\",\"is_admin\":$is_admin,\"console_access\":$([ "$has_console" == "yes" ] && echo true || echo false)}")
    done

    echo ""

    # Check root account MFA
    log_info "Checking AWS root account MFA..."
    local root_mfa
    root_mfa=$(aws iam get-account-summary --query "SummaryMap.AccountMFAEnabled" --output text 2>/dev/null)

    if [[ "$root_mfa" == "1" ]]; then
        log_pass "Root account MFA: ENABLED"
    else
        log_fail "Root account MFA: DISABLED (CRITICAL OGIS-2 violation!)"
        ((CRITICAL_NO_MFA++))
    fi

    # Check IAM password policy
    log_info "Checking IAM password policy..."
    local pwd_policy
    if pwd_policy=$(aws iam get-account-password-policy 2>/dev/null); then
        local min_length
        min_length=$(echo "$pwd_policy" | jq -r '.PasswordPolicy.MinimumPasswordLength // 0')
        local require_symbols
        require_symbols=$(echo "$pwd_policy" | jq -r '.PasswordPolicy.RequireSymbols // false')
        local max_age
        max_age=$(echo "$pwd_policy" | jq -r '.PasswordPolicy.MaxPasswordAge // 0')

        log_info "  Min length: ${min_length}, Symbols required: ${require_symbols}, Max age: ${max_age} days"
    else
        log_warn "  No password policy configured"
    fi
}

# ---------------------------------------------------------------------------
# Okta MFA Audit
# ---------------------------------------------------------------------------
audit_okta() {
    log_info "=== Okta MFA Audit ==="

    if [[ -z "${OKTA_DOMAIN:-}" || -z "${OKTA_API_TOKEN:-}" ]]; then
        log_warn "OKTA_DOMAIN or OKTA_API_TOKEN not set. Skipping Okta audit."
        log_warn "Set: export OKTA_DOMAIN=acmetocasino.okta.com"
        log_warn "Set: export OKTA_API_TOKEN=your-token"
        return
    fi

    if ! command -v curl >/dev/null 2>&1; then
        log_warn "curl not found. Skipping Okta audit."
        return
    fi

    local base_url="https://${OKTA_DOMAIN}/api/v1"

    # Fetch active users
    log_info "Fetching Okta users..."
    local users_response
    users_response=$(curl -s -H "Authorization: SSWS ${OKTA_API_TOKEN}" \
        -H "Accept: application/json" \
        "${base_url}/users?filter=status+eq+%22ACTIVE%22&limit=200" 2>/dev/null)

    if [[ -z "$users_response" ]] || echo "$users_response" | jq -e '.errorCode' >/dev/null 2>&1; then
        log_error "Failed to fetch Okta users. Check API token and domain."
        return
    fi

    local user_count
    user_count=$(echo "$users_response" | jq 'length')
    log_info "Found ${user_count} active Okta users"

    echo ""
    printf "  %-35s %-12s %-20s %-10s\n" "EMAIL" "MFA STATUS" "MFA FACTORS" "ADMIN"
    printf "  %-35s %-12s %-20s %-10s\n" "-----" "----------" "-----------" "-----"

    echo "$users_response" | jq -c '.[]' | while read -r user; do
        local email
        email=$(echo "$user" | jq -r '.profile.email')
        local user_id
        user_id=$(echo "$user" | jq -r '.id')

        ((TOTAL_ACCOUNTS++))

        # Fetch enrolled MFA factors
        local factors
        factors=$(curl -s -H "Authorization: SSWS ${OKTA_API_TOKEN}" \
            -H "Accept: application/json" \
            "${base_url}/users/${user_id}/factors" 2>/dev/null)

        local active_factors
        active_factors=$(echo "$factors" | jq '[.[] | select(.status == "ACTIVE")] | length')

        local factor_types
        factor_types=$(echo "$factors" | jq -r '[.[] | select(.status == "ACTIVE") | .factorType] | join(", ")')

        # Check admin status (simplified - check group membership)
        local is_admin=false
        local groups
        groups=$(curl -s -H "Authorization: SSWS ${OKTA_API_TOKEN}" \
            -H "Accept: application/json" \
            "${base_url}/users/${user_id}/groups" 2>/dev/null)

        if echo "$groups" | jq -r '.[].profile.name' | grep -qi "admin\|super\|platform"; then
            is_admin=true
        fi

        if [[ "$active_factors" -gt 0 ]]; then
            ((MFA_ENABLED++))
            printf "  ${GREEN}%-35s %-12s %-20s %-10s${NC}\n" \
                "$email" "ENABLED" "${factor_types:0:20}" "$is_admin"
        else
            ((MFA_DISABLED++))
            printf "  ${RED}%-35s %-12s %-20s %-10s${NC}\n" \
                "$email" "DISABLED" "none" "$is_admin"

            if [[ "$is_admin" == true ]]; then
                ((CRITICAL_NO_MFA++))
                log_fail "  CRITICAL: Admin '$email' has NO MFA!"
            fi
        fi

        RESULTS+=("{\"provider\":\"okta\",\"account\":\"$email\",\"mfa_enabled\":$([ "$active_factors" -gt 0 ] && echo true || echo false),\"mfa_type\":\"$factor_types\",\"is_admin\":$is_admin}")
    done

    echo ""
}

# ---------------------------------------------------------------------------
# Local System Account Audit
# ---------------------------------------------------------------------------
audit_local_accounts() {
    log_info "=== Local System Account Audit ==="

    # Check for accounts with login shells (potential interactive access)
    log_info "Checking accounts with login shells..."

    echo ""
    printf "  %-20s %-8s %-20s %-10s\n" "USER" "UID" "SHELL" "SUDO"
    printf "  %-20s %-8s %-20s %-10s\n" "----" "---" "-----" "----"

    while IFS=: read -r username _ uid _ _ _ shell; do
        # Skip system accounts (UID < 1000) except root
        if [[ "$uid" -lt 1000 && "$username" != "root" ]]; then
            continue
        fi

        # Skip nologin/false shells
        if [[ "$shell" == */nologin || "$shell" == */false ]]; then
            continue
        fi

        ((TOTAL_ACCOUNTS++))

        # Check sudo access
        local has_sudo="no"
        if groups "$username" 2>/dev/null | grep -qw "sudo\|wheel\|admin"; then
            has_sudo="yes"
        fi

        # Check if PAM MFA is configured (Google Authenticator, Duo, etc.)
        local pam_mfa="unknown"
        if [[ -f /etc/pam.d/sshd ]]; then
            if grep -q "pam_google_authenticator\|pam_duo\|pam_yubico" /etc/pam.d/sshd 2>/dev/null; then
                pam_mfa="configured"
                ((MFA_ENABLED++))
            else
                pam_mfa="not configured"
                ((MFA_DISABLED++))
                if [[ "$has_sudo" == "yes" ]]; then
                    ((CRITICAL_NO_MFA++))
                fi
            fi
        fi

        if [[ "$pam_mfa" == "configured" ]]; then
            printf "  ${GREEN}%-20s %-8s %-20s %-10s${NC}\n" \
                "$username" "$uid" "$shell" "$has_sudo"
        else
            printf "  ${RED}%-20s %-8s %-20s %-10s${NC}\n" \
                "$username" "$uid" "$shell" "$has_sudo"
            if [[ "$has_sudo" == "yes" ]]; then
                log_fail "  Sudo user '$username' missing PAM MFA"
            fi
        fi

        RESULTS+=("{\"provider\":\"local\",\"account\":\"$username\",\"uid\":$uid,\"shell\":\"$shell\",\"sudo\":$([ "$has_sudo" == "yes" ] && echo true || echo false),\"pam_mfa\":\"$pam_mfa\"}")

    done < /etc/passwd

    echo ""

    # Check SSH configuration
    log_info "Checking SSH MFA configuration..."
    if [[ -f /etc/ssh/sshd_config ]]; then
        local kbd_interactive
        kbd_interactive=$(grep -c "^ChallengeResponseAuthentication yes\|^KbdInteractiveAuthentication yes" /etc/ssh/sshd_config 2>/dev/null || echo "0")
        local auth_methods
        auth_methods=$(grep "^AuthenticationMethods" /etc/ssh/sshd_config 2>/dev/null || echo "not set")

        if [[ "$kbd_interactive" -gt 0 ]]; then
            log_pass "SSH ChallengeResponse/KbdInteractive: enabled"
        else
            log_warn "SSH ChallengeResponse/KbdInteractive: disabled"
        fi
        log_info "  AuthenticationMethods: $auth_methods"
    fi
}

# ---------------------------------------------------------------------------
# Generate Report
# ---------------------------------------------------------------------------
generate_report() {
    local compliance_pct=0
    if [[ $TOTAL_ACCOUNTS -gt 0 ]]; then
        compliance_pct=$((MFA_ENABLED * 100 / TOTAL_ACCOUNTS))
    fi

    local compliant="NON-COMPLIANT"
    if [[ $MFA_DISABLED -eq 0 && $CRITICAL_NO_MFA -eq 0 ]]; then
        compliant="COMPLIANT"
    fi

    if [[ "$OUTPUT_FORMAT" == "json" ]]; then
        # JSON output for evidence packages
        mkdir -p "$REPORT_DIR"
        local report_file="${REPORT_DIR}/mfa-audit-${DATE_STAMP}.json"

        cat > "$report_file" << EOF
{
  "document_type": "OGIS-2 MFA Coverage Audit",
  "gsf_reference": "GLI-GSF-5, OGIS-2",
  "audit_date": "${TIMESTAMP}",
  "compliance_status": "${compliant}",
  "summary": {
    "total_accounts": ${TOTAL_ACCOUNTS},
    "mfa_enabled": ${MFA_ENABLED},
    "mfa_disabled": ${MFA_DISABLED},
    "critical_accounts_without_mfa": ${CRITICAL_NO_MFA},
    "coverage_percentage": ${compliance_pct}
  },
  "ogis2_requirement": "100% MFA coverage on all administrative accounts",
  "accounts": [$(IFS=','; echo "${RESULTS[*]:-}")]
}
EOF
        log_info "JSON report saved to: ${report_file}"
    fi

    # Summary
    echo ""
    echo "============================================================"
    echo "  MFA Audit Summary - OGIS-2 Compliance Check"
    echo "============================================================"
    echo ""
    echo "  Date:          ${TIMESTAMP}"
    echo "  Total Accounts: ${TOTAL_ACCOUNTS}"
    echo ""

    if [[ $MFA_ENABLED -gt 0 ]]; then
        echo -e "  MFA Enabled:    ${GREEN}${MFA_ENABLED}${NC}"
    fi
    if [[ $MFA_DISABLED -gt 0 ]]; then
        echo -e "  MFA Disabled:   ${RED}${MFA_DISABLED}${NC}"
    else
        echo -e "  MFA Disabled:   ${GREEN}0${NC}"
    fi

    echo "  Coverage:       ${compliance_pct}%"
    echo ""

    if [[ $CRITICAL_NO_MFA -gt 0 ]]; then
        echo -e "  ${RED}CRITICAL: ${CRITICAL_NO_MFA} admin/critical accounts lack MFA!${NC}"
        echo ""
    fi

    if [[ "$compliant" == "COMPLIANT" ]]; then
        echo -e "  Status: ${GREEN}OGIS-2 COMPLIANT${NC} (100% MFA coverage)"
    else
        echo -e "  Status: ${RED}OGIS-2 NON-COMPLIANT${NC}"
        echo "  Action Required: Enable MFA on all ${MFA_DISABLED} accounts"
        echo "  OGIS-2 mandates: 100% coverage, no exceptions"
    fi

    echo ""
    echo "============================================================"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    local aws_only=false
    local okta_only=false
    local critical_only=false

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --aws-only)    aws_only=true; shift ;;
            --okta-only)   okta_only=true; shift ;;
            --critical-only) critical_only=true; shift ;;
            --output)      OUTPUT_FORMAT="$2"; shift 2 ;;
            --help|-h)
                echo "Usage: $0 [--aws-only] [--okta-only] [--output json|text]"
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                exit 2
                ;;
        esac
    done

    echo ""
    echo "============================================================"
    echo "  OGIS-2 MFA Coverage Audit v${VERSION}"
    echo "  GLI-GSF-5, OGIS-2: Back Office Administration Security"
    echo "============================================================"
    echo ""
    echo "  Requirement: 100% MFA on all administrative accounts"
    echo "  Hardware tokens for critical roles"
    echo "  Software tokens for standard privileged users"
    echo ""

    check_dependencies

    if [[ "$okta_only" != true ]]; then
        audit_aws_iam
    fi

    if [[ "$aws_only" != true ]]; then
        audit_okta
    fi

    if [[ "$aws_only" != true && "$okta_only" != true ]]; then
        audit_local_accounts
    fi

    generate_report

    # Exit code reflects compliance status
    if [[ $MFA_DISABLED -gt 0 || $CRITICAL_NO_MFA -gt 0 ]]; then
        exit 1
    fi
    exit 0
}

main "$@"
