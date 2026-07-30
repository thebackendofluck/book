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

# switchover.sh — execute the blue-to-green (or green-to-blue) switchover
#
# Sequence, and why it is in this order:
#
#   1. preflight_checks        cheap gate: pods up, health endpoint answers
#   2. run_full_validation     the real gate: login, games, wallet, WebSocket,
#                              TLS expiry (pre_switch_validation.sh) and a full
#                              synthetic player journey (synthetic_tests.py)
#   3. handover_lease          old colour releases the primary lease, we wait out
#                              the lease TTL, new colour claims it
#   4. haproxy_switch          raise the new colour's weight, then drop the old
#   5. verify_traffic          confirm players are being served by the new colour
#   6. drain_old_cluster       cordon and let WebSockets close
#
# Steps 3 and 4 are in that order because a colour must own the right to write
# player balances before it receives a single bet. Between the release in step 3
# and the claim completing, no cluster is primary and wallet writes fail closed;
# that window is LEASE_TTL_SECONDS plus a few seconds and it is the price of
# single-writer semantics. See sql/cluster_registry.sql.
#
# Any failure from step 4 onwards restores the HAProxy weights before exiting,
# via the EXIT trap. Exiting with the old backend drained and the new one
# unverified would leave production with neither side taking new connections.
#
# Required environment:
#   CASINO_DB_URL             libpq URL for the shared player database. Must come
#                             from an EnvironmentFile or Vault, never a command
#                             line: ps and journald both leak arguments.
#   SYNTHETIC_TEST_PASSWORD   credential for the synthetic player, from Vault.
#   ALERT_WEBHOOK_URL         optional Slack webhook.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HAPROXY_SOCKET="${HAPROXY_SOCKET:-/run/haproxy/admin.sock}"
LOG_DIR="${CASINO_LOG_DIR:-/var/log/casino}"
LOG_FILE="${LOG_DIR}/switchover-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$LOG_DIR"
ALERT_WEBHOOK="${ALERT_WEBHOOK_URL:-}"
KUBE_DIR="${CASINO_KUBE_DIR:-/root/.kube}"
ACTIVE_CLUSTER_FILE="${CASINO_ACTIVE_CLUSTER_FILE:-/etc/haproxy/active-cluster}"

# Where provisioning leaves per-cluster facts. Note that create_casino_cluster.sh
# still writes the ingress IP to /tmp, which does not survive systemd's
# PrivateTmp=true: the provision and switchover units each get their own /tmp, so
# the switchover cannot see a file the provisioner wrote there. Prefer a real
# state directory and keep /tmp only as a fallback for manual runs.
CASINO_STATE_DIR="${CASINO_STATE_DIR:-/var/lib/casino}"

# Must match sql/cluster_registry.sql and the wallet service's lease TTL.
LEASE_TTL_SECONDS="${WALLET_LEASE_TTL_SECONDS:-20}"
HANDOVER_GRACE_SECONDS="${HANDOVER_GRACE_SECONDS:-3}"

# How far the cutover got. The EXIT trap uses this to decide whether production
# is currently in a state that needs undoing.
SWITCH_STATE="pristine"   # pristine | weights-changed | committed | rolled-back
declare -A SAVED_WEIGHT=()

log() {
    local level="$1"; shift
    local msg="$*"
    local ts; ts="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "${ts} [${level}] ${msg}" | tee -a "$LOG_FILE"
}

alert_slack() {
    [[ -z "$ALERT_WEBHOOK" ]] && return 0
    curl -sf -X POST "$ALERT_WEBHOOK" \
        -H 'Content-Type: application/json' \
        -d "{\"text\": \"[casino-switchover] $*\"}" || true
}

die() {
    log ERROR "$*"
    alert_slack "CRITICAL: Switchover failed: $*"
    # Rollback is the EXIT trap's job, so that an unexpected set -e abort is
    # cleaned up the same way an explicit die is.
    exit 1
}

on_exit() {
    local rc=$?
    if [[ "$SWITCH_STATE" == "weights-changed" ]]; then
        log ERROR "Exiting (rc=${rc}) with HAProxy weights already changed — rolling back."
        rollback_switch
    fi
    return "$rc"
}
trap on_exit EXIT
trap 'die "interrupted by signal"' INT TERM

haproxy_cmd() {
    echo "$*" | socat stdio "$HAPROXY_SOCKET"
}

db_query() {
    psql "$CASINO_DB_URL" --no-psqlrc --quiet --tuples-only --no-align \
        --set=ON_ERROR_STOP=1 --command "$1"
}

require_tools() {
    local tool
    for tool in socat psql kubectl curl jq python3; do
        command -v "$tool" >/dev/null 2>&1 || die "Required tool not found: $tool"
    done
}

require_env() {
    local name
    for name in "$@"; do
        [[ -n "${!name:-}" ]] || die "Required environment variable is unset: $name"
    done
}

# ── determine direction ───────────────────────────────────────────────────────

CURRENT_ACTIVE="${1:?Usage: $0 <blue|green>}"
[[ "$CURRENT_ACTIVE" == "blue" || "$CURRENT_ACTIVE" == "green" ]] \
    || { echo "Cluster colour must be blue or green, got: $CURRENT_ACTIVE" >&2; exit 2; }
[[ "$CURRENT_ACTIVE" == "blue" ]] && NEW_ACTIVE="green" || NEW_ACTIVE="blue"

log INFO "=== Switchover: $CURRENT_ACTIVE → $NEW_ACTIVE ==="

kubeconfig_for() {
    local color="$1"
    local path="${KUBE_DIR}/casino-${color}.yaml"
    [[ -r "$path" ]] || return 1
    printf '%s' "$path"
}

ingress_ip_for() {
    local color="$1" candidate
    for candidate in "${CASINO_STATE_DIR}/casino-${color}-ingress-ip" \
                     "/tmp/casino-${color}-ingress-ip"; do
        if [[ -r "$candidate" ]]; then
            tr -d '[:space:]' < "$candidate"
            return 0
        fi
    done
    return 1
}

# ── pre-flight: cheap checks before we spend ten minutes on validation ────────

preflight_checks() {
    log INFO "Running pre-flight checks on $NEW_ACTIVE cluster..."

    local kubeconfig
    kubeconfig=$(kubeconfig_for "$NEW_ACTIVE") \
        || die "Kubeconfig not found or not readable: ${KUBE_DIR}/casino-${NEW_ACTIVE}.yaml"
    export KUBECONFIG="$kubeconfig"

    # All pods running. grep -c exits 1 on no match, which is the good case here.
    local not_running
    not_running=$(kubectl get pods -n casino-prod --no-headers 2>/dev/null \
        | grep -vc 'Running\|Completed' || true)
    [[ "${not_running:-0}" -eq 0 ]] || die "$not_running pods not running in $NEW_ACTIVE cluster"

    local ingress_ip
    ingress_ip=$(ingress_ip_for "$NEW_ACTIVE") \
        || die "Ingress IP file not found for $NEW_ACTIVE (looked in $CASINO_STATE_DIR and /tmp)"

    local health_response
    health_response=$(curl -sf --max-time 10 \
        -H "Host: casino.internal" \
        "http://${ingress_ip}/health" 2>/dev/null | jq -r '.status') \
        || die "Health check request failed for $NEW_ACTIVE"

    [[ "$health_response" == "ok" ]] \
        || die "Health check returned non-ok status: $health_response"

    log INFO "Pre-flight checks passed for $NEW_ACTIVE"
}

# ── the real gate ─────────────────────────────────────────────────────────────

# Both of these scripts existed and were called by nothing. The nightly rotation
# was gating a live traffic cut on a pod count, one health curl and one balance
# read, while a seven-check smoke suite and a full synthetic player journey sat
# unused in the same directory. A non-zero exit from either aborts the switchover
# before any weight is touched.
run_full_validation() {
    local color="$1"
    local ingress_ip
    ingress_ip=$(ingress_ip_for "$color") || die "Ingress IP file not found for $color"

    log INFO "Running pre_switch_validation.sh against $color..."
    "${SCRIPT_DIR}/pre_switch_validation.sh" "$color" 2>&1 | tee -a "$LOG_FILE" \
        || die "pre_switch_validation.sh failed for $color — refusing to switch traffic"

    log INFO "Running synthetic_tests.py against $color..."
    INGRESS_IP="$ingress_ip" CLUSTER_COLOR="$color" \
        python3 "${SCRIPT_DIR}/../python/synthetic_tests.py" 2>&1 | tee -a "$LOG_FILE" \
        || die "synthetic_tests.py failed for $color — refusing to switch traffic"

    log INFO "Full validation passed for $color"
}

# ── primary lease handover ────────────────────────────────────────────────────

lease_holder() {
    db_query "SELECT cluster_color FROM current_primary_lease()" 2>/dev/null | tr -d '[:space:]'
}

# Ask a colour's wallet pods to claim the lease, then confirm in the database.
#
# Confirmation is a query, not "kubectl logs | grep -c 'Claimed primary status'".
# A log grep matches pods of any age, matches the previous rotation's line if the
# window overlaps, and reports success when kubectl itself fails. The registry is
# the thing that actually decides who may write, so ask it.
claim_primary() {
    local color="$1"
    local kubeconfig
    kubeconfig=$(kubeconfig_for "$color") || { log ERROR "No kubeconfig for $color"; return 1; }

    log INFO "Asking $color to claim the primary lease..."
    KUBECONFIG="$kubeconfig" kubectl set env deployment/wallet-service \
        -n casino-prod CLAIM_PRIMARY=true || return 1

    # A restart is needed even when CLAIM_PRIMARY is already true: a pod that lost
    # the lease stays read-only by design and never re-claims on its own. There is
    # deliberately no automatic re-claim in the wallet service, because a pod that
    # steals a lapsed lease on its own would start writing on a colour HAProxy is
    # no longer sending traffic to.
    KUBECONFIG="$kubeconfig" kubectl rollout restart deployment/wallet-service \
        -n casino-prod || return 1
    KUBECONFIG="$kubeconfig" kubectl rollout status deployment/wallet-service \
        -n casino-prod --timeout=180s || return 1

    local holder i
    for i in $(seq 1 20); do
        holder=$(lease_holder || true)
        if [[ "$holder" == "$color" ]]; then
            log INFO "Primary lease confirmed for $color (attempt $i)"
            return 0
        fi
        sleep 3
    done

    log ERROR "$color did not take the primary lease (registry still shows: '${holder:-none}')"
    return 1
}

# Move the right to write player balances from one colour to the other.
handover_lease() {
    local from="$1" to="$2"

    log INFO "Releasing primary lease from $from..."
    db_query "SELECT release_primary_cluster('${from}')" >/dev/null \
        || { log ERROR "Could not release the primary lease from $from"; return 1; }

    # release_primary_cluster expires the lease in the database immediately, so
    # the fence rejects stale writes from this instant. A pod that has not yet
    # noticed can still believe it is primary until its own deadline passes, so
    # wait that out before letting the other colour claim.
    local wait_s=$(( LEASE_TTL_SECONDS + HANDOVER_GRACE_SECONDS ))
    log INFO "Waiting ${wait_s}s for any $from lease holder to time out (wallet writes fail closed)..."
    sleep "$wait_s"

    claim_primary "$to"
}

# ── haproxy switchover ────────────────────────────────────────────────────────

set_weight() {
    local backend="$1" server="$2" weight="$3"
    haproxy_cmd "set weight ${backend}/${server} ${weight}" >/dev/null
}

snapshot_weights() {
    local backend server raw
    for backend in casino_active casino_ws_active; do
        for server in blue green; do
            # "get weight" answers e.g. "100 (initial 100)"
            raw=$(haproxy_cmd "get weight ${backend}/${server}") \
                || die "Cannot read weight of ${backend}/${server} from HAProxy"
            raw="${raw%% *}"
            [[ "$raw" =~ ^[0-9]+$ ]] \
                || die "Unexpected weight for ${backend}/${server}: '${raw}'"
            SAVED_WEIGHT["${backend}/${server}"]="$raw"
        done
    done
    log INFO "Saved HAProxy weights: $(
        for backend in casino_active casino_ws_active; do
            for server in blue green; do
                printf '%s=%s ' "${backend}/${server}" "${SAVED_WEIGHT[${backend}/${server}]}"
            done
        done)"
}

restore_weights() {
    local backend failed=0
    # Raise the previously active colour first. Restoring in the other order would
    # pass through a moment with both servers at weight 0, which is a backend with
    # no eligible server and a 503 for everyone in it.
    for backend in casino_active casino_ws_active; do
        set_weight "$backend" "$CURRENT_ACTIVE" \
            "${SAVED_WEIGHT[${backend}/${CURRENT_ACTIVE}]}" || failed=1
    done
    for backend in casino_active casino_ws_active; do
        set_weight "$backend" "$NEW_ACTIVE" \
            "${SAVED_WEIGHT[${backend}/${NEW_ACTIVE}]}" || failed=1
    done
    return "$failed"
}

rollback_switch() {
    log WARN "ROLLBACK: restoring HAProxy weights so $CURRENT_ACTIVE takes new connections again"

    if restore_weights; then
        SWITCH_STATE="rolled-back"
        log INFO "ROLLBACK: HAProxy weights restored to $CURRENT_ACTIVE"
        alert_slack "Switchover rolled back: traffic restored to $CURRENT_ACTIVE"
    else
        log ERROR "ROLLBACK FAILED: could not restore HAProxy weights via $HAPROXY_SOCKET"
        alert_slack "CRITICAL: HAProxy weight rollback FAILED. Production may be serving no backend. Restore by hand: for b in casino_active casino_ws_active; do echo \"set weight \$b/${CURRENT_ACTIVE} 100\" | socat stdio ${HAPROXY_SOCKET}; done"
        # Deliberately keep going: the lease also has to go back, and an operator
        # needs both facts in the same alert stream.
    fi

    # Traffic is back on the old colour, so the old colour has to be able to write
    # again. Without this, players reach a cluster whose wallet writes are fenced.
    log WARN "ROLLBACK: handing the primary lease back to $CURRENT_ACTIVE"
    if handover_lease "$NEW_ACTIVE" "$CURRENT_ACTIVE"; then
        log INFO "ROLLBACK: $CURRENT_ACTIVE holds the primary lease again"
    else
        log ERROR "ROLLBACK: $CURRENT_ACTIVE does NOT hold the primary lease. Wallet writes are refused platform-wide until an operator intervenes."
        alert_slack "CRITICAL: rollback restored traffic to ${CURRENT_ACTIVE} but it does not hold the primary lease. Wallet writes are failing closed. Run: SELECT * FROM current_primary_lease();"
    fi
}

haproxy_switch() {
    log INFO "Executing HAProxy switchover: $CURRENT_ACTIVE → $NEW_ACTIVE..."

    snapshot_weights

    # Everything from here on is undoable only by restore_weights.
    SWITCH_STATE="weights-changed"

    # Raise the incoming colour BEFORE draining the outgoing one. The reverse
    # order leaves both servers at weight 0 for as long as the socket round trip
    # takes, and a backend with no eligible server returns 503.
    set_weight casino_active    "$NEW_ACTIVE" 100 || die "Cannot raise weight of $NEW_ACTIVE"
    set_weight casino_ws_active "$NEW_ACTIVE" 100 || die "Cannot raise ws weight of $NEW_ACTIVE"

    set_weight casino_active    "$CURRENT_ACTIVE" 0 || die "Cannot drain $CURRENT_ACTIVE"
    set_weight casino_ws_active "$CURRENT_ACTIVE" 0 || die "Cannot drain ws $CURRENT_ACTIVE"

    # Give HAProxy a moment to settle before reading stats back
    sleep 2

    local backend_stats status
    backend_stats=$(haproxy_cmd "show stat" | grep "^casino_active,${NEW_ACTIVE}," | head -1) \
        || die "No HAProxy stats row for casino_active/${NEW_ACTIVE}"
    status=$(echo "$backend_stats" | cut -d',' -f18)
    [[ "$status" == "UP" ]] || die "New backend $NEW_ACTIVE not UP in HAProxy stats: $status"

    echo "$NEW_ACTIVE" > "$ACTIVE_CLUSTER_FILE"

    log INFO "HAProxy switchover complete. Active: $NEW_ACTIVE"
}

# ── verify traffic routing ────────────────────────────────────────────────────

verify_traffic() {
    log INFO "Verifying traffic is routing to $NEW_ACTIVE..."

    local vip_ip="${CASINO_VIP:-10.0.10.5}"
    local attempts=5
    local success=0
    local i served_by

    for i in $(seq 1 "$attempts"); do
        served_by=$(curl -sf --max-time 5 \
            "http://${vip_ip}/health" \
            -H "Host: casino.internal" 2>/dev/null \
            | jq -r '.cluster' 2>/dev/null) || served_by=""

        if [[ "$served_by" == "$NEW_ACTIVE" ]]; then
            success=$((success + 1))
        fi
        sleep 1
    done

    # die() exits, and the EXIT trap restores the weights we changed above.
    [[ "$success" -ge 3 ]] \
        || die "Only $success/$attempts health checks confirmed routing to $NEW_ACTIVE"

    log INFO "Traffic verification passed: $success/$attempts requests served by $NEW_ACTIVE"
}

# ── drain old cluster ─────────────────────────────────────────────────────────

drain_old_cluster() {
    log INFO "Draining $CURRENT_ACTIVE cluster (allowing existing connections to close)..."

    local kubeconfig
    if ! kubeconfig=$(kubeconfig_for "$CURRENT_ACTIVE"); then
        log WARN "No kubeconfig for $CURRENT_ACTIVE, skipping drain"
        return 0
    fi
    export KUBECONFIG="$kubeconfig"

    kubectl cordon -l "cluster=$CURRENT_ACTIVE" 2>/dev/null \
        || log WARN "Could not cordon $CURRENT_ACTIVE nodes; teardown will proceed anyway"

    local drain_wait="${DRAIN_WAIT_SECONDS:-300}"
    log INFO "Waiting ${drain_wait}s for WebSocket connections to drain from $CURRENT_ACTIVE..."
    sleep "$drain_wait"

    log INFO "$CURRENT_ACTIVE drain complete"
}

# ── main ─────────────────────────────────────────────────────────────────────

main() {
    local start_time; start_time=$(date +%s)

    require_tools
    require_env CASINO_DB_URL SYNTHETIC_TEST_PASSWORD

    local holder
    holder=$(lease_holder) || die "Cannot query the primary lease at CASINO_DB_URL"
    log INFO "Primary lease currently held by: ${holder:-none}"

    alert_slack "Starting switchover: $CURRENT_ACTIVE → $NEW_ACTIVE"

    preflight_checks
    run_full_validation "$NEW_ACTIVE"

    handover_lease "$CURRENT_ACTIVE" "$NEW_ACTIVE" \
        || die "Primary lease handover to $NEW_ACTIVE failed; traffic never moved"

    haproxy_switch
    verify_traffic

    SWITCH_STATE="committed"

    local switch_time=$(( $(date +%s) - start_time ))
    log INFO "=== Switchover completed in ${switch_time}s ==="
    alert_slack "Switchover complete: $NEW_ACTIVE is now active (${switch_time}s). Draining $CURRENT_ACTIVE."

    # Foreground, not "drain_old_cluster &". The switchover runs from a
    # Type=oneshot systemd unit, and the default KillMode=control-group kills
    # whatever is left in the cgroup the moment the main process exits — so a
    # backgrounded drain was killed mid-sleep every night and "drain complete"
    # was never reached. TimeoutStartSec=900 leaves room for the 300s wait.
    drain_old_cluster

    log INFO "Drain finished. Teardown runs from the destroy timer."
}

main "$@"
