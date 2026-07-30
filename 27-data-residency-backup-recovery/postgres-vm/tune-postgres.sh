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

# tune-postgres.sh
# Auto-tunes postgresql.conf based on detected (or specified) RAM and CPU.
# Works on an existing running PostgreSQL instance.
#
# Usage:
#   ./tune-postgres.sh --auto
#   ./tune-postgres.sh --ram 32768 --cpu 16 --apply
#   ./tune-postgres.sh --auto --apply --reload

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
pass()   { echo -e "${GREEN}[OK]${NC}  $*"; }
info()   { echo -e "${YELLOW}[..]${NC} $*"; }
fail()   { echo -e "${RED}[ERR]${NC} $*" >&2; exit 1; }

RAM_MB=0; CPU_COUNT=0; AUTO=0; APPLY=0; RELOAD=0
PG_VERSION=16; PGDATA=""
CONF_FILE=""
PROFILE=""   # small | medium | large | xlarge
NVME_WAL_DEV=""    # e.g. /dev/nvme1n1 for WAL NVMe (optional, for I/O tuning)
NVME_DATA_DEV=""   # e.g. /dev/nvme2n1 for DATA NVMe (optional)

usage() {
cat <<EOF
Usage: $0 [OPTIONS]

  --auto              Auto-detect RAM and CPU from the system
  --ram MB            Override RAM in MB
  --cpu N             Override CPU count
  --pg-version N      PostgreSQL version (default: 16)
  --pgdata PATH       Override PGDATA path
  --apply             Write tuned values to postgresql.conf
  --reload            Reload PostgreSQL after applying (requires --apply)
  --profile PROFILE   Apply a hardware-class preset: small|medium|large|xlarge
                        small:  4-8 CPU, 16-32GB RAM, 1 NVMe
                        medium: 8-16 CPU, 64-128GB RAM, 2 NVMe
                        large:  32-64 CPU, 256-512GB RAM, 4+ NVMe
                        xlarge: 64+ CPU, 512GB+ RAM, 8+ NVMe (ops-host-class)
  --nvme-wal DEV      NVMe device for WAL disk (e.g. /dev/nvme1n1) for I/O tuning
  --nvme-data DEV     NVMe device for DATA disk (e.g. /dev/nvme2n1) for I/O tuning

Examples:
  $0 --auto                           # Show recommended values, no changes
  $0 --auto --apply                   # Auto-detect and write to postgresql.conf
  $0 --profile large --apply --reload # Apply large-server preset
  $0 --ram 65536 --cpu 16 --apply --reload
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)         AUTO=1;               shift ;;
        --ram)          RAM_MB="$2";          shift 2 ;;
        --cpu)          CPU_COUNT="$2";       shift 2 ;;
        --pg-version)   PG_VERSION="$2";      shift 2 ;;
        --pgdata)       PGDATA="$2";          shift 2 ;;
        --apply)        APPLY=1;              shift ;;
        --reload)       RELOAD=1;             shift ;;
        --profile)      PROFILE="$2";         shift 2 ;;
        --nvme-wal)     NVME_WAL_DEV="$2";    shift 2 ;;
        --nvme-data)    NVME_DATA_DEV="$2";   shift 2 ;;
        --help|-h)      usage; exit 0 ;;
        *) echo "Unknown: $1"; exit 1 ;;
    esac
done

# Apply profile presets (override --ram/--cpu if --profile given)
if [[ -n "$PROFILE" ]]; then
    case "$PROFILE" in
        small)  RAM_MB=24576;  CPU_COUNT=6  ;;   # 24 GB, 6 cores (midpoint of small range)
        medium) RAM_MB=98304;  CPU_COUNT=12 ;;   # 96 GB, 12 cores
        large)  RAM_MB=393216; CPU_COUNT=48 ;;   # 384 GB, 48 cores
        xlarge) RAM_MB=786432; CPU_COUNT=96 ;;   # 768 GB, 96 cores
        *) fail "--profile must be small, medium, large, or xlarge" ;;
    esac
    info "Profile '${PROFILE}': using RAM=${RAM_MB}MB CPU=${CPU_COUNT}"
fi

[[ $AUTO -eq 0 && $RAM_MB -eq 0 && -z "$PROFILE" ]] && { echo "ERROR: specify --auto, --ram, or --profile"; usage; exit 1; }

# Auto-detect
if [[ $AUTO -eq 1 ]]; then
    RAM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo 2>/dev/null || sysctl -n hw.memsize 2>/dev/null | awk '{print int($1/1024/1024)}')
    CPU_COUNT=$(nproc 2>/dev/null || sysctl -n hw.logicalcpu 2>/dev/null || echo 4)
    info "Auto-detected: RAM=${RAM_MB}MB  CPU=${CPU_COUNT}"
fi

[[ $CPU_COUNT -eq 0 ]] && CPU_COUNT=$(nproc 2>/dev/null || echo 4)

# Locate PGDATA
if [[ -z "$PGDATA" ]]; then
    PGDATA=$(sudo -u postgres psql -tAc "SHOW data_directory;" 2>/dev/null || echo "")
    [[ -z "$PGDATA" ]] && PGDATA="/var/lib/postgresql/${PG_VERSION}/main"
fi
CONF_FILE="${PGDATA}/postgresql.conf"

# ─── Compute values ────────────────────────────────────────────────────────
SHB=$(( RAM_MB / 4 ))
ECB=$(( RAM_MB * 3 / 4 ))
WMB=$(( RAM_MB / 200 )); [[ $WMB -lt 4 ]] && WMB=4
MMB=$(( RAM_MB / 8 ))
MPG=$(( CPU_COUNT / 2 )); [[ $MPG -lt 1 ]] && MPG=1
SHMMAX=$(( RAM_MB * 1024 * 1024 * 3 / 4 ))
SHMALL=$(( SHMMAX / 4096 ))
# Huge pages: shared_buffers / 2 MB per huge page
HUGE_PAGES=$(( SHB / 2 )); [[ $HUGE_PAGES -lt 128 ]] && HUGE_PAGES=128
# WAL sizing: scale with RAM
MAX_WAL_GB=$(( RAM_MB / 8192 )); [[ $MAX_WAL_GB -lt 2 ]] && MAX_WAL_GB=2; [[ $MAX_WAL_GB -gt 16 ]] && MAX_WAL_GB=16
MIN_WAL_GB=$(( MAX_WAL_GB / 4 )); [[ $MIN_WAL_GB -lt 1 ]] && MIN_WAL_GB=1
# Autovacuum workers: scale with CPU
AV_WORKERS=$(( CPU_COUNT / 8 )); [[ $AV_WORKERS -lt 3 ]] && AV_WORKERS=3; [[ $AV_WORKERS -gt 10 ]] && AV_WORKERS=10

# ─── Display recommended values ───────────────────────────────────────────
echo ""
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo -e "${BOLD} PostgreSQL Tuning Recommendations${NC}"
echo -e "${BOLD} RAM: ${RAM_MB}MB  |  CPU: ${CPU_COUNT} cores${NC}"
echo -e "${BOLD}═══════════════════════════════════════════════════════${NC}"
echo ""
printf "  %-40s %s\n"  "shared_buffers"                  "${SHB}MB"
printf "  %-40s %s\n"  "effective_cache_size"            "${ECB}MB"
printf "  %-40s %s\n"  "work_mem"                        "${WMB}MB"
printf "  %-40s %s\n"  "maintenance_work_mem"            "${MMB}MB"
printf "  %-40s %s\n"  "wal_buffers"                     "64MB"
printf "  %-40s %s\n"  "max_connections"                 "200"
printf "  %-40s %s\n"  "max_worker_processes"            "${CPU_COUNT}"
printf "  %-40s %s\n"  "max_parallel_workers"            "${CPU_COUNT}"
printf "  %-40s %s\n"  "max_parallel_workers_per_gather" "${MPG}"
printf "  %-40s %s\n"  "max_parallel_maintenance_workers" "${MPG}"
printf "  %-40s %s\n"  "checkpoint_completion_target"    "0.9"
printf "  %-40s %s\n"  "random_page_cost"                "1.1"
printf "  %-40s %s\n"  "effective_io_concurrency"        "200"
printf "  %-40s %s\n"  "huge_pages"                      "try"
printf "  %-40s %s\n"  "max_wal_size"                    "${MAX_WAL_GB}GB"
printf "  %-40s %s\n"  "min_wal_size"                    "${MIN_WAL_GB}GB"
printf "  %-40s %s\n"  "wal_compression"                 "zstd"
printf "  %-40s %s\n"  "jit"                             "on"
printf "  %-40s %s\n"  "autovacuum_max_workers"          "${AV_WORKERS}"
printf "  %-40s %s\n"  "autovacuum_vacuum_scale_factor"  "0.02"
printf "  %-40s %s\n"  "autovacuum_vacuum_cost_delay"    "2ms"
echo ""
printf "  %-40s %s\n"  "kernel.shmmax (sysctl)"          "${SHMMAX}"
printf "  %-40s %s\n"  "kernel.shmall (sysctl)"          "${SHMALL}"
printf "  %-40s %s\n"  "vm.nr_hugepages (sysctl)"        "${HUGE_PAGES}"
printf "  %-40s %s\n"  "vm.swappiness"                   "1"
printf "  %-40s %s\n"  "vm.overcommit_memory"            "2"
printf "  %-40s %s\n"  "vm.overcommit_ratio"             "90"
printf "  %-40s %s\n"  "THP"                             "never (disable transparent huge pages)"
printf "  %-40s %s\n"  "I/O scheduler (NVMe)"            "none (bypass — NVMe has internal queue)"
echo ""

if [[ $APPLY -eq 0 ]]; then
    info "Dry mode — use --apply to write these values to ${CONF_FILE}"
    exit 0
fi

# ─── Apply to postgresql.conf ──────────────────────────────────────────────
[[ -f "$CONF_FILE" ]] || fail "postgresql.conf not found at ${CONF_FILE}"
[[ $EUID -ne 0 ]] && fail "Must run as root to modify ${CONF_FILE}"

BACKUP="${CONF_FILE}.bak.$(date +%Y%m%d%H%M%S)"
cp "$CONF_FILE" "$BACKUP"
pass "Backed up postgresql.conf to ${BACKUP}"

# Function to set a parameter (update if exists, append if not)
set_param() {
    local KEY="$1" VAL="$2"
    if grep -qE "^#?[[:space:]]*${KEY}[[:space:]]*=" "$CONF_FILE"; then
        sed -i "s|^#\?[[:space:]]*${KEY}[[:space:]]*=.*|${KEY} = ${VAL}|" "$CONF_FILE"
    else
        echo "${KEY} = ${VAL}" >> "$CONF_FILE"
    fi
}

set_param shared_buffers                  "${SHB}MB"
set_param effective_cache_size            "${ECB}MB"
set_param work_mem                        "${WMB}MB"
set_param maintenance_work_mem            "${MMB}MB"
set_param wal_buffers                     "64MB"
set_param checkpoint_completion_target    "0.9"
set_param random_page_cost                "1.1"
set_param effective_io_concurrency        "200"
set_param max_worker_processes            "${CPU_COUNT}"
set_param max_parallel_workers_per_gather "${MPG}"
set_param max_parallel_workers            "${CPU_COUNT}"
set_param max_parallel_maintenance_workers "${MPG}"
set_param huge_pages                      "try"
set_param max_connections                 "200"
set_param log_min_duration_statement      "1000"
set_param lock_timeout                    "5000"
set_param statement_timeout               "30000"
set_param idle_in_transaction_session_timeout "60000"
set_param track_io_timing                 "on"
set_param default_statistics_target       "100"
# WAL tuning
set_param max_wal_size                    "${MAX_WAL_GB}GB"
set_param min_wal_size                    "${MIN_WAL_GB}GB"
set_param wal_compression                 "zstd"
set_param wal_buffers                     "64MB"
# On ZFS/CoW filesystems: disable wal_init_zero and wal_recycle
set_param wal_init_zero                   "off"
set_param wal_recycle                     "off"
# JIT for complex analytical queries (casino reporting)
set_param jit                             "on"
set_param jit_above_cost                  "100000"
set_param jit_inline_above_cost           "500000"
set_param jit_optimize_above_cost         "500000"
# Aggressive autovacuum for high-transaction casino workloads
set_param autovacuum_max_workers          "${AV_WORKERS}"
set_param autovacuum_naptime              "10s"
set_param autovacuum_vacuum_threshold     "50"
set_param autovacuum_vacuum_scale_factor  "0.02"
set_param autovacuum_analyze_threshold    "50"
set_param autovacuum_analyze_scale_factor "0.01"
set_param autovacuum_vacuum_cost_delay    "2ms"
set_param autovacuum_vacuum_cost_limit    "1000"
# Explicit huge pages (vm.nr_hugepages set in kernel section below)
set_param huge_pages                      "try"

pass "postgresql.conf updated"

# Apply kernel parameters
sysctl -w kernel.shmmax="${SHMMAX}" 2>/dev/null && pass "kernel.shmmax=${SHMMAX}"
sysctl -w kernel.shmall="${SHMALL}" 2>/dev/null && pass "kernel.shmall=${SHMALL}"
sysctl -w vm.swappiness=1 2>/dev/null && pass "vm.swappiness=1"
sysctl -w vm.dirty_ratio=40 2>/dev/null
sysctl -w vm.dirty_background_ratio=10 2>/dev/null
sysctl -w vm.overcommit_memory=2 2>/dev/null
sysctl -w vm.overcommit_ratio=90 2>/dev/null
sysctl -w net.core.somaxconn=65535 2>/dev/null

# Explicit huge pages (2 MB pages)
sysctl -w vm.nr_hugepages="${HUGE_PAGES}" 2>/dev/null && pass "vm.nr_hugepages=${HUGE_PAGES} (${SHB}MB shared_buffers / 2MB)"

# Disable Transparent Huge Pages — THP causes latency spikes in PostgreSQL
if [[ -f /sys/kernel/mm/transparent_hugepage/enabled ]]; then
    echo never > /sys/kernel/mm/transparent_hugepage/enabled && pass "THP disabled (enabled=never)"
    echo never > /sys/kernel/mm/transparent_hugepage/defrag  && pass "THP defrag disabled"
else
    info "THP sysfs not found — skipping (may be in container)"
fi

# I/O scheduler: NVMe devices should use 'none' (bypass scheduler)
for dev_path in "${NVME_WAL_DEV}" "${NVME_DATA_DEV}"; do
    [[ -z "${dev_path}" ]] && continue
    dev=$(basename "${dev_path}")
    sched_file="/sys/block/${dev}/queue/scheduler"
    if [[ -f "$sched_file" ]]; then
        echo none > "$sched_file" && pass "I/O scheduler for ${dev} set to none (NVMe bypass)"
    else
        warn "Scheduler file not found for ${dev}: ${sched_file}"
    fi
done

# Readahead: WAL = 2 MB sequential, DATA = 128 KB random
if [[ -n "${NVME_WAL_DEV}" ]] && command -v blockdev >/dev/null 2>&1; then
    blockdev --setra 4096 "${NVME_WAL_DEV}" 2>/dev/null && pass "WAL readahead: 4096 sectors (2MB) on ${NVME_WAL_DEV}"
fi
if [[ -n "${NVME_DATA_DEV}" ]] && command -v blockdev >/dev/null 2>&1; then
    blockdev --setra 256 "${NVME_DATA_DEV}" 2>/dev/null && pass "DATA readahead: 256 sectors (128KB) on ${NVME_DATA_DEV}"
fi

# NUMA: detect topology and log recommendation
if command -v numactl >/dev/null 2>&1; then
    NUMA_NODES=$(numactl --hardware 2>/dev/null | grep -c "^node [0-9]" || echo 1)
    if [[ $NUMA_NODES -gt 1 ]]; then
        info "NUMA: ${NUMA_NODES} nodes detected"
        info "NUMA tip: pin PostgreSQL to a single node for memory locality, or use --interleave=all"
        info "  systemd override: ExecStart=/usr/bin/numactl --interleave=all /usr/lib/postgresql/${PG_VERSION}/bin/postgres"
    else
        pass "NUMA: single-node system — no NUMA pinning needed"
    fi
fi

# CPU: stop irqbalance on dedicated DB servers to reduce interrupt jitter
if systemctl is-active --quiet irqbalance 2>/dev/null; then
    info "irqbalance is running — on a dedicated DB server, consider: systemctl stop irqbalance"
fi

pass "Kernel parameters applied"

if [[ $RELOAD -eq 1 ]]; then
    info "Reloading PostgreSQL..."
    sudo -u postgres psql -c "SELECT pg_reload_conf();" 2>/dev/null \
        && pass "PostgreSQL configuration reloaded" \
        || fail "pg_reload_conf() failed"
else
    info "Use --reload or 'SELECT pg_reload_conf();' to apply without restart"
    info "Note: shared_buffers and max_connections require a full restart"
fi

echo ""
pass "Tuning complete. Verify with: sudo -u postgres psql -c 'SHOW shared_buffers;'"
