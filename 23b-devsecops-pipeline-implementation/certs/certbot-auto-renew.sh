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

set -euo pipefail

NOTIFY_WEBHOOK="${SLACK_OPS_WEBHOOK:-}"
LOG_FILE="${LOG_FILE:-/var/log/certbot-renew.log}"
DOMAINS=("acmetocasino.com" "*.acmetocasino.com" "staging.acmetocasino.com")

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

notify() {
  local msg="$1"
  if [[ -n "$NOTIFY_WEBHOOK" ]]; then
    curl -s -X POST "$NOTIFY_WEBHOOK" \
      -H "Content-Type: application/json" \
      -d "{\"text\": \"[certbot] ${msg}\"}" >/dev/null || true
  fi
}

log "Starting certificate renewal check"
if certbot renew \
  --dns-njalla \
  --dns-njalla-credentials /etc/letsencrypt/njalla-credentials.ini \
  --deploy-hook "kubectl rollout restart deployment/traefik -n kube-system" \
  --post-hook "systemctl reload nginx || true" \
  --quiet; then
  log "Renewal completed successfully"
  notify "Certificate renewal completed"
else
  log "Certificate renewal command failed"
  notify "Certificate renewal command failed"
fi

for domain in "${DOMAINS[@]}"; do
  expiry="$(echo | openssl s_client -servername "$domain" -connect "${domain}:443" 2>/dev/null \
    | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2 || true)"
  [[ -z "$expiry" ]] && continue
  expiry_epoch="$(date -d "$expiry" +%s 2>/dev/null || date -jf "%b %d %T %Y %Z" "$expiry" +%s)"
  days_left=$(((expiry_epoch - $(date +%s)) / 86400))
  if [[ "$days_left" -lt 14 ]]; then
    notify "Certificate for ${domain} expires in ${days_left} days"
    log "WARNING: ${domain} expires in ${days_left} days"
  fi
done
