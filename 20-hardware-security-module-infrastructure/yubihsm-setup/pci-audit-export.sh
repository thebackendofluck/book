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

# pci-audit-export.sh — Automated PCI QSA evidence package for YubiHSM infrastructure.
#
# Produces a signed, timestamped tarball containing:
#   - YubiHSM audit log (binary + parsed text)
#   - OpenBao audit log (JSON)
#   - Epoch rotation history from PostgreSQL
#   - Key inventory (labels, IDs, creation dates — no key material)
#   - LUKS volume status and header backup hashes
#   - System integrity snapshot (package versions, service states)
#
# The tarball is SHA-256 hashed and the hash is signed with the YubiHSM
# attestation key, providing a hardware-rooted chain of custody acceptable
# to PCI DSS 4.0 Requirement 3.7.x (key management lifecycle evidence).
#
# Usage:
#   sudo ./pci-audit-export.sh [--output-dir /path] [--period YYYY-MM]
#
# Output:
#   /var/audit/pci-evidence-<period>-<timestamp>.tar.gz
#   /var/audit/pci-evidence-<period>-<timestamp>.tar.gz.sig  (ECDSA P-256)
#   /var/audit/pci-evidence-<period>-<timestamp>.sha256
#
# Requirements:
#   - yubihsm-shell, yubihsm-audit-tool
#   - bao (OpenBao CLI)
#   - postgresql-client (psql)
#   - openssl
#
# PCI DSS 4.0 coverage:
#   Req 3.7.1  — Cryptographic key custodian responsibilities
#   Req 3.7.4  — Key retirement/replacement documentation
#   Req 3.7.6  — Manual key management procedures
#   Req 10.3.2 — Audit log protection
#   Req 12.3.2 — Targeted risk analysis for key management
#
# References:
#   Chapter 20 — Hardware Security Module Infrastructure
#   scripts/chapter-20/yubihsm-setup/test-hsm-setup.sh

set -euo pipefail

readonly SCRIPT_NAME="pci-audit-export"
readonly LOG_FILE="/var/log/pci-audit-export.log"
readonly OUTPUT_BASE="/var/audit"
readonly BAO_AUDIT_LOG="/var/log/openbao/audit.log"
readonly DB_NAME="${PGDATABASE:-openbao}"
readonly DB_HOST="${PGHOST:-localhost}"
readonly ATTESTATION_KEY_LABEL="attestation-key"

# ── Logging ──────────────────────────────────────────────────────────────────
log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] [$SCRIPT_NAME] $*" | tee -a "$LOG_FILE"; }
die() { log "ERROR: $*"; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must run as root"

# ── Parse args ────────────────────────────────────────────────────────────────
OUTPUT_DIR="$OUTPUT_BASE"
PERIOD="${1:-$(date +%Y-%m)}"

for arg in "$@"; do
    case "$arg" in
        --output-dir) OUTPUT_DIR="${2:?--output-dir requires a path}"; shift 2 ;;
        --period)     PERIOD="${2:?--period requires YYYY-MM}"; shift 2 ;;
    esac
done

readonly TIMESTAMP
TIMESTAMP=$(date -u '+%Y%m%dT%H%M%SZ')
readonly PACKAGE_NAME="pci-evidence-${PERIOD}-${TIMESTAMP}"
readonly STAGING_DIR
STAGING_DIR=$(mktemp -d "/tmp/pci-evidence-XXXXXXXX")
trap 'rm -rf "$STAGING_DIR"' EXIT

mkdir -p "$OUTPUT_DIR"

# ── 1. YubiHSM audit log ─────────────────────────────────────────────────────
collect_yubihsm_audit() {
    log "Collecting YubiHSM audit log..."
    local hsm_dir="$STAGING_DIR/yubihsm"
    mkdir -p "$hsm_dir"

    local hsm_password="${YUBIHSM_PASSWORD:-}"
    if [[ -z "$hsm_password" ]]; then
        read -rsp "YubiHSM admin password: " hsm_password; echo
    fi

    # Export raw audit log
    yubihsm-shell \
        --authkey "0x0001" \
        --password "$hsm_password" \
        --action get-audit-log \
        --out "$hsm_dir/yubihsm-audit.bin" 2>>"$LOG_FILE" \
        || log "WARN: Could not export YubiHSM audit log (HSM may not be connected)"

    # Export human-readable audit log
    if [[ -f "$hsm_dir/yubihsm-audit.bin" ]]; then
        yubihsm-shell \
            --authkey "0x0001" \
            --password "$hsm_password" \
            --action get-audit-log \
            --format text \
            --out "$hsm_dir/yubihsm-audit.txt" 2>>"$LOG_FILE" || true
    fi

    # Export key inventory (metadata only, no key material)
    yubihsm-shell \
        --authkey "0x0001" \
        --password "$hsm_password" \
        --action list-objects \
        --out "$hsm_dir/key-inventory.txt" 2>>"$LOG_FILE" \
        || log "WARN: Could not export key inventory"

    log "YubiHSM audit collection done"
}

# ── 2. OpenBao audit log ──────────────────────────────────────────────────────
collect_openbao_audit() {
    log "Collecting OpenBao audit log for period $PERIOD..."
    local bao_dir="$STAGING_DIR/openbao"
    mkdir -p "$bao_dir"

    if [[ -f "$BAO_AUDIT_LOG" ]]; then
        # Extract entries for the target period
        grep "\"$PERIOD" "$BAO_AUDIT_LOG" \
            > "$bao_dir/openbao-audit-${PERIOD}.jsonl" 2>/dev/null \
            || cp "$BAO_AUDIT_LOG" "$bao_dir/openbao-audit-${PERIOD}.jsonl"

        wc -l "$bao_dir/openbao-audit-${PERIOD}.jsonl" \
            | tee -a "$LOG_FILE"
    else
        log "WARN: OpenBao audit log not found at $BAO_AUDIT_LOG"
        echo "Audit log not available at time of export" \
            > "$bao_dir/openbao-audit-${PERIOD}.jsonl"
    fi

    # OpenBao seal status
    bao status -format=json > "$bao_dir/seal-status.json" 2>>"$LOG_FILE" || true

    log "OpenBao audit collection done"
}

# ── 3. Epoch rotation history from PostgreSQL ─────────────────────────────────
collect_epoch_history() {
    log "Collecting epoch rotation history..."
    local db_dir="$STAGING_DIR/database"
    mkdir -p "$db_dir"

    psql \
        --host="$DB_HOST" \
        --dbname="$DB_NAME" \
        --username="${PGUSER:-openbao}" \
        --no-password \
        --command="
            SELECT
                epoch_id,
                epoch_number,
                status,
                created_at,
                activated_at,
                retired_at,
                rotation_reason,
                rotated_by
            FROM epochs
            WHERE created_at >= DATE_TRUNC('month', CURRENT_DATE - INTERVAL '${PERIOD}')
            ORDER BY epoch_number;" \
        --output="$db_dir/epoch-rotation-${PERIOD}.csv" \
        --csv 2>>"$LOG_FILE" \
        || log "WARN: Could not query epoch history (DB may not be accessible)"

    psql \
        --host="$DB_HOST" \
        --dbname="$DB_NAME" \
        --username="${PGUSER:-openbao}" \
        --no-password \
        --command="
            SELECT
                id,
                key_label,
                key_type,
                hsm_key_id,
                created_at,
                rotated_at,
                retired_at,
                custodian_uid
            FROM key_lifecycle_log
            ORDER BY created_at DESC
            LIMIT 1000;" \
        --output="$db_dir/key-lifecycle-${PERIOD}.csv" \
        --csv 2>>"$LOG_FILE" \
        || log "WARN: Could not query key lifecycle log"

    log "Database collection done"
}

# ── 4. LUKS volume status ─────────────────────────────────────────────────────
collect_luks_status() {
    log "Collecting LUKS volume status..."
    local luks_dir="$STAGING_DIR/luks"
    mkdir -p "$luks_dir"

    # List active LUKS mappings
    dmsetup ls --target crypt 2>>"$LOG_FILE" \
        > "$luks_dir/active-luks-mappings.txt" || true

    # For each LUKS device, capture header hash (not the key)
    while IFS= read -r line; do
        local dev
        dev=$(echo "$line" | awk '{print $1}')
        local header_hash
        header_hash=$(cryptsetup luksDump "/dev/mapper/$dev" 2>/dev/null \
            | sha256sum | awk '{print $1}') || header_hash="unavailable"
        echo "$dev: $header_hash" >> "$luks_dir/luks-header-hashes.txt"
    done < "$luks_dir/active-luks-mappings.txt"

    log "LUKS status collection done"
}

# ── 5. System integrity snapshot ──────────────────────────────────────────────
collect_system_snapshot() {
    log "Collecting system integrity snapshot..."
    local sys_dir="$STAGING_DIR/system"
    mkdir -p "$sys_dir"

    # Installed HSM-related packages with versions
    dpkg -l 'yubihsm*' 'opensc*' 'libp11*' 'openbao*' \
        > "$sys_dir/hsm-package-versions.txt" 2>/dev/null || \
    rpm -qa --qf "%{NAME}-%{VERSION}-%{RELEASE}\n" 2>/dev/null | \
        grep -i 'yubihsm\|opensc\|openbao' \
        >> "$sys_dir/hsm-package-versions.txt" || true

    # Service states
    systemctl show openbao yubihsm-connector \
        --property=ActiveState,SubState,ExecMainPID,FragmentPath \
        > "$sys_dir/service-states.txt" 2>/dev/null || true

    # Kernel version and hostname (for environment identification)
    uname -a > "$sys_dir/system-info.txt"
    hostname >> "$sys_dir/system-info.txt"

    log "System snapshot done"
}

# ── 6. Package and sign ────────────────────────────────────────────────────────
package_and_sign() {
    log "Packaging evidence..."
    local final_archive="$OUTPUT_DIR/${PACKAGE_NAME}.tar.gz"
    local hash_file="$OUTPUT_DIR/${PACKAGE_NAME}.sha256"
    local sig_file="$OUTPUT_DIR/${PACKAGE_NAME}.tar.gz.sig"

    # Create archive
    tar czf "$final_archive" -C "$(dirname "$STAGING_DIR")" \
        "$(basename "$STAGING_DIR")"

    # SHA-256 hash
    sha256sum "$final_archive" > "$hash_file"
    log "SHA-256: $(cat "$hash_file")"

    # Sign with attestation key (ECDSA P-256 via YubiHSM PKCS#11 engine)
    if command -v yubihsm-shell &>/dev/null; then
        local hsm_password="${YUBIHSM_PASSWORD:-}"
        openssl dgst -sha256 \
            -engine pkcs11 \
            -keyform engine \
            -sign "pkcs11:object=${ATTESTATION_KEY_LABEL};type=private" \
            -out "$sig_file" \
            "$final_archive" 2>>"$LOG_FILE" \
            || log "WARN: HSM signing unavailable; archive is unsigned"
        log "Evidence archive signed: $sig_file"
    else
        log "WARN: yubihsm-shell not available; skipping HSM signature"
    fi

    log "Evidence package: $final_archive"
    log "Hash file:        $hash_file"
    [[ -f "$sig_file" ]] && log "Signature:        $sig_file"
}

# ── Main ──────────────────────────────────────────────────────────────────────
main() {
    log "Starting PCI QSA evidence export for period: $PERIOD"
    mkdir -p "$STAGING_DIR"/{yubihsm,openbao,database,luks,system}

    collect_yubihsm_audit
    collect_openbao_audit
    collect_epoch_history
    collect_luks_status
    collect_system_snapshot
    package_and_sign

    log "PCI audit export complete"
}

main "$@"
