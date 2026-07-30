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

# cf-onboard-user.sh
# Add a user to Cloudflare Access groups and generate WARP enrollment
# Usage: ./cf-onboard-user.sh <email> <team>
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

CF_API_TOKEN="${CF_API_TOKEN:?Set CF_API_TOKEN}"
CF_ACCOUNT_ID="${CF_ACCOUNT_ID:?Set CF_ACCOUNT_ID}"
API_BASE="https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}"
EMAIL="${1:?Usage: $0 <email> <team>}"
TEAM="${2:?Usage: $0 <email> <team>}"
LOG_FILE="/var/log/cf-onboard.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }

cf_api() {
    local method="$1" path="$2" data="${3:-}"
    local args=(-sf -X "${method}" -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json")
    [[ -n "${data}" ]] && args+=(-d "${data}")
    curl "${args[@]}" "${API_BASE}${path}"
}

log "Onboarding ${EMAIL} to team ${TEAM}"

# Map team to Access groups
declare -A TEAM_GROUPS=(
    [engineering]="engineering,monitoring-access"
    [operations]="operations,monitoring-access,backoffice-access"
    [dba]="dba-team,monitoring-access"
    [support]="backoffice-access"
    [compliance]="backoffice-access,monitoring-access"
)

GROUPS="${TEAM_GROUPS[${TEAM}]:?Invalid team: ${TEAM}}"

# Add user to each Access group
IFS=',' read -ra GROUP_ARRAY <<< "${GROUPS}"
for group_name in "${GROUP_ARRAY[@]}"; do
    log "Adding ${EMAIL} to group: ${group_name}"

    # Find group ID
    GROUP_ID=$(cf_api GET "/access/groups" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for g in data.get('result', []):
    if g['name'] == '${group_name}':
        print(g['id'])
        break
")

    if [[ -z "${GROUP_ID}" ]]; then
        log "  WARNING: Group '${group_name}' not found, skipping"
        continue
    fi

    # Get current group config and add user email to include rules
    CURRENT=$(cf_api GET "/access/groups/${GROUP_ID}")
    UPDATED=$(echo "${CURRENT}" | python3 -c "
import json, sys
data = json.load(sys.stdin)['result']
includes = data.get('include', [])
# Add email if not already present
email_exists = any(
    r.get('email', {}).get('email') == '${EMAIL}'
    for r in includes
)
if not email_exists:
    includes.append({'email': {'email': '${EMAIL}'}})
data['include'] = includes
print(json.dumps({'include': data['include'], 'name': data['name'], 'require': data.get('require', []), 'exclude': data.get('exclude', [])}))
")

    cf_api PUT "/access/groups/${GROUP_ID}" "${UPDATED}" > /dev/null
    log "  Added to ${group_name}"
done

# Generate WARP enrollment instructions
cat <<ENROLL

==============================================================
WARP Client Enrollment for ${EMAIL}
==============================================================

STEP 1: Install Cloudflare WARP
  macOS:   https://developers.cloudflare.com/cloudflare-one/connections/connect-devices/warp/download-warp/
  Windows: https://developers.cloudflare.com/cloudflare-one/connections/connect-devices/warp/download-warp/
  Linux:   curl https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
           sudo apt install cloudflare-warp

STEP 2: Enroll in the AcmeToCasino team
  Open WARP > Preferences > Account > Login to Cloudflare Zero Trust
  Enter team name: acmetocasino

STEP 3: Authenticate
  You will be redirected to your IdP (Google/Okta).
  Enter your credentials and touch your YubiKey when prompted.

STEP 4: Verify
  Visit https://admin.${CF_DOMAIN:-acmetocasino.com} to confirm access.

Your access level (${TEAM}):
  Groups: ${GROUPS}
==============================================================

ENROLL

log "Onboarding complete for ${EMAIL}"
