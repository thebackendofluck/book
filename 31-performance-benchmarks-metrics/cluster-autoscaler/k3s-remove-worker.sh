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
# k3s-remove-worker.sh
# Gracefully removes a worker node from the K3s cluster and destroys the VM.
#
# Usage:
#   ./k3s-remove-worker.sh --name worker-01
#   ./k3s-remove-worker.sh --name worker-01 --keep-vm   # remove from cluster but keep VM

set -euo pipefail

# ─── Colours ───────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONF_FILE="${K3S_AUTOSCALER_CONF:-/etc/k3s-autoscaler.conf}"
[[ -f "$CONF_FILE" ]] && source "$CONF_FILE"
[[ -f "${SCRIPT_DIR}/k3s-autoscaler.conf" ]] && source "${SCRIPT_DIR}/k3s-autoscaler.conf"

LOG_FILE="${LOG_FILE:-/var/log/k3s-autoscaler.log}"
mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || LOG_FILE="/tmp/k3s-autoscaler.log"

log()  { local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"; echo -e "${BLUE}[..]${NC} [$ts] $*" | tee -a "$LOG_FILE"; }
pass() { local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"; echo -e "${GREEN}[OK]${NC} [$ts] $*" | tee -a "$LOG_FILE"; }
fail() { local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"; echo -e "${RED}[ERR]${NC} [$ts] $*" | tee -a "$LOG_FILE" >&2; exit 1; }
warn() { local ts; ts="$(date '+%Y-%m-%d %H:%M:%S')"; echo -e "${YELLOW}[!!]${NC} [$ts] $*" | tee -a "$LOG_FILE"; }
banner() {
    echo -e "\n${BOLD}══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD} $*${NC}" | tee -a "$LOG_FILE"
    echo -e "${BOLD}══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
}

VM_NAME=""
KEEP_VM=0
SSH_USER="${SSH_USER:-operator}"

usage() {
cat <<EOF
Usage: $0 [OPTIONS]

Required:
  --name NAME      Node/VM name to remove

Optional:
  --keep-vm        Remove from K3s cluster but keep the VM running
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)    VM_NAME="$2"; shift 2 ;;
        --keep-vm) KEEP_VM=1;    shift ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

[[ -z "$VM_NAME" ]] && fail "--name is required"

START_TIME=$(date +%s)
banner "Removing K3s Worker: ${VM_NAME}"

# ═══════════════════════════════════════════════════════════════════════════
# Phase 1: Cordon node (prevent new pods)
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 1: Cordon Node"

if k3s kubectl get node "${VM_NAME}" &>/dev/null; then
    log "Cordoning node '${VM_NAME}'..."
    k3s kubectl cordon "${VM_NAME}"
    pass "Node cordoned — no new pods will be scheduled"
else
    warn "Node '${VM_NAME}' not found in cluster — skipping cordon/drain"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Phase 2: Drain node (evict pods)
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 2: Drain Node"

if k3s kubectl get node "${VM_NAME}" &>/dev/null; then
    log "Draining node '${VM_NAME}' (timeout: 120s)..."
    log "Active pods on node before drain:"
    k3s kubectl get pods --all-namespaces --field-selector="spec.nodeName=${VM_NAME}" \
        --no-headers 2>/dev/null | tee -a "$LOG_FILE" || true

    if k3s kubectl drain "${VM_NAME}" \
        --ignore-daemonsets \
        --delete-emptydir-data \
        --force \
        --grace-period=60 \
        --timeout=120s 2>&1 | tee -a "$LOG_FILE"; then
        pass "Node drained successfully"
    else
        warn "Drain had errors (some pods may have been force-evicted)"
    fi

    # Verify pods have migrated
    log "Waiting 10s for pod migrations to settle..."
    sleep 10
    REMAINING=$(k3s kubectl get pods --all-namespaces \
        --field-selector="spec.nodeName=${VM_NAME}" --no-headers 2>/dev/null \
        | grep -cv "kube-system" || echo "0")
    if [[ "$REMAINING" -gt 0 ]]; then
        warn "${REMAINING} non-system pods still on node (DaemonSets expected)"
    else
        pass "All non-system pods migrated"
    fi
fi

# ═══════════════════════════════════════════════════════════════════════════
# Phase 3: Remove from K3s cluster
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 3: Remove from Cluster"

if k3s kubectl get node "${VM_NAME}" &>/dev/null; then
    log "Deleting node '${VM_NAME}' from cluster..."
    k3s kubectl delete node "${VM_NAME}" --timeout=30s
    pass "Node removed from cluster"
else
    warn "Node already absent from cluster"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Phase 4: Uninstall K3s agent on VM
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 4: Uninstall K3s Agent"

# Get the VM IP from virsh or cluster info
VM_IP=""
VM_IP=$(virsh domifaddr "${VM_NAME}" 2>/dev/null | awk '/ipv4/{gsub(/\/[0-9]+/,"",$4); print $4}' | head -1)

if [[ -z "$VM_IP" ]]; then
    # Try to find IP from cloud-init config
    NETCONF="${VM_IMAGE_DIR:-/nvme-0-zfs/vms}/files/${VM_NAME}-cloud-init/network-config"
    if [[ -f "$NETCONF" ]]; then
        VM_IP=$(grep -oP '\d+\.\d+\.\d+\.\d+' "$NETCONF" | head -1)
    fi
fi

if [[ -n "$VM_IP" ]]; then
    log "Uninstalling K3s agent on ${VM_IP}..."
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes \
           "${SSH_USER}@${VM_IP}" "sudo /usr/local/bin/k3s-agent-uninstall.sh" 2>/dev/null; then
        pass "K3s agent uninstalled"
    else
        warn "Could not SSH to ${VM_IP} to uninstall agent (VM may already be down)"
    fi
else
    warn "Could not determine VM IP — skipping agent uninstall"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Phase 5: Destroy VM
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 5: Destroy VM"

if [[ $KEEP_VM -eq 1 ]]; then
    warn "Keeping VM '${VM_NAME}' as requested (--keep-vm)"
else
    if virsh list --all --name 2>/dev/null | grep -q "^${VM_NAME}$"; then
        log "Stopping VM '${VM_NAME}'..."
        virsh destroy "${VM_NAME}" 2>/dev/null || true
        sleep 2

        log "Removing VM definition and storage..."
        virsh undefine "${VM_NAME}" --remove-all-storage 2>/dev/null || {
            warn "virsh undefine --remove-all-storage failed, trying manual cleanup"
            virsh undefine "${VM_NAME}" 2>/dev/null || true
            # Clean up disk files manually
            rm -f "${VM_IMAGE_DIR:-/nvme-0-zfs/vms}/images/${VM_NAME}-os.qcow2"
        }

        # Clean up cloud-init artifacts
        rm -rf "${VM_IMAGE_DIR:-/nvme-0-zfs/vms}/files/${VM_NAME}-cloud-init" 2>/dev/null || true
        rm -f "${VM_IMAGE_DIR:-/nvme-0-zfs/vms}/iso/${VM_NAME}-cloud-init.iso" 2>/dev/null || true

        pass "VM '${VM_NAME}' destroyed and storage cleaned"
    else
        warn "VM '${VM_NAME}' not found in libvirt — nothing to destroy"
    fi
fi

# Remove SSH known_hosts entry
if [[ -n "$VM_IP" ]]; then
    ssh-keygen -R "${VM_IP}" 2>/dev/null || true
    ssh-keygen -R "${VM_NAME}" 2>/dev/null || true
fi

END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))

banner "Worker Node Removed"
pass "Total time: ${ELAPSED}s ($(( ELAPSED / 60 ))m $(( ELAPSED % 60 ))s)"

# Log scale event
EVENTS_LOG="${SCALE_EVENTS_LOG:-/var/log/k3s-autoscaler-events.log}"
echo "$(date '+%Y-%m-%d %H:%M:%S') SCALE_DOWN name=${VM_NAME} elapsed=${ELAPSED}s" >> "$EVENTS_LOG" 2>/dev/null || true
