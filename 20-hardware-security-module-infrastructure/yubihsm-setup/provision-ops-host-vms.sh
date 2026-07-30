#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 20, Hardware Security Module Infrastructure.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2034  # Config and color constants
# provision-ops-host-vms.sh
# Create 3 VMs on the ops-host hypervisor (10.0.0.11) for the OpenBao cluster.
# VMs: bao-01, bao-02, bao-03 — 4 vCPU, 8 GB RAM, Ubuntu 24.04, br0 bridge.
#
# Run on the ops-host KVM hypervisor as root.
# Requires: virt-install, virsh, libvirt, Ubuntu 24.04 cloud image.
#
# Sizing reference (from igaming-master-docs.html):
#   OpenBao nodes: 3 × 4 vCPU, 8 GB RAM, 100 GB SSD — 99.95% SLA

set -euo pipefail

LOG_FILE="/var/log/provision-bao-vms.log"

# ── Configuration ──────────────────────────────────────────────────────────────
HYPERVISOR_IP="10.0.0.11"
NETWORK_BRIDGE="br0"
NETWORK_PREFIX="10.0.10"   # VMs get .11, .12, .13
GATEWAY="${NETWORK_PREFIX}.1"
DNS_SERVERS="1.1.1.1,8.8.8.8"
NETMASK="24"

VM_VCPUS=4
VM_RAM_MB=8192
VM_DISK_GB=100
VM_OS_VARIANT="ubuntu24.04"

UBUNTU_IMAGE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
IMAGE_CACHE_DIR="/var/lib/libvirt/images/cache"
BASE_IMAGE="${IMAGE_CACHE_DIR}/ubuntu-24.04-cloud.img"
VM_IMAGE_DIR="/var/lib/libvirt/images"

SSH_PUBKEY_FILE="${HOME}/.ssh/id_ed25519.pub"
NTP_SERVER="pool.ntp.org"

declare -A VM_IPS
VM_IPS["bao-01"]="${NETWORK_PREFIX}.11"
VM_IPS["bao-02"]="${NETWORK_PREFIX}.12"
VM_IPS["bao-03"]="${NETWORK_PREFIX}.13"

# ── Logging helpers ────────────────────────────────────────────────────────────
log()  { echo "[$(date -Is)] INFO  $*" | tee -a "$LOG_FILE"; }
warn() { echo "[$(date -Is)] WARN  $*" | tee -a "$LOG_FILE"; }
die()  { echo "[$(date -Is)] ERROR $*" | tee -a "$LOG_FILE"; exit 1; }

# ── Preflight ──────────────────────────────────────────────────────────────────
preflight() {
    log "=== Preflight checks on hypervisor ${HYPERVISOR_IP} ==="
    [[ $EUID -eq 0 ]] || die "Must run as root"

    command -v virsh        >/dev/null 2>&1 || die "virsh not found — install libvirt"
    command -v virt-install >/dev/null 2>&1 || die "virt-install not found"
    command -v qemu-img     >/dev/null 2>&1 || die "qemu-img not found"
    command -v cloud-localds >/dev/null 2>&1 || \
        apt-get install -y cloud-image-utils || \
        warn "cloud-localds not found — will use alternative method"

    # Verify bridge exists
    ip link show "$NETWORK_BRIDGE" >/dev/null 2>&1 || \
        die "Network bridge $NETWORK_BRIDGE not found. Create it first."

    # Check SSH public key
    if [[ ! -f "$SSH_PUBKEY_FILE" ]]; then
        warn "SSH public key not found at $SSH_PUBKEY_FILE"
        warn "VMs will be provisioned without SSH key — change cloud-init user-data"
    fi

    log "Preflight passed"
}

# ── Download Ubuntu 24.04 cloud image ─────────────────────────────────────────
download_base_image() {
    log "=== Downloading Ubuntu 24.04 cloud image ==="
    mkdir -p "$IMAGE_CACHE_DIR"

    if [[ -f "$BASE_IMAGE" ]]; then
        log "Base image already cached: $BASE_IMAGE"
        return 0
    fi

    log "Downloading: $UBUNTU_IMAGE_URL"
    wget --progress=bar:force:noscroll \
        -O "${BASE_IMAGE}.tmp" \
        "$UBUNTU_IMAGE_URL"
    mv "${BASE_IMAGE}.tmp" "$BASE_IMAGE"
    log "Base image downloaded: $BASE_IMAGE"
}

# ── Generate cloud-init user-data for a VM ────────────────────────────────────
generate_cloud_init() {
    local vm_name="$1"
    local vm_ip="$2"
    local tmpdir
    tmpdir=$(mktemp -d)

    local ssh_authorized_keys=""
    if [[ -f "$SSH_PUBKEY_FILE" ]]; then
        ssh_authorized_keys="    - $(cat "$SSH_PUBKEY_FILE")"
    fi

    # user-data: cloud-config
    cat > "${tmpdir}/user-data" << USERDATA_EOF
#cloud-config
hostname: ${vm_name}
fqdn: ${vm_name}.vm.internal

users:
  - name: ubuntu
    sudo: ALL=(ALL) NOPASSWD:ALL
    groups: users, admin
    shell: /bin/bash
    ssh_authorized_keys:
${ssh_authorized_keys}

# Disable root password login
disable_root: true
ssh_pwauth: false

# Install required packages
packages:
  - curl
  - wget
  - gnupg
  - apt-transport-https
  - ca-certificates
  - python3
  - cryptsetup
  - openssl
  - jq
  - htop
  - vim
  - unattended-upgrades

package_update: true
package_upgrade: true

# Kernel hardening (PCI DSS Req. 2)
write_files:
  - path: /etc/sysctl.d/99-igaming.conf
    content: |
      # Disable swap — PCI DSS + performance (no secrets on disk)
      vm.swappiness = 0
      # Network hardening
      net.ipv4.tcp_syncookies = 1
      net.ipv4.conf.all.rp_filter = 1
      net.ipv4.conf.default.rp_filter = 1
      net.ipv4.conf.all.accept_redirects = 0
      net.ipv4.conf.all.send_redirects = 0
      net.ipv6.conf.all.disable_ipv6 = 1
      # Performance tuning
      net.core.somaxconn = 65535
      net.ipv4.tcp_max_syn_backlog = 65535
      fs.file-max = 1000000

  - path: /etc/security/limits.d/igaming.conf
    content: |
      *    soft nofile 65536
      *    hard nofile 65536
      root soft nofile 65536
      root hard nofile 65536

  - path: /etc/openbao/.gitkeep
    content: ""

runcmd:
  # Apply sysctl settings
  - sysctl --system
  # Disable swap permanently
  - swapoff -a
  - sed -i '/swap/d' /etc/fstab
  # Set timezone
  - timedatectl set-timezone UTC
  # Create openbao user
  - useradd --system --home /opt/openbao --shell /usr/sbin/nologin openbao || true
  # Create directories
  - mkdir -p /opt/openbao/{data,tls,config} /var/log/openbao
  - chown -R openbao:openbao /opt/openbao /var/log/openbao
  # Set permissions for openbao config dir
  - chmod 700 /opt/openbao/data
  # Copy CA cert placeholder (will be replaced by setup scripts)
  - mkdir -p /etc/openbao
  - echo "PLACEHOLDER - replace with actual CA cert from bao-01" > /etc/openbao/ca.crt

final_message: "Cloud-init complete for ${vm_name} at ${vm_ip}"

power_state:
  mode: reboot
  message: "Rebooting after cloud-init provisioning"
  timeout: 30
USERDATA_EOF

    # network-config: static IP
    cat > "${tmpdir}/network-config" << NETCFG_EOF
version: 2
ethernets:
  ens3:
    addresses:
      - ${vm_ip}/${NETMASK}
    routes:
      - to: default
        via: ${GATEWAY}
    nameservers:
      addresses: [${DNS_SERVERS}]
NETCFG_EOF

    # meta-data
    cat > "${tmpdir}/meta-data" << METADATA_EOF
instance-id: ${vm_name}-$(date +%s)
local-hostname: ${vm_name}
METADATA_EOF

    # Create cloud-init ISO
    local iso_path="${VM_IMAGE_DIR}/${vm_name}-cloud-init.iso"
    if command -v cloud-localds &>/dev/null; then
        cloud-localds \
            --network-config="${tmpdir}/network-config" \
            "$iso_path" \
            "${tmpdir}/user-data" \
            "${tmpdir}/meta-data"
    else
        # Fallback: use genisoimage
        genisoimage -output "$iso_path" \
            -volid cidata -joliet -rock \
            "${tmpdir}/user-data" "${tmpdir}/meta-data" "${tmpdir}/network-config" \
            2>/dev/null || \
        die "Neither cloud-localds nor genisoimage found"
    fi

    rm -rf "$tmpdir"
    echo "$iso_path"
}

# ── Create a single VM ────────────────────────────────────────────────────────
create_vm() {
    local vm_name="$1"
    local vm_ip="$2"

    log "=== Creating VM: ${vm_name} (IP: ${vm_ip}) ==="

    # Check if VM already exists
    if virsh dominfo "$vm_name" &>/dev/null; then
        log "VM ${vm_name} already exists — skipping"
        return 0
    fi

    # Create VM disk (thin provisioned qcow2 based on cloud image)
    local disk_path="${VM_IMAGE_DIR}/${vm_name}.qcow2"
    if [[ ! -f "$disk_path" ]]; then
        qemu-img create \
            -f qcow2 \
            -b "$BASE_IMAGE" \
            -F qcow2 \
            "$disk_path" \
            "${VM_DISK_GB}G"
        log "Disk created: $disk_path (${VM_DISK_GB}G, thin provisioned)"
    fi

    # Generate cloud-init ISO
    local cloudinit_iso
    cloudinit_iso=$(generate_cloud_init "$vm_name" "$vm_ip")
    log "Cloud-init ISO: $cloudinit_iso"

    # Define and start the VM
    virt-install \
        --name "$vm_name" \
        --vcpus "$VM_VCPUS" \
        --memory "$VM_RAM_MB" \
        --cpu host-passthrough \
        --disk "path=${disk_path},format=qcow2,bus=virtio,cache=writeback" \
        --disk "path=${cloudinit_iso},device=cdrom,bus=sata" \
        --network "bridge=${NETWORK_BRIDGE},model=virtio" \
        --os-variant "$VM_OS_VARIANT" \
        --graphics none \
        --console pty,target_type=serial \
        --noautoconsole \
        --import \
        --boot hd,cdrom

    log "VM ${vm_name} created and starting"

    # Wait for VM to get to a running state
    local attempts=0
    while ! virsh dominfo "$vm_name" | grep -q "running"; do
        sleep 2
        (( attempts++ ))
        [[ $attempts -lt 30 ]] || { warn "VM ${vm_name} did not start in 60s"; break; }
    done

    log "VM ${vm_name} is running (IP: ${vm_ip})"
}

# ── Wait for VMs to finish cloud-init ─────────────────────────────────────────
wait_for_vms() {
    log "=== Waiting for VMs to complete cloud-init provisioning ==="
    log "This may take 3-5 minutes for package installation..."

    for vm_name in bao-01 bao-02 bao-03; do
        local vm_ip="${VM_IPS[$vm_name]}"
        local attempts=0

        log "Waiting for SSH on ${vm_name} (${vm_ip})..."
        until ssh -o StrictHostKeyChecking=no \
                  -o ConnectTimeout=5 \
                  -o BatchMode=yes \
                  "ubuntu@${vm_ip}" "echo ready" &>/dev/null; do
            sleep 5
            (( attempts++ ))
            [[ $attempts -lt 60 ]] || { warn "SSH timeout for ${vm_name}"; break; }
        done
        log "${vm_name}: SSH accessible"
    done
}

# ── Print post-provision instructions ─────────────────────────────────────────
print_next_steps() {
    cat << 'EOF'

==========================================================================
VM PROVISIONING COMPLETE — Next Steps
==========================================================================
VMs created:
  bao-01: 10.0.10.11  (primary — YubiHSM 2 node)
  bao-02: 10.0.10.12  (standby)
  bao-03: 10.0.10.13  (standby)

1. SSH access (from ops-host hypervisor):
   ssh ubuntu@10.0.10.11
   ssh ubuntu@10.0.10.12
   ssh ubuntu@10.0.10.13

2. Add /etc/hosts entries on all nodes:
   echo "10.0.10.11 bao-01" >> /etc/hosts
   echo "10.0.10.12 bao-02" >> /etc/hosts
   echo "10.0.10.13 bao-03" >> /etc/hosts

3. Physically connect YubiHSM 2 to the server running bao-01.

4. On bao-01: run setup-yubihsm-connector.sh
   HSM_PIN=<new-pin> bash setup-yubihsm-connector.sh

5. On ALL nodes: run setup-openbao-cluster.sh
   NODE_ID=bao-01 NODE_IP=10.0.10.11 BAO_HSM_PIN=<pin> \
     bash setup-openbao-cluster.sh

6. Initialize cluster (on bao-01 only):
   NODE_ID=bao-01 NODE_IP=10.0.10.11 BAO_HSM_PIN=<pin> \
     BAO_TOKEN=<root-token> bash setup-openbao-cluster.sh --init

7. SAVE recovery keys from /tmp/bao-init.json in two physical safes.
   Then: shred -u /tmp/bao-init.json
==========================================================================
EOF
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    exec > >(tee -a "$LOG_FILE") 2>&1
    log "=== ops-host VM Provisioning Start ($(date)) ==="
    log "Hypervisor: ${HYPERVISOR_IP}"
    log "Network: ${NETWORK_BRIDGE} (${NETWORK_PREFIX}.0/${NETMASK})"

    preflight
    download_base_image

    for vm_name in bao-01 bao-02 bao-03; do
        create_vm "$vm_name" "${VM_IPS[$vm_name]}"
    done

    if [[ "${1:-}" == "--wait" ]]; then
        wait_for_vms
    fi

    print_next_steps
    log "=== Provisioning complete ==="
}

main "$@"
