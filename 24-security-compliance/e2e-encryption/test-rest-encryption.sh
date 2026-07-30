#!/usr/bin/env bash
# Companion code for "The Backend of Luck" - Chapter 24, Security and Compliance.
# https://thebackendofluck.com | https://github.com/thebackendofluck/book
# SPDX-License-Identifier: Apache-2.0
#
# FOR TESTING AND EVALUATION ONLY. NOT FOR PRODUCTION USE.
# Published to demonstrate the patterns explained in the book. This code is
# not certified for real-money gaming: operating a gambling platform requires
# your own licence, independent test-lab certification (GLI, eCOGRA or
# equivalent) and regulator approval.

# test-rest-encryption.sh — Verify encryption at rest across all storage layers
# Tests LUKS2 volumes, PostgreSQL TDE, column-level encryption, S3/Wasabi SSE,
# and scans for unencrypted data on disk.
#
# Usage:
#   ./test-rest-encryption.sh [--target HOST] [--report /path/to/report.log]
#
# Compliance: PCI DSS v4.0.1 Req.3.5.1; GDPR Art.32; ISO 27001:2022 A.8.24

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
TARGET_HOST="${TARGET_HOST:-localhost}"
REPORT_FILE="${REPORT_FILE:-/tmp/rest-encryption-test.log}"
PG_HOST="${PG_HOST:-${TARGET_HOST}}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-postgres}"
PG_DB="${PG_DB:-postgres}"
PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log()    { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${REPORT_FILE}"; }
pass()   { log "  PASS  $*"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail()   { log "  FAIL  $*"; FAIL_COUNT=$((FAIL_COUNT + 1)); }
warn()   { log "  WARN  $*"; WARN_COUNT=$((WARN_COUNT + 1)); }
section(){ log ""; log "=== $* ==="; }

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        warn "Command '$1' not found — skipping related tests"
        return 1
    fi
    return 0
}

pg_query() {
    PGPASSWORD="${PG_PASSWORD:-}" \
        psql -h "${PG_HOST}" -p "${PG_PORT}" \
        -U "${PG_USER}" -d "${PG_DB}" \
        -t -A -c "$1" 2>/dev/null || echo ""
}

# ---------------------------------------------------------------------------
# Test 1: LUKS2 disk encryption
# ---------------------------------------------------------------------------
test_luks_encryption() {
    section "LUKS2 Disk Encryption Tests"

    if ! require_cmd cryptsetup; then return; fi

    # List block devices and check for LUKS
    local devices
    devices=$(lsblk -o NAME,FSTYPE -p -l 2>/dev/null | awk '$2=="crypto_LUKS"{print $1}' || true)

    if [ -z "${devices}" ]; then
        # No LUKS devices — check if any data volumes exist unencrypted
        local data_devices
        data_devices=$(lsblk -o NAME,MOUNTPOINT -p -l 2>/dev/null | \
            awk '$2~/\/var\/lib\/postgresql|\/var\/lib\/redis|\/data/{print $1}' || true)

        if [ -n "${data_devices}" ]; then
            fail "Data volumes found without LUKS encryption: ${data_devices}"
        else
            warn "No LUKS devices detected — either no block devices or not applicable (container env)"
        fi
        return
    fi

    local device_count=0
    while IFS= read -r dev; do
        [ -z "${dev}" ] && continue
        device_count=$((device_count + 1))

        local luks_info
        luks_info=$(cryptsetup luksDump "${dev}" 2>&1 || true)

        if echo "${luks_info}" | grep -q "Version:.*2"; then
            pass "LUKS2 confirmed on ${dev}"
        elif echo "${luks_info}" | grep -q "Version:.*1"; then
            warn "LUKS1 on ${dev} — upgrade to LUKS2 recommended (FIPS 140-3)"
        fi

        # Check cipher
        if echo "${luks_info}" | grep -q "aes-xts"; then
            pass "AES-XTS cipher confirmed on ${dev}"
        else
            local cipher
            cipher=$(echo "${luks_info}" | grep "Cipher name" | awk '{print $NF}' || echo "unknown")
            warn "Non-AES-XTS cipher on ${dev}: ${cipher}"
        fi

        # Check key size
        if echo "${luks_info}" | grep -qE "MK bits:.*512|Key size:.*512"; then
            pass "512-bit key (AES-XTS-256 effective) confirmed on ${dev}"
        else
            local keysize
            keysize=$(echo "${luks_info}" | grep -E "MK bits|Key size" | head -1 || echo "unknown")
            warn "Key size on ${dev}: ${keysize} — expected 512 bits for AES-XTS-256"
        fi

        # Check PBKDF
        if echo "${luks_info}" | grep -q "argon2id"; then
            pass "argon2id PBKDF confirmed on ${dev} (memory-hard, GPU-resistant)"
        else
            local pbkdf
            pbkdf=$(echo "${luks_info}" | grep "PBKDF:" | awk '{print $NF}' || echo "unknown")
            warn "PBKDF on ${dev}: ${pbkdf} — argon2id recommended"
        fi

    done <<< "${devices}"

    if [ "${device_count}" -eq 0 ]; then
        warn "No LUKS devices found"
    else
        pass "Checked ${device_count} LUKS device(s)"
    fi
}

# ---------------------------------------------------------------------------
# Test 2: PostgreSQL TDE
# ---------------------------------------------------------------------------
test_postgresql_tde() {
    section "PostgreSQL Transparent Data Encryption Tests"

    if ! require_cmd psql; then
        warn "psql not available — skipping PostgreSQL TDE tests"
        return
    fi

    # Check if pg_tde extension is installed
    local tde_ext
    tde_ext=$(pg_query "SELECT extname FROM pg_extension WHERE extname = 'pg_tde';" 2>/dev/null || echo "")
    if [ "${tde_ext}" = "pg_tde" ]; then
        pass "pg_tde extension is installed"
    else
        warn "pg_tde extension not found — TDE may not be configured on this instance"
    fi

    # Check if principal key is set
    local principal_key
    principal_key=$(pg_query "SELECT pg_tde_is_principal_key_set();" 2>/dev/null || echo "")
    if [ "${principal_key}" = "t" ]; then
        pass "pg_tde principal key is set"
    elif [ "${principal_key}" = "f" ]; then
        fail "pg_tde principal key NOT set — data not encrypted"
    else
        warn "Could not determine TDE principal key status"
    fi

    # Check for tables using TDE access method
    local encrypted_tables
    encrypted_tables=$(pg_query "
        SELECT count(*) FROM pg_class c
        JOIN pg_am a ON c.relam = a.oid
        WHERE a.amname = 'tde_heap'
          AND c.relkind = 'r';" 2>/dev/null || echo "0")

    if [ "${encrypted_tables:-0}" -gt "0" ]; then
        pass "${encrypted_tables} table(s) using TDE heap access method"
    else
        warn "No tables found using tde_heap — may be using standard heap with TDE wrapper"
    fi

    # Check ssl is on
    local ssl_status
    ssl_status=$(pg_query "SHOW ssl;" 2>/dev/null || echo "")
    if [ "${ssl_status}" = "on" ]; then
        pass "PostgreSQL SSL is enabled (ssl = on)"
    else
        fail "PostgreSQL SSL is disabled — connections may be unencrypted"
    fi

    # Check that WAL encryption is considered
    local wal_level
    wal_level=$(pg_query "SHOW wal_level;" 2>/dev/null || echo "")
    log "  INFO  PostgreSQL wal_level: ${wal_level:-unknown} (verify WAL files are on LUKS-encrypted volume)"
}

# ---------------------------------------------------------------------------
# Test 3: Column-level encryption roundtrip
# ---------------------------------------------------------------------------
test_column_encryption() {
    section "Column-Level Encryption Tests"

    if ! require_cmd psql; then
        warn "psql not available — skipping column encryption tests"
        return
    fi

    # Check for PII columns that should be encrypted
    local pii_columns
    pii_columns=$(pg_query "
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND column_name IN ('email', 'phone', 'date_of_birth',
                              'document_number', 'address', 'full_name')
        ORDER BY table_name, column_name;" 2>/dev/null || echo "")

    if [ -z "${pii_columns}" ]; then
        warn "No PII columns found in public schema — either no player tables or different schema"
        return
    fi

    # For each PII column, verify data looks encrypted (base64 or hex, not plaintext)
    local found_plain=0
    while IFS='|' read -r table_name column_name data_type; do
        [ -z "${table_name}" ] && continue

        # Sample 5 non-null values
        local sample
        sample=$(pg_query "
            SELECT ${column_name} FROM ${table_name}
            WHERE ${column_name} IS NOT NULL
            LIMIT 5;" 2>/dev/null || echo "")

        if [ -z "${sample}" ]; then
            warn "${table_name}.${column_name}: no data to sample"
            continue
        fi

        # Check if any value looks like a plaintext email address
        if echo "${sample}" | grep -qE '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'; then
            fail "${table_name}.${column_name}: contains PLAINTEXT email address — PCI DSS / GDPR violation"
            found_plain=$((found_plain + 1))
        # Check if any value looks like a plaintext phone number
        elif echo "${sample}" | grep -qE '^\+?[0-9]{7,15}$'; then
            fail "${table_name}.${column_name}: contains PLAINTEXT phone number"
            found_plain=$((found_plain + 1))
        # Check if value looks like base64-encoded ciphertext (expected)
        elif echo "${sample}" | grep -qE '^[A-Za-z0-9+/]{20,}={0,2}$'; then
            pass "${table_name}.${column_name}: data appears to be base64-encoded (encrypted)"
        else
            warn "${table_name}.${column_name}: data type '${data_type}' — manual review recommended"
        fi
    done <<< "${pii_columns}"

    if [ "${found_plain}" -eq 0 ]; then
        pass "No plaintext PII detected in sampled columns"
    fi
}

# ---------------------------------------------------------------------------
# Test 4: S3 / Wasabi server-side encryption
# ---------------------------------------------------------------------------
test_s3_encryption() {
    section "S3 / Wasabi SSE Tests"

    if ! require_cmd aws; then
        warn "AWS CLI not available — skipping S3 SSE tests"
        return
    fi

    local buckets
    buckets=$(aws s3api list-buckets \
        --query 'Buckets[].Name' \
        --output text 2>/dev/null || echo "")

    if [ -z "${buckets}" ]; then
        warn "No S3 buckets found (or AWS credentials not configured)"
        return
    fi

    for bucket in ${buckets}; do
        # Check server-side encryption configuration
        local sse_config
        sse_config=$(aws s3api get-bucket-encryption \
            --bucket "${bucket}" 2>/dev/null || echo "")

        if echo "${sse_config}" | grep -qE "AES256|aws:kms"; then
            local algorithm
            algorithm=$(echo "${sse_config}" | \
                python3 -c "import sys,json; d=json.load(sys.stdin); \
                    print(d['ServerSideEncryptionConfiguration']['Rules'][0]\
                    ['ApplyServerSideEncryptionByDefault']['SSEAlgorithm'])" \
                2>/dev/null || echo "configured")
            pass "Bucket '${bucket}': SSE enabled (${algorithm})"
        else
            fail "Bucket '${bucket}': SSE NOT configured — data stored unencrypted"
        fi

        # Check versioning (required for proper lifecycle management)
        local versioning
        versioning=$(aws s3api get-bucket-versioning \
            --bucket "${bucket}" \
            --query 'Status' --output text 2>/dev/null || echo "")
        if [ "${versioning}" = "Enabled" ]; then
            pass "Bucket '${bucket}': versioning enabled"
        else
            warn "Bucket '${bucket}': versioning not enabled — impacts GDPR deletion audit"
        fi

        # Check lifecycle rules for backup expiration
        local lifecycle
        lifecycle=$(aws s3api get-bucket-lifecycle-configuration \
            --bucket "${bucket}" 2>/dev/null || echo "")
        if [ -n "${lifecycle}" ]; then
            pass "Bucket '${bucket}': lifecycle rules configured"
        else
            warn "Bucket '${bucket}': no lifecycle rules — backup expiration not automated"
        fi
    done
}

# ---------------------------------------------------------------------------
# Test 5: Redis RDB encryption check
# ---------------------------------------------------------------------------
test_redis_encryption() {
    section "Redis Encryption-at-Rest Tests"

    local redis_rdb_paths=(
        "/var/lib/redis/dump.rdb"
        "/data/redis/dump.rdb"
        "/opt/redis/dump.rdb"
    )

    local rdb_found=0
    for rdb_path in "${redis_rdb_paths[@]}"; do
        if [ -f "${rdb_path}" ]; then
            rdb_found=1

            # RDB files start with "REDIS" if plaintext
            local header
            header=$(head -c 5 "${rdb_path}" 2>/dev/null || echo "")
            if [ "${header}" = "REDIS" ]; then
                warn "${rdb_path}: RDB file is plaintext — verify it is on LUKS-encrypted volume"
                # Check if the underlying filesystem is on a LUKS device
                local mount_point
                mount_point=$(df "${rdb_path}" 2>/dev/null | awk 'NR==2{print $1}' || echo "")
                local luks_backing
                luks_backing=$(lsblk -o NAME,TYPE,PKNAME -p -l 2>/dev/null | \
                    awk -v dev="${mount_point}" '$0~dev && $2=="crypt"{print "LUKS"}' || echo "")
                if [ "${luks_backing}" = "LUKS" ]; then
                    pass "${rdb_path}: RDB is on LUKS-encrypted volume (protected at block level)"
                else
                    fail "${rdb_path}: RDB not protected by LUKS or AES-RDB"
                fi
            else
                pass "${rdb_path}: RDB does not start with plaintext 'REDIS' header (encrypted)"
            fi
        fi
    done

    if [ "${rdb_found}" -eq 0 ]; then
        warn "No Redis RDB files found in standard locations — may be in container or non-standard path"
    fi

    # Check Redis config for TLS
    if require_cmd redis-cli 2>/dev/null; then
        local tls_port
        tls_port=$(redis-cli -h "${TARGET_HOST}" -p "${REDIS_TLS_PORT:-6380}" \
            --tls --no-auth-warning -a "${REDIS_PASSWORD:-}" \
            CONFIG GET tls-port 2>/dev/null | tail -1 || echo "")
        if [ -n "${tls_port}" ] && [ "${tls_port}" != "0" ]; then
            pass "Redis TLS port active: ${tls_port}"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Test 6: Kafka log encryption
# ---------------------------------------------------------------------------
test_kafka_encryption() {
    section "Kafka Log Encryption Tests"

    local kafka_log_dirs=(
        "/var/lib/kafka"
        "/opt/kafka/logs"
        "/data/kafka"
    )

    for kafka_dir in "${kafka_log_dirs[@]}"; do
        if [ -d "${kafka_dir}" ]; then
            # Check if log directory is on a LUKS volume
            local mount_point
            mount_point=$(df "${kafka_dir}" 2>/dev/null | awk 'NR==2{print $1}' || echo "")
            local luks_check
            luks_check=$(lsblk -o TYPE,MOUNTPOINT -p -l 2>/dev/null | \
                awk -v mp="${kafka_dir}" '$2==mp && $1=="crypt"{print "LUKS"}' || echo "")

            if [ "${luks_check}" = "LUKS" ]; then
                pass "Kafka log directory ${kafka_dir} is on LUKS-encrypted volume"
            else
                warn "Kafka log directory ${kafka_dir} — verify it is on encrypted storage"
            fi

            # Check for segment log files — they should not contain plaintext PII
            if find "${kafka_dir}" -maxdepth 1 -name "*.log" 2>/dev/null | head -1 | grep -q ".log"; then
                warn "Kafka .log files found — verify no PII in message payloads"
                log "  INFO  Kafka application-level encryption (AES per topic) recommended for PII topics"
            fi
        fi
    done

    # Check Kafka broker TLS config
    local kafka_configs=(
        "/etc/kafka/server.properties"
        "/opt/kafka/config/server.properties"
    )

    for kafka_conf in "${kafka_configs[@]}"; do
        if [ -f "${kafka_conf}" ]; then
            if grep -q "ssl.keystore.location" "${kafka_conf}"; then
                pass "Kafka SSL keystore configured in ${kafka_conf}"
            else
                fail "Kafka SSL not configured in ${kafka_conf}"
            fi

            if grep -q "SASL_SSL" "${kafka_conf}"; then
                pass "Kafka using SASL_SSL listener (authentication + encryption)"
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# Test 7: Scan for unencrypted data on disk
# ---------------------------------------------------------------------------
test_unencrypted_data_scan() {
    section "Unencrypted Data Scan (disk)"

    local scan_dirs=(
        "/tmp"
        "/var/log"
        "/var/www/html"
        "/opt/app"
    )

    local findings=0

    for scan_dir in "${scan_dirs[@]}"; do
        if [ ! -d "${scan_dir}" ]; then continue; fi

        log "  INFO  Scanning ${scan_dir} for PII patterns..."

        # Search for email patterns in text files (limit to readable files < 10MB)
        local email_hits
        email_hits=$(find "${scan_dir}" -type f -size -10M \
            \( -name "*.log" -o -name "*.txt" -o -name "*.json" -o -name "*.csv" \) \
            2>/dev/null | \
            xargs grep -l -E '[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}' \
            2>/dev/null | head -5 || true)

        if [ -n "${email_hits}" ]; then
            fail "Possible email addresses found in ${scan_dir}:"
            echo "${email_hits}" | while IFS= read -r f; do
                log "    FILE: ${f}"
            done
            findings=$((findings + 1))
        fi

        # Search for credit card PAN patterns
        local pan_hits
        pan_hits=$(find "${scan_dir}" -type f -size -10M \
            \( -name "*.log" -o -name "*.txt" -o -name "*.json" \) \
            2>/dev/null | \
            xargs grep -l -P '\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13})\b' \
            2>/dev/null | head -5 || true)

        if [ -n "${pan_hits}" ]; then
            fail "Possible credit card PANs found in ${scan_dir}:"
            echo "${pan_hits}" | while IFS= read -r f; do
                log "    FILE: ${f}"
            done
            findings=$((findings + 1))
        fi
    done

    if [ "${findings}" -eq 0 ]; then
        pass "No plaintext PII patterns found in scanned directories"
    fi

    log "  INFO  Run pii-scanner.py for comprehensive PII scan including database"
}

# ---------------------------------------------------------------------------
# Test 8: Check for secrets in environment variables
# ---------------------------------------------------------------------------
test_env_secrets() {
    section "Environment Variable / Docker Secret Tests"

    # Check running containers for secrets in ENV (requires docker)
    if require_cmd docker 2>/dev/null; then
        local containers
        containers=$(docker ps --format "{{.Names}}" 2>/dev/null || echo "")

        for container in ${containers}; do
            local env_vars
            env_vars=$(docker inspect "${container}" \
                --format '{{range .Config.Env}}{{.}}{{"\n"}}{{end}}' 2>/dev/null || true)

            # Look for obvious secret patterns
            local secret_vars
            secret_vars=$(echo "${env_vars}" | \
                grep -iE '(password|secret|key|token|api_key)=[^$]' | \
                grep -v '^\(.*\)=\$' | head -5 || true)

            if [ -n "${secret_vars}" ]; then
                warn "Container '${container}': possible secrets in ENV vars:"
                # Redact the values before logging
                echo "${secret_vars}" | sed 's/=.*/=[REDACTED]/' | \
                    while IFS= read -r line; do log "    ${line}"; done
            else
                pass "Container '${container}': no obvious plaintext secrets in ENV"
            fi
        done
    fi

    # Check current process environment for exposed secrets
    if [ -f /proc/self/environ ]; then
        local proc_secrets
        proc_secrets=$(tr '\0' '\n' </proc/self/environ 2>/dev/null | \
            grep -iE '(password|secret|key|token)=' | \
            grep -v 'PATH\|_HOME\|KEYRING' | head -5 || true)
        if [ -n "${proc_secrets}" ]; then
            warn "Current process has secret-like environment variables (expected if running in service context)"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print_summary() {
    section "Test Summary"
    log "  Total PASS:    ${PASS_COUNT}"
    log "  Total WARN:    ${WARN_COUNT}"
    log "  Total FAIL:    ${FAIL_COUNT}"
    log ""
    log "  Report saved to: ${REPORT_FILE}"
    log ""

    if [ "${FAIL_COUNT}" -gt 0 ]; then
        log "  STATUS: FAIL — ${FAIL_COUNT} test(s) failed. Remediate before PCI DSS audit."
        return 1
    elif [ "${WARN_COUNT}" -gt 0 ]; then
        log "  STATUS: WARN — review warnings before next compliance review."
        return 0
    else
        log "  STATUS: PASS — all at-rest encryption tests passed."
        return 0
    fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --target)  TARGET_HOST="$2";  PG_HOST="${PG_HOST:-$2}"; shift 2 ;;
            --report)  REPORT_FILE="$2";  shift 2 ;;
            --pg-host) PG_HOST="$2";      shift 2 ;;
            --pg-user) PG_USER="$2";      shift 2 ;;
            --pg-db)   PG_DB="$2";        shift 2 ;;
            *) echo "Unknown option: $1"; exit 1 ;;
        esac
    done

    : >"${REPORT_FILE}"
    log "=== At-Rest Encryption Test Suite ==="
    log "Target:     ${TARGET_HOST}"
    log "PG Host:    ${PG_HOST}:${PG_PORT}"
    log "Started:    $(date)"
    log "Compliance: PCI DSS v4.0.1 Req.3.5.1; GDPR Art.32; ISO 27001:2022 A.8.24"

    test_luks_encryption
    test_postgresql_tde
    test_column_encryption
    test_s3_encryption
    test_redis_encryption
    test_kafka_encryption
    test_unencrypted_data_scan
    test_env_secrets
    print_summary
}

main "$@"
