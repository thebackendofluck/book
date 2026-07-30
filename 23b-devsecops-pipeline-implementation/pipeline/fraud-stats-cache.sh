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

# Cache fraud detection stats from OPS_HOST to local Redis
# Deploy to: /opt/scripts/fraud-stats-cache.sh on 203.0.113.1
# Crontab: * * * * * /opt/scripts/fraud-stats-cache.sh
#
# Fetches ES stats from OPS_HOST via SSH and stores in local Redis
# with 120s TTL so the dashboard reads from cache instead of proxying.

set -euo pipefail

OPS_HOST="admin@10.0.0.11"
LOCAL_REDIS="redis-cli -p 6381"
LOCK="/tmp/fraud-stats-cache.lock"

# Prevent overlapping runs
exec 9>"$LOCK"
flock -n 9 || { echo "Already running, skipping"; exit 0; }

fetch() {
    # shellcheck disable=SC2029
    ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o BatchMode=yes "$OPS_HOST" "$1" 2>/dev/null || echo ""
}

# Fetch from OPS_HOST (single SSH connection with multiplexed commands)
EVENTS=$(fetch "curl -s http://localhost:9200/_cat/count/casino-events-*?h=count" | tr -d ' ')
ALERTS=$(fetch "curl -s http://localhost:9200/_cat/count/fraud-alerts-*?h=count" | tr -d ' ')
ES_HEALTH=$(fetch "curl -s http://localhost:9200/_cluster/health")
ES_DOCS=$(fetch "curl -s 'http://localhost:9200/_cat/indices?h=docs.count&format=json'")

# Compute summary stats
TOTAL_DOCS=""
if [[ -n "$ES_DOCS" ]]; then
    TOTAL_DOCS=$(echo "$ES_DOCS" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print(sum(int(d.get('docs.count', 0)) for d in data))
except:
    print('')
" 2>/dev/null)
fi

# Store in Redis with TTL
$LOCAL_REDIS SET fraud:events "${EVENTS:-0}" EX 120 >/dev/null 2>&1
$LOCAL_REDIS SET fraud:alerts "${ALERTS:-0}" EX 120 >/dev/null 2>&1
$LOCAL_REDIS SET fraud:es_health "${ES_HEALTH:-{}}" EX 120 >/dev/null 2>&1
$LOCAL_REDIS SET fraud:es_docs "${ES_DOCS:-[]}" EX 120 >/dev/null 2>&1
[[ -n "$TOTAL_DOCS" ]] && $LOCAL_REDIS SET fraud:total_docs "$TOTAL_DOCS" EX 120 >/dev/null 2>&1
$LOCAL_REDIS SET fraud:last_updated "$(date -u +%Y-%m-%dT%H:%M:%SZ)" EX 120 >/dev/null 2>&1
