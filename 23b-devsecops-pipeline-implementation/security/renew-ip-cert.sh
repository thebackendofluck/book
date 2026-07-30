#!/bin/bash
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
#
# renew-ip-cert.sh — Auto-renewal for bare IP SSL certificates
#
# IP certificates (e.g., for load balancers or direct-IP access) have
# significantly shorter validity periods than domain certificates:
#   - Domain certs (Let's Encrypt): 90 days
#   - IP certs: typically 7-14 days depending on the CA
#
# This script runs twice daily via cron and force-renews when the cert
# has 3 or fewer days remaining (half the typical 7-day lifetime).
#
# Crontab entry:
#   0 3,15 * * * /opt/scripts/renew-ip-cert.sh
# ============================================================================

set -euo pipefail

# --- Configuration ---
CERT_NAME="YOUR_SERVER_IP"                      # e.g., "203.0.113.1"
CERTBOT="/opt/certbot-venv/bin/certbot"         # Path to certbot binary
RENEW_THRESHOLD=3                               # Days remaining to trigger renewal
LOG="/var/log/ip-cert-renew.log"
DEPLOY_HOOK="systemctl reload nginx"

# --- Paths ---
CERT_PATH="/etc/letsencrypt/live/${CERT_NAME}/fullchain.pem"

# --- Functions ---
log() { echo "$(date '+%Y-%m-%d %H:%M:%S') [$1] $2" >> "$LOG"; }

# --- Pre-checks ---
if [ ! -f "$CERT_PATH" ]; then
    log "ERROR" "Certificate file not found: $CERT_PATH"
    exit 1
fi

if [ ! -x "$CERTBOT" ]; then
    log "ERROR" "Certbot not found or not executable: $CERTBOT"
    exit 1
fi

# --- Check expiry ---
EXPIRY=$(openssl x509 -in "$CERT_PATH" -noout -enddate 2>/dev/null | cut -d= -f2)
EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s 2>/dev/null || echo 0)
NOW_EPOCH=$(date +%s)
DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))

log "INFO" "IP cert ${CERT_NAME}: ${DAYS_LEFT} days remaining (expires: ${EXPIRY})"

# --- Renew if needed ---
if [ "$DAYS_LEFT" -le "$RENEW_THRESHOLD" ]; then
    log "RENEW" "Forcing renewal (${DAYS_LEFT} days left, threshold: ${RENEW_THRESHOLD})"

    if $CERTBOT renew \
        --cert-name "$CERT_NAME" \
        --force-renewal \
        --non-interactive \
        --deploy-hook "$DEPLOY_HOOK" >> "$LOG" 2>&1; then

        # Verify renewal
        NEW_EXPIRY=$(openssl x509 -in "$CERT_PATH" -noout -enddate 2>/dev/null | cut -d= -f2)
        NEW_DAYS=$(( ($(date -d "$NEW_EXPIRY" +%s) - NOW_EPOCH) / 86400 ))
        log "OK" "Renewed successfully — new expiry: ${NEW_EXPIRY} (${NEW_DAYS} days)"
    else
        log "ERROR" "Renewal failed — check certbot logs"
        exit 1
    fi
else
    log "SKIP" "${DAYS_LEFT} days remaining — no renewal needed"
fi

# --- Rotate log ---
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    mv "$LOG" "${LOG}.old"
fi
