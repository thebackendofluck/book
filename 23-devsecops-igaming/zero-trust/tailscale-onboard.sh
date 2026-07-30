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

# tailscale-onboard.sh
# Onboard a new employee to the Tailscale network
# Usage: ./tailscale-onboard.sh <email> <team> [--notify]
# Teams: engineering, operations, dba, support, compliance, executives
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

TAILSCALE_API_KEY="${TAILSCALE_API_KEY:?Set TAILSCALE_API_KEY env var}"
TAILNET="${TAILSCALE_TAILNET:-acmetocasino.com}"
API_BASE="https://api.tailscale.com/api/v2"
EMAIL="${1:?Usage: $0 <email> <team> [--notify]}"
TEAM="${2:?Usage: $0 <email> <team> [--notify]}"
NOTIFY="${3:-}"
LOG_FILE="/var/log/tailscale-onboard.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }

# Validate team
VALID_TEAMS=("engineering" "operations" "dba" "support" "compliance" "executives")
if [[ ! " ${VALID_TEAMS[*]} " =~ [[:space:]]${TEAM}[[:space:]] ]]; then
    echo "ERROR: Invalid team '${TEAM}'. Valid: ${VALID_TEAMS[*]}" >&2
    exit 1
fi

log "Onboarding ${EMAIL} to team ${TEAM}"

# ---------------------------------------------------------------------------
# 1. Create a pre-authorized auth key with appropriate tags
# ---------------------------------------------------------------------------
TAGS="[]"
EXPIRY_SECS=7776000  # 90 days (PCI DSS compliant)
case "${TEAM}" in
    engineering) TAGS='["tag:staging"]' ;;
    operations)  TAGS='["tag:production", "tag:staging"]' ;;
    dba)         TAGS='["tag:database"]' ;;
    support)     TAGS='["tag:backoffice"]' ;;
    compliance)  TAGS='["tag:backoffice", "tag:monitoring"]' ;;
    executives)  TAGS='["tag:backoffice"]' ;;
esac

AUTH_KEY_RESPONSE=$(curl -sf -X POST "${API_BASE}/tailnet/${TAILNET}/keys" \
    -u "${TAILSCALE_API_KEY}:" \
    -H "Content-Type: application/json" \
    -d "{
        \"capabilities\": {
            \"devices\": {
                \"create\": {
                    \"reusable\": false,
                    \"ephemeral\": false,
                    \"preauthorized\": true,
                    \"tags\": ${TAGS}
                }
            }
        },
        \"expirySeconds\": ${EXPIRY_SECS},
        \"description\": \"Onboarding key for ${EMAIL} (${TEAM})\"
    }")

AUTH_KEY=$(echo "${AUTH_KEY_RESPONSE}" | python3 -c "import json,sys; print(json.load(sys.stdin)['key'])")
KEY_ID=$(echo "${AUTH_KEY_RESPONSE}" | python3 -c "import json,sys; print(json.load(sys.stdin)['id'])")

log "Auth key created: ${KEY_ID} (expires in 90 days)"

# ---------------------------------------------------------------------------
# 2. Generate onboarding instructions
# ---------------------------------------------------------------------------
INSTRUCTIONS=$(cat <<ONBOARD
=============================================================
Welcome to AcmeToCasino Tailscale Network
=============================================================

Hi! You've been added to the ${TEAM} team.

STEP 1: Install Tailscale
  macOS:   brew install tailscale
  Linux:   curl -fsSL https://tailscale.com/install.sh | sh
  Windows: https://tailscale.com/download/windows

STEP 2: Authenticate
  tailscale up --auth-key=${AUTH_KEY}

STEP 3: Register your YubiKey
  When prompted by Keycloak, insert your YubiKey and touch
  the sensor. This registers the key for future logins.

STEP 4: Verify connectivity
  tailscale status
  tailscale ping <any-server-hostname>

Your access level (${TEAM}):
$(case "${TEAM}" in
    engineering) echo "  - Staging servers (full access)"
                 echo "  - Monitoring dashboards (Grafana, Prometheus)"
                 echo "  - API servers (HTTP only)" ;;
    operations)  echo "  - All servers (full access)"
                 echo "  - SSH to all non-database servers"
                 echo "  - Database SSH requires approval" ;;
    dba)         echo "  - Database servers (PostgreSQL port + SSH)"
                 echo "  - Monitoring dashboards" ;;
    support)     echo "  - Backoffice web interface (HTTPS only)" ;;
    compliance)  echo "  - Backoffice web interface (HTTPS only)"
                 echo "  - Monitoring dashboards (read-only)" ;;
    executives)  echo "  - Backoffice dashboards (HTTPS only)" ;;
esac)

This auth key expires in 90 days. You'll be prompted to
re-authenticate using your YubiKey.

Need help? Ping #infra-support on Slack.
=============================================================
ONBOARD
)

echo "${INSTRUCTIONS}"

# ---------------------------------------------------------------------------
# 3. Send notification if requested
# ---------------------------------------------------------------------------
if [[ "${NOTIFY}" == "--notify" ]]; then
    SLACK_WEBHOOK="${SLACK_INFRA_WEBHOOK:-}"
    if [[ -n "${SLACK_WEBHOOK}" ]]; then
        log "Sending Slack notification..."
        curl -sf -X POST "${SLACK_WEBHOOK}" \
            -H "Content-Type: application/json" \
            -d "{
                \"text\": \"New Tailscale onboarding: ${EMAIL} added to ${TEAM} team. Key ID: ${KEY_ID}\"
            }"
    fi

    # Log to audit trail
    log "AUDIT: Onboarded ${EMAIL} to ${TEAM}, key=${KEY_ID}"
fi

log "Onboarding complete for ${EMAIL}"
