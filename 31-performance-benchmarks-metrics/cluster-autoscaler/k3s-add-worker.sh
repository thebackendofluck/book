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
# k3s-add-worker.sh
# Creates a KVM VM and joins it to the K3s cluster as a worker node.
#
# Usage:
#   ./k3s-add-worker.sh --name worker-01 --ip 10.0.10.41 --cpu 16 --ram 32768
#   ./k3s-add-worker.sh --name worker-01 --ip 10.0.10.41  # uses defaults from config

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

# ─── Defaults from config ─────────────────────────────────────────────────
VM_NAME=""
VM_IP=""
VM_CPU="${WORKER_CPU:-16}"
VM_RAM="${WORKER_RAM:-32768}"
VM_DISK="${WORKER_DISK:-50}"
K3S_SERVER_URL="${K3S_SERVER_URL:-https://10.0.0.11:6443}"
K3S_TOKEN="${K3S_TOKEN:-}"
VM_IMAGE_DIR="${VM_IMAGE_DIR:-/nvme-0-zfs/vms}"
GOLDEN_IMAGE="${GOLDEN_IMAGE:-/nvme-0-zfs/vms/golden/noble-golden-generic.qcow2}"
BRIDGE="${BRIDGE:-br1}"
GATEWAY="${GATEWAY:-10.0.10.1}"
DNS="${DNS:-10.0.10.242,10.0.10.1,8.8.8.8}"
SSH_USER="${SSH_USER:-operator}"
SSH_PUB_KEY1="${SSH_PUB_KEY1:-}"
SSH_PUB_KEY2="${SSH_PUB_KEY2:-}"

usage() {
cat <<EOF
Usage: $0 [OPTIONS]

Required:
  --name NAME      VM/node name (e.g. worker-01)
  --ip IP          Static IP on 10.0.10.0/24

Optional:
  --cpu N          vCPU count (default: ${VM_CPU})
  --ram MB         RAM in MB (default: ${VM_RAM})
  --disk GB        OS disk size (default: ${VM_DISK})
  --token TOKEN    K3s join token (default: from config)
  --server URL     K3s server URL (default: ${K3S_SERVER_URL})
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)   VM_NAME="$2";        shift 2 ;;
        --ip)     VM_IP="$2";          shift 2 ;;
        --cpu)    VM_CPU="$2";         shift 2 ;;
        --ram)    VM_RAM="$2";         shift 2 ;;
        --disk)   VM_DISK="$2";        shift 2 ;;
        --token)  K3S_TOKEN="$2";      shift 2 ;;
        --server) K3S_SERVER_URL="$2"; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

[[ -z "$VM_NAME" ]]  && fail "--name is required"
[[ -z "$VM_IP" ]]    && fail "--ip is required"
[[ -z "$K3S_TOKEN" ]] && fail "K3S_TOKEN is required (set in config or --token)"

START_TIME=$(date +%s)
banner "Adding K3s Worker: ${VM_NAME} (${VM_IP})"
log "Config: ${VM_CPU} CPU, ${VM_RAM}MB RAM, ${VM_DISK}GB disk"

# ─── Preflight checks ─────────────────────────────────────────────────────
[[ ! -f "$GOLDEN_IMAGE" ]] && fail "Golden image not found: $GOLDEN_IMAGE"

if virsh list --all --name 2>/dev/null | grep -q "^${VM_NAME}$"; then
    fail "VM '${VM_NAME}' already exists. Remove it first with k3s-remove-worker.sh"
fi

if k3s kubectl get node "${VM_NAME}" &>/dev/null; then
    fail "Node '${VM_NAME}' already registered in cluster"
fi

# Check IP not already in use (quick ping test)
if ping -c 1 -W 1 "${VM_IP}" &>/dev/null; then
    fail "IP ${VM_IP} is already responding on the network"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Phase 1: Create VM disk
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 1: Create VM Disk"

OS_DISK_PATH="${VM_IMAGE_DIR}/images/${VM_NAME}-os.qcow2"
CLOUD_INIT_DIR="${VM_IMAGE_DIR}/files/${VM_NAME}-cloud-init"
CLOUD_INIT_ISO="${VM_IMAGE_DIR}/iso/${VM_NAME}-cloud-init.iso"

mkdir -p "${VM_IMAGE_DIR}/images" "${VM_IMAGE_DIR}/iso" "${VM_IMAGE_DIR}/files"

if [[ ! -f "$OS_DISK_PATH" ]]; then
    log "Cloning golden image -> OS disk (${VM_DISK}GB)..."
    qemu-img create -f qcow2 -b "$GOLDEN_IMAGE" -F qcow2 "$OS_DISK_PATH"
    GOLDEN_VSIZE_GB=$(qemu-img info "$GOLDEN_IMAGE" 2>/dev/null | awk '/virtual size/{gsub(/[^0-9.]/,"",$3); print int($3)}')
    if [[ "${VM_DISK}" -gt "${GOLDEN_VSIZE_GB:-50}" ]]; then
        qemu-img resize "$OS_DISK_PATH" "${VM_DISK}G"
    fi
    pass "OS disk created: $OS_DISK_PATH"
else
    warn "OS disk already exists: $OS_DISK_PATH"
fi

# ═══════════════════════════════════════════════════════════════════════════
# Phase 2: Cloud-init
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 2: Cloud-init Configuration"

mkdir -p "$CLOUD_INIT_DIR"

# Build SSH keys array for cloud-init
SSH_KEYS_YAML=""
[[ -n "$SSH_PUB_KEY1" ]] && SSH_KEYS_YAML+="      - ${SSH_PUB_KEY1}"$'\n'
[[ -n "$SSH_PUB_KEY2" ]] && SSH_KEYS_YAML+="      - ${SSH_PUB_KEY2}"

cat > "${CLOUD_INIT_DIR}/user-data" <<CLOUDINIT
#cloud-config
hostname: ${VM_NAME}
manage_etc_hosts: true
timezone: Europe/Amsterdam
system_info:
  default_user:
    name: ${SSH_USER}
    groups: [sudo, adm]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh_authorized_keys:
${SSH_KEYS_YAML}
ssh_pwauth: false
packages:
  - qemu-guest-agent
  - net-tools
  - curl
  - jq
  - vim
  - htop
  - iotop
  - sysstat
package_update: true
package_upgrade: false
write_files:
  - path: /etc/sysctl.d/99-k3s-worker.conf
    permissions: "0644"
    content: |
      net.core.somaxconn = 65535
      net.ipv4.tcp_max_syn_backlog = 65535
      net.ipv4.tcp_keepalive_time = 300
      net.ipv4.tcp_keepalive_intvl = 30
      net.ipv4.tcp_keepalive_probes = 10
      net.ipv4.ip_forward = 1
      net.bridge.bridge-nf-call-iptables = 1
      fs.file-max = 200000
      fs.inotify.max_user_instances = 8192
      fs.inotify.max_user_watches = 524288
      vm.swappiness = 1
      vm.max_map_count = 262144
  - path: /etc/security/limits.d/99-k3s.conf
    permissions: "0644"
    content: |
      * soft nofile 65535
      * hard nofile 65535
      * soft nproc 65535
      * hard nproc 65535
runcmd:
  - sysctl --system
  - systemctl enable qemu-guest-agent
  - systemctl start qemu-guest-agent
  - echo done > /tmp/cloud-init.done
CLOUDINIT

cat > "${CLOUD_INIT_DIR}/meta-data" <<META
instance-id: ${VM_NAME}
local-hostname: ${VM_NAME}
META

cat > "${CLOUD_INIT_DIR}/network-config" <<NETCONF
version: 2
ethernets:
  enp1s0:
    addresses: ["${VM_IP}/24"]
    gateway4: ${GATEWAY}
    nameservers:
      addresses: [${DNS// /}]
    dhcp4: false
NETCONF

if command -v cloud-localds &>/dev/null; then
    cloud-localds \
        --network-config="${CLOUD_INIT_DIR}/network-config" \
        "$CLOUD_INIT_ISO" \
        "${CLOUD_INIT_DIR}/user-data" \
        "${CLOUD_INIT_DIR}/meta-data"
else
    genisoimage -output "$CLOUD_INIT_ISO" -volid cidata -joliet -rock \
        "${CLOUD_INIT_DIR}/user-data" \
        "${CLOUD_INIT_DIR}/meta-data" \
        "${CLOUD_INIT_DIR}/network-config" 2>/dev/null
fi
pass "Cloud-init ISO: $CLOUD_INIT_ISO"

# ═══════════════════════════════════════════════════════════════════════════
# Phase 3: Create and boot VM
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 3: Create VM"

log "Creating VM with virt-install..."
virt-install \
    --connect=qemu:///system \
    --name="${VM_NAME}" \
    --ram="${VM_RAM}" \
    --vcpus="${VM_CPU}" \
    --cpu host \
    --os-variant=ubuntu24.04 \
    --virt-type=kvm --hvm --arch x86_64 \
    --autostart \
    --disk "path=${OS_DISK_PATH},device=disk,bus=virtio,cache=none,format=qcow2" \
    --network "bridge=${BRIDGE},model=virtio" \
    --cdrom="$CLOUD_INIT_ISO" \
    --graphics vnc \
    --console pty,target_type=serial \
    --noautoconsole \
    --boot hd,menu=on \
    --force

pass "VM '${VM_NAME}' created and booting"

# ═══════════════════════════════════════════════════════════════════════════
# Phase 4: Wait for SSH
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 4: Wait for SSH"

log "Waiting for VM to boot and cloud-init to complete..."
for i in $(seq 1 60); do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes \
           "${SSH_USER}@${VM_IP}" "test -f /tmp/cloud-init.done" 2>/dev/null; then
        pass "VM up and cloud-init complete"
        break
    fi
    [[ $(( i % 6 )) -eq 0 ]] && log "  ...waiting (${i}x5s = $((i*5))s)..."
    sleep 5
    if [[ $i -eq 60 ]]; then
        warn "VM unreachable after 5 minutes, attempting cleanup..."
        virsh destroy "${VM_NAME}" 2>/dev/null || true
        virsh undefine "${VM_NAME}" --remove-all-storage 2>/dev/null || true
        fail "VM ${VM_IP} unreachable after 5 minutes — rolled back"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════
# Phase 5: Install K3s agent
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 5: Install K3s Agent"

log "Installing K3s agent on ${VM_NAME}..."
# shellcheck disable=SC2087
ssh -o StrictHostKeyChecking=no "${SSH_USER}@${VM_IP}" "sudo bash -s" <<INSTALL_K3S
set -euo pipefail

# Install K3s agent
export INSTALL_K3S_SKIP_START=false
export K3S_URL="${K3S_SERVER_URL}"
export K3S_TOKEN="${K3S_TOKEN}"

curl -sfL https://get.k3s.io | INSTALL_K3S_CHANNEL=v1.35 sh -s - agent \
    --node-name "${VM_NAME}" \
    --node-label "autoscaler-managed=true" \
    --kubelet-arg="max-pods=110"

# Verify k3s-agent is running
systemctl is-active --quiet k3s-agent || {
    echo "ERROR: k3s-agent failed to start"
    journalctl -u k3s-agent --no-pager -n 20
    exit 1
}
echo "K3s agent installed and running"
INSTALL_K3S

pass "K3s agent installed on ${VM_NAME}"

# ═══════════════════════════════════════════════════════════════════════════
# Phase 6: Wait for node Ready
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 6: Wait for Node Ready"

log "Waiting for node '${VM_NAME}' to become Ready..."
for i in $(seq 1 60); do
    NODE_STATUS=$(k3s kubectl get node "${VM_NAME}" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || echo "NotFound")
    if [[ "$NODE_STATUS" == "True" ]]; then
        pass "Node '${VM_NAME}' is Ready"
        break
    fi
    [[ $(( i % 6 )) -eq 0 ]] && log "  ...waiting for Ready (${i}x5s, status: ${NODE_STATUS})..."
    sleep 5
    if [[ $i -eq 60 ]]; then
        warn "Node not ready after 5 min, attempting cleanup..."
        k3s kubectl delete node "${VM_NAME}" 2>/dev/null || true
        ssh -o StrictHostKeyChecking=no "${SSH_USER}@${VM_IP}" \
            "sudo /usr/local/bin/k3s-agent-uninstall.sh" 2>/dev/null || true
        virsh destroy "${VM_NAME}" 2>/dev/null || true
        virsh undefine "${VM_NAME}" --remove-all-storage 2>/dev/null || true
        fail "Node '${VM_NAME}' never became Ready — rolled back"
    fi
done

# ═══════════════════════════════════════════════════════════════════════════
# Phase 7: Label and verify
# ═══════════════════════════════════════════════════════════════════════════
banner "Phase 7: Label and Verify"

k3s kubectl label node "${VM_NAME}" node-role.kubernetes.io/worker=true --overwrite 2>/dev/null || true
k3s kubectl label node "${VM_NAME}" autoscaler-managed=true --overwrite
pass "Labels applied"

END_TIME=$(date +%s)
ELAPSED=$(( END_TIME - START_TIME ))

banner "Worker Node Added Successfully"
echo ""
k3s kubectl get node "${VM_NAME}" -o wide
echo ""
pass "Total time: ${ELAPSED}s ($(( ELAPSED / 60 ))m $(( ELAPSED % 60 ))s)"

# Log the scale event
EVENTS_LOG="${SCALE_EVENTS_LOG:-/var/log/k3s-autoscaler-events.log}"
echo "$(date '+%Y-%m-%d %H:%M:%S') SCALE_UP name=${VM_NAME} ip=${VM_IP} cpu=${VM_CPU} ram=${VM_RAM} elapsed=${ELAPSED}s" >> "$EVENTS_LOG" 2>/dev/null || true
