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

# tailscale-rotate-keys.sh
# Rotate auth keys for all tagged servers
# Usage: ./tailscale-rotate-keys.sh [--dry-run]
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

TAILSCALE_API_KEY="${TAILSCALE_API_KEY:?Set TAILSCALE_API_KEY env var}"
TAILNET="${TAILSCALE_TAILNET:-acmetocasino.com}"
API_BASE="https://api.tailscale.com/api/v2"
DRY_RUN="${1:-}"
LOG_FILE="/var/log/tailscale-key-rotation.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }

log "Starting auth key rotation for ${TAILNET}"

# Fetch all active (non-revoked) keys
KEYS_JSON=$(curl -sf "${API_BASE}/tailnet/${TAILNET}/keys" \
    -u "${TAILSCALE_API_KEY}:")

# Parse keys that are older than 60 days or expiring within 30 days
KEYS_TO_ROTATE=$(echo "${KEYS_JSON}" | python3 -c "
import json, sys
from datetime import datetime, timedelta, timezone

data = json.load(sys.stdin)
now = datetime.now(timezone.utc)
rotate_threshold = now + timedelta(days=30)

for key in data.get('keys', []):
    if key.get('revoked', False):
        continue
    expires = key.get('expires', '')
    if not expires:
        continue
    exp_dt = datetime.fromisoformat(expires.replace('Z', '+00:00'))
    if exp_dt < rotate_threshold:
        print(f\"{key['id']}|{key.get('description', 'no description')}|{expires}\")
")

if [[ -z "${KEYS_TO_ROTATE}" ]]; then
    log "No keys require rotation at this time"
    exit 0
fi

log "Keys requiring rotation:"
echo "${KEYS_TO_ROTATE}" | while IFS='|' read -r kid desc expires; do
    log "  Key ${kid}: ${desc} (expires: ${expires})"
done

if [[ "${DRY_RUN}" == "--dry-run" ]]; then
    log "DRY RUN: No changes made"
    exit 0
fi

# For each key, create a replacement and revoke the old one
echo "${KEYS_TO_ROTATE}" | while IFS='|' read -r kid desc expires; do
    log "Rotating key ${kid}..."

    # Create replacement key with same capabilities
    NEW_KEY_RESP=$(curl -sf -X POST "${API_BASE}/tailnet/${TAILNET}/keys" \
        -u "${TAILSCALE_API_KEY}:" \
        -H "Content-Type: application/json" \
        -d "{
            \"capabilities\": {
                \"devices\": {
                    \"create\": {
                        \"reusable\": true,
                        \"ephemeral\": false,
                        \"preauthorized\": true,
                        \"tags\": [\"tag:production\"]
                    }
                }
            },
            \"expirySeconds\": 7776000,
            \"description\": \"Rotated from ${kid}: ${desc}\"
        }")

    NEW_KID=$(echo "${NEW_KEY_RESP}" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")
    log "  New key created: ${NEW_KID}"

    # Revoke old key
    curl -sf -X DELETE "${API_BASE}/tailnet/${TAILNET}/keys/${kid}" \
        -u "${TAILSCALE_API_KEY}:"
    log "  Old key revoked: ${kid}"
done

log "Key rotation complete"
