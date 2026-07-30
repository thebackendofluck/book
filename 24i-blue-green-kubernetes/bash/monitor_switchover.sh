#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24i, Blue-Green Cluster Switching for iGaming Kubernetes Environm.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# monitor_switchover.sh — Post-switchover monitoring with auto-rollback
set -euo pipefail

NEW_ACTIVE="${1:?Usage: $0 <new_active_cluster> <old_active_cluster>}"
OLD_ACTIVE="${2:?Usage: $0 <new_active_cluster> <old_active_cluster>}"
MONITOR_WINDOW="${3:-600}"  # 10 minutes
ERROR_THRESHOLD_RATE="0.05"  # 5% error rate triggers rollback
LATENCY_THRESHOLD_MS="2000"  # 2000ms P99 triggers rollback

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [MONITOR] $*"; }

check_error_rate() {
    # Query Prometheus for 5xx rate in the last 60s
    local error_rate
    error_rate=$(curl -sf "http://prometheus.casino.internal:9090/api/v1/query" \
        --data-urlencode "query=sum(rate(nginx_ingress_controller_requests{status=~\"5..\",cluster=\"${NEW_ACTIVE}\"}[60s])) / sum(rate(nginx_ingress_controller_requests{cluster=\"${NEW_ACTIVE}\"}[60s]))" \
        2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null)

    [[ -z "$error_rate" || "$error_rate" == "null" ]] && return 0

    local should_rollback
    should_rollback=$(echo "$error_rate > $ERROR_THRESHOLD_RATE" | bc -l)
    if [[ "$should_rollback" == "1" ]]; then
        log ERROR "Error rate $error_rate exceeds threshold $ERROR_THRESHOLD_RATE — triggering rollback"
        return 1
    fi
    return 0
}

check_p99_latency() {
    local p99_ms
    p99_ms=$(curl -sf "http://prometheus.casino.internal:9090/api/v1/query" \
        --data-urlencode "query=histogram_quantile(0.99, sum(rate(nginx_ingress_controller_response_duration_seconds_bucket{cluster=\"${NEW_ACTIVE}\"}[60s])) by (le)) * 1000" \
        2>/dev/null | jq -r '.data.result[0].value[1]' 2>/dev/null)

    [[ -z "$p99_ms" || "$p99_ms" == "null" ]] && return 0

    local should_rollback
    should_rollback=$(echo "$p99_ms > $LATENCY_THRESHOLD_MS" | bc -l)
    if [[ "$should_rollback" == "1" ]]; then
        log ERROR "P99 latency ${p99_ms}ms exceeds threshold ${LATENCY_THRESHOLD_MS}ms — triggering rollback"
        return 1
    fi
    return 0
}

rollback() {
    log ERROR "ROLLING BACK: $NEW_ACTIVE → $OLD_ACTIVE"
    /opt/casino/scripts/switchover.sh "$NEW_ACTIVE"
    log ERROR "Rollback complete. Notifying on-call."
    curl -sf -X POST "$PAGERDUTY_WEBHOOK" \
        -H 'Content-Type: application/json' \
        -d "{\"routing_key\":\"${PAGERDUTY_KEY}\",\"event_action\":\"trigger\",\"payload\":{\"summary\":\"Casino cluster auto-rollback triggered: ${NEW_ACTIVE} unhealthy, reverted to ${OLD_ACTIVE}\",\"severity\":\"critical\",\"source\":\"casino-switchover-monitor\"}}" || true
}

log INFO "Monitoring $NEW_ACTIVE for ${MONITOR_WINDOW}s (rollback if errors > ${ERROR_THRESHOLD_RATE} or P99 > ${LATENCY_THRESHOLD_MS}ms)"

end_time=$(( $(date +%s) + MONITOR_WINDOW ))
while [[ $(date +%s) -lt $end_time ]]; do
    check_error_rate || { rollback; exit 1; }
    check_p99_latency || { rollback; exit 1; }
    sleep 30
done

log INFO "Monitoring window complete. $NEW_ACTIVE is stable."
