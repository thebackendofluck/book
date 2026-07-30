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

# rotation-driver.sh — Orchestrates the full daily rotation lifecycle
# Called by systemd timers with argument: provision | validate | switchover | destroy

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${CASINO_STATE_FILE:-/etc/casino/rotation-state.json}"
LOG_DIR="${CASINO_LOG_DIR:-/var/log/casino}"
CONFIG_DIR="${CASINO_CONFIG_DIR:-/etc/casino}"
ALERT_WEBHOOK="${ALERT_WEBHOOK_URL:-}"

mkdir -p "$LOG_DIR"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') [ROTATION] $*" | tee -a "${LOG_DIR}/rotation.log"; }

alert_slack() {
    [[ -z "$ALERT_WEBHOOK" ]] && return 0
    curl -sf -X POST "$ALERT_WEBHOOK" \
        -H 'Content-Type: application/json' \
        -d "{\"text\": \"[casino-rotation] $*\"}" || true
}

die() {
    log "ERROR: $*"
    alert_slack "CRITICAL: $*"
    exit 1
}

get_state() { jq -r ".${1}" "$STATE_FILE" 2>/dev/null || echo "null"; }

set_state() {
    local key="$1" val="$2"
    local tmp
    # Same directory, so the mv is an atomic rename rather than a cross-device
    # copy that would drop the file's ownership and mode.
    tmp=$(mktemp "${STATE_FILE}.XXXXXX") || die "Cannot create a temp file next to $STATE_FILE"
    chmod --reference="$STATE_FILE" "$tmp" 2>/dev/null || true
    # --arg, not string interpolation: the value goes through jq as data.
    if ! jq --arg v "$val" ".${key} = \$v" "$STATE_FILE" > "$tmp"; then
        rm -f "$tmp"
        die "Cannot write ${key}=${val} to $STATE_FILE"
    fi
    mv "$tmp" "$STATE_FILE"
}

# Determine current and next cluster colors
get_current_color() { get_state "active_color"; }
get_next_color() {
    local current; current=$(get_current_color)
    [[ "$current" == "blue" ]] && echo "green" || echo "blue"
}

# The full smoke suite plus the synthetic player journey. switchover.sh runs these
# again as its own gate immediately before cutting traffic; running them here too
# means a cluster that came up broken at 02:00 is known about an hour before
# anybody's session depends on it.
validate_cluster() {
    local color="$1"

    log "Running pre_switch_validation.sh against $color..."
    "${SCRIPT_DIR}/pre_switch_validation.sh" "$color" 2>&1 | tee -a "${LOG_DIR}/rotation.log" \
        || return 1

    local ingress_ip
    ingress_ip=$(tr -d '[:space:]' < "${CASINO_STATE_DIR:-/var/lib/casino}/casino-${color}-ingress-ip" \
        2>/dev/null || tr -d '[:space:]' < "/tmp/casino-${color}-ingress-ip") \
        || { log "ERROR: no ingress IP recorded for $color"; return 1; }

    log "Running synthetic_tests.py against $color..."
    INGRESS_IP="$ingress_ip" CLUSTER_COLOR="$color" \
        python3 "${SCRIPT_DIR}/../python/synthetic_tests.py" 2>&1 \
        | tee -a "${LOG_DIR}/rotation.log" \
        || return 1

    return 0
}

case "${1:?Usage: $0 <provision|validate|switchover|destroy>}" in
    provision)
        NEXT=$(get_next_color)
        log "=== PROVISION: Creating $NEXT cluster ==="

        # Determine which IPs to use for this color
        # shellcheck source=/dev/null
        source "${CONFIG_DIR}/${NEXT}-hosts.conf"

        IMAGE_TAG=$(cat "${CONFIG_DIR}/current-image-tag") \
            || die "Cannot read current image tag"

        export IMAGE_TAG CLUSTER_COLOR="$NEXT"

        "${SCRIPT_DIR}/create_casino_cluster.sh" \
            "$NEXT" "${CONFIG_DIR}/${NEXT}-cluster.conf" \
            || die "Provisioning failed for $NEXT"

        # pending_color is what switchover checks before it will do anything, so
        # it is only set once the new cluster has passed validation. A cluster
        # that provisioned but cannot serve a login must not look ready.
        log "=== VALIDATE: $NEXT ==="
        if ! validate_cluster "$NEXT"; then
            set_state "provision_status" "validation-failed"
            die "Validation failed for the freshly provisioned $NEXT cluster. pending_color left unset, so tonight's switchover will refuse to run."
        fi

        set_state "pending_color" "$NEXT"
        set_state "provision_status" "ok"
        set_state "provision_completed_at" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
        log "=== PROVISION COMPLETE: $NEXT cluster ready and validated ==="
        ;;

    validate)
        TARGET=$(get_state "pending_color")
        [[ "$TARGET" != "null" && -n "$TARGET" ]] || TARGET=$(get_next_color)
        log "=== VALIDATE: $TARGET ==="
        validate_cluster "$TARGET" || die "Validation failed for $TARGET"
        log "=== VALIDATE COMPLETE: $TARGET passed ==="
        ;;

    switchover)
        CURRENT=$(get_current_color)
        NEXT=$(get_next_color)

        # Verify provisioning completed
        PENDING=$(get_state "pending_color")
        [[ "$PENDING" == "$NEXT" ]] \
            || die "Expected pending=$NEXT but state shows pending=$PENDING. Provision may have failed."

        log "=== SWITCHOVER: $CURRENT → $NEXT ==="

        "${SCRIPT_DIR}/switchover.sh" "$CURRENT" \
            || die "Switchover failed"

        # Update state
        set_state "active_color" "$NEXT"
        set_state "previous_color" "$CURRENT"
        set_state "switchover_completed_at" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

        log "=== SWITCHOVER COMPLETE: $NEXT is now active ==="
        ;;

    destroy)
        PREVIOUS=$(get_state "previous_color")
        [[ "$PREVIOUS" != "null" && -n "$PREVIOUS" ]] \
            || { log "No previous cluster to destroy"; exit 0; }

        # destroy_cluster.sh does not exist in this bundle. It is referenced here,
        # in the chapter's rotation timeline, and by casino-cluster-destroy.timer
        # (which additionally has no matching .service unit), but no copy of it is
        # in the repository. Say so instead of failing obscurely: an un-destroyed
        # cluster still holds the previous night's secrets and still has network
        # access to the shared player database, which is the entire thing daily
        # rotation is supposed to prevent.
        DESTROY_SCRIPT="${SCRIPT_DIR}/destroy_cluster.sh"
        if [[ ! -x "$DESTROY_SCRIPT" ]]; then
            set_state "destroy_status" "missing-script"
            die "Cannot tear down the $PREVIOUS cluster: ${DESTROY_SCRIPT} is missing or not executable. The $PREVIOUS cluster is STILL RUNNING with credentials from the last rotation and still reaches the shared database. previous_color stays set to $PREVIOUS so the next run retries."
        fi

        log "=== DESTROY: Tearing down $PREVIOUS cluster ==="

        # A failed teardown used to be logged as "WARN continuing" while
        # previous_color was reset to null, so the state file recorded a
        # successful destroy of a cluster that was still running, and nothing ever
        # retried. Teardown failure is now fatal and leaves the state alone.
        if ! "$DESTROY_SCRIPT" "$PREVIOUS" 2>&1 | tee -a "${LOG_DIR}/rotation.log"; then
            set_state "destroy_status" "failed"
            set_state "destroy_failed_at" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
            die "Teardown of the $PREVIOUS cluster FAILED. It may still be running with live credentials and database access. previous_color stays set to $PREVIOUS so the next run retries; investigate before the next rotation."
        fi

        set_state "destroy_status" "ok"
        set_state "previous_color" "null"
        set_state "destroy_completed_at" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

        log "=== DESTROY COMPLETE: $PREVIOUS cluster removed ==="
        ;;

    *)
        die "Unknown command: $1"
        ;;
esac
