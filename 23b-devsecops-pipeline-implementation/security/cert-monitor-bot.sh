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

# Certificate monitoring bot
# Checks all certs, alerts if any expire within 14 days
# Runs via cron every 6 hours

LOG="/var/log/cert-monitor.log"
DASHBOARD_WEBHOOK="http://10.0.0.11:3080/api/webhooks/certs"

ALERT=false
CERTS_JSON="["

for CONF in /etc/letsencrypt/renewal/*.conf; do
    CERT_NAME=$(basename "$CONF" .conf)
    CERT_PATH="/etc/letsencrypt/live/${CERT_NAME}/fullchain.pem"
    [ ! -f "$CERT_PATH" ] && continue
    
    EXPIRY=$(openssl x509 -in "$CERT_PATH" -noout -enddate 2>/dev/null | cut -d= -f2)
    EXPIRY_EPOCH=$(date -d "$EXPIRY" +%s 2>/dev/null)
    NOW_EPOCH=$(date +%s)
    DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
    
    # Auto-renew if within 30 days
    if [ "$DAYS_LEFT" -le 30 ]; then
        echo "$(date) [RENEW] $CERT_NAME expires in $DAYS_LEFT days — attempting renewal" >> "$LOG"
        /opt/certbot-venv/bin/certbot renew --cert-name "$CERT_NAME" --non-interactive --deploy-hook 'systemctl reload nginx' >> "$LOG" 2>&1
        
        # Re-check after renewal
        NEW_EXPIRY=$(openssl x509 -in "$CERT_PATH" -noout -enddate 2>/dev/null | cut -d= -f2)
        NEW_DAYS=$(( ($(date -d "$NEW_EXPIRY" +%s) - NOW_EPOCH) / 86400 ))
        DAYS_LEFT=$NEW_DAYS
    fi
    
    # Alert if still within 14 days
    if [ "$DAYS_LEFT" -le 14 ]; then
        ALERT=true
        echo "$(date) [ALERT] $CERT_NAME expires in $DAYS_LEFT days!" >> "$LOG"
    fi
    
    STATUS="ok"
    [ "$DAYS_LEFT" -le 30 ] && STATUS="warning"
    [ "$DAYS_LEFT" -le 7 ] && STATUS="critical"
    
    CERTS_JSON="${CERTS_JSON}{\"domain\":\"${CERT_NAME}\",\"days_left\":${DAYS_LEFT},\"expiry\":\"${EXPIRY}\",\"status\":\"${STATUS}\"},"
done

CERTS_JSON="${CERTS_JSON%,}]"

# Update SSL status JSON for dashboard
echo "$CERTS_JSON" > /var/www/new.acmetocasino.com/ssl-status.json

# Post to dashboard webhook
curl -s -X POST "$DASHBOARD_WEBHOOK" \
    -H "Content-Type: application/json" \
    -d "{\"type\":\"cert_status\",\"certs\":${CERTS_JSON},\"timestamp\":\"$(date -Iseconds)\"}" \
    --connect-timeout 5 >/dev/null 2>&1 || true

echo "$(date) [OK] Cert check complete" >> "$LOG"
