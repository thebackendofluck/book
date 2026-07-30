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

# tailscale-offboard.sh
# Offboard an employee from the Tailscale network
# Usage: ./tailscale-offboard.sh <email> [--force]
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

TAILSCALE_API_KEY="${TAILSCALE_API_KEY:?Set TAILSCALE_API_KEY env var}"
TAILNET="${TAILSCALE_TAILNET:-acmetocasino.com}"
API_BASE="https://api.tailscale.com/api/v2"
EMAIL="${1:?Usage: $0 <email> [--force]}"
FORCE="${2:-}"
LOG_FILE="/var/log/tailscale-offboard.log"
AUDIT_FILE="/var/log/tailscale-audit-offboard.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }
audit() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] OFFBOARD: $*" | tee -a "$AUDIT_FILE"; }

log "Starting offboarding for ${EMAIL}"

# ---------------------------------------------------------------------------
# 1. Find all devices belonging to this user
# ---------------------------------------------------------------------------
log "Fetching device list..."
DEVICES=$(curl -sf "${API_BASE}/tailnet/${TAILNET}/devices" \
    -u "${TAILSCALE_API_KEY}:" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
devices = [d for d in data.get('devices', []) if d.get('user', '') == '${EMAIL}']
for d in devices:
    last_seen = d.get('lastSeen', 'never')
    print(f\"{d['id']}|{d['name']}|{d.get('hostname', 'unknown')}|{last_seen}\")
if not devices:
    print('NONE')
")

if [[ "${DEVICES}" == "NONE" ]]; then
    log "No devices found for ${EMAIL}"
    echo "WARNING: No Tailscale devices found for ${EMAIL}. User may have already been removed."
    exit 0
fi

echo "Devices found for ${EMAIL}:"
echo "---"
echo "${DEVICES}" | while IFS='|' read -r id name hostname last_seen; do
    echo "  Device: ${name} (${hostname})"
    echo "  Last seen: ${last_seen}"
    echo "  ID: ${id}"
    echo "---"
done

# ---------------------------------------------------------------------------
# 2. Confirm removal (unless --force)
# ---------------------------------------------------------------------------
if [[ "${FORCE}" != "--force" ]]; then
    read -rp "Remove all devices for ${EMAIL}? (yes/no): " CONFIRM
    if [[ "${CONFIRM}" != "yes" ]]; then
        log "Offboarding cancelled by operator"
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# 3. Audit last connections before removal
# ---------------------------------------------------------------------------
audit "User: ${EMAIL}"
audit "Devices at time of offboarding:"
echo "${DEVICES}" | while IFS='|' read -r id name hostname last_seen; do
    audit "  Device=${name} Hostname=${hostname} LastSeen=${last_seen} ID=${id}"
done

# ---------------------------------------------------------------------------
# 4. Delete all user devices
# ---------------------------------------------------------------------------
echo "${DEVICES}" | while IFS='|' read -r id name hostname last_seen; do
    log "Removing device: ${name} (${id})..."
    HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
        -X DELETE "${API_BASE}/device/${id}" \
        -u "${TAILSCALE_API_KEY}:")

    if [[ "${HTTP_CODE}" == "200" ]]; then
        log "  Removed: ${name}"
        audit "REMOVED: Device=${name} ID=${id}"
    else
        log "  WARNING: Failed to remove ${name} (HTTP ${HTTP_CODE})"
        audit "FAILED: Device=${name} ID=${id} HTTP=${HTTP_CODE}"
    fi
done

# ---------------------------------------------------------------------------
# 5. Revoke any active auth keys for this user
# ---------------------------------------------------------------------------
log "Revoking auth keys..."
KEYS=$(curl -sf "${API_BASE}/tailnet/${TAILNET}/keys" \
    -u "${TAILSCALE_API_KEY}:" \
    | python3 -c "
import json, sys
data = json.load(sys.stdin)
for k in data.get('keys', []):
    desc = k.get('description', '')
    if '${EMAIL}' in desc:
        print(k['id'])
")

if [[ -n "${KEYS}" ]]; then
    echo "${KEYS}" | while read -r key_id; do
        curl -sf -X DELETE "${API_BASE}/tailnet/${TAILNET}/keys/${key_id}" \
            -u "${TAILSCALE_API_KEY}:"
        log "Revoked auth key: ${key_id}"
        audit "REVOKED_KEY: ${key_id}"
    done
else
    log "No active auth keys found for ${EMAIL}"
fi

# ---------------------------------------------------------------------------
# 6. Slack notification
# ---------------------------------------------------------------------------
SLACK_WEBHOOK="${SLACK_INFRA_WEBHOOK:-}"
if [[ -n "${SLACK_WEBHOOK}" ]]; then
    DEVICE_COUNT=$(echo "${DEVICES}" | wc -l)
    curl -sf -X POST "${SLACK_WEBHOOK}" \
        -H "Content-Type: application/json" \
        -d "{
            \"text\": \"Tailscale offboarding complete: ${EMAIL}. ${DEVICE_COUNT} device(s) removed. Audit log: ${AUDIT_FILE}\"
        }"
fi

audit "OFFBOARDING COMPLETE: ${EMAIL}"
log "Offboarding complete. Audit trail: ${AUDIT_FILE}"
