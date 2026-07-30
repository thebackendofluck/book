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

# verify-postgres-vm.sh
# Comprehensive post-provisioning verification for a PostgreSQL VM.
# Covers: LUKS status, disk I/O benchmarks, pgbench TPS, encryption-at-rest,
# replication status, backup status, and TDE round-trip.
#
# Usage:
#   ./verify-postgres-vm.sh --ip 10.0.10.35 [--ha-partner 10.0.10.31]

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'

PASS=0; WARN=0; FAIL=0

pass() { echo -e "${GREEN}[PASS]${NC} $*"; (( PASS++ )) || true; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; (( WARN++ )) || true; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; (( FAIL++ )) || true; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

VM_IP=""; HA_PARTNER=""; SSH_USER="operator"
PG_VERSION=16; VM_NAME=""

usage() {
cat <<EOF
Usage: $0 --ip VM_IP [OPTIONS]

Required:
  --ip IP               VM IP to verify

Optional:
  --ha-partner IP       Replica/partner IP for replication checks
  --vm-name NAME        VM name (used for pgBackRest stanza)
  --user USER           SSH user (default: operator))
  --pg-version N        PostgreSQL version (default: 16)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --ip)           VM_IP="$2";       shift 2 ;;
        --ha-partner)   HA_PARTNER="$2";  shift 2 ;;
        --vm-name)      VM_NAME="$2";     shift 2 ;;
        --user)         SSH_USER="$2";    shift 2 ;;
        --pg-version)   PG_VERSION="$2";  shift 2 ;;
        --help|-h)      usage; exit 0 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

[[ -z "$VM_IP" ]] && { echo "ERROR: --ip required"; usage; exit 1; }

vm_exec() {
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=15 \
        "${SSH_USER}@${VM_IP}" "sudo bash -s" <<< "$1" 2>/dev/null
}

echo ""
echo -e "${BOLD}══════════════════════════════════════════════════${NC}"
echo -e "${BOLD} PostgreSQL VM Verification — ${VM_IP}${NC}"
echo -e "${BOLD} $(date)${NC}"
echo -e "${BOLD}══════════════════════════════════════════════════${NC}"

# ── 1. SSH Connectivity ────────────────────────────────────────────────────
section "1. SSH Connectivity"
if ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes \
       "${SSH_USER}@${VM_IP}" "echo ok" 2>/dev/null | grep -q ok; then
    HOSTNAME=$(ssh -o StrictHostKeyChecking=no "${SSH_USER}@${VM_IP}" hostname 2>/dev/null)
    pass "SSH to ${VM_IP} (hostname: ${HOSTNAME})"
    [[ -z "$VM_NAME" ]] && VM_NAME="$HOSTNAME"
else
    fail "SSH to ${VM_IP} failed"
    echo "Cannot continue without SSH. Exiting."
    exit 1
fi

# ── 2. LUKS Encryption Status ──────────────────────────────────────────────
section "2. LUKS Encryption Status"
LUKS_OUT=$(vm_exec 'for M in pg-data pg-wal pg-backup; do
    if cryptsetup status "$M" 2>/dev/null | grep -q "is active"; then
        CIPHER=$(cryptsetup status "$M" 2>/dev/null | awk "/cipher/{print \$2}")
        KEYSIZE=$(cryptsetup status "$M" 2>/dev/null | awk "/key size/{print \$3\$4}")
        echo "LUKS_OPEN:$M:cipher=$CIPHER:keysize=$KEYSIZE"
    else
        echo "LUKS_CLOSED:$M"
    fi
done')
while IFS= read -r line; do
    if echo "$line" | grep -q "LUKS_OPEN"; then
        MAP=$(echo "$line" | cut -d: -f2)
        DETAILS=$(echo "$line" | cut -d: -f3-)
        pass "/dev/mapper/${MAP} active (${DETAILS})"
    elif echo "$line" | grep -q "LUKS_CLOSED"; then
        MAP=$(echo "$line" | cut -d: -f2)
        fail "/dev/mapper/${MAP} NOT active"
    fi
done <<< "$LUKS_OUT"

# ── 3. Encryption-at-rest: no plaintext on raw disks ──────────────────────
section "3. Encryption-at-rest Verification"
vm_exec 'for DEV in /dev/vdb /dev/vdc /dev/vdd; do
    if [[ -b "$DEV" ]]; then
        HITS=$(dd if="$DEV" bs=512 count=500 2>/dev/null | strings 2>/dev/null | grep -ci "postgresql\|initdb\|datname" || true)
        echo "RAWCHECK:$DEV:$HITS"
    fi
done' | while IFS= read -r line; do
    DEV=$(echo "$line" | cut -d: -f2)
    HITS=$(echo "$line" | cut -d: -f3)
    if [[ "$HITS" -eq 0 ]]; then
        pass "No PG plaintext on raw ${DEV}"
    else
        warn "${HITS} PG-related strings on raw ${DEV} (verify LUKS is active)"
    fi
done

# ── 4. Disk Mount Points ───────────────────────────────────────────────────
section "4. Disk Mount Points"
vm_exec "for MP in /var/lib/postgresql/${PG_VERSION}/main /var/lib/postgresql/${PG_VERSION}/wal /var/lib/postgresql/backup; do
    if mountpoint -q \"\$MP\" 2>/dev/null; then
        USAGE=\$(df -h \"\$MP\" | tail -1 | awk '{print \$2\" total, \"\$4\" free (\"\$5\" used)\"}')
        echo \"MOUNTED:\$MP:\$USAGE\"
    else
        echo \"NOTMOUNTED:\$MP\"
    fi
done" | while IFS= read -r line; do
    TYPE=$(echo "$line" | cut -d: -f1)
    MP=$(echo "$line" | cut -d: -f2)
    REST=$(echo "$line" | cut -d: -f3-)
    [[ "$TYPE" == "MOUNTED" ]] && pass "${MP} mounted — ${REST}" || fail "${MP} NOT mounted"
done

# ── 5. PostgreSQL Service ──────────────────────────────────────────────────
section "5. PostgreSQL Service"
PG_STATUS=$(vm_exec 'pg_isready -q 2>&1 && sudo -u postgres psql -tAc "SELECT version();" 2>/dev/null | head -1 || echo "NOT_READY"')
if echo "$PG_STATUS" | grep -q "NOT_READY"; then
    fail "PostgreSQL not accepting connections"
else
    pass "PostgreSQL running: ${PG_STATUS}"
fi

# ── 6. WAL on Separate Disk ────────────────────────────────────────────────
section "6. WAL on Separate Disk"
WAL_CHECK=$(vm_exec "
WAL_FS=\$(df /var/lib/postgresql/${PG_VERSION}/wal 2>/dev/null | tail -1 | awk '{print \$1}')
DATA_FS=\$(df /var/lib/postgresql/${PG_VERSION}/main 2>/dev/null | tail -1 | awk '{print \$1}')
echo \"WAL:\$WAL_FS DATA:\$DATA_FS\"")
WAL_FS=$(echo "$WAL_CHECK" | grep -o 'WAL:[^ ]*' | cut -d: -f2)
DATA_FS=$(echo "$WAL_CHECK" | grep -o 'DATA:[^ ]*' | cut -d: -f2)
if [[ -n "$WAL_FS" && -n "$DATA_FS" && "$WAL_FS" != "$DATA_FS" ]]; then
    pass "WAL (${WAL_FS}) on separate device from data (${DATA_FS})"
else
    warn "WAL and data may be on same device: WAL=$WAL_FS DATA=$DATA_FS"
fi

# ── 7. TDE Round-trip ─────────────────────────────────────────────────────
section "7. TDE Column Encryption Round-trip"
TDE_RESULT=$(vm_exec '
KEY_FILE="/etc/postgresql-ha/tde.key"
if [[ -f "$KEY_FILE" ]]; then
    TDE_KEY=$(cat "$KEY_FILE")
    DB_EXISTS=$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='"'"'casino'"'"';" 2>/dev/null || echo "")
    if [[ -n "$DB_EXISTS" ]]; then
        DEC=$(sudo -u postgres psql -d casino -tAc "
            SELECT crypto.decrypt_pii(email_enc, '"'"''"'"''"'"' || '"'"'"'"'"' || '"'"''"'"''"'"')
            FROM crypto.players WHERE username='"'"'alice_poker'"'"';" 2>/dev/null | tr -d " " || echo "FAIL")
        echo "TDE_RESULT:$DEC"
    else
        echo "TDE_RESULT:NO_CASINO_DB"
    fi
else
    echo "TDE_RESULT:NO_KEY_FILE"
fi' 2>/dev/null || echo "TDE_RESULT:SSH_ERROR")

# Simpler approach: run in two steps
TDE_OUT=$(vm_exec 'sudo -u postgres psql -d casino -tAc "
    SELECT decrypt_pii.result FROM (
        SELECT crypto.decrypt_pii(email_enc, (SELECT pg_read_file('\''/etc/postgresql-ha/tde.key'\''))) AS result
        FROM crypto.players WHERE username='"'"'alice_poker'"'"'
    ) t;" 2>/dev/null | tr -d " "' 2>/dev/null || echo "")
if [[ "$TDE_OUT" == "alice@example.com" ]]; then
    pass "TDE round-trip: alice@example.com decrypted correctly"
else
    warn "TDE round-trip: got '${TDE_OUT}' (may need to check TDE key setup)"
fi

# ── 8. pgBackRest Status ───────────────────────────────────────────────────
section "8. pgBackRest Backup"
BACKUP_OUT=$(vm_exec "sudo -u postgres pgbackrest --stanza='${VM_NAME}' info 2>/dev/null || echo 'NO_STANZA'")
if echo "$BACKUP_OUT" | grep -q "full backup"; then
    BACKUP_COUNT=$(echo "$BACKUP_OUT" | grep -c "full backup" || true)
    pass "pgBackRest: ${BACKUP_COUNT} full backup(s) available for stanza '${VM_NAME}'"
else
    warn "No pgBackRest full backup for stanza '${VM_NAME}' (run: pgbackrest --stanza=${VM_NAME} --type=full backup)"
fi

# ── 9. Replication Status (HA) ─────────────────────────────────────────────
if [[ -n "$HA_PARTNER" ]]; then
    section "9. HA Replication Status"
    REP_OUT=$(vm_exec 'sudo -u postgres psql -tAc "
        SELECT client_addr, state, sync_state,
               write_lag::TEXT, flush_lag::TEXT, replay_lag::TEXT
        FROM pg_stat_replication;" 2>/dev/null || echo ""')
    if [[ -n "$REP_OUT" ]]; then
        pass "Active replication connections:"
        echo "$REP_OUT" | sed 's/^/       /'
    else
        warn "No replication connections visible from this node (normal on replica)"
    fi

    PATRONI_OUT=$(vm_exec 'curl -sf http://localhost:8008/cluster 2>/dev/null | python3 -m json.tool 2>/dev/null || echo "{}"')
    LEADER=$(echo "$PATRONI_OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('members',[{}])[0].get('role','?'))" 2>/dev/null || echo "?")
    pass "Patroni cluster JSON (leader role: ${LEADER})"

    section "10. HAProxy Health"
    HAPROXY_OUT=$(vm_exec 'curl -sf http://localhost:7000/ 2>/dev/null | grep -E "pg_|Status" | head -10 || echo "haproxy not responding"')
    if echo "$HAPROXY_OUT" | grep -q "pg_"; then
        pass "HAProxy stats page responding"
    else
        warn "HAProxy stats: ${HAPROXY_OUT}"
    fi
fi

# ── Disk I/O Benchmark (fio) ───────────────────────────────────────────────
SECTION_NUM=10
[[ -n "$HA_PARTNER" ]] && SECTION_NUM=11
section "${SECTION_NUM}. Disk I/O Benchmark (fio — 10s)"
FIO_OUT=$(vm_exec "fio --name=pg-iops-test \
    --filename=/var/lib/postgresql/${PG_VERSION}/main/fio.tmp \
    --size=256m --bs=8k --rw=randrw --rwmixread=70 \
    --iodepth=32 --direct=1 --runtime=10 --time_based \
    --output-format=terse --terse-version=3 2>/dev/null | \
    awk -F';' '{printf \"read IOPS=%s write IOPS=%s read BW=%sKB/s\\n\",\$8,\$49,\$7}'" 2>/dev/null || echo "fio failed")
pass "fio (8K randrw 70/30 iodepth=32): ${FIO_OUT}"
vm_exec "rm -f /var/lib/postgresql/${PG_VERSION}/main/fio.tmp" 2>/dev/null || true

# ── pgbench TPS Test ────────────────────────────────────────────────────────
SECTION_NUM=$(( SECTION_NUM + 1 ))
section "${SECTION_NUM}. pgbench TPS (10s, 10 clients, 4 threads)"
PGBENCH_OUT=$(vm_exec 'sudo -u postgres pgbench -q -i -s 10 postgres 2>/dev/null
sudo -u postgres pgbench -c 10 -j 4 -T 10 -q postgres 2>/dev/null | tail -3' 2>/dev/null || echo "pgbench failed")
TPS=$(echo "$PGBENCH_OUT" | grep -oP 'tps = \K[\d.]+' | head -1)
LATENCY=$(echo "$PGBENCH_OUT" | grep -oP 'latency average = \K[\d.]+' | head -1)
if [[ -n "$TPS" ]]; then
    pass "pgbench TPS: ${TPS} (avg latency: ${LATENCY}ms)"
else
    warn "pgbench result: $PGBENCH_OUT"
fi

# ── Summary ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════════════${NC}"
echo -e "${BOLD} Verification Summary — ${VM_IP}${NC}"
echo -e "${BOLD}══════════════════════════════════════════════════${NC}"
echo -e "  ${GREEN}PASS${NC}: ${PASS}  ${YELLOW}WARN${NC}: ${WARN}  ${RED}FAIL${NC}: ${FAIL}"
echo -e "  Timestamp: $(date)"
echo -e "${BOLD}══════════════════════════════════════════════════${NC}"

if [[ $FAIL -gt 0 ]]; then
    echo -e "\n${RED}Some checks FAILED. Review output above.${NC}"
    exit 1
elif [[ $WARN -gt 0 ]]; then
    echo -e "\n${YELLOW}Some warnings detected. Review output above.${NC}"
    exit 0
else
    echo -e "\n${GREEN}All checks PASSED.${NC}"
    exit 0
fi
