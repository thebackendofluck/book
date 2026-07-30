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

# Poll local fraud system and push status JSON to production server
set -euo pipefail

ES="http://127.0.0.1:9200"
ALERTS="http://127.0.0.1:8083"
OUT="/tmp/fraud-status.json"
REMOTE="root@203.0.113.1"
REMOTE_PATH="/var/www/new.acmetocasino.com/fraud-status.json"

# ES cluster health
es_health=$(curl -sf "$ES/_cluster/health" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"status\",\"unknown\"))" 2>/dev/null || echo "detached")

# ES total doc count
es_docs=$(curl -sf "$ES/_cat/count?h=count" 2>/dev/null | tr -d " " || echo "0")

# Casino events count
events=$(curl -sf "$ES/casino-events-*/_count" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"count\",0))" 2>/dev/null || echo "0")

# Fraud alerts total
alerts=$(curl -sf "$ALERTS/api/v1/alerts/history" 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get(\"total\",0))" 2>/dev/null || echo "0")

# Timestamp
ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

cat > "$OUT" << JSONEOF
{"events_ingested":${events},"active_alerts":${alerts},"es_documents":${es_docs},"es_health":"${es_health}","source":"ops-host","last_updated":"${ts}"}
JSONEOF

scp -q -o ConnectTimeout=5 -o BatchMode=yes "$OUT" "$REMOTE:$REMOTE_PATH" 2>/dev/null
