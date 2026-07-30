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

# vpn-usage-tracker.sh
# Monitor legacy VPN connections during migration
# Run daily via cron during Phase 3 of Zero Trust migration
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

VPN_LOG="${VPN_LOG:-/var/log/openvpn/status.log}"
OUTPUT="/var/log/vpn-migration-audit.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$OUTPUT"; }

if [[ ! -f "${VPN_LOG}" ]]; then
    log "No VPN log found at ${VPN_LOG} -- VPN may already be decommissioned"
    exit 0
fi

# Count active VPN sessions
ACTIVE=$(grep -c "^CLIENT_LIST" "${VPN_LOG}" 2>/dev/null || echo "0")
log "Active VPN sessions: ${ACTIVE}"

# Extract connected users
if [[ "${ACTIVE}" -gt 0 ]]; then
    log "Connected users (still using legacy VPN):"
    grep "^CLIENT_LIST" "${VPN_LOG}" | while IFS=',' read -r _ user real_ip vpn_ip _ _ _ _; do
        log "  User: ${user} | Real IP: ${real_ip} | VPN IP: ${vpn_ip}"
    done

    # Alert if anyone is still on legacy VPN after week 5
    SLACK_WEBHOOK="${SLACK_INFRA_WEBHOOK:-}"
    if [[ -n "${SLACK_WEBHOOK}" ]]; then
        curl -sf -X POST "${SLACK_WEBHOOK}" \
            -H "Content-Type: application/json" \
            -d "{\"text\": \"VPN Migration: ${ACTIVE} user(s) still connected via legacy VPN. Contact them to migrate.\"}"
    fi
else
    log "No active VPN sessions -- migration may be complete"
fi
