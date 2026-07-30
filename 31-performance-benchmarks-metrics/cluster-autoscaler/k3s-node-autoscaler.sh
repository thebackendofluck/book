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
# k3s-node-autoscaler.sh
# Main autoscaler daemon — monitors K3s cluster metrics and automatically
# adds or removes worker VMs based on CPU load and pending pods.
#
# Runs as a one-shot check (designed for systemd timer) or continuous daemon.
#
# Usage:
#   ./k3s-node-autoscaler.sh              # single check (for timer)
#   ./k3s-node-autoscaler.sh --daemon      # continuous loop
#   ./k3s-node-autoscaler.sh --dry-run     # show what would happen

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${K3S_AUTOSCALER_CONF:-/etc/k3s-autoscaler.conf}"
[[ -f "$CONF_FILE" ]] && source "$CONF_FILE"
[[ -f "${SCRIPT_DIR}/k3s-autoscaler.conf" ]] && source "${SCRIPT_DIR}/k3s-autoscaler.conf"

# ─── Config with defaults ─────────────────────────────────────────────────
SCALE_UP_CPU_PERCENT="${SCALE_UP_CPU_PERCENT:-70}"
SCALE_DOWN_CPU_PERCENT="${SCALE_DOWN_CPU_PERCENT:-30}"
SCALE_DOWN_WAIT_SECONDS="${SCALE_DOWN_WAIT_SECONDS:-300}"
PENDING_POD_GRACE_SECONDS="${PENDING_POD_GRACE_SECONDS:-30}"
COOLDOWN_SECONDS="${COOLDOWN_SECONDS:-120}"
MIN_WORKERS="${MIN_WORKERS:-0}"
MAX_WORKERS="${MAX_WORKERS:-5}"
WORKER_CPU="${WORKER_CPU:-16}"
WORKER_RAM="${WORKER_RAM:-32768}"
WORKER_IP_BASE="${WORKER_IP_BASE:-10.0.10}"
WORKER_IP_START="${WORKER_IP_START:-41}"
WORKER_NAME_PREFIX="${WORKER_NAME_PREFIX:-k3s-autoscale-worker}"
LOG_FILE="${LOG_FILE:-/var/log/k3s-autoscaler.log}"
SCALE_EVENTS_LOG="${SCALE_EVENTS_LOG:-/var/log/k3s-autoscaler-events.log}"

# State files
STATE_DIR="/var/run/k3s-autoscaler"
LAST_SCALE_FILE="${STATE_DIR}/last-scale-time"
IDLE_SINCE_FILE="${STATE_DIR}/idle-since"
PENDING_SINCE_FILE="${STATE_DIR}/pending-since"

mkdir -p "$STATE_DIR" "$(dirname "$LOG_FILE")" 2>/dev/null || {
    STATE_DIR="/tmp/k3s-autoscaler"
    mkdir -p "$STATE_DIR"
}

DAEMON_MODE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --daemon)  DAEMON_MODE=1; shift ;;
        --dry-run) DRY_RUN=1;     shift ;;
        --help|-h)
            echo "Usage: $0 [--daemon] [--dry-run]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

log()  { local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"; echo "[$ts] INFO  $*" >> "$LOG_FILE"; }
warn() { local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"; echo "[$ts] WARN  $*" >> "$LOG_FILE"; }
err()  { local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"; echo "[$ts] ERROR $*" >> "$LOG_FILE" >&2; }

# ─── Helper functions ──────────────────────────────────────────────────────

now_epoch() { date +%s; }

cooldown_active() {
    if [[ -f "$LAST_SCALE_FILE" ]]; then
        local last_scale elapsed
        last_scale=$(cat "$LAST_SCALE_FILE")
        elapsed=$(( $(now_epoch) - last_scale ))
        if [[ $elapsed -lt $COOLDOWN_SECONDS ]]; then
            log "Cooldown active: ${elapsed}s / ${COOLDOWN_SECONDS}s since last scale event"
            return 0
        fi
    fi
    return 1
}

record_scale_event() {
    now_epoch > "$LAST_SCALE_FILE"
    rm -f "$IDLE_SINCE_FILE" "$PENDING_SINCE_FILE"
}

get_managed_workers() {
    # Return list of autoscaler-managed worker nodes
    k3s kubectl get nodes -l "autoscaler-managed=true" \
        --no-headers -o custom-columns="NAME:.metadata.name" 2>/dev/null || true
}

count_managed_workers() {
    local count
    count=$(get_managed_workers | grep -c . 2>/dev/null || true)
    echo "${count:-0}" | tr -d '[:space:]'
}

get_avg_cpu() {
    # Parse 'kubectl top nodes' output — CPU% is shown as millicores
    # We compute: (sum of used CPU across all nodes / sum of allocatable CPU) * 100
    local top_output
    top_output=$(k3s kubectl top nodes --no-headers 2>/dev/null) || {
        warn "kubectl top nodes failed — metrics-server may not be ready"
        echo "-1"
        return
    }

    if [[ -z "$top_output" ]]; then
        echo "-1"
        return
    fi

    # kubectl top nodes output: NAME  CPU(cores)  CPU%  MEMORY(bytes)  MEMORY%
    local total_pct count avg
    total_pct=0
    count=0
    while IFS= read -r line; do
        # Extract CPU% column (3rd field, strip trailing %)
        local pct
        pct=$(echo "$line" | awk '{gsub(/%/,"",$3); print $3}')
        if [[ "$pct" =~ ^[0-9]+$ ]]; then
            total_pct=$(( total_pct + pct ))
            count=$(( count + 1 ))
        fi
    done <<< "$top_output"

    if [[ $count -eq 0 ]]; then
        echo "-1"
    else
        avg=$(( total_pct / count ))
        echo "$avg"
    fi
}

get_pending_pods() {
    local count
    count=$(k3s kubectl get pods --all-namespaces \
        --field-selector=status.phase=Pending \
        --no-headers 2>/dev/null | grep -c . 2>/dev/null || true)
    echo "${count:-0}" | tr -d '[:space:]'
}

find_next_worker_slot() {
    # Find next available worker name + IP
    for i in $(seq 1 "$MAX_WORKERS"); do
        local idx name ip
        idx=$(printf "%03d" "$i")
        name="${WORKER_NAME_PREFIX}-${idx}"
        ip="${WORKER_IP_BASE}.$(( WORKER_IP_START + i - 1 ))"

        if ! k3s kubectl get node "$name" &>/dev/null && \
           ! virsh list --all --name 2>/dev/null | grep -q "^${name}$"; then
            echo "${name}|${ip}"
            return 0
        fi
    done
    return 1
}

find_removable_worker() {
    # Find the most recently added worker that has no non-system pods
    local workers
    workers=$(get_managed_workers)
    [[ -z "$workers" ]] && return 1

    # Iterate in reverse order (remove newest first)
    local worker
    for worker in $(echo "$workers" | sort -r); do
        local non_system_pods
        non_system_pods=$(k3s kubectl get pods --all-namespaces \
            --field-selector="spec.nodeName=${worker}" --no-headers 2>/dev/null \
            | grep -cv "^kube-system " || echo "0")

        if [[ "$non_system_pods" -eq 0 ]]; then
            echo "$worker"
            return 0
        fi
    done
    return 1
}

# ─── Scale actions ─────────────────────────────────────────────────────────

do_scale_up() {
    local slot name ip
    slot=$(find_next_worker_slot) || {
        warn "SCALE_UP: No available worker slots (all ${MAX_WORKERS} in use)"
        return 1
    }
    name="${slot%%|*}"
    ip="${slot##*|}"

    log "SCALE_UP: Adding worker ${name} (${ip}) — CPU=${WORKER_CPU}, RAM=${WORKER_RAM}MB"

    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY_RUN: Would run: ${SCRIPT_DIR}/k3s-add-worker.sh --name ${name} --ip ${ip} --cpu ${WORKER_CPU} --ram ${WORKER_RAM}"
        return 0
    fi

    if "${SCRIPT_DIR}/k3s-add-worker.sh" \
        --name "$name" --ip "$ip" \
        --cpu "$WORKER_CPU" --ram "$WORKER_RAM" 2>&1 | tee -a "$LOG_FILE"; then
        record_scale_event
        log "SCALE_UP: Worker ${name} added successfully"
        echo "$(date '+%Y-%m-%d %H:%M:%S') AUTOSCALE_UP name=${name} ip=${ip}" >> "$SCALE_EVENTS_LOG"
    else
        err "SCALE_UP: Failed to add worker ${name}"
        return 1
    fi
}

do_scale_down() {
    local worker
    worker=$(find_removable_worker) || {
        log "SCALE_DOWN: No removable workers found (all have running pods)"
        return 1
    }

    log "SCALE_DOWN: Removing worker ${worker}"

    if [[ $DRY_RUN -eq 1 ]]; then
        log "DRY_RUN: Would run: ${SCRIPT_DIR}/k3s-remove-worker.sh --name ${worker}"
        return 0
    fi

    if "${SCRIPT_DIR}/k3s-remove-worker.sh" --name "$worker" 2>&1 | tee -a "$LOG_FILE"; then
        record_scale_event
        log "SCALE_DOWN: Worker ${worker} removed successfully"
        echo "$(date '+%Y-%m-%d %H:%M:%S') AUTOSCALE_DOWN name=${worker}" >> "$SCALE_EVENTS_LOG"
    else
        err "SCALE_DOWN: Failed to remove worker ${worker}"
        return 1
    fi
}

# ─── Main evaluation ──────────────────────────────────────────────────────

evaluate() {
    local avg_cpu pending_pods worker_count now

    avg_cpu=$(get_avg_cpu)
    pending_pods=$(get_pending_pods)
    worker_count=$(count_managed_workers)
    now=$(now_epoch)

    log "CHECK: avg_cpu=${avg_cpu}% pending_pods=${pending_pods} workers=${worker_count}/${MAX_WORKERS}"

    # Bail if metrics unavailable
    if [[ "$avg_cpu" -eq -1 ]]; then
        warn "Metrics unavailable — skipping evaluation"
        return
    fi

    # Check cooldown
    if cooldown_active; then
        return
    fi

    # ── Scale-up evaluation ──
    local should_scale_up=0

    # Trigger: high CPU
    if [[ "$avg_cpu" -ge "$SCALE_UP_CPU_PERCENT" ]]; then
        log "Scale-up trigger: CPU ${avg_cpu}% >= ${SCALE_UP_CPU_PERCENT}%"
        should_scale_up=1
    fi

    # Trigger: pending pods for > grace period
    if [[ "$pending_pods" -gt 0 ]]; then
        if [[ -f "$PENDING_SINCE_FILE" ]]; then
            local pending_since elapsed
            pending_since=$(cat "$PENDING_SINCE_FILE")
            elapsed=$(( now - pending_since ))
            if [[ $elapsed -ge $PENDING_POD_GRACE_SECONDS ]]; then
                log "Scale-up trigger: ${pending_pods} pending pods for ${elapsed}s >= ${PENDING_POD_GRACE_SECONDS}s"
                should_scale_up=1
            fi
        else
            echo "$now" > "$PENDING_SINCE_FILE"
            log "Pending pods detected (${pending_pods}), starting grace timer"
        fi
    else
        rm -f "$PENDING_SINCE_FILE"
    fi

    if [[ $should_scale_up -eq 1 ]]; then
        if [[ "$worker_count" -ge "$MAX_WORKERS" ]]; then
            warn "Would scale up but already at MAX_WORKERS (${worker_count}/${MAX_WORKERS})"
        else
            do_scale_up
        fi
        return
    fi

    # ── Scale-down evaluation ──
    if [[ "$avg_cpu" -lt "$SCALE_DOWN_CPU_PERCENT" && "$worker_count" -gt "$MIN_WORKERS" && "$pending_pods" -eq 0 ]]; then
        if [[ -f "$IDLE_SINCE_FILE" ]]; then
            local idle_since elapsed
            idle_since=$(cat "$IDLE_SINCE_FILE")
            elapsed=$(( now - idle_since ))
            if [[ $elapsed -ge $SCALE_DOWN_WAIT_SECONDS ]]; then
                log "Scale-down trigger: CPU ${avg_cpu}% < ${SCALE_DOWN_CPU_PERCENT}% for ${elapsed}s >= ${SCALE_DOWN_WAIT_SECONDS}s"
                do_scale_down
            else
                log "Low CPU (${avg_cpu}%) for ${elapsed}s / ${SCALE_DOWN_WAIT_SECONDS}s — waiting"
            fi
        else
            echo "$now" > "$IDLE_SINCE_FILE"
            log "Low CPU detected (${avg_cpu}%), starting idle timer"
        fi
    else
        rm -f "$IDLE_SINCE_FILE"
    fi
}

# ─── Entry point ───────────────────────────────────────────────────────────

log "=== k3s-node-autoscaler started (mode=$(if [[ $DAEMON_MODE -eq 1 ]]; then echo daemon; else echo oneshot; fi), dry_run=${DRY_RUN}) ==="

if [[ $DAEMON_MODE -eq 1 ]]; then
    log "Running in daemon mode (30s interval). PID=$$"
    trap 'log "Autoscaler daemon stopped (PID=$$)"; exit 0' SIGTERM SIGINT

    while true; do
        evaluate || err "Evaluation cycle failed"
        sleep 30
    done
else
    evaluate || err "Evaluation failed"
fi
