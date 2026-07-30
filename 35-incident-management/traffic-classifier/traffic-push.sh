#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# traffic-push.sh — nginx traffic metrics collector
#
# Parses /var/log/nginx/access.log for the last 5 minutes, computes:
#   - req/s (last 60s, last 5m)
#   - unique IPs in last 5m
#   - HTTP error rate (4xx+5xx / total)
#   - top 5 paths, IPs, user agents
#   - bot score (known bot UAs / total requests)
#   - UA diversity score
#
# Pushes result as JSON to Redis key traffic:status (TTL 60s).
# Also appends a snapshot to traffic:history list (max 120 entries, 1 hour).
#
# Deploy on: root@203.0.113.1 (production nginx + Redis server)
#
# Cron setup (runs every 30 s via two staggered entries):
#   * * * * *        /opt/new-platform/scripts/traffic-push.sh >> /var/log/traffic-push.log 2>&1
#   * * * * * sleep 30 && /opt/new-platform/scripts/traffic-push.sh >> /var/log/traffic-push.log 2>&1
#
# Requirements: gawk, sort, date (GNU coreutils), docker (for redis-cli)

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# shellcheck disable=SC2034  # vars below are referenced inside awk -v blocks
# ---------------------------------------------------------------------------
NGINX_LOG="/var/log/nginx/access.log"
REDIS_CONTAINER="new-casino-redis"
REDIS_PORT=6379
REDIS_KEY="traffic:status"
REDIS_HISTORY_KEY="traffic:history"
REDIS_CAMPAIGN_KEY="traffic:campaign"
REDIS_OVERRIDE_KEY="traffic:override"
REDIS_TTL=60
REDIS_HISTORY_TTL=3700
HISTORY_MAX=120

LOCK_FILE="/tmp/traffic-push.lock"
TMP_LOG="/tmp/traffic-push-entries.log"
TMP_PAYLOAD="/tmp/traffic-push-payload.json"

# ---------------------------------------------------------------------------
# Lock: prevent overlapping runs
# ---------------------------------------------------------------------------
if [ -f "$LOCK_FILE" ]; then
    LOCK_PID=$(cat "$LOCK_FILE" 2>/dev/null || echo "")
    if [ -n "$LOCK_PID" ] && kill -0 "$LOCK_PID" 2>/dev/null; then
        exit 0
    fi
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE" "$TMP_LOG" "$TMP_PAYLOAD"' EXIT

# ---------------------------------------------------------------------------
# Verify nginx log is readable
# ---------------------------------------------------------------------------
if [ ! -r "$NGINX_LOG" ]; then
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] ERROR: Cannot read $NGINX_LOG" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Helper: redis-cli via Docker
# ---------------------------------------------------------------------------
redis_cmd() {
    docker exec "$REDIS_CONTAINER" redis-cli -p "$REDIS_PORT" "$@" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Extract last 5 minutes of log entries
# ---------------------------------------------------------------------------
NOW_EPOCH=$(date -u +%s)
# CUTOFF_300 is passed into awk via -v; CUTOFF_60 is computed inside awk
CUTOFF_300=$((NOW_EPOCH - 300))

# Strategy: tail the last 20 000 lines (covers ~hours of traffic at moderate rps),
# then filter by timestamp inside awk.
#
# Awk parses nginx combined log, converts the timestamp to Unix epoch via
# gawk mktime(), and prints: epoch ip status path ua
#
# Note: split on double-quote delimiter; take the last non-empty token as the UA.
# This avoids the trailing dollar-slash pattern that confuses shellcheck SC1089.

tail -n 20000 "$NGINX_LOG" 2>/dev/null \
| awk -v cutoff="$CUTOFF_300" '
BEGIN {
    m["Jan"]=1; m["Feb"]=2; m["Mar"]=3; m["Apr"]=4
    m["May"]=5; m["Jun"]=6; m["Jul"]=7; m["Aug"]=8
    m["Sep"]=9; m["Oct"]=10; m["Nov"]=11; m["Dec"]=12
}
{
    # Parse timestamp: [DD/Mon/YYYY:HH:MM:SS +TZTZ]
    if (!match($0, /\[([0-9]+)\/([A-Za-z]+)\/([0-9]+):([0-9]+):([0-9]+):([0-9]+) ([+-][0-9]+)\]/, arr))
        next

    day  = arr[1]+0; mon = m[arr[2]]; yr  = arr[3]+0
    hr   = arr[4]+0; mn  = arr[5]+0;  sc2 = arr[6]+0
    tz   = arr[7]
    tzh  = substr(tz,2,2)+0; tzm = substr(tz,4,2)+0
    tzsec = (substr(tz,1,1) == "+") ? -(tzh*3600+tzm*60) : (tzh*3600+tzm*60)
    ts = mktime(sprintf("%d %d %d %d %d %d", yr, mon, day, hr, mn, sc2)) + tzsec

    if (ts < cutoff) next

    ip     = $1
    status = $9

    # Extract path from the request field (e.g. "GET /path HTTP/1.1")
    if (match($0, /"[A-Z]+ ([^ ?\"]+)/, preq))
        path = preq[1]
    else
        path = "/"

    # Extract UA: split on double-quote, last non-empty segment is the UA
    n = split($0, parts, "\"")
    ua = "-"
    for (i = n; i >= 1; i--) {
        if (parts[i] != "" && parts[i] != " ") {
            ua = parts[i]
            break
        }
    }

    print ts, ip, status, path, ua
}
' > "$TMP_LOG" 2>/dev/null || true

TOTAL=$(wc -l < "$TMP_LOG" 2>/dev/null || echo 0)
TOTAL="${TOTAL//[[:space:]]/}"

# ---------------------------------------------------------------------------
# Empty window — push quiet status and exit
# ---------------------------------------------------------------------------
if [ "$TOTAL" -eq 0 ]; then
    NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    printf '{"status":"NORMAL","confidence":95.0,"metrics":{"computed_at":"%s","window_seconds":300,"total_requests":0,"rps":{"current":0.0,"avg_5m":0.0},"unique_ips_5m":0,"ua_diversity_score":100.0,"bot_score":0.0,"error_rate":0.0,"status_counts":{},"top_paths":[],"top_ips":[],"top_uas":[],"top_countries":[],"top_asns":[]},"campaign":{},"override":null,"generated_at":"%s","source":"push_script"}\n' \
        "$NOW_ISO" "$NOW_ISO" > "$TMP_PAYLOAD"
    redis_cmd SET "$REDIS_KEY" "$(cat "$TMP_PAYLOAD")" EX "$REDIS_TTL" > /dev/null 2>&1 || true
    exit 0
fi

# ---------------------------------------------------------------------------
# Compute per-field metrics via awk (single pass over TMP_LOG)
# Fields: epoch ip status path ua...
# ---------------------------------------------------------------------------
METRICS_JSON=$(awk -v now="$NOW_EPOCH" -v cutoff60="$((NOW_EPOCH - 60))" '
BEGIN {
    total=0; recent_60=0
    err_4xx=0; err_5xx=0; ua_count=0; ua_unique=0; bot_count=0
    span=300
}
{
    ts=$1; ip=$2; status=$3; path=$4
    ua=""
    for (i=5; i<=NF; i++) ua = (ua=="" ? $i : ua " " $i)

    total++
    if (ts+0 >= cutoff60) recent_60++

    ip_seen[ip]=1
    ip_cnt5m[ip]++

    s = status+0
    if (s>=400 && s<500) err_4xx++
    else if (s>=500)     err_5xx++

    path_cnt[path]++

    ua_count++
    if (!(ua in ua_cnt)) { ua_unique++; ua_cnt[ua]=0 }
    ua_cnt[ua]++

    # Bot UA heuristic
    if (ua ~ /[Bb]ot|[Cc]rawl|[Ss]pider|wget|curl|[Pp]ython|[Gg]o-http|[Jj]ava\/|[Aa]xios|libwww|[Ss]crapy|[Hh]eadless/)
        bot_count++
}
END {
    rps_current = (recent_60 > 0) ? recent_60/60.0 : 0
    rps_5m      = total / span
    unique_ips  = length(ip_seen)
    total_errors= err_4xx + err_5xx
    error_rate  = (total>0) ? (total_errors/total)*100 : 0
    ua_div      = (ua_count>0) ? (ua_unique/ua_count)*100 : 100
    bot_score   = (ua_count>0) ? (bot_count/ua_count)*100 : 0

    printf "RPS_CURRENT=%.2f\n", rps_current
    printf "RPS_5M=%.2f\n", rps_5m
    printf "UNIQUE_IPS=%d\n", unique_ips
    printf "ERROR_RATE=%.2f\n", error_rate
    printf "UA_DIV=%.1f\n", ua_div
    printf "BOT_SCORE=%.1f\n", bot_score
    printf "TOTAL=%d\n", total
    printf "ERR_4XX=%d\n", err_4xx
    printf "ERR_5XX=%d\n", err_5xx

    # Emit raw count-TAB-label lines for each category, delimited by sentinel
    for (p in path_cnt)  printf "P\t%d\t%s\n", path_cnt[p], p
    for (i in ip_cnt5m)  printf "I\t%d\t%s\n", ip_cnt5m[i], i
    for (u in ua_cnt)    printf "U\t%d\t%s\n", ua_cnt[u],   substr(u,1,80)
}
' "$TMP_LOG")

# Parse scalar values
RPS_CURRENT=$(printf '%s' "$METRICS_JSON" | awk -F= '/^RPS_CURRENT=/{print $2}')
RPS_5M=$(     printf '%s' "$METRICS_JSON" | awk -F= '/^RPS_5M=/{print $2}')
UNIQUE_IPS=$( printf '%s' "$METRICS_JSON" | awk -F= '/^UNIQUE_IPS=/{print $2}')
ERROR_RATE=$( printf '%s' "$METRICS_JSON" | awk -F= '/^ERROR_RATE=/{print $2}')
UA_DIV=$(     printf '%s' "$METRICS_JSON" | awk -F= '/^UA_DIV=/{print $2}')
BOT_SCORE=$(  printf '%s' "$METRICS_JSON" | awk -F= '/^BOT_SCORE=/{print $2}')
TOTAL_REQ=$(  printf '%s' "$METRICS_JSON" | awk -F= '/^TOTAL=/{print $2}')
ERR_4XX=$(    printf '%s' "$METRICS_JSON" | awk -F= '/^ERR_4XX=/{print $2}')
ERR_5XX=$(    printf '%s' "$METRICS_JSON" | awk -F= '/^ERR_5XX=/{print $2}')

RPS_CURRENT=${RPS_CURRENT:-0.00}
RPS_5M=${RPS_5M:-0.00}
UNIQUE_IPS=${UNIQUE_IPS:-0}
ERROR_RATE=${ERROR_RATE:-0.00}
UA_DIV=${UA_DIV:-100.0}
BOT_SCORE=${BOT_SCORE:-0.0}
TOTAL_REQ=${TOTAL_REQ:-0}
ERR_4XX=${ERR_4XX:-0}
ERR_5XX=${ERR_5XX:-0}

# ---------------------------------------------------------------------------
# Build JSON arrays for top paths / IPs / UAs
# Input: lines tagged P / I / U with format: TAG \t count \t label
# ---------------------------------------------------------------------------
build_top_json() {
    local tag="$1"   # P, I, or U
    local field="$2" # JSON key name
    local limit=5
    local json="["
    local first=1
    local n=0

    while IFS=$'\t' read -r cnt label; do
        [ -z "$cnt" ] && continue
        [ "$n" -ge "$limit" ] && break
        label_esc=$(printf '%s' "$label" | sed 's/\\/\\\\/g; s/"/\\"/g')
        if [ "$first" -eq 1 ]; then
            json="${json}{\"${field}\":\"${label_esc}\",\"count\":${cnt}}"
            first=0
        else
            json="${json},{\"${field}\":\"${label_esc}\",\"count\":${cnt}}"
        fi
        n=$((n + 1))
    done < <(
        printf '%s' "$METRICS_JSON" \
            | awk -v t="$tag" -F'\t' '$1==t{print $2"\t"$3}' \
            | sort -rn \
            | head -n "$limit"
    )
    printf '%s]' "$json"
}

TOP_PATHS_JSON=$(build_top_json "P" "path")
TOP_IPS_JSON=$(  build_top_json "I" "ip")
TOP_UAS_JSON=$(  build_top_json "U" "ua")

# ---------------------------------------------------------------------------
# Read campaign + override state from Redis
# ---------------------------------------------------------------------------
CAMPAIGN_RAW=$(redis_cmd GET "$REDIS_CAMPAIGN_KEY" 2>/dev/null || echo "")
CAMPAIGN_ACTIVE="false"
CAMPAIGN_DATA="{}"
if [ -n "$CAMPAIGN_RAW" ]; then
    CAMPAIGN_DATA="$CAMPAIGN_RAW"
    if printf '%s' "$CAMPAIGN_RAW" | grep -q '"active":true'; then
        CAMPAIGN_ACTIVE="true"
    fi
fi

OVERRIDE_RAW=$(redis_cmd GET "$REDIS_OVERRIDE_KEY" 2>/dev/null || echo "")
OVERRIDE_STATUS=""
if [ -n "$OVERRIDE_RAW" ]; then
    OVERRIDE_STATUS=$(printf '%s' "$OVERRIDE_RAW" | grep -oP '"status"\s*:\s*"\K[^"]+' 2>/dev/null || echo "")
fi

# ---------------------------------------------------------------------------
# Traffic classification
# ---------------------------------------------------------------------------
if [ -n "$OVERRIDE_STATUS" ]; then
    STATUS="$OVERRIDE_STATUS"
    CONFIDENCE=100.0
else
    read -r STATUS CONFIDENCE <<< "$(awk \
        -v rps="$RPS_CURRENT" \
        -v bot="$BOT_SCORE" \
        -v ua_div="$UA_DIV" \
        -v err="$ERROR_RATE" \
        -v campaign="$CAMPAIGN_ACTIVE" \
        'BEGIN {
            status     = "NORMAL"
            confidence = 95.0

            if (rps+0 >= 200 || (bot+0 >= 60 && rps+0 > 20) || (ua_div+0 < 5 && rps+0 > 20)) {
                status = "ATTACK"
                score  = (rps+0 >= 200) ? (rps+0)/200 : ((bot+0 >= 60) ? (bot+0)/60 : 2)
                confidence = (score > 1) ? 95 : score*70+25
                if (confidence > 98) confidence = 98
            } else if (campaign == "true" && rps+0 >= 10) {
                status     = "CAMPAIGN"
                confidence = 85.0
            } else if (rps+0 >= 50 || err+0 >= 15 || ua_div+0 < 10) {
                status     = "ELEVATED"
                confidence = 75.0
            }

            printf "%s %.1f\n", status, confidence
        }')"
fi

# ---------------------------------------------------------------------------
# Build and push JSON payload
# ---------------------------------------------------------------------------
NOW_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)

STATUS_COUNTS_JSON=$(printf '{"2xx":%d,"4xx":%d,"5xx":%d}' \
    "$((TOTAL_REQ - ERR_4XX - ERR_5XX))" "$ERR_4XX" "$ERR_5XX")

# Use printf to file to avoid ARG_MAX issues
printf '{
  "status": "%s",
  "confidence": %s,
  "campaign": %s,
  "override": %s,
  "generated_at": "%s",
  "source": "push_script",
  "metrics": {
    "computed_at": "%s",
    "window_seconds": 300,
    "total_requests": %s,
    "rps": { "current": %s, "avg_5m": %s },
    "unique_ips_5m": %s,
    "ua_diversity_score": %s,
    "bot_score": %s,
    "error_rate": %s,
    "status_counts": %s,
    "top_paths": %s,
    "top_ips":   %s,
    "top_uas":   %s,
    "top_countries": [],
    "top_asns":      []
  }
}\n' \
    "$STATUS" \
    "$CONFIDENCE" \
    "$CAMPAIGN_DATA" \
    "$([ -n "$OVERRIDE_STATUS" ] && printf '"%s"' "$OVERRIDE_STATUS" || echo 'null')" \
    "$NOW_ISO" \
    "$NOW_ISO" \
    "$TOTAL_REQ" \
    "$RPS_CURRENT" \
    "$RPS_5M" \
    "$UNIQUE_IPS" \
    "$UA_DIV" \
    "$BOT_SCORE" \
    "$ERROR_RATE" \
    "$STATUS_COUNTS_JSON" \
    "$TOP_PATHS_JSON" \
    "$TOP_IPS_JSON" \
    "$TOP_UAS_JSON" \
    > "$TMP_PAYLOAD"

redis_cmd SET "$REDIS_KEY" "$(cat "$TMP_PAYLOAD")" EX "$REDIS_TTL" > /dev/null

# ---------------------------------------------------------------------------
# Append snapshot to history list for sparklines
# Executed as a pipeline to the redis-cli --pipe mode (3 commands, 1 connection)
# ---------------------------------------------------------------------------
SNAPSHOT=$(printf '{"ts":%d,"status":"%s","rps":%s,"unique_ips_5m":%s,"error_rate":%s,"bot_score":%s,"ua_diversity_score":%s}' \
    "$NOW_EPOCH" "$STATUS" "$RPS_CURRENT" "$UNIQUE_IPS" "$ERROR_RATE" "$BOT_SCORE" "$UA_DIV")

# The printf format strings below contain Redis RESP protocol bytes ($5, $4, $1)
# which are literal dollar signs, not shell variables. SC2016 is intentional.
# shellcheck disable=SC2016
{
    printf '*3\r\n$5\r\nLPUSH\r\n$%d\r\n%s\r\n$%d\r\n%s\r\n' \
        "${#REDIS_HISTORY_KEY}" "$REDIS_HISTORY_KEY" \
        "${#SNAPSHOT}" "$SNAPSHOT"
    # shellcheck disable=SC2016
    printf '*4\r\n$5\r\nLTRIM\r\n$%d\r\n%s\r\n$1\r\n0\r\n$%d\r\n%d\r\n' \
        "${#REDIS_HISTORY_KEY}" "$REDIS_HISTORY_KEY" \
        "${#HISTORY_MAX}" "$((HISTORY_MAX - 1))"
    # shellcheck disable=SC2016
    printf '*3\r\n$6\r\nEXPIRE\r\n$%d\r\n%s\r\n$4\r\n%d\r\n' \
        "${#REDIS_HISTORY_KEY}" "$REDIS_HISTORY_KEY" \
        "$REDIS_HISTORY_TTL"
} | docker exec -i "$REDIS_CONTAINER" redis-cli -p "$REDIS_PORT" --pipe > /dev/null 2>&1 || true

echo "[${NOW_ISO}] status=${STATUS} confidence=${CONFIDENCE} rps=${RPS_CURRENT} ips=${UNIQUE_IPS} err=${ERROR_RATE}% bot=${BOT_SCORE}%"
