#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24m, Security Workflow Automation with n8n on Kubernetes.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# f2b-pfsense-unban.sh — remove an IP from the pfSense fail2ban alias.
# Inverse of f2b-pfsense-ban.sh: `.address - ["$IP"]` instead of `+`.
# See Chapter 24m 13.3.
set -euo pipefail
IP="${1:?usage: $0 <ip> <jail>}"; JAIL="${2:-unknown}"
LOG=/var/log/fail2ban/pfsense-integration.log
TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

# shellcheck source=/dev/null
source /etc/fail2ban/pfsense.conf            # PFSENSE_API_URL=https://10.0.10.1/api/v2
# shellcheck source=/dev/null
source /opt/vault-agent/fail2ban/pfsense.env # PFSENSE_API_KEY=<32 hex>

H_KEY="X-API-Key: ${PFSENSE_API_KEY}"
ALIAS=$(curl -ksS -H "$H_KEY" "${PFSENSE_API_URL}/firewall/aliases" \
  | jq -c ".data[] | select(.name==\"${PFSENSE_ALIAS}\")")
ID=$(echo "$ALIAS" | jq .id)
NEW=$(echo "$ALIAS" | jq -c ".address - [\"$IP\"] | unique")

PATCH=$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 8 -X PATCH \
  -H "$H_KEY" -H 'Content-Type: application/json' \
  -d "{\"id\":$ID,\"address\":$NEW}" "${PFSENSE_API_URL}/firewall/alias")

APPLY=$(curl -ksS -o /dev/null -w '%{http_code}' --max-time 10 -X POST \
  -H "$H_KEY" "${PFSENSE_API_URL}/firewall/apply")

echo "$TS [UNBAN] jail=$JAIL ip=$IP id=$ID patch=$PATCH apply=$APPLY" >>"$LOG"
[[ "$PATCH" == "200" && "$APPLY" == "200" ]] || exit 1
