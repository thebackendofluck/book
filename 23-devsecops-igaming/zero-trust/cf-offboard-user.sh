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

# cf-offboard-user.sh
# Remove user from all Cloudflare Access groups and revoke sessions
# Usage: ./cf-offboard-user.sh <email> [--force]
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

CF_API_TOKEN="${CF_API_TOKEN:?Set CF_API_TOKEN}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:?Set CF_ACCOUNT_ID}"
API_BASE="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}"
EMAIL="${1:?Usage: $0 <email> [--force]}"
FORCE="${2:-}"
LOG_FILE="/var/log/cf-offboard.log"
AUDIT_FILE="/var/log/cf-audit-offboard.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }
audit() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] OFFBOARD: $*" | tee -a "$AUDIT_FILE"; }

cf_api() {
    local method="$1" path="$2" data="${3:-}"
    local args=(-sf -X "${method}" -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json")
    [[ -n "${data}" ]] && args+=(-d "${data}")
    curl "${args[@]}" "${API_BASE}${path}"
}

log "Starting offboarding for ${EMAIL}"
audit "Initiated offboarding for ${EMAIL}"

# ---------------------------------------------------------------------------
# 1. Find and remove user from all Access groups
# ---------------------------------------------------------------------------
GROUPS=$(cf_api GET "/access/groups" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for g in data.get('result', []):
    includes = g.get('include', [])
    for rule in includes:
        email_val = rule.get('email', {}).get('email', '')
        if email_val == '${EMAIL}':
            print(f\"{g['id']}|{g['name']}\")
            break
")

if [[ -z "${GROUPS}" ]]; then
    log "User ${EMAIL} not found in any Access groups"
else
    echo "User ${EMAIL} found in groups:"
    echo "${GROUPS}" | while IFS='|' read -r gid gname; do
        echo "  - ${gname} (${gid})"
    done

    if [[ "${FORCE}" != "--force" ]]; then
        read -rp "Remove from all groups and revoke access? (yes/no): " CONFIRM
        if [[ "${CONFIRM}" != "yes" ]]; then
            log "Offboarding cancelled"
            exit 0
        fi
    fi

    echo "${GROUPS}" | while IFS='|' read -r gid gname; do
        log "Removing ${EMAIL} from group: ${gname}"

        UPDATED=$(cf_api GET "/access/groups/${gid}" | python3 -c "
import json, sys
data = json.load(sys.stdin)['result']
includes = [r for r in data.get('include', []) if r.get('email', {}).get('email') != '${EMAIL}']
data['include'] = includes
print(json.dumps({'include': data['include'], 'name': data['name'], 'require': data.get('require', []), 'exclude': data.get('exclude', [])}))
")

        cf_api PUT "/access/groups/${gid}" "${UPDATED}" > /dev/null
        audit "REMOVED from group: ${gname} (${gid})"
    done
fi

# ---------------------------------------------------------------------------
# 2. Revoke active sessions
# ---------------------------------------------------------------------------
log "Revoking active sessions for ${EMAIL}..."

# List all Access applications and revoke user sessions on each
APPS=$(cf_api GET "/access/apps" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for app in data.get('result', []):
    print(f\"{app['id']}|{app['name']}\")
")

echo "${APPS}" | while IFS='|' read -r app_id app_name; do
    # Revoke user tokens for this application
    REVOKE_RESULT=$(cf_api POST "/access/apps/${app_id}/revoke_tokens" \
        "{\"email\": \"${EMAIL}\"}" 2>/dev/null || echo '{"success": false}')

    SUCCESS=$(echo "${REVOKE_RESULT}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('success', False))")
    if [[ "${SUCCESS}" == "True" ]]; then
        log "  Revoked sessions for ${app_name}"
        audit "REVOKED sessions: ${app_name} (${app_id})"
    fi
done

# ---------------------------------------------------------------------------
# 3. Revoke WARP device enrollment
# ---------------------------------------------------------------------------
log "Checking for WARP device enrollments..."
DEVICES=$(cf_api GET "/devices" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for d in data.get('result', []):
    if d.get('user', {}).get('email', '') == '${EMAIL}':
        print(f\"{d['id']}|{d.get('name', 'unknown')}|{d.get('last_seen', 'never')}\")
")

if [[ -n "${DEVICES}" ]]; then
    echo "${DEVICES}" | while IFS='|' read -r did dname dlast; do
        log "Revoking WARP device: ${dname} (last seen: ${dlast})"
        cf_api DELETE "/devices/${did}" > /dev/null 2>&1 || true
        audit "REVOKED device: ${dname} (${did}) last_seen=${dlast}"
    done
fi

audit "OFFBOARDING COMPLETE: ${EMAIL}"
log "Offboarding complete. Audit trail: ${AUDIT_FILE}"
