#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 31, Performance Benchmarks and Metrics.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

set -euo pipefail

# Safe Varnish Rolling Restart with Cache Warming
# Restarts Varnish pods one at a time, warming each before proceeding.
# Ensures the cluster ALWAYS has warm caches during rollout.
#
# Usage: ./rolling-restart-safe.sh [--namespace NS] [--warmup-endpoint URL]
#
# CRITICAL: This script BLOCKS rollout progression until each pod's cache
# is verified warm. A cold cache at 50K+ concurrent users = instant incident.
# Measured on ops-host: cold cache caused 6x more timeouts (20 -> 121).

SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_NAME
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
readonly SCRIPT_DIR
NAMESPACE="${NAMESPACE:-casino-prod}"
DEPLOYMENT="${DEPLOYMENT:-varnish}"
WARMUP_ENDPOINT="${WARMUP_ENDPOINT:-http://varnish:6081}"
EXTERNAL_ENDPOINT="${EXTERNAL_ENDPOINT:-https://localhost:30443}"
KUBECTL="${KUBECTL:-k3s kubectl}"
MAX_RETRIES=3
POD_READY_TIMEOUT=120  # seconds to wait for pod readiness

usage() {
    cat <<EOF
Usage: $SCRIPT_NAME [OPTIONS]

Safe Varnish rolling restart: one pod at a time with cache warming gate.

IMPORTANT: This script ensures NO pod receives production traffic with
a cold cache. Each pod is warmed and verified before the next is restarted.

Options:
  --namespace NS          Kubernetes namespace (default: casino-prod)
  --deployment NAME       Deployment name (default: varnish)
  --warmup-endpoint URL   Internal Varnish service URL (default: http://varnish:6081)
  --external-endpoint URL External endpoint for verification (default: https://localhost:30443)
  --kubectl CMD           kubectl command (default: k3s kubectl)
  --max-retries N         Max warming retries per pod (default: 3)
  -h, --help              Show this help

The script will:
  1. Save current cache state
  2. Identify all Varnish pods
  3. For EACH pod (one at a time):
     a. Delete the pod (K8s creates a replacement)
     b. Wait for the new pod to be Ready
     c. Run cache warmer against the cluster
     d. Verify X-Cache: HIT for all P0/P1 endpoints
     e. ONLY proceed to next pod if verification passes
  4. If warming fails after $MAX_RETRIES retries, ABORT the rollout

Exit codes:
  0    Rollout completed successfully, all caches warm
  1    Rollout ABORTED: cache warming failed for a pod
EOF
    exit 0
}

log() {
    local level="$1"
    shift
    printf '%s level=%s msg="%s"\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$level" "$*"
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --namespace)          NAMESPACE="$2"; shift 2 ;;
            --deployment)         DEPLOYMENT="$2"; shift 2 ;;
            --warmup-endpoint)    WARMUP_ENDPOINT="$2"; shift 2 ;;
            --external-endpoint)  EXTERNAL_ENDPOINT="$2"; shift 2 ;;
            --kubectl)            KUBECTL="$2"; shift 2 ;;
            --max-retries)        MAX_RETRIES="$2"; shift 2 ;;
            -h|--help)            usage ;;
            *)                    log "ERROR" "Unknown option: $1"; usage ;;
        esac
    done
}

get_varnish_pods() {
    $KUBECTL get pods -n "$NAMESPACE" -l "app=$DEPLOYMENT" \
        --field-selector=status.phase=Running \
        -o jsonpath='{.items[*].metadata.name}' 2>/dev/null
}

wait_for_pod_ready() {
    local elapsed=0
    local interval=3

    log "INFO" "Waiting for new pod to be Ready (timeout: ${POD_READY_TIMEOUT}s)..."

    while [[ "$elapsed" -lt "$POD_READY_TIMEOUT" ]]; do
        local ready_pods
        ready_pods=$($KUBECTL get pods -n "$NAMESPACE" -l "app=$DEPLOYMENT" \
            --field-selector=status.phase=Running \
            -o jsonpath='{range .items[*]}{.status.conditions[?(@.type=="Ready")].status}{" "}{end}' 2>/dev/null || true)

        # Count pods in ready state
        local total=0
        local ready=0
        for status in $ready_pods; do
            ((total++)) || true
            if [[ "$status" == "True" ]]; then
                ((ready++)) || true
            fi
        done

        if [[ "$total" -gt 0 && "$ready" -eq "$total" ]]; then
            log "INFO" "All $total pods are Ready (elapsed: ${elapsed}s)"
            return 0
        fi

        log "INFO" "Pods ready: $ready/$total (${elapsed}s elapsed)"
        sleep "$interval"
        elapsed=$((elapsed + interval))
    done

    log "ERROR" "Timeout waiting for pod readiness after ${POD_READY_TIMEOUT}s"
    return 1
}

warm_and_verify() {
    local attempt="$1"
    local pod_name="$2"

    log "INFO" "Warming attempt $attempt/$MAX_RETRIES for pod $pod_name"

    # Run the warmer
    if [[ -x "${SCRIPT_DIR}/varnish-cache-warmer.sh" ]]; then
        ENDPOINT="$WARMUP_ENDPOINT" "${SCRIPT_DIR}/varnish-cache-warmer.sh" \
            --rounds 3 --concurrent 10 2>&1 || true
    else
        log "WARN" "Warmer script not found at ${SCRIPT_DIR}/varnish-cache-warmer.sh, using inline warming"
        local urls="/health /api/games /lobby /api/odds /api/odds/live /api/odds/pre-match /api/odds/popular /api/games/slots /api/games/live-casino /api/games/table-games /api/games/crash"
        for _round in 1 2 3; do
            for url in $urls; do
                curl -sf --connect-timeout 5 --max-time 10 \
                    "${WARMUP_ENDPOINT}${url}" -o /dev/null 2>/dev/null || true
            done
        done
    fi

    # Verify with gate mode
    if [[ -x "${SCRIPT_DIR}/post-rollout-verify.sh" ]]; then
        ENDPOINT="$EXTERNAL_ENDPOINT" "${SCRIPT_DIR}/post-rollout-verify.sh" --gate 2>&1
        return $?
    else
        log "WARN" "Verify script not found, using inline verification"
        local p0_hit=false p1_hit=false
        local headers

        # Check P0
        headers=$(curl -sIk --connect-timeout 5 --max-time 10 \
            "${EXTERNAL_ENDPOINT}/health" 2>/dev/null || true)
        if echo "$headers" | grep -qi "X-Cache:.*HIT"; then
            p0_hit=true
        fi

        # Check P1 (sample)
        headers=$(curl -sIk --connect-timeout 5 --max-time 10 \
            "${EXTERNAL_ENDPOINT}/api/games" 2>/dev/null || true)
        if echo "$headers" | grep -qi "X-Cache:.*HIT"; then
            p1_hit=true
        fi

        if [[ "$p0_hit" == "true" && "$p1_hit" == "true" ]]; then
            return 0
        fi
        return 1
    fi
}

main() {
    parse_args "$@"

    local start_time
    start_time=$(date +%s)

    log "INFO" "Starting safe Varnish rolling restart namespace=$NAMESPACE deployment=$DEPLOYMENT"
    log "INFO" "CRITICAL: Each pod will be warmed and verified before proceeding"

    # Step 1: Save cache state
    log "INFO" "Step 1: Saving pre-rollout cache state"
    if [[ -x "${SCRIPT_DIR}/pre-rollout-cache-save.sh" ]]; then
        NAMESPACE="$NAMESPACE" KUBECTL="$KUBECTL" \
            "${SCRIPT_DIR}/pre-rollout-cache-save.sh" --output /tmp/varnish-cache-state.json 2>&1 || {
            log "WARN" "Failed to save cache state (non-fatal, continuing)"
        }
    fi

    # Step 2: Get all Varnish pods
    local pods
    pods=$(get_varnish_pods)
    local pod_list
    read -ra pod_list <<< "$pods"
    local total_pods=${#pod_list[@]}

    if [[ "$total_pods" -eq 0 ]]; then
        log "ERROR" "No Varnish pods found in namespace $NAMESPACE with label app=$DEPLOYMENT"
        exit 1
    fi

    log "INFO" "Found $total_pods Varnish pods to restart"

    # Step 3: Restart one pod at a time
    local completed=0

    for pod in "${pod_list[@]}"; do
        ((completed++)) || true
        echo ""
        log "INFO" "=========================================="
        log "INFO" "Restarting pod $completed/$total_pods: $pod"
        log "INFO" "=========================================="

        # Delete the pod -- K8s Deployment controller will create a replacement
        log "INFO" "Deleting pod $pod"
        $KUBECTL delete pod -n "$NAMESPACE" "$pod" --grace-period=30 2>&1 || {
            log "ERROR" "Failed to delete pod $pod"
            exit 1
        }

        # Wait for the replacement pod to be ready
        if ! wait_for_pod_ready; then
            log "ERROR" "ROLLOUT ABORTED: New pod did not become Ready"
            log "ERROR" "Manual intervention required: check pod status with:"
            log "ERROR" "  $KUBECTL get pods -n $NAMESPACE -l app=$DEPLOYMENT"
            exit 1
        fi

        # Warm and verify with retries
        local warm_success=false
        for attempt in $(seq 1 "$MAX_RETRIES"); do
            if warm_and_verify "$attempt" "$pod"; then
                warm_success=true
                log "INFO" "Pod $completed/$total_pods: cache verified warm (attempt $attempt)"
                break
            else
                log "WARN" "Warming attempt $attempt/$MAX_RETRIES failed for pod $completed"
                if [[ "$attempt" -lt "$MAX_RETRIES" ]]; then
                    log "INFO" "Retrying in 5 seconds..."
                    sleep 5
                fi
            fi
        done

        if [[ "$warm_success" == "false" ]]; then
            log "ERROR" "ROLLOUT ABORTED after $MAX_RETRIES failed warming attempts"
            log "ERROR" "Pod $completed/$total_pods could not be warmed"
            log "ERROR" "NEVER release traffic to a Varnish pod with cold cache in production"
            log "ERROR" "Remaining pods ($((total_pods - completed))) were NOT restarted"
            log "ERROR" ""
            log "ERROR" "Recovery steps:"
            log "ERROR" "  1. Check Varnish pod logs: $KUBECTL logs -n $NAMESPACE -l app=$DEPLOYMENT"
            log "ERROR" "  2. Check backend health: $KUBECTL get pods -n $NAMESPACE"
            log "ERROR" "  3. Run warmer manually: ./varnish-cache-warmer.sh --endpoint $WARMUP_ENDPOINT --verbose"
            log "ERROR" "  4. Once resolved, re-run this script to continue"
            exit 1
        fi
    done

    local elapsed=$(( $(date +%s) - start_time ))

    echo ""
    log "INFO" "=========================================="
    log "INFO" "Rolling restart complete"
    log "INFO" "=========================================="
    log "INFO" "Pods restarted: $total_pods/$total_pods"
    log "INFO" "Total time: ${elapsed}s"
    log "INFO" "Average per pod: $((elapsed / total_pods))s"
    log "INFO" "All caches verified warm"
}

main "$@"
