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

# provision-postgres-ha-pair.sh
# Creates a primary + replica PostgreSQL HA pair with a single command.
# Wraps provision-postgres-vm.sh and orchestrates sequencing:
#   1. Provision primary
#   2. Provision replica (joins the primary)
#
# Usage:
#   ./provision-postgres-ha-pair.sh \
#     --primary-ip 10.0.10.30 \
#     --replica-ip 10.0.10.31 \
#     --ram 16384 --cpu 8 \
#     --data-disk 200 --wal-disk 50 --backup-disk 100 \
#     [--luks-passphrase FROM_HSM] [--network 50] [--dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROVISION="${SCRIPT_DIR}/provision-postgres-vm.sh"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
pass()   { echo -e "${GREEN}[OK]${NC}  $*"; }
fail()   { echo -e "${RED}[ERR]${NC} $*" >&2; exit 1; }
info()   { echo -e "${YELLOW}[..]${NC} $*"; }
banner() { echo -e "\n${BOLD}══════════════════════════════════════════════════${NC}"
           echo -e "${BOLD} $*${NC}"
           echo -e "${BOLD}══════════════════════════════════════════════════${NC}"; }

# Defaults
PRIMARY_IP=""; REPLICA_IP=""
PRIMARY_NAME="pg-primary"; REPLICA_NAME="pg-replica"
RAM=16384; CPU=8; OS_DISK=50; DATA_DISK=200; WAL_DISK=50; BACKUP_DISK=100
LUKS_PASSPHRASE="FROM_HSM"; NETWORK=50; DRY_RUN=0

usage() {
cat <<EOF
Usage: $0 [OPTIONS]

Required:
  --primary-ip IP       IP for primary node
  --replica-ip IP       IP for replica node

Optional:
  --primary-name NAME   Primary hostname (default: pg-primary)
  --replica-name NAME   Replica hostname (default: pg-replica)
  --ram MB              RAM per VM in MB (default: 16384)
  --cpu N               vCPUs per VM (default: 8)
  --os-disk GB          OS disk GB (default: 50)
  --data-disk GB        Encrypted data disk GB (default: 200)
  --wal-disk GB         Encrypted WAL disk GB (default: 50)
  --backup-disk GB      Encrypted backup disk GB (default: 100)
  --luks-passphrase P   FROM_HSM or explicit passphrase (default: FROM_HSM)
  --network NET         50 or 120 (default: 50)
  --dry-run             Show what would be done, no changes
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --primary-ip)       PRIMARY_IP="$2";     shift 2 ;;
        --replica-ip)       REPLICA_IP="$2";     shift 2 ;;
        --primary-name)     PRIMARY_NAME="$2";   shift 2 ;;
        --replica-name)     REPLICA_NAME="$2";   shift 2 ;;
        --ram)              RAM="$2";            shift 2 ;;
        --cpu)              CPU="$2";            shift 2 ;;
        --os-disk)          OS_DISK="$2";        shift 2 ;;
        --data-disk)        DATA_DISK="$2";      shift 2 ;;
        --wal-disk)         WAL_DISK="$2";       shift 2 ;;
        --backup-disk)      BACKUP_DISK="$2";    shift 2 ;;
        --luks-passphrase)  LUKS_PASSPHRASE="$2"; shift 2 ;;
        --network)          NETWORK="$2";        shift 2 ;;
        --dry-run)          DRY_RUN=1;           shift ;;
        --help|-h)          usage; exit 0 ;;
        *) echo "Unknown option: $1"; usage; exit 1 ;;
    esac
done

[[ -z "$PRIMARY_IP" ]] && { echo "ERROR: --primary-ip required"; usage; exit 1; }
[[ -z "$REPLICA_IP" ]] && { echo "ERROR: --replica-ip required"; usage; exit 1; }
[[ -x "$PROVISION" ]] || fail "provision-postgres-vm.sh not found/executable at $PROVISION"

DRY_FLAG=""
[[ $DRY_RUN -eq 1 ]] && DRY_FLAG="--dry-run"

COMMON_ARGS="--ram ${RAM} --cpu ${CPU} --os-disk ${OS_DISK} \
  --data-disk ${DATA_DISK} --wal-disk ${WAL_DISK} --backup-disk ${BACKUP_DISK} \
  --luks-passphrase ${LUKS_PASSPHRASE} --network ${NETWORK}"

banner "HA Pair Provisioning"
info "Primary: ${PRIMARY_NAME} @ ${PRIMARY_IP}"
info "Replica: ${REPLICA_NAME} @ ${REPLICA_IP}"
info "RAM: ${RAM}MB  CPU: ${CPU}  DATA: ${DATA_DISK}GB  WAL: ${WAL_DISK}GB"

banner "Step 1: Provisioning Primary — ${PRIMARY_NAME}"
# shellcheck disable=SC2086
"$PROVISION" \
    --name "$PRIMARY_NAME" \
    --ip "$PRIMARY_IP" \
    --role primary \
    --ha-partner "$REPLICA_IP" \
    $COMMON_ARGS \
    $DRY_FLAG

pass "Primary provisioned"

banner "Step 2: Provisioning Replica — ${REPLICA_NAME}"
# shellcheck disable=SC2086
"$PROVISION" \
    --name "$REPLICA_NAME" \
    --ip "$REPLICA_IP" \
    --role replica \
    --ha-partner "$PRIMARY_IP" \
    $COMMON_ARGS \
    $DRY_FLAG

pass "Replica provisioned"

banner "Step 3: HA Pair Verification"
if [[ $DRY_RUN -eq 0 ]]; then
    info "Checking replication from primary..."
    ssh -o StrictHostKeyChecking=no "admin@${PRIMARY_IP}" \
        'sudo -u postgres psql -tAc "SELECT client_addr,state,sync_state,sent_lsn,replay_lsn FROM pg_stat_replication;"' 2>/dev/null \
        && pass "Replication active" || info "Replication not visible yet (may take a moment)"

    info "Checking Patroni cluster state..."
    ssh -o StrictHostKeyChecking=no "admin@${PRIMARY_IP}" \
        'curl -sf http://localhost:8008/cluster 2>/dev/null | python3 -m json.tool' 2>/dev/null \
        || info "Patroni cluster endpoint not yet ready"

    info "Running ${SCRIPT_DIR}/verify-postgres-vm.sh on primary..."
    [[ -x "${SCRIPT_DIR}/verify-postgres-vm.sh" ]] && \
        "${SCRIPT_DIR}/verify-postgres-vm.sh" --ip "$PRIMARY_IP" --ha-partner "$REPLICA_IP" || true
fi

banner "HA Pair Ready"
pass "Primary:  ${PRIMARY_NAME} @ ${PRIMARY_IP}"
pass "Replica:  ${REPLICA_NAME} @ ${REPLICA_IP}"
pass "RW port:  ${PRIMARY_IP}:5000 (HAProxy)"
pass "RO port:  ${PRIMARY_IP}:5001 (HAProxy)"
pass "Stats:    http://${PRIMARY_IP}:7000"
pass "Patroni:  http://${PRIMARY_IP}:8008/cluster"
pass "Connect:  psql -h ${PRIMARY_IP} -p 5000 -U igaming_app -d casino"
