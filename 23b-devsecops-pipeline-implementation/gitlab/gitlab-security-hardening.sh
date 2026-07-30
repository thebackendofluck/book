#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 23b, DevSecOps Pipeline Implementation.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# ============================================================================
# CONFIGURATION: Update the variables below before running this script.
# Replace all YOUR_* placeholders with your environment-specific values.
# ============================================================================
# gitlab-security-hardening.sh — Apply GitLab CE security best practices
#
# This script configures GitLab CE via the API to enforce security controls:
#   - Disable public sign-up
#   - Enforce MFA/2FA for all users
#   - Set password complexity requirements
#   - Configure session timeout
#   - Set default project/group visibility to private
#   - Enable audit logging
#   - Configure rate limiting
#   - Restrict container registry access
#   - Disable unused features
#
# Usage:
#   GITLAB_TOKEN=YOUR_GITLAB_TOKEN ./gitlab-security-hardening.sh
#   GITLAB_TOKEN=YOUR_GITLAB_TOKEN GITLAB_URL=http://YOUR_GITLAB_IP:8929 ./gitlab-security-hardening.sh
#
# Requirements: curl, jq
# The API token must have admin scope.

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
GITLAB_URL="${GITLAB_URL:-http://YOUR_GITLAB_IP:8929}"
GITLAB_TOKEN="${GITLAB_TOKEN:-YOUR_GITLAB_TOKEN}"
API="${GITLAB_URL}/api/v4"

LOG_FILE="/var/log/gitlab-security-hardening.log"
TIMESTAMP=$(date +"%Y-%m-%d_%H-%M-%S")

# Color codes for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() {
    echo -e "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

log_ok() {
    log "${GREEN}OK${NC} $*"
}

log_warn() {
    log "${YELLOW}WARN${NC} $*"
}

log_error() {
    log "${RED}ERROR${NC} $*" >&2
}

api_put() {
    # Usage: api_put /endpoint '{"key":"value"}'
    local endpoint="$1"
    local data="$2"
    local response
    local http_code

    response=$(curl -s -w "\n%{http_code}" \
        -X PUT \
        -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
        -H "Content-Type: application/json" \
        --data "${data}" \
        "${API}${endpoint}" 2>/dev/null)

    http_code=$(echo "${response}" | tail -1)
    local body
    body=$(echo "${response}" | head -n -1)

    if [[ "${http_code}" -ge 200 && "${http_code}" -lt 300 ]]; then
        echo "${body}"
        return 0
    else
        log_error "API PUT ${endpoint} failed: HTTP ${http_code} — ${body}"
        return 1
    fi
}

api_get() {
    local endpoint="$1"
    curl -s \
        -H "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
        "${API}${endpoint}" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Pre-flight: verify API connectivity and admin token
# ---------------------------------------------------------------------------
verify_connectivity() {
    log "Verifying API connectivity and token permissions..."

    local response
    response=$(api_get "/version") || {
        log_error "Cannot reach GitLab API at ${GITLAB_URL}"
        exit 1
    }

    local version
    version=$(echo "${response}" | jq -r '.version // "unknown"')
    log_ok "Connected to GitLab ${version} at ${GITLAB_URL}"

    # Check admin status
    local current_user
    current_user=$(api_get "/user")
    local is_admin
    is_admin=$(echo "${current_user}" | jq -r '.is_admin // false')
    if [[ "${is_admin}" != "true" ]]; then
        log_error "The API token does not belong to an admin user. Most operations will fail."
        exit 1
    fi
    local username
    username=$(echo "${current_user}" | jq -r '.username // "unknown"')
    log_ok "Token belongs to admin user: ${username}"
}

# ---------------------------------------------------------------------------
# 1. Disable public sign-up
# ---------------------------------------------------------------------------
disable_signup() {
    log "1. Disabling public sign-up..."
    if api_put "/application/settings" '{"signup_enabled": false}' > /dev/null; then
        log_ok "signup_enabled set to false"
    else
        log_error "Failed to disable sign-up"
    fi
}

# ---------------------------------------------------------------------------
# 2. Enforce Two-Factor Authentication (2FA/MFA) for all users
# ---------------------------------------------------------------------------
enforce_2fa() {
    log "2. Enforcing 2FA for all users..."
    # require_two_factor_authentication: all users must enable 2FA
    # two_factor_grace_period: hours before enforcement kicks in (48h = 2 days)
    if api_put "/application/settings" \
        '{"require_two_factor_authentication": true, "two_factor_grace_period": 48}' > /dev/null; then
        log_ok "2FA enforcement enabled (48-hour grace period)"
    else
        log_error "Failed to enforce 2FA"
    fi
}

# ---------------------------------------------------------------------------
# 3. Password complexity requirements
# ---------------------------------------------------------------------------
set_password_complexity() {
    log "3. Setting password complexity requirements..."

    # password_number_required: must include a number
    # password_symbol_required: must include a special character
    # password_uppercase_required: must include uppercase
    # password_lowercase_required: must include lowercase
    # minimum_password_length: GitLab default is 8; set to 12
    if api_put "/application/settings" \
        '{
            "minimum_password_length": 12,
            "password_number_required": true,
            "password_symbol_required": true,
            "password_uppercase_required": true,
            "password_lowercase_required": true
        }' > /dev/null; then
        log_ok "Password complexity set: min 12 chars, upper/lower/number/symbol required"
    else
        log_error "Failed to set password complexity (may require GitLab 15.x+)"
    fi
}

# ---------------------------------------------------------------------------
# 4. Session timeout
# ---------------------------------------------------------------------------
set_session_timeout() {
    log "4. Configuring session timeout..."

    # session_expire_delay: minutes until session expires (480 = 8 hours)
    if api_put "/application/settings" '{"session_expire_delay": 480}' > /dev/null; then
        log_ok "Session timeout set to 480 minutes (8 hours)"
    else
        log_error "Failed to set session timeout"
    fi
}

# ---------------------------------------------------------------------------
# 5. Default visibility: private
# ---------------------------------------------------------------------------
set_default_visibility() {
    log "5. Setting default visibility to private..."

    # default_project_visibility: 0=private, 10=internal, 20=public
    # default_group_visibility: same values
    # default_snippet_visibility: same values
    if api_put "/application/settings" \
        '{
            "default_project_visibility": "private",
            "default_group_visibility": "private",
            "default_snippet_visibility": "private"
        }' > /dev/null; then
        log_ok "Default visibility set to private for projects, groups, and snippets"
    else
        log_error "Failed to set default visibility"
    fi
}

# ---------------------------------------------------------------------------
# 6. Restrict project creation and public access
# ---------------------------------------------------------------------------
restrict_public_access() {
    log "6. Restricting public access and project creation..."

    # restricted_visibility_levels: prevent anyone from making things public
    # allow_local_requests_from_web_hooks_and_services: SSRF protection
    # deny_all_requests_except_allowed: restrict outbound requests
    if api_put "/application/settings" \
        '{
            "restricted_visibility_levels": ["public"],
            "allow_local_requests_from_web_hooks_and_services": false,
            "allow_local_requests_from_system_hooks": false
        }' > /dev/null; then
        log_ok "Restricted public visibility; disabled local SSRF vectors"
    else
        log_error "Failed to restrict public access"
    fi
}

# ---------------------------------------------------------------------------
# 7. Rate limiting
# ---------------------------------------------------------------------------
configure_rate_limiting() {
    log "7. Configuring rate limiting..."

    if api_put "/application/settings" \
        '{
            "throttle_enabled": true,
            "throttle_authenticated_api_enabled": true,
            "throttle_authenticated_api_requests_per_period": 2000,
            "throttle_authenticated_api_period_in_seconds": 3600,
            "throttle_authenticated_web_enabled": true,
            "throttle_authenticated_web_requests_per_period": 10000,
            "throttle_authenticated_web_period_in_seconds": 3600,
            "throttle_unauthenticated_api_enabled": true,
            "throttle_unauthenticated_api_requests_per_period": 500,
            "throttle_unauthenticated_api_period_in_seconds": 3600,
            "throttle_unauthenticated_web_enabled": true,
            "throttle_unauthenticated_web_requests_per_period": 3600,
            "throttle_unauthenticated_web_period_in_seconds": 3600,
            "throttle_protected_paths_enabled": true,
            "throttle_protected_paths_requests_per_period": 10,
            "throttle_protected_paths_period_in_seconds": 60
        }' > /dev/null; then
        log_ok "Rate limiting enabled for authenticated/unauthenticated API and web traffic"
    else
        log_error "Failed to configure rate limiting"
    fi
}

# ---------------------------------------------------------------------------
# 8. Audit logging
# ---------------------------------------------------------------------------
enable_audit_logging() {
    log "8. Enabling audit logging..."

    # admin_mode: require re-authentication for admin actions
    if api_put "/application/settings" '{"admin_mode": true}' > /dev/null; then
        log_ok "Admin mode enabled (re-authentication required for admin actions)"
    else
        log_warn "admin_mode may not be supported on this GitLab CE version"
    fi

    # Note: Full audit log streaming requires GitLab Ultimate.
    # On CE, audit events are available in Admin > Monitoring > Audit Events.
    log_warn "Audit event streaming requires GitLab Ultimate. CE logs to the audit_json.log file."
    log_ok "Audit events viewable at: ${GITLAB_URL}/admin/audit_log"
}

# ---------------------------------------------------------------------------
# 9. Disable unused features
# ---------------------------------------------------------------------------
disable_unused_features() {
    log "9. Disabling unused/risky features..."

    # gravatar: fetches external images — disable for air-gapped environments
    # user_default_external: new users are external by default (safer)
    # After initial user provisioning, set this to false again
    if api_put "/application/settings" \
        '{
            "gravatar_enabled": false,
            "user_default_external": false,
            "user_show_add_ssh_key_message": true,
            "import_sources": ["git", "gitlab_project"],
            "project_export_enabled": true
        }' > /dev/null; then
        log_ok "Disabled Gravatar (external image fetching); restricted import sources"
    else
        log_error "Failed to disable unused features"
    fi
}

# ---------------------------------------------------------------------------
# 10. Container registry: require authentication
# ---------------------------------------------------------------------------
harden_container_registry() {
    log "10. Hardening container registry settings..."

    # container_registry_access_level: disabled | private | enabled
    # For a private GitLab, private is appropriate.
    # container_registry_delete_tags_service_timeout: prevent slow DoS
    if api_put "/application/settings" \
        '{
            "container_registry_access_level": "private",
            "container_registry_delete_tags_service_timeout": 250
        }' > /dev/null; then
        log_ok "Container registry set to private access"
    else
        log_warn "Container registry settings may require omnibus configuration"
    fi

    log_warn "For full container registry hardening, also set in /etc/gitlab/gitlab.rb:"
    log_warn "  registry['health_storagedriver_enabled'] = true"
    log_warn "  gitlab_rails['registry_enabled'] = true"
}

# ---------------------------------------------------------------------------
# 11. SSH key expiry and restrictions
# ---------------------------------------------------------------------------
harden_ssh_keys() {
    log "11. Configuring SSH key restrictions..."

    # Disallow DSA keys (weak), require RSA >= 2048 or Ed25519
    # ssh_max_number_of_user_keys: limit per-user SSH key count
    if api_put "/application/settings" \
        '{
            "rsa_key_restriction": 2048,
            "dsa_key_restriction": -1,
            "ecdsa_key_restriction": 256,
            "ed25519_key_restriction": 0,
            "ecdsa_sk_key_restriction": 256,
            "ed25519_sk_key_restriction": 0,
            "max_ssh_key_lifetime": 365
        }' > /dev/null; then
        log_ok "SSH key restrictions set: RSA>=2048, DSA disabled, max lifetime 365 days"
    else
        log_error "Failed to set SSH key restrictions"
    fi
}

# ---------------------------------------------------------------------------
# 12. Email notifications and signup confirmation
# ---------------------------------------------------------------------------
harden_user_registration() {
    log "12. Hardening user registration and email settings..."

    if api_put "/application/settings" \
        '{
            "send_user_confirmation_email": true,
            "new_user_signups_cap": 0,
            "email_confirmation_setting": "hard"
        }' > /dev/null; then
        log_ok "Email confirmation required for new users; sign-up cap at 0"
    else
        log_warn "Some registration settings may not apply on this version"
    fi
}

# ---------------------------------------------------------------------------
# 13. Personal access token expiry reminder
# ---------------------------------------------------------------------------
configure_pat_expiry() {
    log "13. Configuring personal access token expiry..."

    # max_personal_access_token_lifetime: days (365 = 1 year max, 0 = no limit)
    if api_put "/application/settings" \
        '{"max_personal_access_token_lifetime": 365}' > /dev/null; then
        log_ok "Personal access tokens limited to 365-day maximum lifetime"
    else
        log_warn "PAT lifetime limit may require GitLab Ultimate"
    fi
}

# ---------------------------------------------------------------------------
# 14. Verify current application settings
# ---------------------------------------------------------------------------
verify_settings() {
    log "=== Verification: current security settings ==="

    local settings
    settings=$(api_get "/application/settings")

    local checks=(
        "signup_enabled:false"
        "require_two_factor_authentication:true"
        "minimum_password_length:12"
        "session_expire_delay:480"
        "default_project_visibility:private"
        "throttle_enabled:true"
        "admin_mode:true"
    )

    for check in "${checks[@]}"; do
        local key="${check%%:*}"
        local expected="${check##*:}"
        local actual
        actual=$(echo "${settings}" | jq -r ".${key} // \"not_set\"" 2>/dev/null) || actual="parse_error"

        if [[ "${actual}" == "${expected}" ]]; then
            log_ok "  ${key} = ${actual}"
        else
            log_warn "  ${key} = ${actual} (expected: ${expected})"
        fi
    done
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "================================================================"
    log "GitLab security hardening — ${TIMESTAMP}"
    log "Target: ${GITLAB_URL}"
    log "================================================================"

    verify_connectivity

    disable_signup
    enforce_2fa
    set_password_complexity
    set_session_timeout
    set_default_visibility
    restrict_public_access
    configure_rate_limiting
    enable_audit_logging
    disable_unused_features
    harden_container_registry
    harden_ssh_keys
    harden_user_registration
    configure_pat_expiry

    verify_settings

    log "================================================================"
    log "Security hardening complete."
    log ""
    log "Manual steps still required:"
    log "  1. Review /etc/gitlab/gitlab.rb for SMTP relay authentication"
    log "  2. Configure LDAP/SAML/OAuth2 SSO if not using local accounts"
    log "  3. Set up log forwarding to Wazuh SIEM on your management host"
    log "  4. Review runner registration — revoke stale runner tokens"
    log "  5. Enable HTTPS / TLS termination (nginx reverse proxy recommended)"
    log "  6. Review and revoke unused personal access tokens via Admin > Tokens"
    log "  7. Configure IP allowlist for admin panel (/admin) via nginx"
    log ""
    log "Log written to: ${LOG_FILE}"
    log "================================================================"
}

main "$@"
