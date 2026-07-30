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

# web-scan-acunetix.sh
# Acunetix web vulnerability scanner with OWASP Top 10 mapping
# Triggers scans against iGaming web targets and reports findings with severity.
#
# Usage: ./web-scan-acunetix.sh [--group <player-facing|backoffice|api>]
#
# Environment variables:
#   ACUNETIX_URL     - Acunetix server URL (e.g. https://acunetix.acmetocasino.com:3443)
#   ACUNETIX_API_KEY - Acunetix API key
#
# Chapter 23 — DevSecOps for iGaming

set -euo pipefail

ACUNETIX_URL="${ACUNETIX_URL:?Set ACUNETIX_URL}"
ACUNETIX_API_KEY="${ACUNETIX_API_KEY:?Set ACUNETIX_API_KEY}"
TARGET_GROUP="${1:-all}"
REPORT_DIR="${REPORT_DIR:-/var/log/acunetix-reports}"
LOG_FILE="/var/log/acunetix-scan.log"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" | tee -a "$LOG_FILE"; }

mkdir -p "${REPORT_DIR}"

# ---------------------------------------------------------------------------
# iGaming target groups
# ---------------------------------------------------------------------------
declare -A TARGET_GROUPS=(
    [player-facing]="https://acmetocasino.com https://casino.acmetocasino.com https://cashier.acmetocasino.com https://register.acmetocasino.com"
    [backoffice]="https://admin.acmetocasino.com https://crm.acmetocasino.com https://reporting.acmetocasino.com"
    [api]="https://api.acmetocasino.com https://ws.acmetocasino.com"
)

# ---------------------------------------------------------------------------
# Acunetix API helper
# ---------------------------------------------------------------------------
acx_api() {
    local method="$1"
    local path="$2"
    local data="${3:-}"
    local args=(
        -sf
        -X "${method}"
        -H "X-Auth: ${ACUNETIX_API_KEY}"
        -H "Content-Type: application/json"
        --insecure
    )
    [[ -n "${data}" ]] && args+=(-d "${data}")
    curl "${args[@]}" "${ACUNETIX_URL}/api/v1${path}"
}

# ---------------------------------------------------------------------------
# 1. Determine targets to scan
# ---------------------------------------------------------------------------
if [[ "${TARGET_GROUP}" == "all" ]]; then
    TARGETS="${TARGET_GROUPS[player-facing]} ${TARGET_GROUPS[backoffice]} ${TARGET_GROUPS[api]}"
elif [[ -n "${TARGET_GROUPS[${TARGET_GROUP}]+set}" ]]; then
    TARGETS="${TARGET_GROUPS[${TARGET_GROUP}]}"
else
    log "ERROR: Unknown group '${TARGET_GROUP}'. Valid: player-facing, backoffice, api, all"
    exit 1
fi

log "Starting Acunetix scan for group: ${TARGET_GROUP}"

# ---------------------------------------------------------------------------
# 2. Get or create targets in Acunetix
# ---------------------------------------------------------------------------
declare -a SCAN_IDS=()

for target_url in ${TARGETS}; do
    log "Processing target: ${target_url}"

    # Check if target already exists
    TARGET_ID=$(acx_api GET "/targets" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for t in data.get('targets', []):
    if t.get('address') == '${target_url}':
        print(t['target_id'])
        break
" 2>/dev/null || true)

    if [[ -z "${TARGET_ID}" ]]; then
        # Create new target
        TARGET_RESP=$(acx_api POST "/targets" "{
            \"address\": \"${target_url}\",
            \"description\": \"iGaming target - ${TARGET_GROUP}\",
            \"type\": \"default\"
        }")
        TARGET_ID=$(echo "${TARGET_RESP}" | python3 -c "import json,sys; print(json.load(sys.stdin)['target_id'])")
        log "  Created target: ${TARGET_ID}"
    else
        log "  Using existing target: ${TARGET_ID}"
    fi

    # Launch scan with Full Scan + OWASP Top 10 profile
    PROFILE_ID=$(acx_api GET "/scanning_profiles" | python3 -c "
import json, sys
data = json.load(sys.stdin)
# Prefer 'Full Scan' or fall back to first available
for p in data.get('scanning_profiles', []):
    if 'full' in p.get('name', '').lower():
        print(p['profile_id'])
        break
else:
    profiles = data.get('scanning_profiles', [])
    if profiles:
        print(profiles[0]['profile_id'])
" 2>/dev/null || echo "")

    if [[ -z "${PROFILE_ID}" ]]; then
        log "  WARNING: No scanning profile found for ${target_url}, skipping"
        continue
    fi

    SCAN_RESP=$(acx_api POST "/scans" "{
        \"target_id\": \"${TARGET_ID}\",
        \"profile_id\": \"${PROFILE_ID}\",
        \"schedule\": {
            \"disable\": false,
            \"start_date\": null,
            \"time_sensitive\": false
        }
    }")

    SCAN_ID=$(echo "${SCAN_RESP}" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scan_id', ''))" 2>/dev/null || true)
    if [[ -n "${SCAN_ID}" ]]; then
        SCAN_IDS+=("${SCAN_ID}")
        log "  Scan launched: ${SCAN_ID}"
    fi
done

if [[ ${#SCAN_IDS[@]} -eq 0 ]]; then
    log "ERROR: No scans were launched"
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Wait for all scans to complete
# ---------------------------------------------------------------------------
log "Waiting for ${#SCAN_IDS[@]} scan(s) to complete..."
MAX_WAIT=21600  # 6 hours
ELAPSED=0

while true; do
    all_done=true
    for scan_id in "${SCAN_IDS[@]}"; do
        STATUS=$(acx_api GET "/scans/${scan_id}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
print(data.get('current_session', {}).get('status', 'unknown'))
" 2>/dev/null || echo "unknown")

        if [[ "${STATUS}" != "completed" && "${STATUS}" != "failed" && "${STATUS}" != "aborted" ]]; then
            all_done=false
        fi
    done

    if [[ "${all_done}" == "true" ]]; then
        log "All scans completed (${ELAPSED}s elapsed)"
        break
    elif [[ "${ELAPSED}" -ge "${MAX_WAIT}" ]]; then
        log "WARNING: Scan timeout after ${MAX_WAIT}s -- reporting partial results"
        break
    fi

    sleep 120
    ELAPSED=$((ELAPSED + 120))
    log "Still scanning... (${ELAPSED}s elapsed)"
done

# ---------------------------------------------------------------------------
# 4. Collect and summarize vulnerabilities
# ---------------------------------------------------------------------------
log "Collecting vulnerability data..."
SUMMARY_FILE="${REPORT_DIR}/acunetix-summary-$(date -u +%Y%m%d-%H%M).json"

python3 - "${ACUNETIX_URL}" "${ACUNETIX_API_KEY}" "${SCAN_IDS[@]}" > "${SUMMARY_FILE}" <<'SUMMARIZE'
import json
import ssl
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

acx_url = sys.argv[1]
api_key = sys.argv[2]
scan_ids = sys.argv[3:]

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

owasp_map = {
    "SQL Injection": "A03:2021",
    "Cross-site scripting": "A03:2021",
    "Sensitive data exposure": "A02:2021",
    "Broken authentication": "A07:2021",
    "Security misconfiguration": "A05:2021",
    "Insecure direct object references": "A01:2021",
}

findings = {"critical": [], "high": [], "medium": [], "low": [], "informational": []}
severity_map = {4: "critical", 3: "high", 2: "medium", 1: "low", 0: "informational"}

for scan_id in scan_ids:
    url = f"{acx_url}/api/v1/vulnerabilities?q=scan_id:{scan_id};status:open&l=100"
    req = Request(url, headers={"X-Auth": api_key})
    try:
        with urlopen(req, context=ctx) as resp:
            data = json.loads(resp.read().decode())
            for vuln in data.get("vulnerabilities", []):
                sev = severity_map.get(vuln.get("severity", 0), "informational")
                findings[sev].append({
                    "target": vuln.get("target_id", ""),
                    "type": vuln.get("vt_name", ""),
                    "url": vuln.get("affects_url", ""),
                    "cvss": vuln.get("cvss3_score", ""),
                    "owasp": owasp_map.get(vuln.get("vt_name", ""), ""),
                    "description": vuln.get("description", "")[:200],
                })
    except HTTPError as e:
        print(f"Error fetching vulnerabilities for scan {scan_id}: {e}", file=sys.stderr)

print(json.dumps(findings, indent=2))
SUMMARIZE

# ---------------------------------------------------------------------------
# 5. Print human-readable summary
# ---------------------------------------------------------------------------
python3 - "${SUMMARY_FILE}" <<'PRINT_SUMMARY'
import json
import sys

with open(sys.argv[1]) as f:
    findings = json.load(f)

sla = {"critical": "24 hours", "high": "7 days", "medium": "30 days", "low": "90 days"}

print("\n=== ACUNETIX WEB SCAN SUMMARY (OWASP TOP 10) ===")
for severity in ["critical", "high", "medium", "low"]:
    items = findings.get(severity, [])
    if items:
        print(f"\n{severity.upper()} ({len(items)}) — Remediation SLA: {sla.get(severity, 'N/A')}")
        for f in items[:3]:
            owasp = f"[{f['owasp']}]" if f.get('owasp') else ""
            print(f"  {owasp} {f['type']}")
            print(f"    URL: {f['url'][:80]}")
            print(f"    CVSS: {f['cvss']}")
        if len(items) > 3:
            print(f"  ... and {len(items) - 3} more")

total = sum(len(v) for v in findings.values())
print(f"\nTotal open vulnerabilities: {total}")
PRINT_SUMMARY

log "Scan complete. Summary: ${SUMMARY_FILE}"
