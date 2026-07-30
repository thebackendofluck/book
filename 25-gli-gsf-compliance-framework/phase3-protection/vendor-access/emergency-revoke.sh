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

# shellcheck disable=SC2015,SC2034,SC2129,SC2155,SC2294
###############################################################################
# emergency-revoke.sh - Emergency Vendor Access Revocation
# GLI-GSF Phase 3 - Vendor Access Emergency Procedure
#
# Performs complete vendor access revocation in under 5 minutes:
#   1. Revoke IAM credentials and API keys        (~30s)
#   2. Kill active SSH/VPN sessions                (~30s)
#   3. Block vendor IP ranges at firewall          (~30s)
#   4. Rotate affected credentials and secrets     (~60s)
#   5. Notify security team and GIS officer        (~30s)
#   6. Generate evidence package for audit         (~60s)
#
# GLI-GSF-3 Reference: Section 3.2.8 - Emergency Access Revocation
#   - Must complete within 5 minutes of initiation
#   - Full audit trail of all actions taken
#   - Notification to GIS officer and security team
#   - Evidence package generated for ISF review
#
# Usage:
#   ./emergency-revoke.sh --vendor "VendorName"
#   ./emergency-revoke.sh --vendor "VendorName" --ip 203.0.113.50
#   ./emergency-revoke.sh --vendor "VendorName" --reason "Security incident"
#   ./emergency-revoke.sh --vendor "VendorName" --dry-run
#
# Requirements:
#   - AWS CLI v2 (for IAM operations, optional)
#   - jq (JSON parsing)
#   - iptables or nftables (firewall, optional)
#   - curl (notifications, optional)
#
# Environment Variables:
#   SLACK_WEBHOOK_URL  - Slack webhook for security alerts
#   PAGERDUTY_KEY      - PagerDuty routing key
#   GIS_EMAIL          - GIS officer email
#   VENDOR_CONFIG_DIR  - Vendor configuration directory
###############################################################################

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
VERSION="1.0.0"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
DATE_STAMP=$(date +"%Y%m%d-%H%M%S")
START_TIME=$(date +%s)

VENDOR_NAME=""
VENDOR_IP=""
REASON="Emergency revocation - security incident"
DRY_RUN=false

EVIDENCE_DIR="./emergency-revoke-evidence-${DATE_STAMP}"
LOG_FILE="${EVIDENCE_DIR}/revocation-log.txt"
VENDOR_CONFIG_DIR="${VENDOR_CONFIG_DIR:-/etc/gsf/vendor-access}"

SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}"
PAGERDUTY_KEY="${PAGERDUTY_KEY:-}"
GIS_EMAIL="${GIS_EMAIL:-gis-officer@acmetocasino.com}"
SECURITY_EMAIL="${SECURITY_EMAIL:-security-team@acmetocasino.com}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

STEP=0
TOTAL_STEPS=6
ERRORS=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log_step() {
    ((STEP++))
    local elapsed=$(( $(date +%s) - START_TIME ))
    echo -e "\n${CYAN}[Step ${STEP}/${TOTAL_STEPS}] [${elapsed}s elapsed]${NC} $1"
    echo "[$(date -u +%T)] STEP ${STEP}: $1" >> "$LOG_FILE"
}
log_action() { echo -e "  ${GREEN}[OK]${NC} $1"; echo "  [OK] $1" >> "$LOG_FILE"; }
log_skip()   { echo -e "  ${YELLOW}[SKIP]${NC} $1"; echo "  [SKIP] $1" >> "$LOG_FILE"; }
log_error()  { echo -e "  ${RED}[ERROR]${NC} $1"; echo "  [ERROR] $1" >> "$LOG_FILE"; ((ERRORS++)); }
log_dry()    { echo -e "  ${YELLOW}[DRY-RUN]${NC} Would: $1"; echo "  [DRY-RUN] $1" >> "$LOG_FILE"; }

run_or_dry() {
    if [[ "$DRY_RUN" == true ]]; then
        log_dry "$*"
    else
        eval "$@" 2>>"$LOG_FILE" && return 0 || return 1
    fi
}

usage() {
    cat << 'EOF'
Usage: emergency-revoke.sh --vendor NAME [OPTIONS]

Required:
  --vendor NAME        Vendor name to revoke access for

Options:
  --ip IP              Specific vendor IP to block
  --reason TEXT        Reason for revocation
  --dry-run            Show actions without executing
  --help               Show this help

Examples:
  ./emergency-revoke.sh --vendor "NetEnt" --reason "Compromised credentials"
  ./emergency-revoke.sh --vendor "NetEnt" --dry-run
  ./emergency-revoke.sh --vendor "PaymentCo" --ip 203.0.113.50
EOF
    exit 0
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --vendor)  VENDOR_NAME="$2"; shift 2 ;;
            --ip)      VENDOR_IP="$2"; shift 2 ;;
            --reason)  REASON="$2"; shift 2 ;;
            --dry-run) DRY_RUN=true; shift ;;
            --help|-h) usage ;;
            *) echo "Unknown: $1"; usage ;;
        esac
    done
    [[ -z "$VENDOR_NAME" ]] && { echo -e "${RED}Error: --vendor required${NC}"; usage; }
}

# ---------------------------------------------------------------------------
# Step 1: Revoke IAM Credentials (~30s)
# ---------------------------------------------------------------------------
revoke_iam_credentials() {
    log_step "Revoking IAM credentials and API keys for ${VENDOR_NAME}"

    local vendor_prefix="vendor-$(echo "$VENDOR_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')"

    if command -v aws &>/dev/null && aws sts get-caller-identity &>/dev/null 2>&1; then
        local users
        users=$(aws iam list-users --query "Users[?starts_with(UserName, '${vendor_prefix}')].UserName" --output text 2>/dev/null || echo "")

        if [[ -n "$users" ]]; then
            for user in $users; do
                # Deactivate access keys
                local keys
                keys=$(aws iam list-access-keys --user-name "$user" --query "AccessKeyMetadata[].AccessKeyId" --output text 2>/dev/null || echo "")
                for key in $keys; do
                    run_or_dry "aws iam update-access-key --user-name '$user' --access-key-id '$key' --status Inactive" && \
                        log_action "Deactivated key ${key} for ${user}" || log_error "Failed: key ${key}"
                done

                # Remove console access
                run_or_dry "aws iam delete-login-profile --user-name '$user' 2>/dev/null" && \
                    log_action "Removed console for ${user}" || log_skip "No console for ${user}"

                # Detach policies
                local policies
                policies=$(aws iam list-attached-user-policies --user-name "$user" --query "AttachedPolicies[].PolicyArn" --output text 2>/dev/null || echo "")
                for policy in $policies; do
                    run_or_dry "aws iam detach-user-policy --user-name '$user' --policy-arn '$policy'" && \
                        log_action "Detached ${policy}" || log_error "Failed detach ${policy}"
                done
            done
        else
            log_skip "No IAM users with prefix '${vendor_prefix}'"
        fi

        # DenyAll on vendor roles
        local roles
        roles=$(aws iam list-roles --query "Roles[?starts_with(RoleName, '${vendor_prefix}')].RoleName" --output text 2>/dev/null || echo "")
        for role in $roles; do
            run_or_dry "aws iam put-role-policy --role-name '$role' --policy-name 'DenyAll-Emergency' --policy-document '{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Deny\",\"Action\":\"*\",\"Resource\":\"*\"}]}'" && \
                log_action "DenyAll on role ${role}" || log_error "Failed DenyAll on ${role}"
        done
    else
        log_skip "AWS CLI not available"
    fi
}

# ---------------------------------------------------------------------------
# Step 2: Kill Active Sessions (~30s)
# ---------------------------------------------------------------------------
kill_active_sessions() {
    log_step "Killing active SSH/VPN sessions for ${VENDOR_NAME}"

    local ips_to_kill=()
    [[ -n "$VENDOR_IP" ]] && ips_to_kill+=("$VENDOR_IP")

    # Get IPs from vendor config
    if [[ -f "${VENDOR_CONFIG_DIR}/vendors.json" ]] && command -v python3 &>/dev/null; then
        local config_ips
        config_ips=$(python3 -c "
import json, sys
try:
    data = json.load(open('${VENDOR_CONFIG_DIR}/vendors.json'))
    for v in data.values():
        if v.get('name','').lower() == '${VENDOR_NAME}'.lower():
            for ip in v.get('allowed_ips', []):
                print(ip)
except: pass
" 2>/dev/null || true)
        while IFS= read -r ip; do
            [[ -n "$ip" ]] && ips_to_kill+=("$ip")
        done <<< "$config_ips"
    fi

    if [[ ${#ips_to_kill[@]} -eq 0 ]]; then
        log_skip "No vendor IPs found"; return
    fi

    for ip in "${ips_to_kill[@]}"; do
        # Kill SSH sessions
        local ssh_ttys
        ssh_ttys=$(who --ips 2>/dev/null | grep "$ip" | awk '{print $2}' || true)
        if [[ -n "$ssh_ttys" ]]; then
            for tty in $ssh_ttys; do
                local pid
                pid=$(ps -t "$tty" -o pid= 2>/dev/null | head -1 || true)
                [[ -n "$pid" ]] && run_or_dry "kill -9 $pid" && \
                    log_action "Killed SSH on $tty (PID $pid) from $ip"
            done
        else
            log_skip "No SSH sessions from $ip"
        fi

        # Kill OpenVPN sessions
        if [[ -S /var/run/openvpn/management.sock ]]; then
            run_or_dry "echo 'kill $ip' | socat - UNIX-CONNECT:/var/run/openvpn/management.sock" && \
                log_action "Killed OpenVPN for $ip" || log_skip "No OpenVPN session for $ip"
        fi
    done

    # Revoke via vendor access controller
    local script_dir
    script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "${script_dir}/vendor_access_controller.py" ]]; then
        run_or_dry "python3 '${script_dir}/vendor_access_controller.py' revoke-all --vendor '${VENDOR_NAME}' --reason '${REASON}'" && \
            log_action "Revoked all via vendor access controller" || log_error "Controller revocation failed"
    fi
}

# ---------------------------------------------------------------------------
# Step 3: Block Vendor IPs at Firewall (~30s)
# ---------------------------------------------------------------------------
block_vendor_ips() {
    log_step "Blocking vendor IPs at firewall"

    local ips_to_block=()
    [[ -n "$VENDOR_IP" ]] && ips_to_block+=("$VENDOR_IP")

    if [[ -f "${VENDOR_CONFIG_DIR}/vendors.json" ]] && command -v python3 &>/dev/null; then
        local config_ips
        config_ips=$(python3 -c "
import json
try:
    data = json.load(open('${VENDOR_CONFIG_DIR}/vendors.json'))
    for v in data.values():
        if v.get('name','').lower() == '${VENDOR_NAME}'.lower():
            for ip in v.get('allowed_ips', []):
                print(ip)
except: pass
" 2>/dev/null || true)
        while IFS= read -r ip; do
            [[ -n "$ip" ]] && ips_to_block+=("$ip")
        done <<< "$config_ips"
    fi

    [[ ${#ips_to_block[@]} -eq 0 ]] && { log_skip "No IPs to block"; return; }

    for ip in "${ips_to_block[@]}"; do
        if command -v iptables &>/dev/null; then
            run_or_dry "iptables -I INPUT -s '$ip' -j DROP -m comment --comment 'Emergency: ${VENDOR_NAME} $(date +%Y%m%d)'" && \
                log_action "Blocked $ip via iptables" || log_error "Failed iptables block $ip"
        fi
        if command -v nft &>/dev/null; then
            run_or_dry "nft add element inet filter vendor_blocklist { $ip } 2>/dev/null" && \
                log_action "Blocked $ip via nftables" || log_skip "nftables set may not exist"
        fi
        echo "$ip" >> "${EVIDENCE_DIR}/blocked-ips.txt"
    done
}

# ---------------------------------------------------------------------------
# Step 4: Rotate Credentials (~60s)
# ---------------------------------------------------------------------------
rotate_credentials() {
    log_step "Rotating affected credentials"

    cat > "${EVIDENCE_DIR}/rotation-checklist.txt" << 'CHECKLIST'
Credential Rotation Checklist (GLI-GSF-3):
  [ ] Database passwords for vendor-accessible schemas
  [ ] API keys for vendor integration endpoints
  [ ] Service account passwords used by vendor
  [ ] Shared secrets (webhook signing keys)
  [ ] TLS client certificates issued to vendor
  [ ] OAuth client_secret for vendor applications
CHECKLIST
    log_action "Rotation checklist saved"

    # Rotate AWS Secrets Manager entries
    if command -v aws &>/dev/null; then
        local vendor_slug
        vendor_slug=$(echo "$VENDOR_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
        local secrets
        secrets=$(aws secretsmanager list-secrets \
            --query "SecretList[?contains(Name, '${vendor_slug}')].Name" \
            --output text 2>/dev/null || echo "")
        for secret in $secrets; do
            run_or_dry "aws secretsmanager rotate-secret --secret-id '$secret'" && \
                log_action "Rotated: $secret" || log_error "Failed rotate: $secret"
        done
        [[ -z "$secrets" ]] && log_skip "No vendor secrets in Secrets Manager"
    fi

    local new_pass
    new_pass=$(openssl rand -base64 24 2>/dev/null || python3 -c "import secrets; print(secrets.token_urlsafe(24))")
    echo "Rotation password: ${new_pass}" > "${EVIDENCE_DIR}/new-credentials.txt"
    chmod 600 "${EVIDENCE_DIR}/new-credentials.txt"
    log_action "New credentials generated"
}

# ---------------------------------------------------------------------------
# Step 5: Notify Team (~30s)
# ---------------------------------------------------------------------------
notify_team() {
    log_step "Notifying security team and GIS officer"

    local elapsed=$(( $(date +%s) - START_TIME ))
    local message="EMERGENCY VENDOR ACCESS REVOCATION
Vendor: ${VENDOR_NAME}
Reason: ${REASON}
Time: ${TIMESTAMP}
Duration: ${elapsed}s
Errors: ${ERRORS}
Evidence: ${EVIDENCE_DIR}/
GLI-GSF-3 Ref: Section 3.2.8"

    if [[ -n "$SLACK_WEBHOOK_URL" ]]; then
        run_or_dry "curl -s -X POST '$SLACK_WEBHOOK_URL' -H 'Content-Type: application/json' -d '{\"text\":\"EMERGENCY: Vendor ${VENDOR_NAME} access revoked. Reason: ${REASON}\"}'" && \
            log_action "Slack notification sent" || log_error "Slack failed"
    else
        log_skip "SLACK_WEBHOOK_URL not set"
    fi

    if [[ -n "$PAGERDUTY_KEY" ]]; then
        run_or_dry "curl -s -X POST 'https://events.pagerduty.com/v2/enqueue' -H 'Content-Type: application/json' -d '{\"routing_key\":\"${PAGERDUTY_KEY}\",\"event_action\":\"trigger\",\"payload\":{\"summary\":\"Emergency vendor revocation: ${VENDOR_NAME}\",\"severity\":\"critical\",\"source\":\"$(hostname)\"}}'" && \
            log_action "PagerDuty alert triggered" || log_error "PagerDuty failed"
    else
        log_skip "PAGERDUTY_KEY not set"
    fi

    if command -v mail &>/dev/null; then
        echo "$message" | run_or_dry "mail -s 'EMERGENCY: ${VENDOR_NAME} Access Revoked' '${GIS_EMAIL}'" && \
            log_action "Email sent to ${GIS_EMAIL}" || log_error "Email failed"
    else
        log_skip "mail not available"
    fi

    echo "$message" > "${EVIDENCE_DIR}/notification-content.txt"
    log_action "Notification content saved"
}

# ---------------------------------------------------------------------------
# Step 6: Generate Evidence Package (~60s)
# ---------------------------------------------------------------------------
generate_evidence() {
    log_step "Generating evidence package for ISF review"

    local elapsed=$(( $(date +%s) - START_TIME ))
    cat > "${EVIDENCE_DIR}/revocation-summary.json" << SUMMARY
{
  "document_type": "Emergency Vendor Access Revocation Report",
  "gli_gsf_reference": "GLI-GSF-3, Section 3.2.8",
  "vendor": "${VENDOR_NAME}",
  "reason": "${REASON}",
  "initiated_at": "${TIMESTAMP}",
  "completed_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "duration_seconds": ${elapsed},
  "sla_target_seconds": 300,
  "sla_met": $([ $elapsed -le 300 ] && echo true || echo false),
  "dry_run": ${DRY_RUN},
  "errors": ${ERRORS},
  "initiated_by": "$(whoami)",
  "hostname": "$(hostname)"
}
SUMMARY

    # Capture system state for evidence
    ss -tnp 2>/dev/null > "${EVIDENCE_DIR}/active-connections.txt" || true
    who 2>/dev/null > "${EVIDENCE_DIR}/active-logins.txt" || true

    if command -v tar &>/dev/null; then
        tar czf "${EVIDENCE_DIR}.tar.gz" -C "$(dirname "$EVIDENCE_DIR")" "$(basename "$EVIDENCE_DIR")" 2>/dev/null && \
            log_action "Evidence archive: ${EVIDENCE_DIR}.tar.gz"
    fi

    log_action "Evidence package complete"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    parse_args "$@"
    mkdir -p "$EVIDENCE_DIR"
    touch "$LOG_FILE"

    echo ""
    echo -e "${RED}=========================================================${NC}"
    echo -e "${RED}  EMERGENCY VENDOR ACCESS REVOCATION${NC}"
    echo -e "${RED}=========================================================${NC}"
    echo ""
    echo -e "  Vendor:    ${YELLOW}${VENDOR_NAME}${NC}"
    echo -e "  Reason:    ${REASON}"
    echo -e "  Time:      ${TIMESTAMP}"
    echo -e "  Dry Run:   ${DRY_RUN}"
    echo -e "  ${CYAN}GLI-GSF-3 SLA: Must complete within 5 minutes${NC}"
    echo ""

    echo "=== Emergency Vendor Revocation ===" >> "$LOG_FILE"
    echo "Vendor: ${VENDOR_NAME}" >> "$LOG_FILE"
    echo "Reason: ${REASON}" >> "$LOG_FILE"
    echo "Timestamp: ${TIMESTAMP}" >> "$LOG_FILE"

    revoke_iam_credentials
    kill_active_sessions
    block_vendor_ips
    rotate_credentials
    notify_team
    generate_evidence

    local total_elapsed=$(( $(date +%s) - START_TIME ))
    local sla_color="${GREEN}"
    [[ $total_elapsed -gt 300 ]] && sla_color="${RED}"

    echo ""
    echo -e "${RED}=========================================================${NC}"
    echo -e "  REVOCATION COMPLETE"
    echo -e "  Time: ${total_elapsed}s  SLA: ${sla_color}$([ $total_elapsed -le 300 ] && echo MET || echo MISSED)${NC}  Errors: ${ERRORS}"
    echo -e "  Evidence: ${EVIDENCE_DIR}/"
    [[ "$DRY_RUN" == true ]] && echo -e "  ${YELLOW}DRY RUN - No changes made${NC}"
    echo -e "${RED}=========================================================${NC}"
    echo ""

    [[ $ERRORS -eq 0 ]] && exit 0 || exit 1
}

main "$@"
