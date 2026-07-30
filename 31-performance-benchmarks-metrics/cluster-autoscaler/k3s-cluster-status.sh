#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC1090,SC1091
# k3s-cluster-status.sh
# Dashboard script — shows current K3s cluster state, node metrics,
# pod distribution, HPA status, and autoscaler events.
#
# Usage:
#   ./k3s-cluster-status.sh
#   ./k3s-cluster-status.sh --json    # machine-readable output
#   watch -n 5 ./k3s-cluster-status.sh

set -euo pipefail

# ─── Colours ───────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; CYAN='\033[0;36m'; NC='\033[0m'

JSON_MODE=0
[[ "${1:-}" == "--json" ]] && JSON_MODE=1

CONF_FILE="${K3S_AUTOSCALER_CONF:-/etc/k3s-autoscaler.conf}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$CONF_FILE" ]] && source "$CONF_FILE"
[[ -f "${SCRIPT_DIR}/k3s-autoscaler.conf" ]] && source "${SCRIPT_DIR}/k3s-autoscaler.conf"

SCALE_EVENTS_LOG="${SCALE_EVENTS_LOG:-/var/log/k3s-autoscaler-events.log}"

header() {
    if [[ $JSON_MODE -eq 0 ]]; then
        echo ""
        echo -e "${BOLD}${CYAN}━━━ $* ━━━${NC}"
    fi
}

# ═══════════════════════════════════════════════════════════════════════════
# Gather data
# ═══════════════════════════════════════════════════════════════════════════

if [[ $JSON_MODE -eq 0 ]]; then
    echo -e "${BOLD}${BLUE}"
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║           K3s Cluster Autoscaler Dashboard              ║"
    echo "║           $(date '+%Y-%m-%d %H:%M:%S')                         ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo -e "${NC}"
fi

# ─── Nodes ─────────────────────────────────────────────────────────────────
header "Cluster Nodes"

if [[ $JSON_MODE -eq 0 ]]; then
    k3s kubectl get nodes -o wide 2>/dev/null || echo "  (unable to reach cluster)"
fi

TOTAL_NODES=$(k3s kubectl get nodes --no-headers 2>/dev/null | wc -l | tr -d ' ')
READY_NODES=$(k3s kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || echo "0")
MANAGED_WORKERS=$(k3s kubectl get nodes -l "autoscaler-managed=true" --no-headers 2>/dev/null | wc -l | tr -d ' ')

if [[ $JSON_MODE -eq 0 ]]; then
    echo ""
    echo -e "  Total nodes: ${BOLD}${TOTAL_NODES}${NC}  |  Ready: ${GREEN}${READY_NODES}${NC}  |  Autoscaler-managed workers: ${CYAN}${MANAGED_WORKERS}${NC}"
fi

# ─── Node metrics ──────────────────────────────────────────────────────────
header "Node Resource Usage"

TOP_OUTPUT=""
if k3s kubectl top nodes 2>/dev/null; then
    TOP_OUTPUT=$(k3s kubectl top nodes --no-headers 2>/dev/null)
else
    if [[ $JSON_MODE -eq 0 ]]; then
        echo -e "  ${YELLOW}metrics-server not available — install with:${NC}"
        echo "  k3s kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml"
    fi
fi

# Compute average CPU if metrics available
AVG_CPU="-"
if [[ -n "$TOP_OUTPUT" ]]; then
    TOTAL_PCT=0; COUNT=0
    while IFS= read -r line; do
        PCT=$(echo "$line" | awk '{gsub(/%/,"",$3); print $3}')
        if [[ "$PCT" =~ ^[0-9]+$ ]]; then
            TOTAL_PCT=$(( TOTAL_PCT + PCT ))
            COUNT=$(( COUNT + 1 ))
        fi
    done <<< "$TOP_OUTPUT"
    [[ $COUNT -gt 0 ]] && AVG_CPU=$(( TOTAL_PCT / COUNT ))
fi

if [[ $JSON_MODE -eq 0 && "$AVG_CPU" != "-" ]]; then
    local_color="$GREEN"
    [[ "$AVG_CPU" -ge 50 ]] && local_color="$YELLOW"
    [[ "$AVG_CPU" -ge 70 ]] && local_color="$RED"
    echo ""
    echo -e "  Average CPU: ${local_color}${BOLD}${AVG_CPU}%${NC}"
fi

# ─── Pod distribution ─────────────────────────────────────────────────────
header "Pod Distribution by Node"

if [[ $JSON_MODE -eq 0 ]]; then
    NODES=$(k3s kubectl get nodes --no-headers -o custom-columns="NAME:.metadata.name" 2>/dev/null)
    if [[ -n "$NODES" ]]; then
        printf "  %-30s %8s %8s\n" "NODE" "PODS" "NON-SYS"
        printf "  %-30s %8s %8s\n" "----" "----" "-------"
        while IFS= read -r node; do
            TOTAL=$(k3s kubectl get pods --all-namespaces \
                --field-selector="spec.nodeName=${node}" --no-headers 2>/dev/null | wc -l | tr -d ' ')
            NON_SYS=$(k3s kubectl get pods --all-namespaces \
                --field-selector="spec.nodeName=${node}" --no-headers 2>/dev/null \
                | grep -cv "^kube-system " || echo "0")
            printf "  %-30s %8s %8s\n" "$node" "$TOTAL" "$NON_SYS"
        done <<< "$NODES"
    fi
fi

# ─── Pending pods ──────────────────────────────────────────────────────────
header "Pending Pods"

PENDING_COUNT=$(k3s kubectl get pods --all-namespaces \
    --field-selector=status.phase=Pending --no-headers 2>/dev/null | wc -l | tr -d ' ')

if [[ $JSON_MODE -eq 0 ]]; then
    if [[ "$PENDING_COUNT" -gt 0 ]]; then
        echo -e "  ${RED}${BOLD}${PENDING_COUNT} pending pods:${NC}"
        k3s kubectl get pods --all-namespaces --field-selector=status.phase=Pending 2>/dev/null
    else
        echo -e "  ${GREEN}No pending pods${NC}"
    fi
fi

# ─── HPA status ────────────────────────────────────────────────────────────
header "HPA Status"

if [[ $JSON_MODE -eq 0 ]]; then
    HPA_OUTPUT=$(k3s kubectl get hpa --all-namespaces 2>/dev/null)
    if [[ -n "$HPA_OUTPUT" && "$HPA_OUTPUT" != *"No resources found"* ]]; then
        echo "$HPA_OUTPUT"
    else
        echo -e "  ${YELLOW}No HPA configured${NC}"
    fi
fi

# ─── Autoscaler state ─────────────────────────────────────────────────────
header "Autoscaler State"

STATE_DIR="/var/run/k3s-autoscaler"
if [[ $JSON_MODE -eq 0 ]]; then
    if [[ -f "${STATE_DIR}/last-scale-time" ]]; then
        LAST_SCALE=$(cat "${STATE_DIR}/last-scale-time")
        ELAPSED=$(( $(date +%s) - LAST_SCALE ))
        echo -e "  Last scale event: ${ELAPSED}s ago ($(date -d "@${LAST_SCALE}" '+%H:%M:%S' 2>/dev/null || date -r "${LAST_SCALE}" '+%H:%M:%S' 2>/dev/null || echo 'unknown'))"
    else
        echo -e "  ${YELLOW}No scale events recorded${NC}"
    fi

    if [[ -f "${STATE_DIR}/idle-since" ]]; then
        IDLE=$(cat "${STATE_DIR}/idle-since")
        echo -e "  Idle timer: running for $(( $(date +%s) - IDLE ))s"
    fi

    if [[ -f "${STATE_DIR}/pending-since" ]]; then
        PEND=$(cat "${STATE_DIR}/pending-since")
        echo -e "  Pending timer: running for $(( $(date +%s) - PEND ))s"
    fi

    # Show autoscaler service status
    if systemctl is-active --quiet k3s-autoscaler.timer 2>/dev/null; then
        echo -e "  Systemd timer: ${GREEN}active${NC}"
    elif systemctl is-active --quiet k3s-autoscaler.service 2>/dev/null; then
        echo -e "  Systemd service: ${GREEN}active${NC}"
    else
        echo -e "  Systemd timer: ${YELLOW}not running${NC}"
    fi
fi

# ─── Recent scale events ──────────────────────────────────────────────────
header "Recent Scale Events (last 10)"

if [[ $JSON_MODE -eq 0 ]]; then
    if [[ -f "$SCALE_EVENTS_LOG" ]]; then
        tail -10 "$SCALE_EVENTS_LOG"
    else
        echo -e "  ${YELLOW}No events logged yet${NC}"
    fi
fi

# ─── JSON output mode ─────────────────────────────────────────────────────
if [[ $JSON_MODE -eq 1 ]]; then
    cat <<JSONEOF
{
  "timestamp": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "nodes": {
    "total": ${TOTAL_NODES},
    "ready": ${READY_NODES},
    "managed_workers": ${MANAGED_WORKERS}
  },
  "avg_cpu_percent": "${AVG_CPU}",
  "pending_pods": ${PENDING_COUNT},
  "max_workers": ${MAX_WORKERS:-5}
}
JSONEOF
fi

echo ""
