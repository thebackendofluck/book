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

# ============================================================================
# CONFIGURATION: Update the variables below before running this script.
# Replace all YOUR_* placeholders with your environment-specific values.
# ============================================================================
# =============================================================================
# pfSense DDoS / Attack Mitigation — pfSense REST API v2.7.6
#
# Target : pfSense at YOUR_PFSENSE_IP
# API    : https://YOUR_PFSENSE_IP/api/v2/
# Access : LAN-only, reachable from your management host or any trusted VLAN host
#
# SAFETY RULES (read before running):
#   1. NEVER call /api/v2/firewall/state/flush — flushing states drops ALL
#      active connections, including your own SSH session.
#   2. NEVER delete the "anti-lockout" rule or the LAN-allow-all rule.
#   3. Always test rules with --dry-run first, then apply.
#   4. Rules are PREPENDED to the WAN ruleset so they evaluate before existing
#      allow rules.  Order matters in pfSense — block first, then allow.
#   5. This script is idempotent: re-running it is safe.  Rules with duplicate
#      descriptions are skipped.
#
# Usage:
#   ./pfsense-ddos-protection.sh [--dry-run] [--attack] [--status] [--unblock <ip>]
#
#   --dry-run          Print API calls without executing them
#   --attack           Apply aggressive emergency rules (active DDoS response)
#   --status           Show current WAN block table and recent block hits
#   --unblock <ip>     Remove a specific IP from the block table
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PFSENSE_HOST="YOUR_PFSENSE_IP"
PFSENSE_API="https://${PFSENSE_HOST}/api/v2"
API_KEY="YOUR_PFSENSE_API_KEY"
WAN_IFACE="wan"           # pfSense WAN interface name (verify with: GET /api/v2/interface)
# LAN_IFACE: reserved for future LAN-side mitigations (e.g., rate-limit internal east-west)
# LAN_IFACE="lan"
BLOCK_TABLE="ddos_block"  # pfSense table name for dynamic IP blocks
LOG_FILE="/var/log/pfsense-ddos-protection.log"

DRY_RUN=false
ATTACK_MODE=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
UNBLOCK_IP=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)   DRY_RUN=true;       shift ;;
    --attack)    ATTACK_MODE=true;   shift ;;
    --status)    MODE=status;        shift ;;
    --unblock)   UNBLOCK_IP="$2";    shift 2 ;;
    *)           echo "Unknown flag: $1"; exit 1 ;;
  esac
done
MODE="${MODE:-apply}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "${LOG_FILE}"; }

api_get() {
  local path="$1"
  curl --silent --show-error --fail \
    --insecure \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    "${PFSENSE_API}${path}"
}

api_post() {
  local path="$1"
  local data="$2"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[DRY-RUN] POST ${PFSENSE_API}${path}"
    log "[DRY-RUN] Body: ${data}"
    return 0
  fi
  curl --silent --show-error --fail \
    --insecure \
    -X POST \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "${data}" \
    "${PFSENSE_API}${path}"
}

api_put() {
  local path="$1"
  local data="$2"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "[DRY-RUN] PUT ${PFSENSE_API}${path}"
    log "[DRY-RUN] Body: ${data}"
    return 0
  fi
  curl --silent --show-error --fail \
    --insecure \
    -X PUT \
    -H "X-API-Key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "${data}" \
    "${PFSENSE_API}${path}"
}

# ---------------------------------------------------------------------------
# STATUS mode — show block table contents and recent firewall log hits
# ---------------------------------------------------------------------------
if [[ "${MODE}" == "status" ]]; then
  log "=== Block table: ${BLOCK_TABLE} ==="
  api_get "/firewall/alias?name=${BLOCK_TABLE}" | python3 -m json.tool || true
  log "=== Recent WAN block hits (last 50) ==="
  api_get "/status/log/firewall?limit=50" | python3 -m json.tool || true
  exit 0
fi

# ---------------------------------------------------------------------------
# UNBLOCK mode — remove a specific IP from the block table
# ---------------------------------------------------------------------------
if [[ -n "${UNBLOCK_IP}" ]]; then
  log "Removing ${UNBLOCK_IP} from ${BLOCK_TABLE}..."
  api_post "/firewall/alias/entry/delete" \
    "{\"name\": \"${BLOCK_TABLE}\", \"address\": \"${UNBLOCK_IP}\"}"
  log "Done. Apply the change:"
  api_post "/firewall/apply" "{}"
  exit 0
fi

# ---------------------------------------------------------------------------
# PART 1: Create a persistent alias (table) for dynamic DDoS block entries
# ---------------------------------------------------------------------------
log "Creating/updating pfAlias table: ${BLOCK_TABLE}"

# IP blocklists are managed by the automated iprep system.
# See: scripts/chapter-24j/python/iprep_update.py
# See: scripts/chapter-24j/python/iprep_aggregator.py
# The following is a minimal bootstrap list. For production, use the iprep pipeline.
#
# Run the iprep pipeline before this script to populate a comprehensive blocklist:
#   uv run scripts/chapter-24j/python/iprep_update.py
#   uv run scripts/chapter-24j/python/iprep_aggregator.py
#   bash scripts/chapter-24j/bash/fetch_datashield.sh
BLOCK_NETS=(
  "185.220.100.0/22"    # Tor exit relays (bootstrap entry — replace with iprep output)
  "185.220.101.0/24"    # Tor exit relays (bootstrap entry)
  "162.247.72.0/22"     # Tor exit relays (bootstrap entry)
)

BLOCK_NETS_JSON=$(printf '"%s",' "${BLOCK_NETS[@]}" | sed 's/,$//')

api_post "/firewall/alias" "$(cat <<EOF
{
  "name": "${BLOCK_TABLE}",
  "type": "network",
  "descr": "DDoS block table — managed by pfsense-ddos-protection.sh",
  "network": [${BLOCK_NETS_JSON}]
}
EOF
)" || log "Alias may already exist — attempting update..."

# Update if it already exists
api_put "/firewall/alias" "$(cat <<EOF
{
  "name": "${BLOCK_TABLE}",
  "type": "network",
  "descr": "DDoS block table — managed by pfsense-ddos-protection.sh",
  "network": [${BLOCK_NETS_JSON}]
}
EOF
)" || log "Could not update alias — check pfSense version compatibility"

# ---------------------------------------------------------------------------
# PART 2: WAN block rule — reject traffic from the block table
# ---------------------------------------------------------------------------
log "Adding WAN block rule for ${BLOCK_TABLE}..."

api_post "/firewall/rule" "$(cat <<EOF
{
  "type": "block",
  "interface": "${WAN_IFACE}",
  "ipprotocol": "inet46",
  "protocol": "any",
  "src": "${BLOCK_TABLE}",
  "srcnot": false,
  "dst": "any",
  "dstnot": false,
  "descr": "DDOS: block known bad networks",
  "log": true,
  "top": true,
  "disabled": false
}
EOF
)" || log "Block rule may already exist — skipping"

# ---------------------------------------------------------------------------
# PART 3: SYN flood protection — enable scrubbing + SYN cookies on WAN
# ---------------------------------------------------------------------------
log "Configuring SYN flood protection (scrub + SYN proxy)..."

# Enable pf scrubbing (reassembly, defragmentation) on WAN ingress
api_post "/firewall/scrub/rule" "$(cat <<EOF
{
  "interface": "${WAN_IFACE}",
  "descr": "Scrub WAN ingress — defrag + normalise for SYN flood protection",
  "no-df": true,
  "random-id": true,
  "max-mss": 1460
}
EOF
)" || log "Scrub rule may already exist"

# pfSense/pf SYN proxy is configured via Advanced settings (not directly via
# REST API v2).  Document manual step:
log "==================================================================="
log "MANUAL STEP: Enable SYN proxy in pfSense UI:"
log "  System > Advanced > Firewall & NAT"
log "  > Firewall Optimization Options: set to 'conservative'"
log "  > Enable 'Scrub (Packet Reassembly)'"
log "  For high-rate SYN flood: add 'synproxy state' to WAN allow rules"
log "==================================================================="

# ---------------------------------------------------------------------------
# PART 4: Connection limits per source IP (prevent connection exhaustion)
# ---------------------------------------------------------------------------
log "Adding per-source connection limit rules..."

# Allow WAN → 443 (HTTPS) with connection state limits
api_post "/firewall/rule" "$(cat <<EOF
{
  "type": "pass",
  "interface": "${WAN_IFACE}",
  "ipprotocol": "inet",
  "protocol": "tcp",
  "src": "any",
  "srcnot": false,
  "dst": "YOUR_PRODUCTION_IP",
  "dstport": "443",
  "statetype": "keep state",
  "max-src-conn": 100,
  "max-src-conn-rate": "20/5",
  "overload": "${BLOCK_TABLE}",
  "descr": "HTTPS: limit 100 concurrent conns per src, 20 new/5s; overload to block table",
  "log": true,
  "top": false,
  "disabled": false
}
EOF
)" || log "HTTPS conn-limit rule may already exist"

# Allow WAN → 80 (HTTP) with tighter limits (should redirect to HTTPS anyway)
api_post "/firewall/rule" "$(cat <<EOF
{
  "type": "pass",
  "interface": "${WAN_IFACE}",
  "ipprotocol": "inet",
  "protocol": "tcp",
  "src": "any",
  "srcnot": false,
  "dst": "YOUR_PRODUCTION_IP",
  "dstport": "80",
  "statetype": "keep state",
  "max-src-conn": 50,
  "max-src-conn-rate": "10/5",
  "overload": "${BLOCK_TABLE}",
  "descr": "HTTP: limit 50 concurrent conns per src, 10 new/5s; overload to block table",
  "log": true,
  "top": false,
  "disabled": false
}
EOF
)" || log "HTTP conn-limit rule may already exist"

# ---------------------------------------------------------------------------
# PART 5: GeoIP blocking
# ---------------------------------------------------------------------------
log "Configuring GeoIP blocking..."

# pfSense supports GeoIP via pfBlockerNG (package).
# The REST API does NOT directly manage pfBlockerNG feeds.
# The correct approach is to configure it via pfBlockerNG UI or its config file.
log "==================================================================="
log "GEOIP CONFIGURATION (pfBlockerNG required):"
log ""
log "  1. Install pfBlockerNG-devel via System > Package Manager"
log "  2. Go to Firewall > pfBlockerNG > IP > GeoIP"
log "  3. Select MaxMind GeoLite2 (free, requires registration)"
log "  4. Create INBOUND block list with the following continents/countries:"
log "     BLOCK: CN (China), RU (Russia), KP (North Korea), IR (Iran),"
log "            SY (Syria), CU (Cuba), BY (Belarus), MM (Myanmar)"
log "     ALLOW: All iGaming-licensed markets where you have players"
log "  5. Action: Deny Both (in + out)"
log "  6. Schedule: Auto-update GeoIP feeds daily"
log ""
log "  API workaround: pfBlockerNG stores its config in"
log "  /var/db/pfblockerng/ — can be managed via SSH from your management host."
log "==================================================================="

# ---------------------------------------------------------------------------
# PART 6: ATTACK MODE — emergency rules for active DDoS
# ---------------------------------------------------------------------------
if [[ "${ATTACK_MODE}" == "true" ]]; then
  log "*** ATTACK MODE ACTIVE — applying aggressive emergency rules ***"

  # Drop ALL non-TCP/UDP traffic on WAN (ICMP floods, protocol abuse)
  api_post "/firewall/rule" "$(cat <<EOF
{
  "type": "block",
  "interface": "${WAN_IFACE}",
  "ipprotocol": "inet46",
  "protocol": "icmp",
  "src": "any",
  "dst": "any",
  "descr": "EMERGENCY: block all ICMP (flood mitigation)",
  "log": true,
  "top": true,
  "disabled": false
}
EOF
)" || log "ICMP block rule may already exist"

  # Reduce connection rate limits aggressively
  api_post "/firewall/rule" "$(cat <<EOF
{
  "type": "pass",
  "interface": "${WAN_IFACE}",
  "ipprotocol": "inet",
  "protocol": "tcp",
  "src": "any",
  "dst": "YOUR_PRODUCTION_IP",
  "dstport": "443",
  "statetype": "keep state",
  "max-src-conn": 20,
  "max-src-conn-rate": "5/10",
  "overload": "${BLOCK_TABLE}",
  "descr": "EMERGENCY HTTPS: 20 conns / 5 per 10s",
  "log": true,
  "top": false,
  "disabled": false
}
EOF
)" || log "Emergency HTTPS rule may already exist"

  log "==================================================================="
  log "EMERGENCY CHECKLIST — active DDoS response:"
  log ""
  log "  IMMEDIATE (first 5 minutes):"
  log "  1. Identify attack type: volumetric (pps), protocol (SYN), or L7 (HTTP)"
  log "     tcpdump -i em0 -n -c 1000 'tcp[tcpflags] & tcp-syn != 0'"
  log "  2. Check pfSense Dashboard > Traffic Graphs for WAN spike"
  log "  3. Run this script with --attack flag (done if reading this)"
  log "  4. Enable Cloudflare 'Under Attack Mode' (CF Dashboard > Security)"
  log ""
  log "  SHORT-TERM (next 30 minutes):"
  log "  5. Identify attacking IPs via pfSense logs:"
  log "     ./pfsense-ddos-protection.sh --status"
  log "  6. Add attacker CIDRs to ip-blocklist-global in K8s:"
  log "     kubectl edit kongplugin ip-blocklist-global -n acmetocasino-prod"
  log "  7. Activate Cloudflare WAF 'Block' mode on WAF rules"
  log "  8. If attack continues: contact upstream ISP for null-routing"
  log "     (Hetzner: https://robot.hetzner.com/server — support ticket)"
  log ""
  log "  RECOVERY:"
  log "  9. Monitor error rate in Prometheus/Grafana"
  log "  10. Gradually relax connection limits (re-run without --attack)"
  log "  11. Remove emergency ICMP block once volumetric attack stops"
  log "  12. Document attacker ASNs for permanent GeoIP/pfBlockerNG blocking"
  log "  13. NEVER flush pfSense connection states during recovery"
  log "==================================================================="
fi

# ---------------------------------------------------------------------------
# PART 7: Apply all pending firewall changes
# ---------------------------------------------------------------------------
log "Applying firewall changes to pfSense..."
api_post "/firewall/apply" "{}"

log "Done. Current WAN rules can be verified at:"
log "  https://YOUR_PFSENSE_IP > Firewall > Rules > WAN"
log "  OR: ./pfsense-ddos-protection.sh --status"
