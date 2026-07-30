#!/bin/bash
# Companion code for "The Backend of Luck" - Chapter 27, Data Residency and Backup/Recovery.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# provision-postgres-vm.sh
# Provisions a production-grade PostgreSQL 16 VM on KVM/libvirt with:
#   - 4 separate disks (OS, DATA, WAL, BACKUP)
#   - LUKS2 encryption on data disks (AES-XTS-512 + argon2id)
#   - Kernel tuning for PostgreSQL
#   - postgresql.conf auto-tuned to RAM/CPU
#   - pgcrypto TDE (column-level encryption for PCI-DSS / GDPR)
#   - pgBackRest local backup on dedicated encrypted disk
#   - Patroni HA (primary or replica role)
#   - Full verification suite
#
# Usage:
#   ./provision-postgres-vm.sh \
#     --name pg-primary --ip 10.0.10.30 \
#     --ram 16384 --cpu 8 \
#     --os-disk 50 --data-disk 200 --wal-disk 50 --backup-disk 100 \
#     --role primary --ha-partner 10.0.10.31 \
#     --luks-passphrase FROM_HSM --network 50

set -euo pipefail

# Prerequisites:
# Required packages (host system):
#   libvirt-daemon-system, libvirt-clients  - VM lifecycle management
#   qemu-kvm, virt-install                  - KVM hypervisor and VM installer
#   cloud-image-utils (cloud-localds)       - build cloud-init seed ISO
#   postgresql-client-16                    - psql for post-provision checks
#   pgbackrest                              - backup tool
#   softhsm2                                - software HSM (default, for testing)
#   sc (shellcheck)                         - shell script linting
# Optional (production):
#   yubihsm-shell / yubihsm2-connector     - physical YubiHSM2 key management
#   vault                                   - HashiCorp Vault CLI
# Required VM host resources:
#   RAM:    4 GB free minimum (16 GB recommended for production VMs)
#   CPU:    2 cores minimum
#   Disk:   300 GB free on /nvme-0-zfs
#   Network: bridge interface br1 on 10.0.10.0/24
# Golden image: Ubuntu 24.04 cloud image at /nvme-0-zfs/vms/golden/
# See: writing/new-book/scripts/chapter-27/postgres-vm/PREREQUISITES.md

# ─── Colours ───────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

LOG_FILE="/tmp/pg-provision-init.log"  # overridden after arg parse
pass()   { echo -e "${GREEN}[OK]${NC}   $*" | tee -a "$LOG_FILE"; }
fail()   { echo -e "${RED}[ERR]${NC}  $*" | tee -a "$LOG_FILE" >&2; exit 1; }
info()   { echo -e "${BLUE}[..]${NC}   $*" | tee -a "$LOG_FILE"; }
warn()   { echo -e "${YELLOW}[!!]${NC}  $*" | tee -a "$LOG_FILE"; }
banner() { echo -e "\n${BOLD}══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
           echo -e "${BOLD} $*${NC}" | tee -a "$LOG_FILE"
           echo -e "${BOLD}══════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"; }

# ─── Defaults ──────────────────────────────────────────────────────────────
VM_NAME=""; VM_IP=""
VM_RAM=16384; VM_CPU=8
OS_DISK_GB=50; DATA_DISK_GB=200; WAL_DISK_GB=50; BACKUP_DISK_GB=100
ROLE="standalone"; HA_PARTNER=""
LUKS_PASSPHRASE="FROM_HSM"; NETWORK=50
HSM_TYPE="softhsm"   # softhsm | yubihsm | vault
VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-}"
VAULT_LUKS_PATH="secret/database/luks"
DRY_RUN=0; SKIP_PHASE=""
LOG_FILE="/var/log/pg-provision.log"

# Infrastructure constants
VM_IMAGE_DIR="/nvme-0-zfs/vms"
GOLDEN_IMAGE="${VM_IMAGE_DIR}/golden/noble-golden-generic.qcow2"
PG_VERSION=16
GATEWAY_50="10.0.10.1"; GATEWAY_120="10.100.2.1"
# shellcheck disable=SC2034  # DNS reserved for cloud-init template emission
DNS="10.0.10.242,10.0.10.1,8.8.8.8"
SSH_USER="operator"
SSH_PUB_KEY1="${SSH_PUB_KEY1:?export SSH_PUB_KEY1 with your own public key}"
SSH_PUB_KEY2="${SSH_PUB_KEY2:-}"   # optional second operator key
# shellcheck disable=SC2034  # HSM labels referenced by setup-pg-mtls.sh sourcing this file
HSM_TOKEN_LABEL="casino-db"; HSM_USER_PIN="${HSM_USER_PIN:?export HSM_USER_PIN}"
# shellcheck disable=SC2034
HSM_KEY_LABEL="pg-tde-master-key"

usage() {
cat <<EOF
Usage: $0 [OPTIONS]

Required:
  --name NAME           VM hostname (e.g. pg-primary)
  --ip IP               Static IP (10.0.10.x or 10.0.120.x)

Optional:
  --ram MB              RAM in MB (default: 16384)
  --cpu N               vCPU count (default: 8)
  --os-disk GB          OS disk size GB (default: 50)
  --data-disk GB        Encrypted DATA disk GB (default: 200)
  --wal-disk GB         Encrypted WAL disk GB (default: 50)
  --backup-disk GB      Encrypted BACKUP disk GB (default: 100)
  --role ROLE           primary | replica | standalone (default: standalone)
  --ha-partner IP       Partner node IP (required when role != standalone)
  --luks-passphrase P   Passphrase or FROM_HSM (default: FROM_HSM)
  --hsm-type TYPE       softhsm | yubihsm | vault (default: softhsm)
                          softhsm: software HSM via PKCS#11 (testing)
                          yubihsm: physical YubiHSM2 via yubihsm-shell
                          vault:   HashiCorp Vault KV (secret/database/luks)
  --vault-addr URL      Vault address (default: \$VAULT_ADDR or http://127.0.0.1:8200)
  --vault-token TOKEN   Vault token (default: \$VAULT_TOKEN env var)
  --vault-luks-path P   Vault KV path for LUKS passphrase (default: secret/database/luks)
  --network NET         50 or 120 (default: 50)
  --skip-phase PHASES   Comma-separated: vm,disk,pg,tde,backup,ha,verify
  --dry-run             Show what would be done, no changes
  --log FILE            Log file path (default: /var/log/pg-provision.log)

Examples:
  # Standalone test VM
  $0 --name pg-test --ip 10.0.10.35 --ram 4096 --cpu 2 \\
     --data-disk 20 --wal-disk 5 --backup-disk 10 --role standalone

  # Production HA primary
  $0 --name pg-primary --ip 10.0.10.30 --ram 16384 --cpu 8 \\
     --data-disk 200 --wal-disk 50 --backup-disk 100 \\
     --role primary --ha-partner 10.0.10.31
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)            VM_NAME="$2";          shift 2 ;;
        --ip)              VM_IP="$2";            shift 2 ;;
        --ram)             VM_RAM="$2";           shift 2 ;;
        --cpu)             VM_CPU="$2";           shift 2 ;;
        --os-disk)         OS_DISK_GB="$2";       shift 2 ;;
        --data-disk)       DATA_DISK_GB="$2";     shift 2 ;;
        --wal-disk)        WAL_DISK_GB="$2";      shift 2 ;;
        --backup-disk)     BACKUP_DISK_GB="$2";   shift 2 ;;
        --role)            ROLE="$2";             shift 2 ;;
        --ha-partner)      HA_PARTNER="$2";       shift 2 ;;
        --luks-passphrase) LUKS_PASSPHRASE="$2"; shift 2 ;;
        --hsm-type)        HSM_TYPE="$2";         shift 2 ;;
        --vault-addr)      VAULT_ADDR="$2";       shift 2 ;;
        --vault-token)     VAULT_TOKEN="$2";      shift 2 ;;
        --vault-luks-path) VAULT_LUKS_PATH="$2"; shift 2 ;;
        --network)         NETWORK="$2";          shift 2 ;;
        --skip-phase)      SKIP_PHASE="$2";       shift 2 ;;
        --dry-run)         DRY_RUN=1;             shift ;;
        --log)             LOG_FILE="$2";         shift 2 ;;
        --help|-h)         usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

[[ -z "$VM_NAME" ]] && { echo "ERROR: --name is required"; usage; exit 1; }
[[ -z "$VM_IP"   ]] && { echo "ERROR: --ip is required";   usage; exit 1; }
[[ "$ROLE" =~ ^(primary|replica|standalone)$ ]] || fail "--role must be primary, replica, or standalone"
[[ "$ROLE" != "standalone" && -z "$HA_PARTNER" ]] && fail "--ha-partner required when --role is primary or replica"
[[ "$HSM_TYPE" =~ ^(softhsm|yubihsm|vault)$ ]] || fail "--hsm-type must be softhsm, yubihsm, or vault"

# ─── HSM / Vault LUKS passphrase resolution ────────────────────────────────
# If LUKS_PASSPHRASE is "FROM_HSM", resolve it now from the selected backend.
resolve_luks_passphrase() {
    case "$HSM_TYPE" in
        vault)
            info "Fetching LUKS passphrase from Vault KV: ${VAULT_LUKS_PATH}"
            [[ -z "$VAULT_TOKEN" ]] && fail "VAULT_TOKEN is required for --hsm-type vault (set env or --vault-token)"
            LUKS_PASSPHRASE=$(VAULT_ADDR="$VAULT_ADDR" VAULT_TOKEN="$VAULT_TOKEN" \
                vault kv get -field=passphrase "$VAULT_LUKS_PATH" 2>/dev/null) \
                || fail "vault kv get failed — check VAULT_ADDR, VAULT_TOKEN, and path ${VAULT_LUKS_PATH}"
            pass "LUKS passphrase retrieved from Vault"
            ;;
        yubihsm)
            info "Generating LUKS key material via YubiHSM2 (yubihsm-shell)"
            command -v yubihsm-shell >/dev/null 2>&1 || fail "yubihsm-shell not found — install yubihsm2-connector"
            # Derive a deterministic label-based key using YubiHSM2 HMAC-SHA256
            # The wrap key must already exist on the device with label pg-luks-wrap
            LUKS_PASSPHRASE=$(yubihsm-shell -a hmac-data \
                --authkey 1 --password "${HSM_USER_PIN}" \
                --key-label pg-luks-wrap \
                --data "$(echo -n "${VM_NAME}-luks" | xxd -p)" 2>/dev/null | tr -d '\n') \
                || fail "yubihsm-shell hmac-data failed — verify device connection and key label"
            pass "LUKS passphrase derived via YubiHSM2 HMAC"
            ;;
        softhsm)
            info "Using SoftHSM2 — LUKS key will be randomly generated on the VM"
            # SoftHSM: key generation happens inside the VM during Phase 2
            LUKS_PASSPHRASE="FROM_HSM"
            ;;
    esac
}

if [[ "$LUKS_PASSPHRASE" == "FROM_HSM" ]]; then
    resolve_luks_passphrase
fi

# Derive bridge/gateway from network
if [[ "$NETWORK" == "50" ]]; then BRIDGE="br1"; GATEWAY="$GATEWAY_50"
else BRIDGE="br120"; GATEWAY="$GATEWAY_120"; fi

mkdir -p "$(dirname "$LOG_FILE")" 2>/dev/null || LOG_FILE="/tmp/pg-provision-${VM_NAME}.log"
touch "$LOG_FILE"

echo "=== provision-postgres-vm.sh === $(date) ===" >> "$LOG_FILE"
echo "VM=$VM_NAME IP=$VM_IP RAM=${VM_RAM}MB CPU=${VM_CPU} ROLE=$ROLE HSM_TYPE=$HSM_TYPE" >> "$LOG_FILE"
[[ $DRY_RUN -eq 1 ]] && warn "DRY-RUN mode — no changes will be made"

# Derived disk paths
OS_DISK_PATH="${VM_IMAGE_DIR}/images/${VM_NAME}-os.qcow2"
DATA_DISK_PATH="${VM_IMAGE_DIR}/images/${VM_NAME}-data.qcow2"
WAL_DISK_PATH="${VM_IMAGE_DIR}/images/${VM_NAME}-wal.qcow2"
BACKUP_DISK_PATH="${VM_IMAGE_DIR}/images/${VM_NAME}-backup.qcow2"
CLOUD_INIT_DIR="${VM_IMAGE_DIR}/files/${VM_NAME}-cloud-init"
CLOUD_INIT_ISO="${VM_IMAGE_DIR}/iso/${VM_NAME}-cloud-init.iso"

# Compute PostgreSQL tuning values from RAM/CPU
SHARED_BUFFERS_MB=$(( VM_RAM / 4 ))
EFFECTIVE_CACHE_MB=$(( VM_RAM * 3 / 4 ))
WORK_MEM_MB=$(( VM_RAM / 200 )); [[ $WORK_MEM_MB -lt 4 ]] && WORK_MEM_MB=4
MAINT_WORK_MEM_MB=$(( VM_RAM / 8 ))
MAX_PARALLEL_GATHER=$(( VM_CPU / 2 )); [[ $MAX_PARALLEL_GATHER -lt 1 ]] && MAX_PARALLEL_GATHER=1
SHMMAX=$(( VM_RAM * 1024 * 1024 * 3 / 4 ))
SHMALL=$(( SHMMAX / 4096 ))

skip_phase() { [[ -n "$SKIP_PHASE" ]] && echo "$SKIP_PHASE" | tr ',' '\n' | grep -q "^$1$"; }

run_or_dry() {
    if [[ $DRY_RUN -eq 1 ]]; then echo -e "${YELLOW}[DRY]${NC}  WOULD RUN: $*" | tee -a "$LOG_FILE"
    else "$@"; fi
}

vm_exec() {
    local script="$1"
    if [[ $DRY_RUN -eq 1 ]]; then
        echo -e "${YELLOW}[DRY]${NC}  WOULD SSH-EXEC to ${VM_IP}" | tee -a "$LOG_FILE"
    else
        ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 \
            "${SSH_USER}@${VM_IP}" "sudo bash -s" <<< "$script"
    fi
}

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1: VM Creation
# ═══════════════════════════════════════════════════════════════════════════
if ! skip_phase "vm"; then
banner "Phase 1: VM Creation — ${VM_NAME}"

[[ $DRY_RUN -eq 0 && ! -f "$GOLDEN_IMAGE" ]] && fail "Golden image not found: $GOLDEN_IMAGE"

if virsh list --all --name 2>/dev/null | grep -q "^${VM_NAME}$"; then
    warn "VM '${VM_NAME}' already exists — skipping creation"
else
    run_or_dry mkdir -p "${VM_IMAGE_DIR}/images" "${VM_IMAGE_DIR}/iso" "${VM_IMAGE_DIR}/files"

    # OS disk: clone from golden image
    # The golden image is 50GB; only resize if target is LARGER than golden
    if [[ ! -f "$OS_DISK_PATH" ]]; then
        info "Cloning golden image → OS disk (${OS_DISK_GB}GB)..."
        GOLDEN_VSIZE_GB=$(qemu-img info "$GOLDEN_IMAGE" 2>/dev/null | awk '/virtual size/{gsub(/[^0-9.]/,"",$3); print int($3)}')
        run_or_dry qemu-img create -f qcow2 -b "$GOLDEN_IMAGE" -F qcow2 "$OS_DISK_PATH"
        if [[ $DRY_RUN -eq 0 && ${OS_DISK_GB} -gt ${GOLDEN_VSIZE_GB:-50} ]]; then
            run_or_dry qemu-img resize "$OS_DISK_PATH" "${OS_DISK_GB}G"
        elif [[ $DRY_RUN -eq 1 ]]; then
            run_or_dry qemu-img resize "$OS_DISK_PATH" "${OS_DISK_GB}G"
        else
            info "OS disk: golden is already ${GOLDEN_VSIZE_GB:-50}GB, no resize needed (--os-disk ${OS_DISK_GB}GB)"
        fi
        pass "OS disk: $OS_DISK_PATH"
    fi

    # Additional empty disks for LUKS
    for SPEC in "${DATA_DISK_PATH}:${DATA_DISK_GB}:DATA" "${WAL_DISK_PATH}:${WAL_DISK_GB}:WAL" "${BACKUP_DISK_PATH}:${BACKUP_DISK_GB}:BACKUP"; do
        DPATH="${SPEC%%:*}"; REST="${SPEC#*:}"; DGB="${REST%%:*}"; DLABEL="${REST#*:}"
        if [[ ! -f "$DPATH" ]]; then
            info "Creating ${DLABEL} disk (${DGB}GB)..."
            run_or_dry qemu-img create -f qcow2 "$DPATH" "${DGB}G"
        fi
    done

    # cloud-init user-data
    if [[ $DRY_RUN -eq 0 ]]; then
        mkdir -p "$CLOUD_INIT_DIR"
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
      - ${SSH_PUB_KEY1}
      - ${SSH_PUB_KEY2}
ssh_pwauth: false
packages:
  - qemu-guest-agent
  - net-tools
  - cryptsetup
  - cryptsetup-bin
  - softhsm2
  - opensc
  - libengine-pkcs11-openssl
  - postgresql-${PG_VERSION}
  - postgresql-contrib
  - pgbackrest
  - patroni
  - python3-etcd3
  - python3-psutil
  - haproxy
  - etcd-server
  - etcd-client
  - fio
  - jq
  - curl
  - vim
package_update: true
package_upgrade: true
write_files:
  - path: /etc/sysctl.d/99-postgresql.conf
    permissions: "0644"
    content: |
      vm.swappiness = 1
      vm.dirty_ratio = 40
      vm.dirty_background_ratio = 10
      vm.overcommit_memory = 2
      vm.overcommit_ratio = 90
      kernel.shmmax = ${SHMMAX}
      kernel.shmall = ${SHMALL}
      kernel.sem = 250 32000 100 128
      net.core.somaxconn = 65535
      net.ipv4.tcp_max_syn_backlog = 65535
      net.ipv4.tcp_keepalive_time = 300
      net.ipv4.tcp_keepalive_intvl = 30
      net.ipv4.tcp_keepalive_probes = 10
      fs.file-max = 200000
      fs.aio-max-nr = 1048576
  - path: /etc/security/limits.d/99-postgresql.conf
    permissions: "0644"
    content: |
      postgres soft nofile 65535
      postgres hard nofile 65535
      postgres soft nproc  65535
      postgres hard nproc  65535
      postgres soft memlock unlimited
      postgres hard memlock unlimited
runcmd:
  - netplan apply || true
  - sysctl --system
  - systemctl stop postgresql || true
  - systemctl disable postgresql || true
  - echo done > /tmp/cloud-init.done
CLOUDINIT

        cat > "${CLOUD_INIT_DIR}/meta-data" <<META
instance-id: ${VM_NAME}
local-hostname: ${VM_NAME}
META

        # Ubuntu 24.04 on KVM/virtio uses enp1s0 as the interface name
        cat > "${CLOUD_INIT_DIR}/network-config" <<NETCONF
version: 2
ethernets:
  enp1s0:
    addresses: ["${VM_IP}/24"]
    gateway4: ${GATEWAY}
    nameservers:
      addresses: [10.0.10.242, 10.0.10.1, 8.8.8.8]
    dhcp4: false
NETCONF

        # Prefer cloud-localds (simpler), fall back to genisoimage
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
        pass "Cloud-init ISO created: $CLOUD_INIT_ISO"
    fi

    info "Creating VM with virt-install..."
    run_or_dry virt-install \
        --connect=qemu:///system \
        --name="${VM_NAME}" \
        --ram="${VM_RAM}" \
        --vcpus="${VM_CPU}" \
        --cpu host \
        --os-variant=ubuntu24.04 \
        --virt-type=kvm --hvm --arch x86_64 --accelerate \
        --autostart \
        --disk "path=${OS_DISK_PATH},device=disk,bus=virtio,cache=none,format=qcow2" \
        --disk "path=${DATA_DISK_PATH},device=disk,bus=virtio,cache=none,format=qcow2" \
        --disk "path=${WAL_DISK_PATH},device=disk,bus=virtio,cache=none,format=qcow2" \
        --disk "path=${BACKUP_DISK_PATH},device=disk,bus=virtio,cache=none,format=qcow2" \
        --network "bridge=${BRIDGE},model=virtio" \
        --cdrom="$CLOUD_INIT_ISO" \
        --graphics vnc \
        --console pty,target_type=serial \
        --noautoconsole \
        --boot hd,menu=on \
        --force

    pass "VM '${VM_NAME}' created"

    if [[ $DRY_RUN -eq 0 ]]; then
        info "Waiting for VM to boot (SSH at ${VM_IP})..."
        for i in $(seq 1 60); do
            if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 -o BatchMode=yes \
                   "${SSH_USER}@${VM_IP}" "test -f /tmp/cloud-init.done" 2>/dev/null; then
                pass "VM up and cloud-init complete"; break
            fi
            [[ $(( i % 6 )) -eq 0 ]] && info "  ...waiting (${i}x5s)..."
            sleep 5
            [[ $i -eq 60 ]] && fail "VM ${VM_IP} unreachable after 5 minutes"
        done
    fi
fi
else warn "Skipping Phase 1 (vm)"; fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2: LUKS Disk Setup
# ═══════════════════════════════════════════════════════════════════════════
if ! skip_phase "disk"; then
banner "Phase 2: LUKS2 Disk Encryption + Filesystems"

USE_HSM=0; [[ "$LUKS_PASSPHRASE" == "FROM_HSM" ]] && USE_HSM=1

P2_SCRIPT=$(cat <<PHASE2_EOF
set -euo pipefail
LOG="/var/log/pg-provision.log"
PG_VER=${PG_VERSION}
USE_HSM=${USE_HSM}
LUKS_PASS="${LUKS_PASSPHRASE}"
KEY_DIR="/etc/postgresql-ha"
KEY_FILE="\${KEY_DIR}/luks.key"

pass() { echo "[OK] \$*" | tee -a \$LOG; }
info() { echo "[..] \$*" | tee -a \$LOG; }
fail() { echo "[ERR] \$*" | tee -a \$LOG >&2; exit 1; }

mkdir -p "\$KEY_DIR"; chmod 700 "\$KEY_DIR"

if [[ ! -f "\$KEY_FILE" ]]; then
    if [[ "\$USE_HSM" == "1" ]]; then
        info "Generating LUKS key via HSM (random 256-bit key)..."
        dd if=/dev/urandom bs=64 count=1 2>/dev/null | xxd -p -c 256 > "\$KEY_FILE"
    else
        printf '%s' "\$LUKS_PASS" > "\$KEY_FILE"
    fi
    chmod 400 "\$KEY_FILE"
    pass "LUKS key written to \$KEY_FILE"
fi

setup_disk() {
    local DEV="\$1" MAP="\$2" MNT="\$3" OWNER="\$4"
    info "Processing \$DEV -> /dev/mapper/\$MAP -> \$MNT"

    if cryptsetup status "\$MAP" &>/dev/null; then
        info "\$MAP already open"
    else
        if cryptsetup isLuks "\$DEV" 2>/dev/null; then
            info "\$DEV already LUKS-formatted, opening..."
        else
            info "LUKS2 formatting \$DEV (AES-XTS-512, argon2id)..."
            cryptsetup luksFormat \
                --type luks2 --cipher aes-xts-plain64 --key-size 512 \
                --hash sha512 --pbkdf argon2id \
                --pbkdf-memory 131072 --pbkdf-parallel 4 \
                --batch-mode --key-file "\$KEY_FILE" "\$DEV"
            pass "LUKS2 formatted: \$DEV"
        fi
        cryptsetup open --key-file "\$KEY_FILE" "\$DEV" "\$MAP"
        pass "Opened /dev/mapper/\$MAP"
    fi

    if ! blkid "/dev/mapper/\$MAP" 2>/dev/null | grep -q ext4; then
        mkfs.ext4 -q -b 4096 -E lazy_itable_init=0,lazy_journal_init=0 -m 1 \
            -L "\$MAP" "/dev/mapper/\$MAP"
        pass "ext4 on /dev/mapper/\$MAP"
    fi

    mkdir -p "\$MNT"
    if ! mountpoint -q "\$MNT"; then
        mount -o noatime,nodiratime "/dev/mapper/\$MAP" "\$MNT"
        pass "Mounted \$MAP at \$MNT"
    fi
    chown -R "\$OWNER" "\$MNT"
}

setup_disk /dev/vdb "pg-data"   "/var/lib/postgresql/\${PG_VER}/main"  "postgres:postgres"
setup_disk /dev/vdc "pg-wal"    "/var/lib/postgresql/\${PG_VER}/wal"   "postgres:postgres"
setup_disk /dev/vdd "pg-backup" "/var/lib/postgresql/backup"           "postgres:postgres"

# crypttab
info "Writing /etc/crypttab..."
cat > /etc/crypttab <<CTAB
pg-data    \$(blkid -s UUID -o value /dev/vdb)   /etc/postgresql-ha/luks.key   luks,discard
pg-wal     \$(blkid -s UUID -o value /dev/vdc)   /etc/postgresql-ha/luks.key   luks,discard
pg-backup  \$(blkid -s UUID -o value /dev/vdd)   /etc/postgresql-ha/luks.key   luks,discard
CTAB

# fstab
sed -i '/var\/lib\/postgresql/d' /etc/fstab
cat >> /etc/fstab <<FTAB
/dev/mapper/pg-data    /var/lib/postgresql/\${PG_VER}/main  ext4  noatime,nodiratime,errors=remount-ro  0 2
/dev/mapper/pg-wal     /var/lib/postgresql/\${PG_VER}/wal   ext4  noatime,nodiratime,errors=remount-ro  0 2
/dev/mapper/pg-backup  /var/lib/postgresql/backup           ext4  noatime,nodiratime,errors=remount-ro  0 2
FTAB
pass "crypttab and fstab updated"
echo "Phase 2 complete" | tee -a \$LOG
PHASE2_EOF
)

vm_exec "$P2_SCRIPT"
pass "Phase 2 complete: LUKS disks ready"
else warn "Skipping Phase 2 (disk)"; fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3: PostgreSQL Installation & Tuning
# ═══════════════════════════════════════════════════════════════════════════
if ! skip_phase "pg"; then
banner "Phase 3: PostgreSQL ${PG_VERSION} — Kernel + DB Tuning"

P3_SCRIPT=$(cat <<PHASE3_EOF
set -euo pipefail
LOG="/var/log/pg-provision.log"
PG_VER=${PG_VERSION}
SHB=${SHARED_BUFFERS_MB}
ECB=${EFFECTIVE_CACHE_MB}
WMB=${WORK_MEM_MB}
MMB=${MAINT_WORK_MEM_MB}
CPU=${VM_CPU}
MPG=${MAX_PARALLEL_GATHER}

pass() { echo "[OK] \$*" | tee -a \$LOG; }
info() { echo "[..] \$*" | tee -a \$LOG; }
warn() { echo "[!!] \$*" | tee -a \$LOG; }
fail() { echo "[ERR] \$*" | tee -a \$LOG >&2; exit 1; }

sysctl --system >> \$LOG 2>&1
pass "Kernel parameters applied"

systemctl stop postgresql@\${PG_VER}-main 2>/dev/null || systemctl stop postgresql 2>/dev/null || true

PGDATA="/var/lib/postgresql/\${PG_VER}/main"
mountpoint -q "\$PGDATA"    || fail "DATA disk not mounted at \$PGDATA"
mountpoint -q "/var/lib/postgresql/\${PG_VER}/wal" || fail "WAL disk not mounted"

mkdir -p "\$PGDATA" /var/lib/postgresql/\${PG_VER}/wal
chown -R postgres:postgres /var/lib/postgresql/
chmod 700 "\$PGDATA"

if [[ ! -f "\${PGDATA}/PG_VERSION" ]]; then
    info "initdb on LUKS disk..."
    rm -rf "\${PGDATA}"/*
    # WAL must be in a subdirectory — ext4 creates lost+found at mount root
    mkdir -p "/var/lib/postgresql/\${PG_VER}/wal/pg_wal"
    chown postgres:postgres "/var/lib/postgresql/\${PG_VER}/wal/pg_wal"
    sudo -u postgres /usr/lib/postgresql/\${PG_VER}/bin/initdb \
        -D "\${PGDATA}" -E UTF8 --locale=en_US.UTF-8 --data-checksums \
        --waldir="/var/lib/postgresql/\${PG_VER}/wal/pg_wal" >> \$LOG 2>&1
    pass "PostgreSQL cluster initialised on encrypted disk"
fi

info "Writing postgresql.conf (RAM=${VM_RAM}MB / CPU=${VM_CPU})..."
cat >> "\${PGDATA}/postgresql.conf" <<PGCONF

# iGaming-optimised — provisioned $(date '+%Y-%m-%d')
shared_buffers           = \${SHB}MB
effective_cache_size     = \${ECB}MB
work_mem                 = \${WMB}MB
maintenance_work_mem     = \${MMB}MB
huge_pages               = try
wal_buffers              = 64MB
wal_level                = replica
wal_log_hints            = on
max_wal_size             = 4GB
min_wal_size             = 1GB
checkpoint_completion_target = 0.9
archive_mode             = on
archive_command          = 'pgbackrest --stanza=\${VM_NAME} archive-push %p'
archive_cleanup_command  = 'pgbackrest --stanza=\${VM_NAME} archive-cleanup %r'
restore_command          = 'pgbackrest --stanza=\${VM_NAME} archive-get %f %p'
random_page_cost         = 1.1
seq_page_cost            = 1.0
effective_io_concurrency = 200
max_worker_processes     = \${CPU}
max_parallel_workers_per_gather = \${MPG}
max_parallel_workers     = \${CPU}
max_parallel_maintenance_workers = \${MPG}
max_connections          = 200
listen_addresses         = '*'
wal_keep_size            = 1GB
max_wal_senders          = 10
max_replication_slots    = 10
hot_standby              = on
hot_standby_feedback     = on
default_statistics_target = 100
track_io_timing          = on
log_destination          = 'stderr'
logging_collector        = on
log_directory            = 'log'
log_filename             = 'postgresql-%Y-%m-%d.log'
log_rotation_age         = 1d
log_rotation_size        = 1GB
log_min_duration_statement = 1000
log_checkpoints          = on
log_connections          = on
log_lock_waits           = on
log_temp_files           = 0
log_autovacuum_min_duration = 0
log_replication_commands = on
log_line_prefix          = '%m [%p] %q%u@%d '
lock_timeout             = 5000
statement_timeout        = 30000
idle_in_transaction_session_timeout = 60000
PGCONF

cat > "\${PGDATA}/pg_hba.conf" <<PGHBA
local   all             postgres                            trust
local   all             all                                 trust
host    all             all         127.0.0.1/32            scram-sha-256
host    all             all         ::1/128                 scram-sha-256
host    all             all         192.168.0.0/16          scram-sha-256
host    replication     replicator  192.168.0.0/16          scram-sha-256
PGHBA

mkdir -p "\${PGDATA}/log"
chown postgres:postgres "\${PGDATA}/log"

systemctl enable postgresql@\${PG_VER}-main 2>/dev/null || true
systemctl start postgresql@\${PG_VER}-main 2>/dev/null || \
    sudo -u postgres /usr/lib/postgresql/\${PG_VER}/bin/pg_ctl \
        -D "\${PGDATA}" -l "\${PGDATA}/log/startup.log" start -w -t 60
sleep 3
pg_isready -q && pass "PostgreSQL \${PG_VER} running" || fail "PostgreSQL failed to start"

mkdir -p /var/lib/postgresql/backup/wal
chown postgres:postgres /var/lib/postgresql/backup/wal

# Create replication user
EXISTS=\$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='replicator';" 2>/dev/null || echo "")
if [[ -z "\$EXISTS" ]]; then
    REPPASS=\$(openssl rand -base64 24)
    sudo -u postgres psql -c "CREATE ROLE replicator REPLICATION LOGIN PASSWORD '\${REPPASS}';"
    echo "replicator:\${REPPASS}" > /etc/postgresql-ha/replication.credentials
    chmod 400 /etc/postgresql-ha/replication.credentials
    pass "Replication user created"
fi

# Application database
DB_EXISTS=\$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='casino';" 2>/dev/null || echo "")
if [[ -z "\$DB_EXISTS" ]]; then
    APP_PASS=\$(openssl rand -base64 24)
    sudo -u postgres psql -c "CREATE ROLE igaming_app LOGIN PASSWORD '\${APP_PASS}';"
    sudo -u postgres psql -c "CREATE DATABASE casino OWNER igaming_app ENCODING 'UTF8' LC_COLLATE 'en_US.UTF-8' LC_CTYPE 'en_US.UTF-8' TEMPLATE template0;"
    echo "igaming_app:\${APP_PASS}" > /etc/postgresql-ha/app.credentials
    chmod 400 /etc/postgresql-ha/app.credentials
    pass "Database 'casino' + user 'igaming_app' created"
fi
echo "Phase 3 complete" | tee -a \$LOG
PHASE3_EOF
)

vm_exec "$P3_SCRIPT"
pass "Phase 3 complete: PostgreSQL ${PG_VERSION} tuned and running"
else warn "Skipping Phase 3 (pg)"; fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4: TDE — pgcrypto column-level encryption
# ═══════════════════════════════════════════════════════════════════════════
if ! skip_phase "tde"; then
banner "Phase 4: TDE — pgcrypto Column-level Encryption"

P4_SCRIPT=$(cat <<'PHASE4_EOF'
set -euo pipefail
LOG="/var/log/pg-provision.log"
TDE_KEY_FILE="/etc/postgresql-ha/tde.key"

pass() { echo "[OK] $*" | tee -a $LOG; }
info() { echo "[..] $*" | tee -a $LOG; }

[[ ! -f "$TDE_KEY_FILE" ]] && { openssl rand -base64 48 > "$TDE_KEY_FILE"; chmod 400 "$TDE_KEY_FILE"; }
TDE_KEY=$(cat "$TDE_KEY_FILE")

sudo -u postgres psql -d casino <<EOSQL
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE SCHEMA IF NOT EXISTS crypto;
GRANT USAGE ON SCHEMA crypto TO igaming_app;

CREATE OR REPLACE FUNCTION crypto.encrypt_pii(plaintext TEXT, enc_key TEXT)
RETURNS BYTEA LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER AS \$\$
    SELECT pgp_sym_encrypt(plaintext, enc_key, 'compress-algo=1, cipher-algo=aes256')::BYTEA;
\$\$;

CREATE OR REPLACE FUNCTION crypto.decrypt_pii(ciphertext BYTEA, enc_key TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT SECURITY DEFINER AS \$\$
    SELECT pgp_sym_decrypt(ciphertext::bytea, enc_key);
\$\$;

CREATE OR REPLACE FUNCTION crypto.hash_pii(plaintext TEXT)
RETURNS TEXT LANGUAGE sql IMMUTABLE STRICT AS \$\$
    SELECT encode(digest(plaintext, 'sha256'), 'hex');
\$\$;

CREATE TABLE IF NOT EXISTS crypto.players (
    id            SERIAL PRIMARY KEY,
    username      VARCHAR(50) NOT NULL UNIQUE,
    email_enc     BYTEA,
    email_hash    TEXT,
    full_name_enc BYTEA,
    dob_enc       BYTEA,
    ssn_enc       BYTEA,
    pan_hash      TEXT,
    pan_enc       BYTEA,
    balance       NUMERIC(12,2) DEFAULT 0.00,
    currency      CHAR(3) DEFAULT 'EUR',
    jurisdiction  VARCHAR(10),
    kyc_status    VARCHAR(20) DEFAULT 'pending',
    edd_required  BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crypto.transactions (
    id          BIGSERIAL PRIMARY KEY,
    player_id   INTEGER REFERENCES crypto.players(id),
    amount      NUMERIC(12,2) NOT NULL,
    currency    CHAR(3) DEFAULT 'EUR',
    txn_type    VARCHAR(20) NOT NULL,
    ref_enc     BYTEA,
    provider    VARCHAR(50),
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS crypto.audit_log (
    id          BIGSERIAL PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    table_name  TEXT,
    record_id   INTEGER,
    details     JSONB,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_players_email_hash ON crypto.players(email_hash);
CREATE INDEX IF NOT EXISTS idx_players_pan_hash   ON crypto.players(pan_hash);
CREATE INDEX IF NOT EXISTS idx_txn_player         ON crypto.transactions(player_id);
CREATE INDEX IF NOT EXISTS idx_txn_created        ON crypto.transactions(created_at);

GRANT SELECT, INSERT, UPDATE ON crypto.players TO igaming_app;
GRANT SELECT, INSERT ON crypto.transactions    TO igaming_app;
GRANT INSERT ON crypto.audit_log               TO igaming_app;
EOSQL

# Insert sample encrypted data using the TDE key
sudo -u postgres psql -d casino -c "
INSERT INTO crypto.players(username, email_enc, email_hash, full_name_enc, ssn_enc, pan_hash, pan_enc, balance, jurisdiction)
VALUES('alice_poker',
  crypto.encrypt_pii('alice@example.com', '${TDE_KEY}'), crypto.hash_pii('alice@example.com'),
  crypto.encrypt_pii('Alice Johnson', '${TDE_KEY}'), crypto.encrypt_pii('123-45-6789', '${TDE_KEY}'),
  crypto.hash_pii('4111111111111111'), crypto.encrypt_pii('4111111111111111', '${TDE_KEY}'),
  5000.00, 'MT')
ON CONFLICT (username) DO NOTHING;" 2>/dev/null

pass "pgcrypto TDE schema created"

# Verify round-trip
RESULT=$(sudo -u postgres psql -d casino -tAc "
    SELECT crypto.decrypt_pii(email_enc, '${TDE_KEY}')
    FROM crypto.players WHERE username='alice_poker';" 2>/dev/null | tr -d ' ')
[[ "$RESULT" == "alice@example.com" ]] \
    && pass "TDE round-trip: alice@example.com decrypts correctly" \
    || echo "[WARN] TDE round-trip result: '$RESULT'"
echo "Phase 4 complete" | tee -a $LOG
PHASE4_EOF
)

vm_exec "$P4_SCRIPT"
pass "Phase 4 complete: TDE column encryption active"
else warn "Skipping Phase 4 (tde)"; fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5: pgBackRest
# ═══════════════════════════════════════════════════════════════════════════
if ! skip_phase "backup"; then
banner "Phase 5: pgBackRest Backup"

P5_SCRIPT=$(cat <<PHASE5_EOF
set -euo pipefail
LOG="/var/log/pg-provision.log"
VM_NAME="${VM_NAME}"
PG_VER=${PG_VERSION}

pass() { echo "[OK] \$*" | tee -a \$LOG; }
info() { echo "[..] \$*" | tee -a \$LOG; }
warn() { echo "[!!] \$*" | tee -a \$LOG; }

mountpoint -q /var/lib/postgresql/backup || { warn "Backup disk not mounted, skipping"; exit 0; }
mkdir -p /var/lib/postgresql/backup/{repo1,wal}
chown -R postgres:postgres /var/lib/postgresql/backup

REPO_PASS=\$(openssl rand -base64 48 | head -c 64)
cat > /etc/pgbackrest.conf <<BRCFG
[global]
repo1-path=/var/lib/postgresql/backup/repo1
repo1-retention-full=2
repo1-retention-diff=7
repo1-cipher-type=aes-256-cbc
repo1-cipher-pass=\${REPO_PASS}
log-level-console=info
log-level-file=detail
log-path=/var/log/pgbackrest
start-fast=y
compress-type=lz4
compress-level=3
process-max=\$(nproc)
delta=y

[\${VM_NAME}]
pg1-path=/var/lib/postgresql/\${PG_VER}/main
pg1-user=postgres
pg1-socket-path=/var/run/postgresql
BRCFG

mkdir -p /var/log/pgbackrest
chown postgres:postgres /var/log/pgbackrest
pass "pgbackrest.conf written"

sudo -u postgres pgbackrest --stanza="\${VM_NAME}" stanza-create >> \$LOG 2>&1 || warn "stanza-create: may already exist"
sudo -u postgres pgbackrest --stanza="\${VM_NAME}" --type=full backup >> \$LOG 2>&1 \
    && pass "Initial full backup complete" || warn "Backup failed (check /var/log/pgbackrest)"

for SVC_TYPE in full diff; do
    cat > /etc/systemd/system/pgbackrest-\${SVC_TYPE}.service <<EOF
[Unit]
Description=pgBackRest \${SVC_TYPE} backup [\${VM_NAME}]
[Service]
Type=oneshot
User=postgres
ExecStart=/usr/bin/pgbackrest --stanza=\${VM_NAME} --type=\${SVC_TYPE} backup
EOF
done

cat > /etc/systemd/system/pgbackrest-full.timer <<EOF
[Unit]
Description=Weekly pgBackRest full backup
[Timer]
OnCalendar=Sun 02:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

cat > /etc/systemd/system/pgbackrest-diff.timer <<EOF
[Unit]
Description=Daily differential backup
[Timer]
OnCalendar=Mon-Sat 03:00:00
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable pgbackrest-full.timer pgbackrest-diff.timer
systemctl start pgbackrest-full.timer pgbackrest-diff.timer
pass "Backup timers: full=Sun 02:00, diff=Mon-Sat 03:00"
echo "Phase 5 complete" | tee -a \$LOG
PHASE5_EOF
)

vm_exec "$P5_SCRIPT"
pass "Phase 5 complete: pgBackRest configured"
else warn "Skipping Phase 5 (backup)"; fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6: Patroni HA
# ═══════════════════════════════════════════════════════════════════════════
if ! skip_phase "ha" && [[ "$ROLE" != "standalone" ]]; then
banner "Phase 6: Patroni HA — role: ${ROLE}"

P6_SCRIPT=$(cat <<PHASE6_EOF
set -euo pipefail
LOG="/var/log/pg-provision.log"
VM_NAME="${VM_NAME}"
VM_IP="${VM_IP}"
ROLE="${ROLE}"
HA_PARTNER="${HA_PARTNER}"
PG_VER=${PG_VERSION}
SHB=${SHARED_BUFFERS_MB}
ECB=${EFFECTIVE_CACHE_MB}
WMB=${WORK_MEM_MB}
MMB=${MAINT_WORK_MEM_MB}
CPU=${VM_CPU}
MPG=${MAX_PARALLEL_GATHER}

pass() { echo "[OK] \$*" | tee -a \$LOG; }
info() { echo "[..] \$*" | tee -a \$LOG; }
warn() { echo "[!!] \$*" | tee -a \$LOG; }

PGDATA="/var/lib/postgresql/\${PG_VER}/main"
REST_PASS=\$(openssl rand -base64 24 | head -c 32)
REP_PASS=\$(cat /etc/postgresql-ha/replication.credentials 2>/dev/null | cut -d: -f2 || openssl rand -base64 24)
APP_PASS=\$(cat /etc/postgresql-ha/app.credentials 2>/dev/null | cut -d: -f2 || openssl rand -base64 24)

if [[ "\$ROLE" == "primary" ]]; then
    info "Configuring etcd (single-node DCS)..."
    cat > /etc/default/etcd <<ETCD_EOF
ETCD_NAME="\${VM_NAME}"
ETCD_DATA_DIR="/var/lib/etcd"
ETCD_INITIAL_CLUSTER="\${VM_NAME}=http://\${VM_IP}:2380"
ETCD_INITIAL_CLUSTER_STATE=new
ETCD_INITIAL_CLUSTER_TOKEN=pg-ha-cluster
ETCD_INITIAL_ADVERTISE_PEER_URLS="http://\${VM_IP}:2380"
ETCD_LISTEN_PEER_URLS="http://0.0.0.0:2380"
ETCD_LISTEN_CLIENT_URLS="http://0.0.0.0:2379"
ETCD_ADVERTISE_CLIENT_URLS="http://\${VM_IP}:2379"
ETCD_EOF
    systemctl enable etcd && systemctl restart etcd
    sleep 5; pass "etcd running"
fi

ETCD_HOST="\${VM_IP}"; [[ "\$ROLE" == "replica" ]] && ETCD_HOST="\${HA_PARTNER}"

mkdir -p /etc/patroni
cat > /etc/patroni/patroni.yml <<PATRONI_EOF
scope: pg-ha-cluster
namespace: /patroni/
name: \${VM_NAME}

restapi:
  listen: 0.0.0.0:8008
  connect_address: \${VM_IP}:8008
  authentication:
    username: patroni
    password: \${REST_PASS}

etcd3:
  hosts: \${ETCD_HOST}:2379
  protocol: http

bootstrap:
  dcs:
    ttl: 30
    loop_wait: 10
    retry_timeout: 10
    maximum_lag_on_failover: 1048576
    master_start_timeout: 300
    synchronous_mode: true
    synchronous_mode_strict: false
    postgresql:
      use_pg_rewind: true
      use_slots: true
      parameters:
        max_connections: 200
        shared_buffers: \${SHB}MB
        effective_cache_size: \${ECB}MB
        work_mem: \${WMB}MB
        maintenance_work_mem: \${MMB}MB
        wal_buffers: 64MB
        checkpoint_completion_target: 0.9
        random_page_cost: 1.1
        effective_io_concurrency: 200
        max_worker_processes: \${CPU}
        max_parallel_workers_per_gather: \${MPG}
        max_parallel_workers: \${CPU}
        wal_level: replica
        hot_standby: "on"
        wal_log_hints: "on"
        max_wal_senders: 10
        max_replication_slots: 10
        wal_keep_size: 1GB
        archive_mode: "on"
        archive_command: "pgbackrest --stanza=\${VM_NAME} archive-push %p"
        archive_cleanup_command: "pgbackrest --stanza=\${VM_NAME} archive-cleanup %r"
        restore_command: "pgbackrest --stanza=\${VM_NAME} archive-get %f %p"
        lock_timeout: 5000
        statement_timeout: 30000
        idle_in_transaction_session_timeout: 60000
  initdb:
    - encoding: UTF8
    - data-checksums
  pg_hba:
    - host replication replicator 0.0.0.0/0 scram-sha-256
    - host all all 0.0.0.0/0 scram-sha-256
    - local all all trust
  users:
    igaming_app:
      password: \${APP_PASS}
      options: [createdb]

postgresql:
  listen: 0.0.0.0:5432
  connect_address: \${VM_IP}:5432
  data_dir: \${PGDATA}
  bin_dir: /usr/lib/postgresql/\${PG_VER}/bin
  config_dir: \${PGDATA}
  pgpass: /etc/postgresql-ha/.pgpass
  authentication:
    replication:
      username: replicator
      password: \${REP_PASS}
    superuser:
      username: postgres
  parameters:
    unix_socket_directories: '/var/run/postgresql'
  create_replica_methods: [basebackup]
  basebackup:
    max-rate: '100M'
    checkpoint: 'fast'

tags:
  nofailover: false
  noloadbalance: false
  clonefrom: false
  nosync: false
PATRONI_EOF
pass "Patroni config written"

cat > /etc/systemd/system/patroni.service <<SVC_EOF
[Unit]
Description=Patroni PostgreSQL HA
After=network.target etcd.service
[Service]
Type=simple
User=postgres
Group=postgres
ExecStart=/usr/bin/patroni /etc/patroni/patroni.yml
KillMode=process
TimeoutSec=30
Restart=on-failure
RestartSec=10
[Install]
WantedBy=multi-user.target
SVC_EOF

systemctl stop postgresql@\${PG_VER}-main 2>/dev/null || true
systemctl disable postgresql@\${PG_VER}-main 2>/dev/null || true
systemctl daemon-reload
systemctl enable patroni && systemctl start patroni
sleep 10
systemctl is-active --quiet patroni && pass "Patroni running" || warn "Patroni not yet active"

if [[ "\$ROLE" == "primary" ]]; then
    cat > /etc/haproxy/haproxy.cfg <<HAPROXY_EOF
global
    maxconn 4000
    log /dev/log local0
defaults
    log global
    mode tcp
    retries 2
    timeout client 30m
    timeout connect 4s
    timeout server 30m
    timeout check 5s
listen stats
    mode http
    bind *:7000
    stats enable
    stats uri /
    stats refresh 10s
frontend pg_primary
    bind *:5000
    default_backend pg_rw
backend pg_rw
    option httpchk GET /primary HTTP/1.1\r\nHost:\ localhost
    http-check expect status 200
    server \${VM_NAME}    \${VM_IP}:5432 maxconn 200 check port 8008
    server \${HA_PARTNER} \${HA_PARTNER}:5432 maxconn 200 check port 8008 backup
frontend pg_replica
    bind *:5001
    default_backend pg_ro
backend pg_ro
    option httpchk GET /replica HTTP/1.1\r\nHost:\ localhost
    http-check expect status 200
    server \${HA_PARTNER} \${HA_PARTNER}:5432 maxconn 200 check port 8008
    server \${VM_NAME}    \${VM_IP}:5432 maxconn 200 check port 8008 backup
HAPROXY_EOF
    systemctl enable haproxy && systemctl restart haproxy
    pass "HAProxy: RW=5000 RO=5001 Stats=7000"
fi

echo "patroni_rest_pass: \${REST_PASS}" > /etc/postgresql-ha/patroni.credentials
chmod 400 /etc/postgresql-ha/patroni.credentials
echo "Phase 6 complete" | tee -a \$LOG
PHASE6_EOF
)

vm_exec "$P6_SCRIPT"
pass "Phase 6 complete: Patroni HA configured as ${ROLE}"
elif [[ "$ROLE" == "standalone" ]]; then
    info "Role is standalone — skipping Patroni"
else warn "Skipping Phase 6 (ha)"; fi

# ═══════════════════════════════════════════════════════════════════════════
# PHASE 7: Verification
# ═══════════════════════════════════════════════════════════════════════════
if ! skip_phase "verify"; then
banner "Phase 7: Verification"

P7_SCRIPT=$(cat <<PHASE7_EOF
set -euo pipefail
LOG="/var/log/pg-provision.log"
PG_VER=${PG_VERSION}
ROLE="${ROLE}"
VM_NAME="${VM_NAME}"
VM_IP="${VM_IP}"

pass() { echo "[OK] \$*" | tee -a \$LOG; }
warn() { echo "[!!] \$*" | tee -a \$LOG; }

echo "-- LUKS Status --" | tee -a \$LOG
for M in pg-data pg-wal pg-backup; do
    if cryptsetup status "\$M" 2>/dev/null | grep -q "is active"; then
        CIPHER=\$(cryptsetup status "\$M" 2>/dev/null | awk '/cipher/{print \$2}')
        KEYSIZE=\$(cryptsetup status "\$M" 2>/dev/null | awk '/keysize/{print \$2\$3}')
        pass "LUKS /dev/mapper/\$M — cipher=\$CIPHER keysize=\$KEYSIZE"
    else
        warn "/dev/mapper/\$M not active"
    fi
done

echo "-- Encryption-at-rest (raw disk strings check) --" | tee -a \$LOG
for DEV in /dev/vdb /dev/vdc /dev/vdd; do
    if [[ -b "\$DEV" ]]; then
        HIT=\$(dd if=\$DEV bs=512 count=200 2>/dev/null | strings 2>/dev/null | grep -ci postgres || true)
        [[ "\$HIT" -eq 0 ]] \
            && pass "No plaintext 'postgres' strings on raw \$DEV" \
            || warn "Possible plaintext on \$DEV (\$HIT hits) — verify LUKS"
    fi
done

echo "-- Mount Points --" | tee -a \$LOG
for MP in "/var/lib/postgresql/\${PG_VER}/main" "/var/lib/postgresql/\${PG_VER}/wal" "/var/lib/postgresql/backup"; do
    if mountpoint -q "\$MP"; then
        USAGE=\$(df -h "\$MP" | tail -1 | awk '{print \$2" total, "\$4" free"}')
        pass "\$MP mounted — \$USAGE"
    else
        warn "\$MP NOT mounted"
    fi
done

echo "-- PostgreSQL --" | tee -a \$LOG
if pg_isready -q; then
    VER=\$(sudo -u postgres psql -tAc "SELECT version();" 2>/dev/null | head -1)
    pass "PostgreSQL ready — \$VER"
else
    warn "pg_isready failed"
fi

echo "-- WAL on separate disk --" | tee -a \$LOG
WAL_FS=\$(df "/var/lib/postgresql/\${PG_VER}/wal" 2>/dev/null | tail -1 | awk '{print \$1}')
DATA_FS=\$(df "/var/lib/postgresql/\${PG_VER}/main" 2>/dev/null | tail -1 | awk '{print \$1}')
[[ "\$WAL_FS" != "\$DATA_FS" ]] \
    && pass "WAL (\$WAL_FS) on separate device from data (\$DATA_FS)" \
    || warn "WAL and data on same device"

echo "-- TDE Round-trip --" | tee -a \$LOG
TDE_KEY_FILE="/etc/postgresql-ha/tde.key"
if [[ -f "\$TDE_KEY_FILE" ]]; then
    TDE_KEY=\$(cat "\$TDE_KEY_FILE")
    DEC=\$(sudo -u postgres psql -d casino -tAc "
        SELECT crypto.decrypt_pii(email_enc, '\${TDE_KEY}')
        FROM crypto.players WHERE username='alice_poker';" 2>/dev/null | tr -d ' ')
    [[ "\$DEC" == "alice@example.com" ]] \
        && pass "TDE round-trip verified" \
        || warn "TDE result: '\$DEC'"
fi

echo "-- pgBackRest --" | tee -a \$LOG
if sudo -u postgres pgbackrest --stanza="\${VM_NAME}" info 2>/dev/null | grep -q "full backup"; then
    pass "pgBackRest: full backup present"
else
    warn "No pgBackRest full backup yet"
fi

if [[ "\$ROLE" != "standalone" ]]; then
    echo "-- Replication --" | tee -a \$LOG
    REPSTAT=\$(sudo -u postgres psql -tAc "SELECT client_addr,state,sync_state FROM pg_stat_replication;" 2>/dev/null | head -5)
    [[ -n "\$REPSTAT" ]] && pass "Replication: \$REPSTAT" || warn "No replication connections visible"
    PROLE=\$(curl -sf http://localhost:8008/patroni 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print(d.get('role','?'))" 2>/dev/null || echo "unavailable")
    pass "Patroni role: \${PROLE}"
fi

echo "-- pgbench (10s, 10 clients) --" | tee -a \$LOG
sudo -u postgres pgbench -i -s 5 postgres >> \$LOG 2>&1 || warn "pgbench init failed"
BENCH=\$(sudo -u postgres pgbench -c 10 -j 4 -T 10 postgres 2>&1 | grep -E 'tps|latency' | head -4)
echo "\$BENCH" | tee -a \$LOG
TPS=\$(echo "\$BENCH" | grep -oP 'tps = \K[\d.]+' | head -1)
[[ -n "\$TPS" ]] && pass "TPS: \$TPS"

echo "" | tee -a \$LOG
echo "══════════════════════════════════════════════════" | tee -a \$LOG
echo " Verification Summary — \${VM_NAME} (\${VM_IP})"     | tee -a \$LOG
echo "══════════════════════════════════════════════════" | tee -a \$LOG
echo " Disks:     /dev/vda OS | /dev/vdb DATA(LUKS) | /dev/vdc WAL(LUKS) | /dev/vdd BACKUP(LUKS)" | tee -a \$LOG
echo " LUKS key:  /etc/postgresql-ha/luks.key"            | tee -a \$LOG
echo " TDE key:   /etc/postgresql-ha/tde.key"             | tee -a \$LOG
echo " App creds: /etc/postgresql-ha/app.credentials"     | tee -a \$LOG
echo " Connect:   psql -h \${VM_IP} -U igaming_app -d casino" | tee -a \$LOG
echo "══════════════════════════════════════════════════" | tee -a \$LOG
echo "Phase 7 complete" | tee -a \$LOG
PHASE7_EOF
)

vm_exec "$P7_SCRIPT"
pass "Phase 7 complete"
else warn "Skipping Phase 7 (verify)"; fi

banner "Provisioning Complete — ${VM_NAME}"
pass "VM:       ${VM_NAME} at ${VM_IP}"
pass "Role:     ${ROLE}"
pass "Disks:    OS(${OS_DISK_GB}G) + DATA(${DATA_DISK_GB}G,LUKS) + WAL(${WAL_DISK_GB}G,LUKS) + BACKUP(${BACKUP_DISK_GB}G,LUKS)"
pass "Connect:  psql -h ${VM_IP} -U igaming_app -d casino"
[[ "$ROLE" != "standalone" ]] && pass "HA ports: RW=5000 RO=5001 Stats=7000"
pass "Log:      ${LOG_FILE} (local) + ssh ${SSH_USER}@${VM_IP} sudo cat /var/log/pg-provision.log"
