#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 27b, The Jurisdiction Transfer Gateway and Cookie Consent.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# JGW heartbeat — pushes gateway status + summary of all rules to casino dashboard.
#
# Deployed to /opt/scripts/ via rsync; mtime-pinning recommended.
# Source-of-truth lives in the book repo; do not edit /opt/scripts/ in place.
set -euo pipefail

# /etc/new-platform/jgw.env contains: JGW_INTERNAL_TOKEN=<value> (mode 0600 root)
ENV_FILE="${ENV_FILE:-/etc/new-platform/jgw.env}"
[[ -r "$ENV_FILE" ]] || { echo "missing $ENV_FILE"; exit 2; }
# shellcheck source=/dev/null
source "$ENV_FILE"
: "${JGW_INTERNAL_TOKEN:?JGW_INTERNAL_TOKEN must be in $ENV_FILE}"

JGW_URL="${JGW_URL_LOCAL:-http://127.0.0.1:8210}"
DASHBOARD_URL="${JGW_URL:-https://new.acmetocasino.com/api/v2/dash/jgw/heartbeat}"

ready=$(curl -sS --max-time 3 "$JGW_URL/v1/readyz" 2>/dev/null || echo '{"status":"unreachable"}')
expiring=$(curl -sS --max-time 3 "$JGW_URL/v1/rules/expiring?within_days=90" 2>/dev/null || echo '{"count":0,"rules":[]}')
rules=$(curl -sS --max-time 3 "$JGW_URL/v1/rules" 2>/dev/null || echo '{"count":0,"rules":[]}')

payload=$(python3 -c '
import json, sys
ready = json.loads(sys.argv[1])
exp   = json.loads(sys.argv[2])
rules = json.loads(sys.argv[3])
summary = []
for r in (rules.get("rules") or []):
    summary.append({
        "from":    r.get("from_jurisdiction"),
        "to":      r.get("to_destination"),
        "class":   r.get("data_class"),
        "mech":    r.get("mechanism"),
        "allowed": r.get("allowed", True),
        "tia":     bool(r.get("tia_required", False)),
        "expires": r.get("expires_at"),
        "citation": (r.get("citation") or "")[:160],
    })
print(json.dumps({
  "status":          ready.get("status", "unknown"),
  "rules_count":     ready.get("rules_count", 0),
  "rules_sha256":    ready.get("rules_sha256", ""),
  "rules_loaded_at": ready.get("rules_loaded_at", ""),
  "expiring_count":  exp.get("count", 0),
  "expiring":        (exp.get("rules") or [])[:10],
  "all_rules":       summary,
  "backend":         "k3s://compliance/jurisdiction-gateway",
}))
' "$ready" "$expiring" "$rules")

curl -sS --max-time 10 \
  -H "Authorization: Bearer ${JGW_INTERNAL_TOKEN}" \
  -H "Content-Type: application/json" \
  -X POST "$DASHBOARD_URL" \
  -d "$payload" \
  > /tmp/jgw-heartbeat.out

echo "$payload" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(f"heartbeat ok: {d[\"status\"]} / {d[\"rules_count\"]} rules / {d[\"expiring_count\"]} expiring")'
