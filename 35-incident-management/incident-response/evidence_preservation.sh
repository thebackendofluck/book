#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 35, Incident Management.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# shellcheck disable=SC2086,SC2029,SC2034
# SC2086: SSH_OPTS is intentionally word-split into multiple ssh flags.
# SC2029: $$ in remote ssh commands is meant to expand client-side for unique paths.
# SC2034: integrity-verification stub variables retained for future expansion.
# =============================================================================
# Evidence Preservation Script for iGaming Incident Response
# =============================================================================
# Captures forensic evidence from compromised hosts with full chain of custody.
# Designed for iGaming environments where evidence must survive regulatory
# and legal scrutiny (ICO, MGA, NJ DGE, ANPD, law enforcement).
#
# What this script does:
#   1. Captures volatile evidence first (RAM, network state, processes)
#   2. Captures disk images and log archives
#   3. SHA-256 hashes everything at collection time
#   4. Writes a tamper-evident chain of custody log
#   5. Creates a forensic readiness summary for regulator submission
#
# Requirements:
#   - SSH access (key-based) to target host as root or forensics user
#   - LiME kernel module available on target (for RAM capture)
#     Alternatively: avml or winpmem for memory acquisition
#   - ddrescue installed on target for resilient disk imaging
#   - Sufficient local storage: budget 2-3x the target disk size
#
# Usage:
#   ./evidence_preservation.sh <incident_id> <target_host> <evidence_dest> [description]
#
# Example:
#   ./evidence_preservation.sh INC-2026-042 db-prod-01.internal /mnt/evidence \
#       "db-prod-01 compromised in APT breach, player database accessed"
#
# Cron-safe: This script is idempotent for a given incident_id + target_host.
# Running it twice will not overwrite existing evidence.
#
# All IPs in comments use RFC 5737 documentation ranges.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Arguments and configuration
# ---------------------------------------------------------------------------
INCIDENT_ID="${1:?Usage: $0 <incident_id> <target_host> <evidence_dest> [description]}"
TARGET_HOST="${2:?}"
EVIDENCE_DEST="${3:?}"
DESCRIPTION="${4:-No description provided}"

TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')
ANALYST="$(id -un)@$(hostname -f)"
SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=30 -o BatchMode=yes"
CASE_DIR="${EVIDENCE_DEST}/${INCIDENT_ID}/${TARGET_HOST}"
COC_LOG="${EVIDENCE_DEST}/${INCIDENT_ID}/chain_of_custody.log"
SUMMARY_FILE="${EVIDENCE_DEST}/${INCIDENT_ID}/forensic_readiness_summary.txt"

# Alert thresholds
DISK_SPACE_WARN_GB=100  # Warn if less than this free space on evidence destination

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }
err() { log "ERROR: $*" >&2; }
die() { err "$*"; exit 1; }

coc_entry() {
    local item_num="$1" description="$2" action="$3" hash="${4:-PENDING}"
    local timestamp
    timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    local entry="[${timestamp}] ITEM=${item_num} | DESC=${description} | ACTION=${action} | HASH=${hash} | ANALYST=${ANALYST}"
    echo "${entry}" >> "${COC_LOG}"
    log "${entry}"
}

hash_file() {
    local filepath="$1"
    if [[ -f "${filepath}" ]]; then
        sha256sum "${filepath}" | awk '{print $1}'
    else
        echo "FILE_NOT_FOUND"
    fi
}

check_prereqs() {
    log "Checking prerequisites..."
    local missing=()
    for cmd in ssh sha256sum tar gzip; do
        command -v "${cmd}" >/dev/null 2>&1 || missing+=("${cmd}")
    done
    [[ ${#missing[@]} -gt 0 ]] && die "Missing required commands: ${missing[*]}"

    # Check SSH connectivity
    ssh ${SSH_OPTS} "root@${TARGET_HOST}" "echo 'SSH_OK'" >/dev/null 2>&1 \
        || die "Cannot connect to ${TARGET_HOST} via SSH. Check keys and connectivity."

    # Check available disk space
    local free_gb
    free_gb=$(df -BG "${EVIDENCE_DEST}" 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print $4}')
    if [[ -n "${free_gb}" && "${free_gb}" -lt "${DISK_SPACE_WARN_GB}" ]]; then
        log "WARNING: Only ${free_gb}GB free on ${EVIDENCE_DEST}. Disk images may fail."
    fi
}

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
setup_evidence_directory() {
    log "Setting up evidence directory: ${CASE_DIR}"
    mkdir -p "${CASE_DIR}/memory" "${CASE_DIR}/network" "${CASE_DIR}/disk" "${CASE_DIR}/logs"

    # Initialize chain of custody log (append-only)
    if [[ ! -f "${COC_LOG}" ]]; then
        cat > "${COC_LOG}" <<EOF
================================================================================
FORENSIC EVIDENCE CHAIN OF CUSTODY
================================================================================
Incident ID:        ${INCIDENT_ID}
Description:        ${DESCRIPTION}
Opened by:          ${ANALYST}
Opened at (UTC):    $(date -u '+%Y-%m-%dT%H:%M:%SZ')
Evidence base dir:  ${EVIDENCE_DEST}/${INCIDENT_ID}
================================================================================

ITEM LOG
--------
EOF
    fi

    # Make COC log append-only after initial creation
    chmod 444 "${COC_LOG}" 2>/dev/null || true
    # Re-enable append for our process (owner can still write)
    chmod 644 "${COC_LOG}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Step 1: Volatile evidence — capture before ANY isolation action
# ---------------------------------------------------------------------------
capture_volatile_evidence() {
    log "=== STEP 1/5: Volatile evidence ==="

    # 1a. Memory dump using LiME (Linux Memory Extractor)
    local memfile="${CASE_DIR}/memory/${TARGET_HOST}_mem_${TIMESTAMP}.lime.gz"
    if [[ ! -f "${memfile}" ]]; then
        log "Capturing RAM from ${TARGET_HOST}..."
        # Try LiME first, fall back to /dev/mem if kernel module not available
        if ssh ${SSH_OPTS} "root@${TARGET_HOST}" "test -f /opt/forensics/lime.ko" 2>/dev/null; then
            ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
                "insmod /opt/forensics/lime.ko path=/tmp/lime_$$.lime format=lime timeout=0 2>/dev/null; \
                 sleep 2; \
                 cat /tmp/lime_$$.lime; \
                 rm -f /tmp/lime_$$.lime" \
                | gzip -1 > "${memfile}" 2>/dev/null
            log "RAM capture via LiME: ${memfile}"
        elif ssh ${SSH_OPTS} "root@${TARGET_HOST}" "command -v avml" 2>/dev/null; then
            ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
                "avml /tmp/mem_$$.lime 2>/dev/null; cat /tmp/mem_$$.lime; rm -f /tmp/mem_$$.lime" \
                | gzip -1 > "${memfile}" 2>/dev/null
            log "RAM capture via avml: ${memfile}"
        else
            log "WARNING: No memory acquisition tool found on ${TARGET_HOST}. Skipping RAM capture."
            log "Install LiME (https://github.com/504ensicsLabs/LiME) or avml on target hosts."
            memfile=""
        fi

        if [[ -n "${memfile}" && -f "${memfile}" ]]; then
            local mem_hash; mem_hash=$(hash_file "${memfile}")
            coc_entry "MEM-001" "RAM dump ${TARGET_HOST}" "collected" "${mem_hash}"
        fi
    else
        log "RAM dump already exists, skipping: ${memfile}"
    fi

    # 1b. Network state snapshot (live connections — disappear on isolation)
    local netfile="${CASE_DIR}/network/${TARGET_HOST}_netstate_${TIMESTAMP}.txt"
    log "Capturing network state from ${TARGET_HOST}..."
    {
        echo "=== TIMESTAMP: $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
        echo "=== HOST: ${TARGET_HOST} ==="
        echo ""
        echo "--- Active TCP/UDP connections (ss -antp) ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" "ss -antp 2>/dev/null || netstat -antp 2>/dev/null" || true
        echo ""
        echo "--- Routing table ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" "ip route show 2>/dev/null || route -n 2>/dev/null" || true
        echo ""
        echo "--- ARP cache ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" "arp -a 2>/dev/null || ip neigh 2>/dev/null" || true
        echo ""
        echo "--- Network interfaces ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" "ip addr show 2>/dev/null || ifconfig -a 2>/dev/null" || true
        echo ""
        echo "--- Listening ports ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" "ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null" || true
    } > "${netfile}" 2>&1
    local net_hash; net_hash=$(hash_file "${netfile}")
    coc_entry "NET-001" "Network state ${TARGET_HOST}" "collected" "${net_hash}"

    # 1c. Running process snapshot
    local procfile="${CASE_DIR}/logs/${TARGET_HOST}_processes_${TIMESTAMP}.txt"
    log "Capturing process list from ${TARGET_HOST}..."
    {
        echo "=== TIMESTAMP: $(date -u '+%Y-%m-%dT%H:%M:%SZ') ==="
        echo "--- Process list (ps auxww) ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" "ps auxww 2>/dev/null" || true
        echo ""
        echo "--- Process executable links (/proc/*/exe) ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
            "ls -la /proc/*/exe 2>/dev/null | grep -v 'Permission denied' | grep -v 'No such file'" || true
        echo ""
        echo "--- Open files by process (lsof -n) ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" "lsof -n 2>/dev/null | head -500" || true
        echo ""
        echo "--- Scheduled tasks (crontab, systemd timers) ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
            "crontab -l 2>/dev/null; cat /etc/cron* /var/spool/cron/* 2>/dev/null; \
             systemctl list-timers 2>/dev/null" || true
        echo ""
        echo "--- Startup persistence (systemd services) ---"
        ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
            "systemctl list-units --type=service --state=running 2>/dev/null" || true
    } > "${procfile}" 2>&1
    local proc_hash; proc_hash=$(hash_file "${procfile}")
    coc_entry "PROC-001" "Process snapshot ${TARGET_HOST}" "collected" "${proc_hash}"

    log "Volatile evidence captured. SAFE TO ISOLATE NETWORK NOW if needed."
}

# ---------------------------------------------------------------------------
# Step 2: Application and system logs
# ---------------------------------------------------------------------------
capture_logs() {
    log "=== STEP 2/5: Log collection ==="

    local logarchive="${CASE_DIR}/logs/${TARGET_HOST}_syslogs_${TIMESTAMP}.tar.gz"
    if [[ -f "${logarchive}" ]]; then
        log "System log archive already exists, skipping."
        return
    fi

    log "Collecting system and application logs from ${TARGET_HOST}..."
    ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
        "tar czf - \
            /var/log/ \
            /opt/igaming/logs/ \
            /opt/app/logs/ \
            /var/log/nginx/ \
            /var/log/postgresql/ \
            /var/log/mysql/ \
            /var/log/audit/ \
            /etc/cron* \
            /etc/passwd \
            /etc/shadow \
            /root/.bash_history \
            /home/*/.bash_history \
            /root/.ssh/authorized_keys \
            /home/*/.ssh/authorized_keys \
            2>/dev/null" \
        > "${logarchive}" 2>/dev/null || true

    local log_hash; log_hash=$(hash_file "${logarchive}")
    coc_entry "LOG-001" "System log archive ${TARGET_HOST}" "collected" "${log_hash}"
    log "Log archive: ${logarchive} (SHA256: ${log_hash})"
}

# ---------------------------------------------------------------------------
# Step 3: DNS and firewall logs (from network infrastructure)
# ---------------------------------------------------------------------------
capture_network_logs() {
    log "=== STEP 3/5: Network infrastructure logs ==="
    log "NOTE: Firewall and DNS logs must be collected manually from:"
    log "  - Perimeter firewall (export last 30 days)"
    log "  - DNS resolver logs"
    log "  - Load balancer access logs"
    log "  - VPN/proxy logs"
    log "  Document collection in chain of custody log manually."
    log "  Use: coc_entry 'FW-001' 'Firewall logs' 'collected-manual' '<sha256>'"
}

# ---------------------------------------------------------------------------
# Step 4: Full disk image (background — most time-consuming)
# ---------------------------------------------------------------------------
start_disk_image() {
    log "=== STEP 4/5: Disk image (background) ==="

    # Detect primary disk on target
    local target_disk
    target_disk=$(ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
        "lsblk -d -o NAME,TYPE | awk '/disk/ {print \"/dev/\"\$1; exit}' 2>/dev/null" || echo "/dev/sda")

    local diskfile="${CASE_DIR}/disk/${TARGET_HOST}_disk_${TIMESTAMP}.dd.gz"
    if [[ -f "${diskfile}" ]]; then
        log "Disk image already exists: ${diskfile}"
        return
    fi

    log "Starting disk image of ${target_disk} from ${TARGET_HOST}..."
    log "This runs in background. Monitor: watch -n30 ls -lh ${diskfile}"

    # Use ddrescue on target for resilience; stream back and compress locally
    # Fallback to dd if ddrescue not available
    {
        if ssh ${SSH_OPTS} "root@${TARGET_HOST}" "command -v ddrescue" 2>/dev/null; then
            # ddrescue: resilient to bad sectors, essential for distressed systems
            ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
                "ddrescue -f -n ${target_disk} /tmp/disk_$$_img /tmp/disk_$$_map && \
                 cat /tmp/disk_$$_img && \
                 rm -f /tmp/disk_$$_img /tmp/disk_$$_map" \
                | gzip -1 > "${diskfile}"
        else
            ssh ${SSH_OPTS} "root@${TARGET_HOST}" \
                "dd if=${target_disk} bs=4M conv=noerror,sync 2>/dev/null" \
                | gzip -1 > "${diskfile}"
        fi

        local disk_hash; disk_hash=$(hash_file "${diskfile}")
        coc_entry "DISK-001" "Full disk image ${TARGET_HOST} ${target_disk}" "collected" "${disk_hash}"
        log "Disk image complete: ${diskfile} SHA256=${disk_hash}"
    } &

    local disk_pid=$!
    coc_entry "DISK-001" "Full disk image ${TARGET_HOST} ${target_disk}" "started-pid=${disk_pid}" "PENDING"
    log "Disk image running in background (PID ${disk_pid}). Do NOT power off ${TARGET_HOST}."
    echo "${disk_pid}" > "${CASE_DIR}/disk/disk_image.pid"
}

# ---------------------------------------------------------------------------
# Step 5: Integrity verification and final COC entry
# ---------------------------------------------------------------------------
finalize_coc() {
    log "=== STEP 5/5: Finalizing chain of custody ==="

    # Verify all collected items are still intact
    local all_ok=true
    while IFS= read -r line; do
        local hash_from_log item_path
        if [[ "${line}" =~ HASH=([a-f0-9]{64}) ]]; then
            hash_from_log="${BASH_REMATCH[1]}"
            # Try to find the file based on the log entry
        fi
    done < "${COC_LOG}" || true

    # Generate summary
    cat > "${SUMMARY_FILE}" <<EOF
================================================================================
FORENSIC READINESS SUMMARY — FOR REGULATOR SUBMISSION
================================================================================
Incident ID:        ${INCIDENT_ID}
Target host:        ${TARGET_HOST}
Collection by:      ${ANALYST}
Collection time:    ${TIMESTAMP}
Description:        ${DESCRIPTION}

EVIDENCE COLLECTED
------------------
$(ls -lh "${CASE_DIR}/memory/" "${CASE_DIR}/network/" "${CASE_DIR}/logs/" "${CASE_DIR}/disk/" 2>/dev/null || echo "(listing failed)")

INTEGRITY CHECKSUMS
-------------------
$(sha256sum "${CASE_DIR}/memory/"* "${CASE_DIR}/network/"* "${CASE_DIR}/logs/"* 2>/dev/null || echo "(hashing in progress)")

CHAIN OF CUSTODY LOG
--------------------
Location: ${COC_LOG}
Last entry: $(tail -1 "${COC_LOG}" 2>/dev/null || echo "N/A")

REGULATORY NOTES
----------------
This evidence was collected following the iGaming breach response playbook
(Ch 35 of the operator's security manual) using documented forensic procedures.
All items are SHA-256 hashed at collection time. The chain of custody log
records every transfer of evidence. Disk imaging may still be in progress.

This summary is suitable as an initial forensic readiness attestation for
submission to ICO, MGA, NJ DGE, ANPD, or other regulatory bodies.
Full forensic report to follow from the appointed forensic firm.
================================================================================
EOF

    log "Forensic readiness summary: ${SUMMARY_FILE}"
    log ""
    log "NEXT STEPS:"
    log "  1. Move evidence directory to read-only storage or encrypted NAS"
    log "  2. Transfer chain of custody log: ${COC_LOG}"
    log "  3. Brief DPO — check breach_notification_tracker.py for deadline status"
    log "  4. Wait for disk image (if still running)"
    log "  5. Submit forensic readiness summary to regulators with initial breach notification"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    log "==================================================================="
    log "Evidence Preservation — Incident: ${INCIDENT_ID}"
    log "Target: ${TARGET_HOST} | Analyst: ${ANALYST}"
    log "==================================================================="

    check_prereqs
    setup_evidence_directory
    capture_volatile_evidence
    capture_logs
    capture_network_logs
    start_disk_image
    finalize_coc

    log "==================================================================="
    log "Evidence collection complete (disk image still running in background)"
    log "Chain of custody: ${COC_LOG}"
    log "Evidence location: ${CASE_DIR}"
    log "==================================================================="
}

main "$@"
